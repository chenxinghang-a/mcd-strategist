"""麦门日报：活动日历 + 可领券 + 积分 + 抽奖的一站式早报。

每个板块独立容错：单个工具失败只丢该板块，不会毁掉整份日报。
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from .models import extract_coupons, pick


def _section(title: str, lines: List[str]) -> List[str]:
    return [f"## {title}", *lines, ""]


def daily_brief(
    client: Any,
    *,
    now: Optional[str] = None,
) -> str:
    """生成麦门日报文本。"""
    out: List[str] = ["# 🍔 麦门日报", ""]

    # 时间
    if not now:
        try:
            payload = client.call_tool("now-time-info", {})
            now = str(pick(payload, "datetime", "dateTime", "time", "currentTime", default="") or "")
        except Exception as exc:
            now = f"（时间获取失败：{exc}）"
    out.extend(_section("⏰ 现在", [now]))

    # 活动日历
    try:
        payload = client.call_tool("campaign-calendar", {})
        campaigns = pick(payload, "campaigns", "activities", "list", "data", default=[])
        if isinstance(campaigns, list) and campaigns:
            lines = []
            for c in campaigns[:8]:
                name = pick(c, "name", "title", "campaignName", default="未命名活动")
                status = pick(c, "status", "state", default="")
                start = pick(c, "startTime", "beginTime", default="")
                end = pick(c, "endTime", default="")
                span = f"{start} ~ {end}" if start and end else ""
                lines.append(f"- {name}" + (f"（{status}" + (f"｜{span}" if span else "") + "）" if status else ""))
            out.extend(_section("📅 本月活动", lines))
        else:
            out.extend(_section("📅 本月活动", ["- 暂无活动信息"]))
    except Exception as exc:
        out.extend(_section("📅 本月活动", [f"- 获取失败：{exc}"]))

    # 可领优惠券
    try:
        coupons = extract_coupons(client.call_tool("available-coupons", {}))
        if coupons:
            lines = [f"- {c.name}" for c in coupons[:8]]
            if len(coupons) > 8:
                lines.append(f"- ……等共 {len(coupons)} 张")
            out.extend(_section("🎫 可领优惠券（麦麦省）", lines))
        else:
            out.extend(_section("🎫 可领优惠券（麦麦省）", ["- 当前没有可领的券"]))
    except Exception as exc:
        out.extend(_section("🎫 可领优惠券（麦麦省）", [f"- 获取失败：{exc}"]))

    # 积分
    try:
        payload = client.call_tool("query-my-account", {})
        available = pick(payload, "availablePoints", "available", "points", "balance", default=0)
        total = pick(payload, "totalPoints", "total", default=0)
        expiring = pick(payload, "expiringPoints", "expiring", default=0)
        out.extend(
            _section(
                "⭐ 我的积分",
                [
                    f"- 可用积分：{available}",
                    f"- 累计积分：{total}",
                    f"- 即将过期：{expiring}",
                ],
            )
        )
    except Exception as exc:
        out.extend(_section("⭐ 我的积分", [f"- 获取失败：{exc}"]))

    # 积分抽奖
    try:
        payload = client.call_tool("query-lottery-info", {})
        prizes = pick(payload, "prizes", "prizeList", "awards", default=[])
        cost = pick(payload, "cost", "pointsCost", "consume", default=0)
        lines = []
        if cost:
            lines.append(f"- 每次消耗：{cost} 积分")
        if isinstance(prizes, list):
            for p in prizes[:6]:
                name = pick(p, "name", "prizeName", "title", default="奖品")
                lines.append(f"- {name}")
        out.extend(_section("🎰 积分抽奖", lines or ["- 暂无进行中的抽奖"]))
    except Exception as exc:
        out.extend(_section("🎰 积分抽奖", [f"- 获取失败：{exc}"]))

    out.extend(_section("💡 今日建议", [
        "- 先去「可领优惠券」把能领的券领了（mcd radar --bind）",
        "- 预算内吃什么，跑一次 mcd radar --budget 30",
    ]))
    return "\n".join(out)
