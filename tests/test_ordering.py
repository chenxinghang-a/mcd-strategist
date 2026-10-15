"""一句话点餐测试：解析、dry-run、下单边界。"""

import pytest

from mcd_strategist.models import Meal
from mcd_strategist.mockserver import MockMcpClient
from mcd_strategist.ordering import draft_order, parse_text, submit_order


def _menu():
    return [
        Meal.from_mcp({"code": "B0101", "name": "巨无霸套餐", "price": 27.5, "category": "主食套餐"}),
        Meal.from_mcp({"code": "D0301", "name": "薯条(小)", "price": 7.0, "category": "小食"}),
        Meal.from_mcp({"code": "C0401", "name": "可口可乐(中)", "price": 8.0, "category": "饮料"}),
    ]


class TestParseText:
    def test_direct_name(self):
        q = parse_text("来一份巨无霸套餐", _menu())
        assert q == {"B0101": 1}

    def test_synonym(self):
        q = parse_text("我想喝可乐", _menu())
        assert q == {"C0401": 1}

    def test_multiple_items(self):
        q = parse_text("巨无霸套餐加薯条", _menu())
        assert q == {"B0101": 1, "D0301": 1}

    def test_no_match(self):
        assert parse_text("来一杯奶茶", _menu()) == {}


class TestDraftOrder:
    def test_dry_run_does_not_create_order(self):
        client = MockMcpClient()
        plan = draft_order(client, "巨无霸套餐")
        assert plan.quantities == {"B0101": 1}
        assert plan.price is not None and plan.price.payable == 27.5
        assert not plan.submitted
        assert not any(c[0] == "create-order" for c in client.calls)

    def test_store_auto_selected(self):
        client = MockMcpClient()
        plan = draft_order(client, "薯条")
        assert plan.store and plan.store["storeId"] == "MOCK001"

    def test_explicit_store(self):
        client = MockMcpClient()
        plan = draft_order(client, "薯条", store_id="MOCK002")
        assert plan.store["storeId"] == "MOCK002"

    def test_unrecognized_text(self):
        client = MockMcpClient()
        plan = draft_order(client, "给我来杯咖啡")
        assert plan.quantities == {}
        assert plan.notes

    def test_items_shape_for_create_order(self):
        client = MockMcpClient()
        plan = draft_order(client, "巨无霸套餐 薯条")
        assert plan.items == [{"code": "B0101", "quantity": 1}, {"code": "D0301", "quantity": 1}]


class TestSubmitOrder:
    def test_submit_creates_order_with_pay_url(self):
        client = MockMcpClient()
        plan = draft_order(client, "巨无霸套餐")
        submit_order(client, plan)
        assert plan.submitted
        assert plan.order_id == "MOCK-ORDER-0001"
        assert plan.payment_url.startswith("https://")

    def test_submit_delivery_flag(self):
        client = MockMcpClient()
        plan = draft_order(client, "薯条")
        submit_order(client, plan, delivery=True)
        order = client._tool_query_order({})["order"]
        assert order["eatType"] == "DELIVERY"

    def test_submit_rejects_empty_plan(self):
        client = MockMcpClient()
        plan = draft_order(client, "不存在的东西")
        with pytest.raises(ValueError):
            submit_order(client, plan)

    def test_submit_passes_store_and_meals(self):
        client = MockMcpClient()
        plan = draft_order(client, "薯条", store_id="MOCK002")
        submit_order(client, plan)
        order = client._tool_query_order({})["order"]
        assert order["storeId"] == "MOCK002"
        assert order["meals"] == [{"code": "D0301", "quantity": 1}]
