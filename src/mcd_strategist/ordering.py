"""一句话点餐：文本 → 选品 → 选店 → 计价 →（默认 dry-run）→ 确认下单。

安全边界：
- create-order 是唯一会产生真实订单的动作，必须显式 submit=True 才会执行；
- 默认只走到 create-order 之前的 dry-run（含预计支付链接若有）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Dict, List, Optional, Sequence

from .models import Meal, PriceResult, extract_meals, pick

# 常见别名词典：菜单名 -> 用户可能的说法
_SYNONYMS = {
    "薯条": ["薯条", "粗薯", "脆薯", "小薯"],
    "可乐": ["可乐", "可口可乐", "coke", "cola"],
    "巨无霸": ["巨无霸", "大麦克", "big mac"],
    "麦乐鸡": ["麦乐鸡", "鸡块"],
    "麦辣鸡腿堡": ["麦辣", "辣堡"],
    "板烧鸡腿堡": ["板烧"],
    "香芋派": ["香芋派", "派"],
    "菠萝派": ["菠萝派"],
}


@dataclass
class OrderPlan:
    text: str
    meals: List[Meal] = field(default_factory=list)
    quantities: Dict[str, int] = field(default_factory=dict)
    store: Optional[dict] = None
    price: Optional[PriceResult] = None
    notes: List[str] = field(default_factory=list)
    payment_url: Optional[str] = None
    order_id: Optional[str] = None
    submitted: bool = False

    @property
    def items(self) -> List[Dict[str, Any]]:
        return [{"code": code, "quantity": self.quantities[code]} for code in sorted(self.quantities)]

    def describe(self) -> str:
        parts = []
        for code in sorted(self.quantities):
            qty = self.quantities[code]
            meal = next((m for m in self.meals if m.code == code), None)
            name = meal.name if meal else code
            parts.append(f"{name}×{qty}")
        return "、".join(parts) if parts else "（未识别到餐品）"


def _alias_terms(meal: Meal) -> List[str]:
    """收集一道菜的别名词（菜名本身含规范词，或含某个别名的，都视为同组）。"""
    terms: List[str] = []
    for canonical, aliases in _SYNONYMS.items():
        if canonical and (canonical in meal.name or meal.name in canonical):
            terms.extend(aliases)
        elif any(a in meal.name for a in aliases):
            terms.append(canonical)
            terms.extend(aliases)
    seen: set = set()
    out: List[str] = []
    for t in terms:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def parse_text(text: str, meals: Sequence[Meal]) -> Dict[str, int]:
    """把一句自然语言解析成 {餐品编码: 数量}。

    消歧规则：
    1. 菜名全字命中优先于别名命中（"巨无霸套餐" 不会再带上 "巨无霸汉堡"）；
    2. 同一别名命中多道菜时取价格最低的一道（"薯条" → 默认小份）。
    """
    lowered = text.lower()
    full_matches: List[Meal] = []
    alias_matches: List[tuple] = []
    for meal in meals:
        if not meal.name:
            continue
        if meal.name in text or meal.name.lower() in lowered:
            full_matches.append(meal)
            continue
        for term in _alias_terms(meal):
            if term in text or term.lower() in lowered:
                alias_matches.append((meal, term))
                break

    full_names_text = " ".join(m.name for m in full_matches)
    quantities: Dict[str, int] = {}
    for m in full_matches:
        quantities[m.code] = quantities.get(m.code, 0) + 1

    best_by_term: Dict[str, Meal] = {}
    for meal, term in alias_matches:
        if term and term in full_names_text:
            continue  # 已被全名命中覆盖（别名是全名的子串）
        if term not in best_by_term or meal.price < best_by_term[term].price:
            best_by_term[term] = meal
    for meal in best_by_term.values():
        quantities[meal.code] = quantities.get(meal.code, 0) + 1
    return quantities


def resolve_store(client: Any, store_id: Optional[str] = None) -> tuple:
    """确定门店：显式指定 > 附近门店第一个。返回 (store, note)。"""
    if store_id:
        return {"storeId": store_id}, None
    for tool, args in (("query-nearby-stores", {}), ("delivery-query-stores", {})):
        try:
            payload = client.call_tool(tool, args)
        except Exception:
            continue
        stores = pick(payload, "stores", "storeList", "list", "data", default=None)
        if isinstance(stores, list) and stores:
            return stores[0], f"门店由 {tool} 自动选取"
    return None, "未查询到可用门店"


def draft_order(
    client: Any,
    text: str,
    *,
    store_id: Optional[str] = None,
) -> OrderPlan:
    """走到下单前的 dry-run。不会创建真实订单。"""
    plan = OrderPlan(text=text)
    menu = extract_meals(client.call_tool("query-meals", {"storeId": store_id} if store_id else {}))
    if not menu:
        plan.notes.append("菜单为空，无法解析点餐内容")
        return plan

    quantities = parse_text(text, menu)
    if not quantities:
        plan.notes.append(
            "没能从这句话里识别出餐品。试试更明确的说法，例如："
            "「来一份巨无霸套餐加薯条和可乐」。"
        )
        return plan
    plan.meals = [m for m in menu if m.code in quantities]
    plan.quantities = quantities

    store, note = resolve_store(client, store_id)
    plan.store = store
    if note:
        plan.notes.append(note)

    price_args: Dict[str, Any] = {
        "meals": [{"code": c, "quantity": quantities[c]} for c in sorted(quantities)]
    }
    if store:
        sid = pick(store, "storeId", "id", "code", default=None)
        if sid:
            price_args["storeId"] = sid
    plan.price = PriceResult.from_mcp(client.call_tool("calculate-price", price_args))
    plan.notes.append("dry-run 完成：未创建订单。加 --confirm 才会真正下单。")
    return plan


def submit_order(client: Any, plan: OrderPlan, *, delivery: bool = False) -> OrderPlan:
    """真实下单。仅在用户显式确认后调用。"""
    if not plan.quantities:
        raise ValueError("订单为空，拒绝调用 create-order")
    args: Dict[str, Any] = {
        "meals": plan.items,
        "eatType": "DELIVERY" if delivery else "TAKEOUT",
    }
    if plan.store:
        sid = pick(plan.store, "storeId", "id", "code", default=None)
        if sid:
            args["storeId"] = sid
    result = client.call_tool("create-order", args)
    plan.order_id = str(pick(result, "orderId", "id", default="") or "")
    plan.payment_url = str(pick(result, "payUrl", "paymentUrl", "payLink", default="") or "")
    plan.submitted = True
    plan.notes.append("订单已创建（create-order）")
    return plan
