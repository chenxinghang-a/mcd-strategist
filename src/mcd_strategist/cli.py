"""命令行入口：mcd。

子命令：
  radar      省钱雷达（默认场景）
  order      一句话点餐（默认 dry-run，--confirm 才真实下单）
  cancel     取消订单
  lottery    积分抽奖（默认查询；--draw --confirm 才真实消耗积分）
  brief      麦门日报
  nutrition  营养把关
  tools      列出麦当劳 MCP 全部工具（连通性自检）
  demo       离线演示（--mock，不联网）

全局参数：--mock 离线模拟 / --url / --token / --json 机器可读输出 / --store-id
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

from . import __version__
from .briefing import daily_brief
from .lottery import draw_once, lottery_status, my_prizes
from .mcp_client import McpClient, McpError
from .mockserver import MockMcpClient
from .models import extract_meals, extract_nutrition, pick
from .nutrition import assess, combo_nutrition, summarize, swap_suggestions
from .ordering import draft_order, submit_order
from .savings import plan_radar


def make_client(args: argparse.Namespace) -> Any:
    """按参数构造客户端：--mock 或缺少 Token 时走离线模拟。"""
    if getattr(args, "mock", False):
        return MockMcpClient()
    token = args.token or os.environ.get("MCD_MCP_TOKEN")
    if not token:
        print(
            "提示：未检测到 MCD Token（环境变量 MCD_MCP_TOKEN），已切换到离线演示模式。\n"
            "      申请 Token：https://open.mcd.cn/mcp （手机号验证登录 → 控制台 → 激活）\n",
            file=sys.stderr,
        )
        return MockMcpClient()
    client = McpClient(url=args.url, token=token)
    client.initialize()
    return client


def _emit(payload: Any, as_json: bool, render) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        render()


# ---------------------------------------------------------------- radar


def cmd_radar(args: argparse.Namespace) -> int:
    client = make_client(args)
    report = plan_radar(
        client,
        args.budget,
        store_id=args.store_id,
        max_rows=args.rows,
        bind_coupons=args.bind,
        progress=None if args.json else (lambda msg: print(f"… {msg}", file=sys.stderr)),
    )
    data = report.to_dict()

    def render() -> None:
        print(f"🍔 省钱雷达 · 预算 ¥{args.budget:g}" + (f" · 门店 {args.store_id}" if args.store_id else ""))
        for i, row in enumerate(data["rows"], 1):
            flag = "✅" if row["withinBudget"] else "⚠️ 超预算"
            coupon = f"｜券：{row['coupon']}" if row["coupon"] else "｜券：无"
            print(
                f"{i}. {row['label']}{coupon}\n"
                f"   原价 ¥{row['total']:.2f} → 优惠 ¥{row['discount']:.2f}"
                + (f" → 配送 ¥{row['deliveryFee']:.2f}" if row["deliveryFee"] else "")
                + f" → 实付 ¥{row['payable']:.2f} {flag}"
                + (f"｜约 {row['kcal']:.0f} kcal｜性价比 {row['kcalPerYuan']:.1f} kcal/元" if row["kcal"] else "")
            )
        if data["notes"]:
            print("\n备注：")
            for note in data["notes"]:
                print(f"- {note}")
        print("\n提示：以上价格以麦当劳 calculate-price 为准；下单请用 mcd order，默认 dry-run。")

    _emit(data, args.json, render)
    return 0


# ---------------------------------------------------------------- order


def cmd_order(args: argparse.Namespace) -> int:
    client = make_client(args)
    text = " ".join(args.text)
    plan = draft_order(client, text, store_id=args.store_id)
    data = {
        "text": text,
        "meals": plan.describe(),
        "store": plan.store,
        "price": plan.price.__dict__ if plan.price else None,
        "notes": plan.notes,
        "submitted": plan.submitted,
    }

    if args.confirm:
        if plan.quantities:
            plan = submit_order(client, plan, delivery=args.delivery)
            data["submitted"] = plan.submitted
            data["orderId"] = plan.order_id
            data["paymentUrl"] = plan.payment_url
        else:
            print("拒绝下单：未识别到餐品。", file=sys.stderr)
            return 2

    def render() -> None:
        print(f"🧾 点餐单：{plan.describe()}")
        if plan.store:
            print(f"📍 门店：{pick(plan.store, 'name', 'storeName', default=plan.store.get('storeId', ''))}")
        if plan.price:
            print(
                f"💰 原价 ¥{plan.price.total:.2f}｜优惠 ¥{plan.price.discount:.2f}"
                f"｜实付 ¥{plan.price.payable:.2f}"
                + (f"（含配送 ¥{plan.price.delivery_fee:.2f}）" if plan.price.delivery_fee else "")
            )
        if plan.submitted:
            print(f"✅ 订单已创建：{plan.order_id}")
            if plan.payment_url:
                print(f"🔗 支付链接：{plan.payment_url}")
        for note in plan.notes:
            print(f"· {note}")

    _emit(data, args.json, render)
    return 0


# ---------------------------------------------------------------- cancel


def cmd_cancel(args: argparse.Namespace) -> int:
    client = make_client(args)
    result = client.call_tool("cancel-order", {"orderId": args.order_id})
    data = {"cancelled": True, "orderId": args.order_id, "result": result}

    def render() -> None:
        print(f"✅ 已申请取消订单 {args.order_id}")
        if isinstance(result, dict) and result.get("success") is False:
            print("⚠️ 服务端返回取消失败，请用 query-order 确认订单状态")

    _emit(data, args.json, render)
    return 0


# ---------------------------------------------------------------- lottery


def cmd_lottery(args: argparse.Namespace) -> int:
    if args.confirm and not args.draw:
        print("拒绝执行：--confirm 需与 --draw 一起使用。", file=sys.stderr)
        return 2

    client = make_client(args)
    status = lottery_status(client)
    owned = my_prizes(client)

    drawn: Optional[dict] = None
    if args.draw and args.confirm:
        if not status.affordable():
            print(
                f"拒绝抽奖：积分不足（需要 {status.cost}，当前可用 {status.available_points}）。",
                file=sys.stderr,
            )
            return 2
        drawn = draw_once(client)
        owned = my_prizes(client)

    data = {
        "lottery": status.to_dict(),
        "myPrizes": owned,
        "draw": drawn,
        "dryRun": bool(args.draw and not args.confirm),
    }

    def render() -> None:
        print("🎰 积分抽奖" + (f" · {status.status}" if status.status else ""))
        if status.cost:
            quota = f"（当前可用积分：{status.available_points}）" if status.account_ok else ""
            print(f"每次消耗：{status.cost} 积分{quota}")
        if status.prizes:
            print("奖池：" + " / ".join(status.prizes))
        print("\n🎁 我的奖品：" + ("、".join(owned) if owned else "暂无"))
        if args.draw and not args.confirm:
            print(f"\n[dry-run] 将消耗 {status.cost} 积分抽一次；确认真实抽奖请加 --confirm。")
        if drawn is not None:
            if drawn["win"]:
                print(f"\n🎉 恭喜！抽中：{drawn['prize'] or '奖品'}（消耗 {drawn['costPaid']} 积分）")
            else:
                print(f"\n😢 未中奖（消耗 {drawn['costPaid']} 积分）—— 再来一次？")

    _emit(data, args.json, render)
    return 0


# ---------------------------------------------------------------- brief


def cmd_brief(args: argparse.Namespace) -> int:
    client = make_client(args)
    text = daily_brief(client)

    def render() -> None:
        print(text)

    _emit({"brief": text}, args.json, render)
    return 0


# ---------------------------------------------------------------- nutrition


def cmd_nutrition(args: argparse.Namespace) -> int:
    client = make_client(args)
    menu = extract_meals(client.call_tool("query-meals", {}))
    nutrition_items = extract_nutrition(client.call_tool("list-nutrition-foods", {}))
    by_code = {n.code: n for n in nutrition_items}

    if args.meal_code:
        codes = [args.meal_code]
    else:
        text = " ".join(args.text or [])
        from .ordering import parse_text

        codes = list(parse_text(text, menu).keys()) if text else []
        if not codes:
            print("请指定 --meal-code 或输入餐品名称，例如：mcd nutrition 麦辣鸡腿堡套餐", file=sys.stderr)
            return 2

    total = combo_nutrition(codes, by_code)
    label = " + ".join(
        next((m.name for m in menu if m.code == c), c) for c in codes
    )
    issues = assess(total)
    data = {
        "label": label,
        "nutrition": {
            "energyKcal": total.energy_kcal,
            "proteinG": total.protein_g,
            "fatG": total.fat_g,
            "carbG": total.carb_g,
            "sodiumMg": total.sodium_mg,
        },
        "issues": [i.text for i in issues],
    }

    def render() -> None:
        print(summarize(label, total))
        if issues:
            print("\n🔁 更友好的替代建议：")
            suggestions = swap_suggestions(
                total,
                list(by_code.values()),
                limit=3,
            )
            pool = {n.code: n for n in nutrition_items}
            names = {m.code: m.name for m in menu}
            for s in suggestions:
                print(
                    f"- {names.get(s.code, s.code) or s.name}："
                    f"{s.energy_kcal:.0f} kcal（较当前 -{total.energy_kcal - s.energy_kcal:.0f} kcal）"
                )
            if not suggestions:
                print("- （同类中没有更优替代）")

    _emit(data, args.json, render)
    return 0


# ---------------------------------------------------------------- tools


def cmd_tools(args: argparse.Namespace) -> int:
    client = make_client(args)
    tools = client.list_tools()
    data = {"count": len(tools), "tools": [{"name": t.get("name", ""), "description": t.get("description", "")} for t in tools]}

    def render() -> None:
        print(f"麦当劳 MCP 共 {len(tools)} 个工具：")
        for t in tools:
            print(f"- {t.get('name', '')}: {t.get('description', '')}")

    _emit(data, args.json, render)
    return 0


# ---------------------------------------------------------------- demo


def cmd_demo(args: argparse.Namespace) -> int:
    """离线全流程演示（mock 模式强开）。"""
    args.mock = True
    args.token = None
    client = MockMcpClient()
    client.initialize()

    print("=" * 62)
    print("🎬 麦门军师 · 离线演示（--mock，数据为演示用模拟数据）")
    print("=" * 62)

    print("\n>>> 1/5 麦门日报\n")
    print(daily_brief(client))

    print("\n>>> 2/5 省钱雷达：预算 ¥30\n")
    report = plan_radar(client, 30.0, max_rows=3, bind_coupons=True)
    for i, row in enumerate(report.rows[:3], 1):
        print(
            f"{i}. {row.label}｜券：{row.coupon_name or '无'}｜"
            f"原价 ¥{row.price.total:.2f} → 实付 ¥{row.price.payable:.2f}"
            + (f"｜约 {row.kcal:.0f} kcal" if row.kcal else "")
        )

    print("\n>>> 3/5 一句话点餐（dry-run）\n")
    plan = draft_order(client, "来一份巨无霸套餐，再加个薯条")
    print(f"识别：{plan.describe()}")
    if plan.price:
        print(f"原价 ¥{plan.price.total:.2f} → 实付 ¥{plan.price.payable:.2f}")
    for note in plan.notes:
        print(f"· {note}")

    print("\n>>> 4/5 营养把关\n")
    menu = extract_meals(client.call_tool("query-meals", {}))
    by_code = {n.code: n for n in extract_nutrition(client.call_tool("list-nutrition-foods", {}))}
    from .ordering import parse_text

    codes = list(parse_text("巨无霸套餐 薯条(大) 可口可乐(中)", menu).keys())
    total = combo_nutrition(codes, by_code)
    label = " + ".join(next((m.name for m in menu if m.code == c), c) for c in codes)
    print(summarize(label, total))
    names = {m.code: m.name for m in menu}
    for s in swap_suggestions(by_code["B0101"], list(by_code.values()), limit=2):
        print(f"🔁 可换：{names.get(s.code, s.code) or s.name}（{s.energy_kcal:.0f} kcal）")

    print("\n>>> 5/5 积分抽奖\n")
    st = lottery_status(client)
    print("🎰 积分抽奖" + (f" · {st.status}" if st.status else ""))
    if st.cost:
        print(f"每次消耗：{st.cost} 积分（当前可用积分：{st.available_points}）")
    if st.prizes:
        print("奖池：" + " / ".join(st.prizes))
    result = draw_once(client)
    if result["win"]:
        print(f"抽一次：🎉 抽中 {result['prize']}（消耗 {result['costPaid']} 积分）")
    else:
        print(f"抽一次：😢 未中奖（消耗 {result['costPaid']} 积分）")

    print("\n" + "=" * 62)
    print("演示结束。真实使用：申请 Token 后 mcd radar --budget 30")
    print("=" * 62)
    return 0


# ---------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mcd",
        description="麦门军师 —— 基于麦当劳 MCP 的点餐决策助手（省钱雷达 / 一句话点餐 / 营养把关 / 麦门日报）",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--mock", action="store_true", help="离线演示模式（内置模拟 MCP，不联网）")
    p.add_argument("--url", default=os.environ.get("MCD_MCP_URL", "https://mcp.mcd.cn"), help="MCP Server 地址")
    p.add_argument("--token", default=None, help="MCP Token（默认读环境变量 MCD_MCP_TOKEN）")
    p.add_argument("--store-id", default=None, help="门店 ID")
    p.add_argument("--json", action="store_true", help="JSON 输出（机器可读）")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("radar", help="省钱雷达：预算内最优组合")
    sp.add_argument("--budget", type=float, required=True, help="预算（元）")
    sp.add_argument("--rows", type=int, default=5, help="展示前 N 条（默认 5）")
    sp.add_argument("--bind", action="store_true", help="调用 auto-bind-coupons 一键领券（默认只查不领）")
    sp.set_defaults(func=cmd_radar)

    sp = sub.add_parser("order", help="一句话点餐（默认 dry-run）")
    sp.add_argument("text", nargs="+", help="点餐描述，例如：来一份巨无霸套餐")
    sp.add_argument("--confirm", action="store_true", help="确认真实下单（create-order）")
    sp.add_argument("--delivery", action="store_true", help="外送（默认到店取餐）")
    sp.set_defaults(func=cmd_order)

    sp = sub.add_parser("brief", help="麦门日报")
    sp.set_defaults(func=cmd_brief)

    sp = sub.add_parser("cancel", help="取消订单")
    sp.add_argument("order_id", help="订单号")
    sp.set_defaults(func=cmd_cancel)

    sp = sub.add_parser("lottery", help="积分抽奖（默认查询；--draw 抽奖，默认 dry-run）")
    sp.add_argument("--draw", action="store_true", help="发起抽奖（消耗积分；--confirm 才真实执行）")
    sp.add_argument("--confirm", action="store_true", help="确认真实抽奖（消耗积分）")
    sp.set_defaults(func=cmd_lottery)

    sp = sub.add_parser("nutrition", help="营养把关")
    sp.add_argument("text", nargs="*", help="餐品名称，例如：麦辣鸡腿堡套餐")
    sp.add_argument("--meal-code", default=None, help="直接指定餐品编码")
    sp.set_defaults(func=cmd_nutrition)

    sub.add_parser("tools", help="列出 MCP 全部工具").set_defaults(func=cmd_tools)
    sub.add_parser("demo", help="离线演示").set_defaults(func=cmd_demo)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except McpError as exc:
        print(f"MCP 错误：{exc}", file=sys.stderr)
        return 3
    except (ValueError, RuntimeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
