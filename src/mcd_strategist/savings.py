"""省钱雷达：菜单 → 候选组合 → 券优选 → calculate-price → 排名。

设计原则：
1. 所有"价格/优惠"以麦当劳 calculate-price 的返回为准（单一真源），本地只做候选生成与排序；
2. 券的选择采用"先估算后试算"：用券面信息估算价值排序，只对最有希望的少数券调用
   calculate-price，避免把 600 次/分钟的配额浪费在无效组合上；
3. 纯逻辑（候选生成、估算、排序）与 MCP 调用解耦，可离线单测。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .models import (
    Coupon,
    Meal,
    PriceResult,
    extract_coupons,
    extract_meals,
    extract_nutrition,
    pick,
)

ItemList = List[Tuple[str, int]]  # [(meal_code, quantity), ...]

# 每张候选订单最多试算几张券（超出按估算价值截断）
MAX_COUPONS_PER_CANDIDATE = 6


@dataclass
class Candidate:
    """一张候选订单：餐品清单 + 人类可读标签。"""

    label: str
    items: ItemList

    def signature(self) -> Tuple[Tuple[str, int], ...]:
        return tuple(sorted(self.items))


def _estimate_subtotal(items: ItemList, price_of: Dict[str, float]) -> float:
    return sum(price_of.get(code, 0.0) * qty for code, qty in items)


def generate_candidates(
    meals: Sequence[Meal],
    budget: float,
    max_candidates: int = 48,
    price_buffer: float = 1.3,
) -> List[Candidate]:
    """从菜单生成候选订单。

    组合策略（优先级从高到低）：
      1. 每个套餐单品
      2. 套餐 + 最便宜饮料
      3. 主餐（汉堡等）单品
      4. 主餐 + 最便宜饮料 / + 最便宜小食
      5. 主餐 + 饮料 + 小食

    用无券原价做粗过滤（留 30% 余量，因为券可能把价格压回预算内）。
    """
    combos = [m for m in meals if m.role() == "combo" and m.price > 0]
    mains = [m for m in meals if m.role() == "main" and m.price > 0]
    drinks = sorted((m for m in meals if m.role() == "drink" and m.price > 0), key=lambda m: m.price)
    sides = sorted((m for m in meals if m.role() == "side" and m.price > 0), key=lambda m: m.price)

    price_of = {m.code: m.price for m in meals}
    out: List[Candidate] = []
    seen: set = set()

    def add(label: str, items: ItemList) -> None:
        items = [(c, q) for c, q in items if c]
        if not items:
            return
        key = tuple(sorted(items))
        if key in seen:
            return
        est = _estimate_subtotal(items, price_of)
        if budget > 0 and est > budget * price_buffer:
            return
        seen.add(key)
        out.append(Candidate(label=label, items=items))

    cheapest_drink = drinks[0] if drinks else None
    cheapest_side = sides[0] if sides else None

    for m in combos:
        add(f"{m.name}", [(m.code, 1)])
    if cheapest_drink:
        for m in combos:
            add(f"{m.name} + {cheapest_drink.name}", [(m.code, 1), (cheapest_drink.code, 1)])
    for m in mains:
        add(f"{m.name}", [(m.code, 1)])
    if cheapest_drink:
        for m in mains:
            add(f"{m.name} + {cheapest_drink.name}", [(m.code, 1), (cheapest_drink.code, 1)])
    if cheapest_side:
        for m in mains:
            add(f"{m.name} + {cheapest_side.name}", [(m.code, 1), (cheapest_side.code, 1)])
    if cheapest_drink and cheapest_side:
        for m in mains:
            add(
                f"{m.name} + {cheapest_drink.name} + {cheapest_side.name}",
                [(m.code, 1), (cheapest_drink.code, 1), (cheapest_side.code, 1)],
            )
    return out[:max_candidates]


@dataclass
class RadarRow:
    """一条排行结果。"""

    label: str
    items: ItemList
    coupon_name: str = ""
    coupon_id: str = ""
    price: PriceResult = field(default_factory=PriceResult)
    kcal: float = 0.0
    within_budget: bool = True

    @property
    def kcal_per_yuan(self) -> float:
        return self.kcal / self.price.payable if self.price.payable > 0 else 0.0


@dataclass
class RadarReport:
    budget: float
    rows: List[RadarRow] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    store_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "budget": self.budget,
            "storeId": self.store_id,
            "rows": [
                {
                    "label": r.label,
                    "items": [{"code": c, "quantity": q} for c, q in r.items],
                    "coupon": r.coupon_name,
                    "total": round(r.price.total, 2),
                    "discount": round(r.price.discount, 2),
                    "deliveryFee": round(r.price.delivery_fee, 2),
                    "payable": round(r.price.payable, 2),
                    "kcal": round(r.kcal, 1),
                    "kcalPerYuan": round(r.kcal_per_yuan, 2),
                    "withinBudget": r.within_budget,
                }
                for r in self.rows
            ],
            "notes": self.notes,
        }


class _PriceOracle:
    """calculate-price 调用缓存（同一 (订单签名, 券集合) 只算一次）。"""

    def __init__(self, call_tool: Callable[[str, dict], dict]) -> None:
        self._call_tool = call_tool
        self._cache: Dict[Tuple[Tuple[Tuple[str, int], ...], Tuple[str, ...]], PriceResult] = {}

    def price(
        self,
        items: ItemList,
        coupon_ids: Sequence[str] = (),
        extra: Optional[dict] = None,
        store_id: Optional[str] = None,
    ) -> PriceResult:
        key = (tuple(sorted(items)), tuple(sorted(coupon_ids)))
        if key in self._cache:
            return self._cache[key]
        args: Dict[str, Any] = {"meals": [{"code": c, "quantity": q} for c, q in items]}
        if store_id:
            args["storeId"] = store_id
        if coupon_ids:
            args["couponIds"] = list(coupon_ids)
        if extra:
            args.update(extra)
        result = PriceResult.from_mcp(self._call_tool("calculate-price", args))
        self._cache[key] = result
        return result


def plan_radar(
    client: Any,
    budget: float,
    *,
    store_id: Optional[str] = None,
    max_rows: int = 5,
    bind_coupons: bool = False,
    meal_args: Optional[dict] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> RadarReport:
    """执行一次省钱雷达扫描。

    参数：
        client      : McpClient（或同接口的 MockMcpClient）
        budget      : 预算（元）
        store_id    : 门店 ID（不传则由服务端按默认门店处理）
        bind_coupons: 是否调用 auto-bind-coupons 自动领券（默认 False，只查不领）
        max_rows    : 返回前 N 条
        meal_args   : 透传给 query-meals 的额外参数
        progress    : 进度回调（CLI 打印用）
    """
    notes: List[str] = []
    log = progress or (lambda _msg: None)

    # 1) 菜单
    args = dict(meal_args or {})
    if store_id:
        args.setdefault("storeId", store_id)
    log("正在获取门店菜单 …")
    menu = extract_meals(client.call_tool("query-meals", args))
    if not menu:
        raise ValueError("未能从 query-meals 解析出餐品列表")
    price_of = {m.code: m.price for m in menu}

    # 2) 可领优惠券（麦麦省）
    log("正在查询可领取优惠券 …")
    coupons = extract_coupons(client.call_tool("available-coupons", {}))
    bound = 0
    if bind_coupons and coupons:
        log(f"检测到 {len(coupons)} 张可领优惠券，执行一键领券 …")
        bind_result = client.call_tool("auto-bind-coupons", {})
        bound = len(pick(bind_result, "bound", "coupons", "success", default=[]) or [])
        if not bound:
            notes.append("auto-bind-coupons 未返回绑定明细（可能已全部在券包中）")

    # 3) 门店可用券（有 store_id 时补充查询）
    if store_id:
        try:
            log("正在查询门店可用优惠券 …")
            store_coupons = extract_coupons(client.call_tool("query-store-coupons", {"storeId": store_id}))
            known = {c.id for c in coupons}
            coupons.extend(c for c in store_coupons if c.id not in known)
        except Exception as exc:  # 门店券查询失败不影响主流程
            notes.append(f"query-store-coupons 跳过：{exc}")

    # 4) 候选组合
    candidates = generate_candidates(menu, budget)
    if not candidates:
        return RadarReport(
            budget=budget,
            rows=[],
            notes=[f"预算 ¥{budget:g} 内没有找到候选组合（或菜单为空），试试提高预算"],
            store_id=store_id,
        )
    log(f"生成 {len(candidates)} 个候选组合，开始比价 …")

    oracle = _PriceOracle(client.call_tool)
    rows: List[RadarRow] = []
    priced_calls = 0

    # 营养表只取一次（每张候选都去取会把限流配额打满）
    kcal_by_code: Dict[str, float] = {}
    try:
        nutrition_items = extract_nutrition(client.call_tool("list-nutrition-foods", {}))
        kcal_by_code = {n.code: n.energy_kcal for n in nutrition_items}
    except Exception:
        pass  # 营养数据缺失不影响比价主流程

    for cand in candidates:
        base = oracle.price(cand.items, store_id=store_id)
        priced_calls += 1
        subtotal = _estimate_subtotal(cand.items, price_of)
        codes = [c for c, _ in cand.items]

        # 按估算价值挑最有希望的几张券逐一试算
        ranked = sorted(
            coupons,
            key=lambda c: c.estimated_value(subtotal, codes),
            reverse=True,
        )
        top = [c for c in ranked[:MAX_COUPONS_PER_CANDIDATE] if c.estimated_value(subtotal, codes) > 0]
        if not top and any(c.kind == "unknown" for c in ranked[:2]):
            top = ranked[:1]  # 无法静态估算时保底试一张

        best_price, best_coupon = base, None
        for coupon in top:
            result = oracle.price(cand.items, [coupon.id], store_id=store_id)
            priced_calls += 1
            if result.payable < best_price.payable - 1e-9:
                best_price, best_coupon = result, coupon

        # 汇总营养（用于每元性价比排序；失败不阻断）
        kcal = sum(kcal_by_code.get(code, 0.0) * qty for code, qty in cand.items)

        rows.append(
            RadarRow(
                label=cand.label,
                items=list(cand.items),
                coupon_name=best_coupon.name if best_coupon else "",
                coupon_id=best_coupon.id if best_coupon else "",
                price=best_price,
                kcal=kcal,
            )
        )

    # 5) 排名：预算内优先（按应付升序），预算外按应付升序垫底；同价看热量
    within = sorted(
        (r for r in rows if r.price.payable <= budget + 1e-9),
        key=lambda r: (r.price.payable, -r.kcal),
    )
    outside = sorted(
        (r for r in rows if r.price.payable > budget + 1e-9),
        key=lambda r: (r.price.payable, -r.kcal),
    )
    for r in within:
        r.within_budget = True
    for r in outside:
        r.within_budget = False

    report = RadarReport(budget=budget, rows=within[:max_rows] + outside[:max_rows], notes=notes,
                         store_id=store_id)
    if bound:
        notes.insert(0, f"已自动领取 {bound} 张优惠券")
    if not within:
        notes.append(f"预算 ¥{budget:g} 内没有可行组合（已列出最接近的超预算方案）")
    notes.append(f"共试算 {priced_calls} 次 calculate-price（低于 600 次/分钟限额）")
    return report
