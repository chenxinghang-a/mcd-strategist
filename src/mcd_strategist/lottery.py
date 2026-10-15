"""积分抽奖：活动信息、我的奖品与抽奖执行（默认 dry-run）。

对应麦当劳 MCP 工具：
    query-lottery-info   查看积分抽奖活动信息
    draw-lottery         积分抽奖（消耗积分，写操作）
    query-my-prizes      查看我的奖品

设计原则与 order 一致：默认只读 / dry-run；真实消耗积分的抽奖必须显式 --confirm。
活动信息读取失败 → 抛出（调用方需要知道活动不可查）；
积分余额 / 我的奖品读取失败 → 容错降级，不阻断主流程。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .models import pick

LOTTERY_INFO_TOOL = "query-lottery-info"
LOTTERY_DRAW_TOOL = "draw-lottery"
LOTTERY_PRIZES_TOOL = "query-my-prizes"


@dataclass
class LotteryStatus:
    """抽奖活动状态（含积分余额，用于判断能否抽）。"""

    status: str = ""
    cost: int = 0
    prizes: List[str] = field(default_factory=list)
    available_points: int = 0
    account_ok: bool = False  # 积分余额是否成功读取

    def affordable(self) -> bool:
        """积分是否足够抽一次；cost<=0（免费活动）或余额未知时不拦截。"""
        if self.cost <= 0 or not self.account_ok:
            return True
        return self.available_points >= self.cost

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "cost": self.cost,
            "prizes": list(self.prizes),
            "availablePoints": self.available_points,
            "accountOk": self.account_ok,
            "affordable": self.affordable(),
        }


def lottery_status(client: Any) -> LotteryStatus:
    """读取抽奖活动信息 + 积分余额（余额板块独立容错）。"""
    info = client.call_tool(LOTTERY_INFO_TOOL, {})
    status = LotteryStatus(
        status=str(pick(info, "status", "state", "lotteryStatus", default="") or ""),
        cost=int(pick(info, "cost", "pointsCost", "consume", "price", default=0) or 0),
    )
    prizes = pick(info, "prizes", "prizeList", "awards", "list", default=[])
    if isinstance(prizes, list):
        status.prizes = [
            str(
                pick(p, "name", "prizeName", "title", default="奖品")
                if isinstance(p, dict)
                else p
            )
            for p in prizes
        ]
    try:
        account = client.call_tool("query-my-account", {})
        status.available_points = int(
            pick(account, "availablePoints", "available", "points", "balance", default=0) or 0
        )
        status.account_ok = True
    except Exception:
        pass
    return status


def my_prizes(client: Any) -> List[str]:
    """查看我的奖品（容错：读取失败返回空列表）。"""
    try:
        payload = client.call_tool(LOTTERY_PRIZES_TOOL, {})
    except Exception:
        return []
    prizes = pick(payload, "prizes", "list", "data", "awards", default=[])
    if not isinstance(prizes, list):
        return []
    out: List[str] = []
    for p in prizes:
        name = (
            pick(p, "name", "prizeName", "title", default="")
            if isinstance(p, dict)
            else str(p)
        )
        if name:
            out.append(str(name))
    return out


def draw_once(client: Any) -> Dict[str, Any]:
    """真实抽奖一次（消耗积分）。返回归一化结果：win / prize / costPaid / raw。"""
    payload = client.call_tool(LOTTERY_DRAW_TOOL, {})
    win = bool(pick(payload, "win", "isWin", "success", "won", default=False))
    prize_raw = pick(payload, "prize", "prizeName", "award", "prizeItem", default=None)
    if isinstance(prize_raw, dict):
        prize: Optional[str] = str(pick(prize_raw, "name", "prizeName", "title", default="奖品"))
    elif prize_raw is None:
        prize = None
    else:
        prize = str(prize_raw)
    cost_paid = int(pick(payload, "costPaid", "cost", "pointsCost", "costPoints", default=0) or 0)
    return {"win": win, "prize": prize, "costPaid": cost_paid, "raw": payload}
