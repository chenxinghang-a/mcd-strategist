"""麦门军师 —— 基于麦当劳 MCP 的点餐决策 Skill。

模块划分：
- mcp_client  : 麦当劳 MCP（https://mcp.mcd.cn）Streamable HTTP 客户端
- models      : 对 MCP 返回结构的宽松解析与领域模型
- savings     : 省钱雷达（菜单 → 候选组合 → 券优选 → calculate-price → 排名）
- nutrition   : 营养把关（口味与热量/钠的权衡）
- ordering    : 一句话点餐（解析 → 选店 → 计价 → dry-run → 确认下单）
- briefing    : 麦门日报（活动日历 / 优惠券 / 积分 / 抽奖）
- mockserver  : 离线模拟 MCP（测试与 demo 用，不联网）
- cli         : 命令行入口 mcd
"""

from .mcp_client import (
    McpClient,
    McpError,
    RateLimitError,
    ToolCallError,
    UnauthorizedError,
)

__version__ = "1.0.0"
__all__ = [
    "McpClient",
    "McpError",
    "RateLimitError",
    "ToolCallError",
    "UnauthorizedError",
    "__version__",
]
