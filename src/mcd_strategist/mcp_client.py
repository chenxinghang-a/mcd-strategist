"""麦当劳 MCP（Streamable HTTP）客户端。

协议要点（来源：麦当劳 MCP Server 使用指南 https://github.com/M-China/mcd-mcp-server）：
- 接入地址：https://mcp.mcd.cn，Streamable HTTP 协议（POST JSON-RPC 2.0）
- 鉴权：请求头 Authorization: Bearer <MCP Token>
- 响应可能是 application/json，也可能是 text/event-stream（SSE）
- initialize 握手后，服务端通过 Mcp-Session-Id 头维持会话
- 限流：每个 Token 每分钟最多 600 次请求，超出返回 429
- 401：Token 无效、过期或未提供

传输层是可替换的（transport），因此全部测试可以离线完成。
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

try:  # requests 是唯一硬依赖；缺失时给出明确错误而不是 ImportError 栈
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]

PROTOCOL_VERSION = "2025-03-26"
DEFAULT_URL = "https://mcp.mcd.cn"

Transport = Callable[[str, Dict[str, str], dict], Tuple[int, Dict[str, str], bytes]]
"""transport(url, headers, payload) -> (http_status, headers, body_bytes)"""


class McpError(Exception):
    """MCP 协议 / 传输层错误。"""


class UnauthorizedError(McpError):
    """401：MCP Token 无效、已过期或未提供。"""


class RateLimitError(McpError):
    """429：触发限流（每分钟 600 次）。"""


class ToolCallError(McpError):
    """MCP 返回 isError=True 或响应语义为业务错误。"""


def _parse_sse(body: bytes) -> dict:
    """从 SSE 流中提取 JSON-RPC 消息（取最后一个带 jsonrpc 字段的事件）。"""
    text = body.decode("utf-8", "replace")
    message: Optional[dict] = None
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        chunk = line[len("data:"):].strip()
        if not chunk or chunk == "[DONE]":
            continue
        try:
            obj = json.loads(chunk)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(obj, dict) and ("jsonrpc" in obj or "result" in obj or "error" in obj):
            message = obj
    if message is None:
        raise McpError(f"SSE 响应中没有可解析的 JSON-RPC 消息: {body[:200]!r}")
    return message


def _parse_response(content_type: str, body: bytes) -> dict:
    """兼容 application/json 与 text/event-stream 两种响应。"""
    if "text/event-stream" in (content_type or "").lower():
        return _parse_sse(body)
    if not body or not body.strip():
        return {}
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise McpError(f"无法解析 MCP 响应（content-type={content_type}）: {exc}") from exc


class _RateLimiter:
    """简单限速器：默认 8 QPS（官方上限 600/min = 10/s，留足余量）。

    注意：基于 time.monotonic() 做间隔控制，不使用绝对时间做初值。
    """

    def __init__(self, rate_per_sec: float = 8.0) -> None:
        self.rate = float(rate_per_sec)
        self._last: Optional[float] = None

    def acquire(self) -> None:
        now = time.monotonic()
        if self._last is not None:
            wait = self._last + 1.0 / self.rate - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
        self._last = now


def _default_transport(timeout: float = 30.0) -> Transport:
    def post(url: str, headers: Dict[str, str], payload: dict) -> Tuple[int, Dict[str, str], bytes]:
        if requests is None:  # pragma: no cover
            raise McpError("缺少 requests 依赖，请先 pip install requests")
        http = requests.Session()
        try:
            resp = http.post(
                url,
                headers=headers,
                data=json.dumps(payload).encode("utf-8"),
                timeout=timeout,
            )
            return resp.status_code, dict(resp.headers), resp.content
        finally:
            http.close()

    return post  # type: ignore[return-value]


class McpClient:
    """麦当劳 MCP 客户端。

    用法::

        client = McpClient(token="...")      # 或读环境变量 MCD_MCP_TOKEN
        client.initialize()
        client.call_tool("query-meals", {"storeId": "123"})
    """

    def __init__(
        self,
        url: str = DEFAULT_URL,
        token: Optional[str] = None,
        transport: Optional[Transport] = None,
        timeout: float = 30.0,
        max_retries: int = 4,
        rate_per_sec: float = 8.0,
    ) -> None:
        self.url = (url or DEFAULT_URL).rstrip("/")
        self.token = token
        self._transport: Transport = transport or _default_transport(timeout)
        self._max_retries = max(0, int(max_retries))
        self._limiter = _RateLimiter(rate_per_sec)
        self._session_id: Optional[str] = None
        self._next_id = 1
        self._tools_cache: Optional[List[dict]] = None

    # ------------------------------------------------------------------ 基础

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _send(self, payload: dict) -> dict:
        """发送一次 JSON-RPC 请求，带 429/5xx/网络错误重试。返回解析后的 JSON-RPC message。"""
        last_error: Optional[McpError] = None
        for attempt in range(self._max_retries + 1):
            self._limiter.acquire()
            status: Optional[int] = None
            headers: Dict[str, str] = {}
            body = b""
            try:
                status, headers, body = self._transport(self.url, self._headers(), payload)
            except McpError:
                raise
            except Exception as exc:  # 网络层异常：纳入重试
                last_error = McpError("MCP 请求失败：" + str(exc))
                if attempt < self._max_retries:
                    time.sleep(min(2 ** attempt, 8))
                    continue
                raise last_error

            if status == 401:
                raise UnauthorizedError(
                    "401：MCP Token 无效、已过期或未提供（请检查环境变量 MCD_MCP_TOKEN）"
                )
            if status == 429:
                try:
                    retry_after = float(headers.get("Retry-After") or 2)
                except (TypeError, ValueError):
                    retry_after = 2.0
                last_error = RateLimitError(f"429：触发限流，{retry_after}s 后重试")
                if attempt < self._max_retries:
                    time.sleep(retry_after)
                    continue
                raise last_error
            if status is not None and 500 <= status < 600 and attempt < self._max_retries:
                last_error = McpError(f"MCP HTTP {status}: {body[:200]!r}")
                time.sleep(min(2 ** attempt, 8))
                continue
            if status is not None and status >= 400:
                raise McpError(f"MCP HTTP {status}: {body[:200]!r}")

            if headers.get("Mcp-Session-Id"):
                self._session_id = headers["Mcp-Session-Id"]
            message = _parse_response(headers.get("Content-Type", ""), body)
            if isinstance(message, dict) and message.get("error"):
                err = message["error"]
                raise McpError(f"MCP 错误 {err.get('code')}: {err.get('message')}")
            return message

        raise last_error or McpError("MCP 请求失败")

    def _request(self, method: str, params: Optional[dict] = None) -> dict:
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        self._next_id += 1
        if params is not None:
            payload["params"] = params
        message = self._send(payload)
        if isinstance(message, dict) and "result" in message:
            return message["result"]
        return message if isinstance(message, dict) else {}

    def _notify(self, method: str) -> None:
        """通知（无响应体）。失败静默 —— 通知丢失不影响已建立的会话。"""
        try:
            self._limiter.acquire()
            status, headers, _body = self._transport(
                self.url, self._headers(), {"jsonrpc": "2.0", "method": method}
            )
        except Exception:
            return
        if headers.get("Mcp-Session-Id"):
            self._session_id = headers["Mcp-Session-Id"]

    # ------------------------------------------------------------------ MCP 方法

    def initialize(self) -> dict:
        """initialize 握手 + notifications/initialized。"""
        result = self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "mcd-strategist", "version": "1.0.0"},
            },
        )
        self._notify("notifications/initialized")
        return result if isinstance(result, dict) else {}

    def list_tools(self, refresh: bool = False) -> List[dict]:
        if self._tools_cache is None or refresh:
            result = self._request("tools/list", {})
            tools = result.get("tools", []) if isinstance(result, dict) else []
            self._tools_cache = [t for t in tools if isinstance(t, dict)]
        return list(self._tools_cache)

    def call_tool(self, name: str, arguments: Optional[dict] = None) -> dict:
        """调用工具并归一化返回：优先 structuredContent，其次 content[0].text 里的 JSON。"""
        result = self._request("tools/call", {"name": name, "arguments": arguments or {}})
        return self._extract(name, result)

    @staticmethod
    def _extract(name: str, result: dict) -> dict:
        if not isinstance(result, dict):
            raise ToolCallError(f"工具 {name} 返回了非对象结果: {result!r}")
        if result.get("isError"):
            texts = [
                c.get("text", "") for c in (result.get("content") or []) if isinstance(c, dict)
            ]
            raise ToolCallError(f"工具 {name} 执行业务错误: {' '.join(texts)[:300]}")
        if isinstance(result.get("structuredContent"), dict):
            return result["structuredContent"]
        for item in result.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "")
                try:
                    parsed = json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    return {"text": text}
                return parsed if isinstance(parsed, dict) else {"value": parsed}
        return {}
