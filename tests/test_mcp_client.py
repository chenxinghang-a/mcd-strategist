"""MCP 客户端测试：全部通过自定义 transport 离线完成。"""

import json

import pytest

from mcd_strategist.mcp_client import (
    McpClient,
    McpError,
    RateLimitError,
    ToolCallError,
    UnauthorizedError,
    _parse_sse,
)

URL = "https://mcp.mcd.cn"


def make_transport(responses):
    """responses: list[(status, headers, body_bytes)]，按调用顺序消费。"""
    calls = []
    queue = list(responses)

    def transport(url, headers, payload):
        calls.append((url, dict(headers), payload))
        return queue.pop(0)

    transport.calls = calls  # type: ignore[attr-defined]
    transport.queue = queue  # type: ignore[attr-defined]
    return transport


def json_response(payload, status=200, headers=None, content_type="application/json"):
    hdrs = {"Content-Type": content_type}
    hdrs.update(headers or {})
    return status, hdrs, json.dumps(payload).encode("utf-8")


# ---------------------------------------------------------------- 握手与会话


def test_initialize_handshake_and_session_id():
    transport = make_transport([
        json_response(
            {"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {"name": "mcd", "version": "1.0"}}},
            headers={"Mcp-Session-Id": "S-123"},
        ),
        (202, {"Content-Type": "application/json"}, b""),
    ])
    client = McpClient(url=URL, token="TKN", transport=transport)
    result = client.initialize()
    assert result["serverInfo"]["name"] == "mcd"

    payload0 = transport.calls[0][2]
    assert transport.calls[0][0] == URL
    assert payload0["method"] == "initialize"
    assert payload0["params"]["clientInfo"]["name"] == "mcd-strategist"
    assert transport.calls[0][1]["Authorization"] == "Bearer TKN"

    # initialized 通知
    assert transport.calls[1][2]["method"] == "notifications/initialized"

    # 后续请求带 Session-Id 与 Bearer 头
    transport.queue.append(json_response({"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "n"}]}}))
    client.list_tools()
    headers = transport.calls[2][1]
    assert headers["Authorization"] == "Bearer TKN"
    assert headers["Mcp-Session-Id"] == "S-123"


def test_headers_absent_token_and_session():
    transport = make_transport([
        json_response({"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}),
    ])
    client = McpClient(url=URL + "/", token=None, transport=transport)
    client.list_tools()
    headers = transport.calls[0][1]
    assert "Authorization" not in headers
    assert "Mcp-Session-Id" not in headers
    assert transport.calls[0][0] == URL  # url 归一化去尾斜杠


# ---------------------------------------------------------------- SSE / 错误码


def test_sse_response_parsed():
    transport = make_transport([
        (
            200,
            {"Content-Type": "text/event-stream"},
            b"event: message\ndata: {\"jsonrpc\":\"2.0\",\"id\":3,\"result\":{\"tools\":[{\"name\":\"query-meals\"}]}}\n\n",
        ),
    ])
    client = McpClient(url=URL, token="T", transport=transport)
    tools = client.list_tools()
    assert tools == [{"name": "query-meals"}]


def test_sse_parser_prefers_last_message():
    body = (
        b"data: {\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"partial\":true}}\n\n"
        b"data: {\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"partial\":false}}\n\n"
    )
    assert _parse_sse(body)["result"]["partial"] is False


def test_sse_parser_raises_without_message():
    with pytest.raises(McpError):
        _parse_sse(b"event: ping\n\n")


def test_401_raises_unauthorized():
    transport = make_transport([(401, {}, b"unauthorized")])
    client = McpClient(url=URL, token="BAD", transport=transport, max_retries=0)
    with pytest.raises(UnauthorizedError, match="401"):
        client.list_tools()


def test_429_retries_then_succeeds():
    transport = make_transport([
        (429, {"Retry-After": "0"}, b"rate limited"),
        json_response({"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}),
    ])
    client = McpClient(url=URL, token="T", transport=transport, max_retries=3)
    client.list_tools()
    assert len(transport.calls) == 2


def test_429_exhausted_raises():
    transport = make_transport([(429, {"Retry-After": "0"}, b"")] * 5)
    client = McpClient(url=URL, token="T", transport=transport, max_retries=2)
    with pytest.raises(RateLimitError):
        client.list_tools()


def test_500_retry_then_success():
    transport = make_transport([
        (500, {}, b"boom"),
        json_response({"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}),
    ])
    client = McpClient(url=URL, token="T", transport=transport, max_retries=3, rate_per_sec=1000)
    client.list_tools()
    assert len(transport.calls) == 2


def test_jsonrpc_error_raises():
    transport = make_transport([
        json_response({"jsonrpc": "2.0", "id": 1, "error": {"code": -32600, "message": "bad"}}),
    ])
    client = McpClient(url=URL, token="T", transport=transport)
    with pytest.raises(McpError, match="-32600"):
        client.list_tools()


# ---------------------------------------------------------------- 结果归一化


def test_call_tool_prefers_structured_content():
    transport = make_transport([
        json_response({"jsonrpc": "2.0", "id": 2, "result": {"structuredContent": {"ok": 1}}}),
    ])
    client = McpClient(url=URL, token="T", transport=transport)
    assert client.call_tool("query-meals", {}) == {"ok": 1}


def test_call_tool_text_json_fallback():
    transport = make_transport([
        json_response({
            "jsonrpc": "2.0", "id": 2,
            "result": {"content": [{"type": "text", "text": "{\"a\": 42}"}]},
        }),
    ])
    client = McpClient(url=URL, token="T", transport=transport)
    assert client.call_tool("query-meals", {}) == {"a": 42}


def test_call_tool_text_non_json_returns_text():
    transport = make_transport([
        json_response({
            "jsonrpc": "2.0", "id": 2,
            "result": {"content": [{"type": "text", "text": "订单创建成功"}]},
        }),
    ])
    client = McpClient(url=URL, token="T", transport=transport)
    assert client.call_tool("create-order", {}) == {"text": "订单创建成功"}


def test_call_tool_business_error():
    transport = make_transport([
        json_response({
            "jsonrpc": "2.0", "id": 2,
            "result": {"isError": True, "content": [{"type": "text", "text": "门店已打烊"}]},
        }),
    ])
    client = McpClient(url=URL, token="T", transport=transport)
    with pytest.raises(ToolCallError, match="门店已打烊"):
        client.call_tool("create-order", {})


def test_tools_list_cached():
    transport = make_transport([
        json_response({"jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": "a"}]}}),
    ])
    client = McpClient(url=URL, token="T", transport=transport)
    first = client.list_tools()
    second = client.list_tools()
    assert first == second == [{"name": "a"}]
    assert len(transport.calls) == 1  # 命中缓存
