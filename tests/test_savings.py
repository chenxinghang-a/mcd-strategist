"""省钱雷达测试：候选生成、券分类、比价编排。"""

import pytest

from mcd_strategist.models import Coupon, Meal, extract_coupons, extract_meals
from mcd_strategist.mockserver import MockMcpClient
from mcd_strategist.savings import generate_candidates, plan_radar


def _meal(code, name, price, **kw):
    return Meal.from_mcp({"code": code, "name": name, "price": price, **kw})


@pytest.fixture()
def menu():
    return [
        _meal("C1", "麦麦套餐A", 25.0, category="主食套餐"),
        _meal("C2", "麦麦套餐B", 22.0, category="主食套餐"),
        _meal("M1", "元气汉堡", 12.0, category="汉堡"),
        _meal("D1", "冰爽可乐", 6.0, category="饮料"),
        _meal("S1", "黄金薯条", 8.0, category="小食"),
    ]


class TestCandidates:
    def test_roles_classification(self):
        assert _meal("C", "巨无霸套餐", 25).role() == "combo"
        assert _meal("D", "可口可乐(中)", 8, category="饮料").role() == "drink"
        assert _meal("S", "薯条(大)", 10, category="小食").role() == "side"
        assert _meal("M", "巨无霸汉堡", 14, category="汉堡").role() == "main"

    def test_generate_has_all_priority_layers(self, menu):
        cands = generate_candidates(menu, budget=40)
        labels = [c.label for c in cands]
        assert "麦麦套餐A" in labels
        assert "麦麦套餐A + 冰爽可乐" in labels
        assert "元气汉堡" in labels
        assert "元气汉堡 + 黄金薯条" in labels
        assert "元气汉堡 + 冰爽可乐 + 黄金薯条" in labels

    def test_budget_filter_drops_expensive(self, menu):
        # 主餐+饮料+小食 = 26.0 > 18*1.3=23.4 → 被过滤
        cands = generate_candidates(menu, budget=18)
        assert all("元气汉堡 + 冰爽可乐 + 黄金薯条" != c.label for c in cands)

    def test_candidates_capped(self, menu):
        big = menu + [_meal(f"M{i}", f"汉堡{i}", 10.0 + i) for i in range(40)]
        cands = generate_candidates(big, budget=100, max_candidates=10)
        assert len(cands) <= 10

    def test_dedupe(self, menu):
        cands = generate_candidates(menu, budget=100)
        keys = [c.signature() for c in cands]
        assert len(keys) == len(set(keys))


class TestCouponModel:
    def test_threshold(self):
        c = Coupon.from_mcp({"id": "1", "name": "满20减3", "amount": 3, "threshold": 20})
        assert c.kind == "threshold"

    def test_discount(self):
        c = Coupon.from_mcp({"id": "2", "name": "8.8折", "discount": 0.88})
        assert c.kind == "discount"

    def test_item(self):
        c = Coupon.from_mcp({"id": "3", "name": "薯条立减5", "amount": 5, "mealCodes": ["D1"]})
        assert c.kind == "item"

    def test_estimated_value_threshold_not_met(self):
        c = Coupon.from_mcp({"id": "1", "name": "满40减8", "amount": 8, "threshold": 40})
        assert c.estimated_value(25.0, ["M1"]) == 0.0
        assert c.estimated_value(40.0, ["M1"]) == 8.0

    def test_extract_coupons_flat_and_nested(self):
        assert extract_coupons({"coupons": [{"id": "1"}]})[0].id == "1"
        assert extract_coupons([{"id": "2"}])[0].id == "2"

    def test_extract_meals_nested_categories(self):
        payload = {"categories": [{"name": "套餐", "meals": [{"code": "C1", "name": "套餐A", "price": 20}]}]}
        meals = extract_meals(payload)
        assert len(meals) == 1 and meals[0].code == "C1"


class TestRadar:
    def test_radar_within_budget(self):
        client = MockMcpClient()
        report = plan_radar(client, budget=30.0, max_rows=3)
        assert report.rows, "应产生排行结果"
        for row in report.rows:
            assert row.price.payable > 0
        # 预算内方案必须排在超预算方案之前
        within = [r.within_budget for r in report.rows]
        assert within == sorted(within, reverse=True)

    def test_radar_best_row_is_cheapest_within_budget(self):
        client = MockMcpClient()
        report = plan_radar(client, budget=30.0, max_rows=5)
        within = [r for r in report.rows if r.within_budget]
        assert within
        payables = [r.price.payable for r in within]
        assert payables == sorted(payables)

    def test_radar_uses_coupon_when_beneficial(self):
        client = MockMcpClient()
        report = plan_radar(client, budget=30.0, max_rows=5)
        best = report.rows[0]
        assert best.coupon_name, "最优方案应当用上了券"
        assert best.price.discount > 0

    def test_radar_bind_coupons_records_note(self):
        client = MockMcpClient()
        report = plan_radar(client, budget=30.0, bind_coupons=True)
        assert any("自动领取" in n for n in report.notes)

    def test_radar_tiny_budget_shows_note(self):
        client = MockMcpClient()
        report = plan_radar(client, budget=3.0, max_rows=3)
        assert report.rows == []
        assert any("预算" in n for n in report.notes)

    def test_radar_stays_within_rate_limit(self):
        client = MockMcpClient()
        report = plan_radar(client, budget=30.0, max_rows=5)
        price_calls = [c for c in client.calls if c[0] == "calculate-price"]
        # 600 次/分钟的官方限额；我们只允许远低于它的调用量
        assert len(price_calls) < 200
        nutrition_calls = [c for c in client.calls if c[0] == "list-nutrition-foods"]
        assert len(nutrition_calls) == 1  # 营养表只取一次

    def test_radar_empty_menu_raises(self):
        class EmptyClient(MockMcpClient):
            def _tool_query_meals(self, args):
                return {"storeId": "X", "meals": []}

        with pytest.raises(ValueError):
            plan_radar(EmptyClient(), budget=30.0)

    def test_radar_to_dict_shape(self):
        client = MockMcpClient()
        data = plan_radar(client, budget=30.0, max_rows=2).to_dict()
        assert data["budget"] == 30.0
        row = data["rows"][0]
        assert {"label", "items", "coupon", "payable", "withinBudget"} <= set(row)
        assert row["withinBudget"] is True
