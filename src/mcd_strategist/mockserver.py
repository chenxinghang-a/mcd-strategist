"""离线模拟麦当劳 MCP。

用途：
1. pytest 全离线运行（不需要 Token、不联网）；
2. `mcd --mock demo` 让用户在申请 Token 前预览全部能力；
3. 复现 tool 名称与大致返回结构（字段名宽松解析见 models.py）。

⚠️ 这是测试替身（test double）：餐品价格/券面都是编造的演示数据，
与麦当劳真实菜单无关；真实数据请在取得 MCP Token 后去掉 --mock 使用。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from .mcp_client import McpError, ToolCallError

SERVER_URL = "https://mcp.mcd.cn"

# ---------------------------------------------------------------- 工具清单
# 与 https://github.com/M-China/mcd-mcp-server 公布的工具一一对应
TOOLS: List[Tuple[str, str]] = [
    ("list-nutrition-foods", "餐品营养信息列表"),
    ("delivery-query-addresses", "获取用户可配送地址列表"),
    ("delivery-create-address", "新增配送地址"),
    ("delivery-query-stores", "查询可配送的门店列表"),
    ("query-meal-assistance", "查询助餐服务"),
    ("query-nearby-stores", "查询附近可用门店"),
    ("query-store-coupons", "查询用户当前门店下可用的优惠券列表"),
    ("query-meals", "查询当前可售卖的餐品列表"),
    ("query-meal-detail", "查询餐品详情"),
    ("calculate-price", "商品价格计算"),
    ("create-order", "创建订单"),
    ("cancel-order", "取消订单"),
    ("query-order", "查询订单详情"),
    ("order-list", "查询历史订单"),
    ("campaign-calendar", "活动日历查询工具"),
    ("available-coupons", "麦麦省券列表查询"),
    ("auto-bind-coupons", "麦麦省一键领券"),
    ("query-my-coupons", "我的优惠券查询"),
    ("query-my-account", "我的积分查询"),
    ("mall-points-products", "查询麦麦商城商品列表"),
    ("mall-product-detail", "查询麦麦商城商品详情"),
    ("mall-create-order", "积分兑换商品下单"),
    ("now-time-info", "获取当前时间信息"),
    ("query-lottery-info", "查看积分抽奖活动信息"),
    ("draw-lottery", "积分抽奖"),
    ("query-my-prizes", "查看我的奖品"),
    ("mall-order-list", "麦麦商城订单查询"),
    ("mall-order-detail", "麦麦商城订单详情查询"),
    ("query-party-city", "主题活动城市列表查询"),
    ("query-party-store", "主题活动门店列表查询"),
    ("query-partystore-date", "主题活动可预约日期查询"),
    ("query-partystore-session", "主题活动可预约日期场次查询"),
    ("party-order-create", "主题活动订单创建"),
]

# ---------------------------------------------------------------- 菜单
def _meal(code, name, category, price, tags, kcal, protein, fat, carb, sodium, calcium):
    return {
        "code": code,
        "name": name,
        "category": category,
        "price": price,
        "tags": tags,
        "_nutrition": {
            "code": code,
            "name": name,
            "energyKcal": kcal,
            "protein": protein,
            "fat": fat,
            "carbohydrate": carb,
            "sodium": sodium,
            "calcium": calcium,
        },
    }


MEALS: List[dict] = [
    _meal("B0101", "巨无霸套餐", "主食套餐", 27.5, ["汉堡", "人气"], 820, 30, 40, 78, 1150, 220),
    _meal("B0102", "麦辣鸡腿堡套餐", "主食套餐", 26.5, ["汉堡", "辣"], 880, 31, 44, 80, 1280, 210),
    _meal("B0103", "板烧鸡腿堡套餐", "主食套餐", 26.0, ["汉堡"], 760, 32, 30, 76, 980, 230),
    _meal("B0104", "双层鳕鱼堡套餐", "主食套餐", 25.0, ["汉堡"], 700, 26, 32, 74, 890, 190),
    _meal("B0105", "麦香鱼套餐", "主食套餐", 23.5, ["汉堡"], 660, 24, 30, 72, 810, 180),
    _meal("B0106", "麦满分套餐", "早餐套餐", 21.0, ["早餐"], 540, 22, 22, 60, 760, 260),
    _meal("H0201", "巨无霸汉堡", "汉堡", 14.5, ["汉堡"], 560, 26, 30, 45, 900, 190),
    _meal("H0202", "麦辣鸡腿堡", "汉堡", 13.5, ["汉堡", "辣"], 590, 25, 33, 46, 1020, 180),
    _meal("H0203", "板烧鸡腿堡", "汉堡", 13.0, ["汉堡"], 480, 26, 20, 45, 780, 190),
    _meal("H0204", "麦香鱼", "汉堡", 11.5, ["汉堡"], 390, 17, 18, 42, 620, 130),
    _meal("D0301", "薯条(小)", "小食", 7.0, ["薯条", "小食"], 230, 3, 11, 31, 190, 20),
    _meal("D0302", "薯条(中)", "小食", 8.5, ["薯条", "小食"], 320, 4, 15, 43, 260, 30),
    _meal("D0303", "薯条(大)", "小食", 10.0, ["薯条", "小食"], 430, 5, 20, 57, 350, 40),
    _meal("D0304", "麦乐鸡(5块)", "小食", 11.0, ["小食"], 260, 15, 16, 16, 560, 40),
    _meal("D0305", "菠萝派", "小食", 6.5, ["派", "小食"], 250, 2, 12, 34, 130, 20),
    _meal("D0306", "香芋派", "小食", 6.5, ["派", "小食"], 240, 2, 11, 33, 120, 20),
    _meal("C0401", "可口可乐(中)", "饮料", 8.0, ["可乐", "饮料"], 160, 0, 0, 40, 15, 0),
    _meal("C0402", "可口可乐(大)", "饮料", 9.0, ["可乐", "饮料"], 210, 0, 0, 53, 20, 0),
    _meal("C0403", "鲜煮咖啡", "饮料", 10.0, ["咖啡", "饮料"], 90, 3, 3, 12, 30, 40),
    _meal("C0404", "雪碧(中)", "饮料", 8.0, ["饮料"], 150, 0, 0, 38, 14, 0),
    _meal("S0501", "圆筒冰淇淋", "甜品", 5.0, ["冰淇淋", "甜品"], 150, 4, 5, 22, 60, 120),
    _meal("S0502", "麦旋风", "甜品", 9.5, ["冰淇淋", "甜品"], 330, 7, 12, 47, 130, 200),
]

NUTRITION = [m.pop("_nutrition") for m in MEALS]

# ---------------------------------------------------------------- 券
COUPONS: List[dict] = [
    {"id": "CP0001", "name": "满20减3", "amount": 3.0, "threshold": 20.0},
    {"id": "CP0002", "name": "满40减8", "amount": 8.0, "threshold": 40.0},
    {"id": "CP0003", "name": "麦麦省8.8折券", "discount": 0.88},
    {"id": "CP0004", "name": "薯条立减5元", "amount": 5.0, "mealCodes": ["D0301", "D0302", "D0303"]},
    {"id": "CP0005", "name": "麦辣鸡腿堡套餐立减6元", "amount": 6.0, "mealCodes": ["B0102"]},
]

# ---------------------------------------------------------------- 其他
STORES = [
    {"storeId": "MOCK001", "name": "麦当劳(科技园餐厅)", "address": "广东省深圳市南山区科技园南路 1 号",
     "distance": 350},
    {"storeId": "MOCK002", "name": "麦当劳(创业大厦餐厅)", "address": "广东省深圳市南山区创业路 88 号",
     "distance": 900},
]

ADDRESSES = [
    {"addressId": "AD0001", "name": "公司", "phone": "138****0000",
     "address": "南山区科技园南路 1 号 A 座 12F", "deliverable": True},
]

CAMPAIGNS = [
    {"name": "1024 程序员节 · 麦麦致敬代码人", "status": "进行中",
     "startTime": "2026-10-09", "endTime": "2026-10-25"},
    {"name": "麦乐送免配送费周", "status": "进行中", "startTime": "2026-10-10", "endTime": "2026-10-18"},
    {"name": "麦麦省领券日 · 满20减3", "status": "进行中", "startTime": "2026-10-01", "endTime": "2026-10-31"},
    {"name": "早餐卡买一送一", "status": "已结束", "startTime": "2026-09-01", "endTime": "2026-09-30"},
]

POINTS = {"availablePoints": 2860, "totalPoints": 12400, "frozenPoints": 0, "expiringPoints": 500}

LOTTERY = {"status": "进行中", "cost": 100,
           "prizes": [{"name": "巨无霸免费兑换券"}, {"name": "50 积分"}, {"name": "薯条(小)"}]}

MALL_PRODUCTS = [
    {"productId": "P0001", "name": "麦麦程序员徽章", "points": 1500, "stock": 100},
    {"productId": "P0002", "name": "汉堡回车键玩具", "points": 3200, "stock": 30},
]


def _price_of() -> Dict[str, float]:
    return {m["code"]: m["price"] for m in MEALS}


def calculate_price(args: dict) -> dict:
    """确定性的价格计算：满减 / 折扣 / 单品券按序各生效一次。"""
    items = args.get("meals") or []
    prices = _price_of()
    detail = []
    total = 0.0
    for it in items:
        code = str(it.get("code", ""))
        qty = int(it.get("quantity", 1) or 1)
        price = prices.get(code, 0.0)
        total += price * qty
        detail.append({"code": code, "quantity": qty, "price": price})

    discount = 0.0
    used = []
    for cid in args.get("couponIds") or []:
        coupon = next((c for c in COUPONS if c["id"] == cid), None)
        if not coupon:
            continue
        codes = {str(it.get("code", "")) for it in items}
        if coupon.get("threshold") and total >= coupon["threshold"]:
            discount += coupon["amount"]
            used.append(coupon["name"])
        elif coupon.get("discount"):
            discount += total * (1 - coupon["discount"])
            used.append(coupon["name"])
        elif coupon.get("mealCodes") and codes & set(coupon["mealCodes"]):
            discount += coupon["amount"]
            used.append(coupon["name"])

    delivery = 6.0 if args.get("eatType") == "DELIVERY" else 0.0
    return {
        "totalPrice": round(total, 2),
        "discountAmount": round(discount, 2),
        "deliveryFee": delivery,
        "payableAmount": round(total - discount + delivery, 2),
        "usedCoupons": used,
        "items": detail,
    }


class MockMcpClient:
    """与 McpClient 同接口（initialize/list_tools/call_tool）的离线实现。"""

    url = SERVER_URL

    def __init__(self, *, latency: float = 0.0) -> None:
        self.latency = latency
        self.initialized = False
        self.calls: List[Tuple[str, dict]] = []
        self._orders: List[dict] = []

    # ---------------------------------------------------------------- 协议
    def initialize(self) -> dict:
        self.initialized = True
        return {
            "protocolVersion": "2025-03-26",
            "serverInfo": {"name": "mock-mcd-mcp", "version": "1.8.9"},
            "capabilities": {"tools": {}},
        }

    def list_tools(self, refresh: bool = False) -> List[dict]:
        return [{"name": name, "description": desc} for name, desc in TOOLS]

    # ---------------------------------------------------------------- 调用
    def call_tool(self, name: str, arguments: Optional[dict] = None) -> dict:
        args = arguments or {}
        self.calls.append((name, dict(args)))
        if self.latency:
            time.sleep(self.latency)
        handler = getattr(self, f"_tool_{name.replace('-', '_')}", None)
        if handler is None:
            raise McpError(f"未知工具：{name}")
        return handler(args)

    # ---------------------------------------------------------------- handlers
    def _tool_query_meals(self, args: dict) -> dict:
        store_id = args.get("storeId")
        return {"storeId": store_id or "MOCK001", "meals": MEALS}

    def _tool_query_meal_detail(self, args: dict) -> dict:
        code = str(args.get("code") or args.get("mealCode") or "")
        meal = next((m for m in MEALS if m["code"] == code), None)
        if meal is None:
            raise ToolCallError(f"餐品不存在：{code}")
        return {"meal": meal, "composition": ["面包", "肉饼", "酱汁", "蔬菜"]}

    def _tool_calculate_price(self, args: dict) -> dict:
        return calculate_price(args)

    def _tool_query_nearby_stores(self, args: dict) -> dict:
        return {"stores": STORES}

    def _tool_delivery_query_stores(self, args: dict) -> dict:
        return {"stores": STORES}

    def _tool_delivery_query_addresses(self, args: dict) -> dict:
        return {"addresses": ADDRESSES}

    def _tool_delivery_create_address(self, args: dict) -> dict:
        address = dict(args)
        address.setdefault("addressId", "AD%04d" % (len(ADDRESSES) + 1))
        ADDRESSES.append(address)
        return address

    def _tool_available_coupons(self, args: dict) -> dict:
        return {"coupons": COUPONS}

    def _tool_query_store_coupons(self, args: dict) -> dict:
        return {"coupons": COUPONS[:2]}

    def _tool_query_my_coupons(self, args: dict) -> dict:
        return {"coupons": COUPONS[:2]}

    def _tool_auto_bind_coupons(self, args: dict) -> dict:
        return {"bound": [c["id"] for c in COUPONS], "count": len(COUPONS)}

    def _tool_list_nutrition_foods(self, args: dict) -> dict:
        return {"foods": NUTRITION}

    def _tool_campaign_calendar(self, args: dict) -> dict:
        return {"campaigns": CAMPAIGNS}

    def _tool_now_time_info(self, args: dict) -> dict:
        # 演示数据固定时间，保证测试可复现
        return {
            "datetime": "2026-10-15 10:41:00",
            "timestamp": 1760493660,
            "weekday": "Thursday",
            "timezone": "Asia/Shanghai",
        }

    def _tool_query_my_account(self, args: dict) -> dict:
        return dict(POINTS)

    def _tool_query_lottery_info(self, args: dict) -> dict:
        return dict(LOTTERY)

    def _tool_draw_lottery(self, args: dict) -> dict:
        return {"win": False, "prize": None, "costPaid": LOTTERY["cost"]}

    def _tool_query_my_prizes(self, args: dict) -> dict:
        return {"prizes": [], "total": 0}

    def _tool_mall_points_products(self, args: dict) -> dict:
        return {"products": MALL_PRODUCTS}

    def _tool_mall_product_detail(self, args: dict) -> dict:
        pid = str(args.get("productId") or "")
        product = next((p for p in MALL_PRODUCTS if p["productId"] == pid), None)
        if product is None:
            raise ToolCallError(f"商品不存在：{pid}")
        return product

    def _tool_mall_order_list(self, args: dict) -> dict:
        return {"orders": []}

    def _tool_mall_order_detail(self, args: dict) -> dict:
        return {"order": None}

    def _tool_mall_create_order(self, args: dict) -> dict:
        return {"exchangeOrderId": "MOCK-EX-0001", "voucherCode": "MOCK-VC-0001"}

    def _tool_create_order(self, args: dict) -> dict:
        order = {
            "orderId": "MOCK-ORDER-%04d" % (len(self._orders) + 1),
            "status": "WAIT_BUYER_PAY",
            "payUrl": "https://api.mcd.cn/mock/pay/MOCK-ORDER-%04d" % (len(self._orders) + 1),
            "eatType": args.get("eatType", "TAKEOUT"),
            "storeId": args.get("storeId", "MOCK001"),
            "meals": args.get("meals", []),
        }
        self._orders.append(order)
        return order

    def _tool_cancel_order(self, args: dict) -> dict:
        return {"success": True, "orderId": args.get("orderId", "")}

    def _tool_query_order(self, args: dict) -> dict:
        return {"order": self._orders[-1] if self._orders else None}

    def _tool_order_list(self, args: dict) -> dict:
        return {"orders": self._orders}

    def _tool_query_meal_assistance(self, args: dict) -> dict:
        return {"assistance": ["宝宝椅", "轮椅通道"]}

    def _tool_query_party_city(self, args: dict) -> dict:
        return {"cities": [{"cityId": "440300", "name": "深圳市"}]}

    def _tool_query_party_store(self, args: dict) -> dict:
        return {"stores": STORES[:1]}

    def _tool_query_partystore_date(self, args: dict) -> dict:
        return {"dates": ["2026-10-20", "2026-10-21"]}

    def _tool_query_partystore_session(self, args: dict) -> dict:
        return {"sessions": [{"sessionId": "AM", "name": "上午场"}, {"sessionId": "PM", "name": "下午场"}]}

    def _tool_party_order_create(self, args: dict) -> dict:
        return {"orderId": "MOCK-PARTY-0001", "status": "WAIT_BUYER_PAY"}
