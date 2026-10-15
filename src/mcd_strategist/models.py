"""领域模型与 MCP 返回结构的宽松解析。

麦当劳 MCP 没有公开每个 Tool 的 JSON schema（只能运行时 tools/list 获知），
所以这里对字段名做多候选容错读取（pick()），并统一 Meal/Coupon/PriceResult/Nutrition
四种模型供上层逻辑使用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------- 容错取值


def pick(source: Any, *names: str, default: Any = None) -> Any:
    """按候选键名依次取值：MCP 字段命名未知，取第一个非 None 的命中。"""
    if not isinstance(source, dict):
        return default
    for name in names:
        if name in source and source[name] is not None:
            return source[name]
    return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_str_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if isinstance(v, (str, int, float))]
    return []


# ---------------------------------------------------------------- 餐品


DRINK_KEYWORDS = (
    "饮料", "饮品", "可乐", "咖啡", "果汁", "奶茶", "雪顶", "麦炫酷",
    "红茶", "绿茶", "豆浆", "拿铁", "美式", "冰饮", "热饮", " water", "Water",
)
SIDE_KEYWORDS = (
    "小食", "薯条", "鸡翅", "鸡块", "麦乐鸡", "派", "冰淇淋", "新地",
    "玉米", "沙拉", "洋葱圈", "薯饼", "脆薯饼", "菠萝派",
)
COMBO_KEYWORDS = ("套餐", "组合", "礼盒", "桶")


@dataclass
class Meal:
    code: str
    name: str
    category: str = ""
    price: float = 0.0
    tags: List[str] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mcp(cls, data: dict) -> "Meal":
        tags = pick(data, "tags", "labels", "tagList", default=[])
        return cls(
            code=str(pick(data, "code", "mealCode", "productCode", "mealProductCode", "id", default="")),
            name=str(pick(data, "name", "mealName", "title", "productName", default="未命名餐品")),
            category=str(pick(data, "category", "categoryName", "mealCategory", "type", default="")),
            price=_as_float(pick(data, "price", "currentPrice", "salePrice", "originalPrice", "memberPrice", default=0.0)),
            tags=_as_str_list(tags),
            raw=dict(data) if isinstance(data, dict) else {},
        )

    def role(self) -> str:
        """粗分为 combo / drink / side / main，供候选组合生成使用。"""
        text = self.name + " " + self.category + " " + " ".join(self.tags)
        if any(k in text for k in COMBO_KEYWORDS):
            return "combo"
        if any(k in text for k in DRINK_KEYWORDS):
            return "drink"
        if any(k in text for k in SIDE_KEYWORDS):
            return "side"
        return "main"


def extract_meals(payload: Any) -> List[Meal]:
    """从 query-meals 等返回中提取餐品列表，兼容扁平/分类嵌套/裸列表。"""
    if isinstance(payload, list):
        return [Meal.from_mcp(m) for m in payload if isinstance(m, dict)]
    if not isinstance(payload, dict):
        return []
    meals_raw = pick(payload, "meals", "mealList", "products", "items", "list", default=None)
    if isinstance(meals_raw, list):
        return [Meal.from_mcp(m) for m in meals_raw if isinstance(m, dict)]
    categories = pick(payload, "categories", "categoryList", "menus", default=None)
    if isinstance(categories, list):
        out: List[Meal] = []
        for cat in categories:
            if isinstance(cat, dict):
                nested = pick(cat, "meals", "mealList", "products", "items", default=[])
                if isinstance(nested, list):
                    out.extend(Meal.from_mcp(m) for m in nested if isinstance(m, dict))
        return out
    return []


# ---------------------------------------------------------------- 优惠券


@dataclass
class Coupon:
    id: str
    name: str
    kind: str = "unknown"  # threshold(满减) / discount(折扣) / item(指定餐品) / unknown
    amount: float = 0.0
    threshold: float = 0.0
    discount: float = 0.0
    codes: List[str] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mcp(cls, data: dict) -> "Coupon":
        amount = _as_float(pick(data, "amount", "value", "denomination", "couponAmount", "discountAmount", default=0.0))
        threshold = _as_float(
            pick(data, "threshold", "minAmount", "limitAmount", "conditionAmount",
                 "thresholdAmount", "useThreshold", default=0.0)
        )
        discount = _as_float(pick(data, "discount", "rate", "discountRate", "percent", default=0.0))
        codes = _as_str_list(
            pick(data, "mealCodes", "codes", "productCodes", "mealProductCodes",
                 "applicableMeals", "scope", default=[])
        )
        if threshold > 0 and amount > 0:
            kind = "threshold"
        elif 0 < discount <= 1:
            kind = "discount"
        elif codes:
            kind = "item"
        else:
            kind = "unknown"
        return cls(
            id=str(pick(data, "id", "couponId", "couponTemplateId", default="")),
            name=str(pick(data, "name", "title", "couponName", "description", default="未命名优惠券")),
            kind=kind,
            amount=amount,
            threshold=threshold,
            discount=discount,
            codes=codes,
            raw=dict(data) if isinstance(data, dict) else {},
        )

    def estimated_value(self, subtotal: float, codes: Sequence[str]) -> float:
        """估算券对某张候选订单的预计优惠额（用于排序、减少 calculate-price 调用次数）。"""
        if self.kind == "threshold":
            if self.threshold and subtotal < self.threshold:
                return 0.0
            return self.amount
        if self.kind == "discount":
            return subtotal * (1.0 - self.discount) if subtotal > 0 else 0.0
        if self.kind == "item":
            hit = [c for c in codes if c in set(self.codes)]
            if not hit:
                return 0.0
            return min(self.amount, subtotal) if self.amount else subtotal * 0.2
        # unknown：无法静态估算，调用方决定是否仍然试算
        return 0.0


def extract_coupons(payload: Any) -> List[Coupon]:
    if isinstance(payload, list):
        return [Coupon.from_mcp(c) for c in payload if isinstance(c, dict)]
    if not isinstance(payload, dict):
        return []
    raw = pick(payload, "coupons", "couponList", "data", "items", "list", default=None)
    if isinstance(raw, list):
        return [Coupon.from_mcp(c) for c in raw if isinstance(c, dict)]
    return []


# ---------------------------------------------------------------- 价格结果


@dataclass
class PriceResult:
    total: float = 0.0
    discount: float = 0.0
    delivery_fee: float = 0.0
    payable: float = 0.0
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mcp(cls, data: Any) -> "PriceResult":
        if not isinstance(data, dict):
            return cls()
        total = _as_float(pick(data, "totalPrice", "originalPrice", "totalAmount",
                               "goodsAmount", "totalFee", default=0.0))
        discount = _as_float(pick(data, "discountAmount", "discount", "couponAmount",
                                  "preferentialAmount", "promotionAmount", default=0.0))
        delivery = _as_float(pick(data, "deliveryFee", "deliveryAmount", "shippingFee",
                                  "deliveryPrice", default=0.0))
        payable = _as_float(pick(data, "payableAmount", "payAmount", "actualAmount",
                                 "totalPayable", "paymentAmount", default=0.0))
        if payable == 0.0 and (total or discount or delivery):
            payable = total - discount + delivery
        return cls(total=total, discount=discount, delivery_fee=delivery,
                   payable=payable, raw=dict(data))


# ---------------------------------------------------------------- 营养


@dataclass
class Nutrition:
    code: str = ""
    name: str = ""
    energy_kcal: float = 0.0
    protein_g: float = 0.0
    fat_g: float = 0.0
    carb_g: float = 0.0
    sodium_mg: float = 0.0
    calcium_mg: float = 0.0
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mcp(cls, data: Any) -> "Nutrition":
        if not isinstance(data, dict):
            return cls()
        return cls(
            code=str(pick(data, "code", "mealCode", "id", default="")),
            name=str(pick(data, "name", "mealName", "title", default="")),
            energy_kcal=_as_float(pick(data, "energy", "energyKcal", "kcal", "energy_kcal",
                                       "热量", "calorie", default=0.0)),
            protein_g=_as_float(pick(data, "protein", "protein_g", "蛋白质", default=0.0)),
            fat_g=_as_float(pick(data, "fat", "fat_g", "脂肪", default=0.0)),
            carb_g=_as_float(pick(data, "carbohydrate", "carb", "carb_g", "碳水",
                                  "碳水化合物", default=0.0)),
            sodium_mg=_as_float(pick(data, "sodium", "sodium_mg", "钠", default=0.0)),
            calcium_mg=_as_float(pick(data, "calcium", "calcium_mg", "钙", default=0.0)),
            raw=dict(data),
        )

    def __add__(self, other: "Nutrition") -> "Nutrition":
        return Nutrition(
            code="", name="",
            energy_kcal=self.energy_kcal + other.energy_kcal,
            protein_g=self.protein_g + other.protein_g,
            fat_g=self.fat_g + other.fat_g,
            carb_g=self.carb_g + other.carb_g,
            sodium_mg=self.sodium_mg + other.sodium_mg,
            calcium_mg=self.calcium_mg + other.calcium_mg,
        )


def extract_nutrition(payload: Any) -> List[Nutrition]:
    if isinstance(payload, list):
        return [Nutrition.from_mcp(n) for n in payload if isinstance(n, dict)]
    if not isinstance(payload, dict):
        return []
    raw = pick(payload, "foods", "nutrition", "list", "items", "data", default=None)
    if isinstance(raw, list):
        return [Nutrition.from_mcp(n) for n in raw if isinstance(n, dict)]
    return []


def nutrition_by_code(items: Sequence[Nutrition]) -> Dict[str, Nutrition]:
    return {n.code: n for n in items if n.code}
