"""麦门日报与 CLI 测试。"""

import json

import pytest

from mcd_strategist.briefing import daily_brief
from mcd_strategist.cli import main
from mcd_strategist.mcp_client import McpError
from mcd_strategist.mockserver import MockMcpClient, TOOLS

from mcd_strategist.models import (
    Nutrition,
    extract_meals,
    extract_nutrition,
    pick,
)


class TestBrief:
    def test_sections_present(self):
        text = daily_brief(MockMcpClient())
        for section in ("麦门日报", "现在", "本月活动", "可领优惠券", "我的积分", "积分抽奖"):
            assert section in text

    def test_campaign_content(self):
        text = daily_brief(MockMcpClient())
        assert "1024" in text

    def test_points_content(self):
        text = daily_brief(MockMcpClient())
        assert "2860" in text

    def test_tolerates_single_tool_failure(self):
        class FlakyClient(MockMcpClient):
            def call_tool(self, name, arguments=None):
                if name == "campaign-calendar":
                    raise McpError("活动日历暂时不可用")
                return super().call_tool(name, arguments)

        text = daily_brief(FlakyClient())
        assert "获取失败" in text
        assert "我的积分" in text  # 其他板块不受影响

    def test_explicit_time(self):
        text = daily_brief(MockMcpClient(), now="2026-10-15 10:00:00")
        assert "2026-10-15 10:00:00" in text


class TestCli:
    def test_tools_lists_all(self, capsys):
        rc = main(["--mock", "tools"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "麦当劳 MCP 共 33 个工具" in out
        assert "calculate-price" in out

    def test_radar_json_output(self, capsys):
        rc = main(["--mock", "--json", "radar", "--budget", "30"])
        out = capsys.readouterr().out
        assert rc == 0
        data = json.loads(out)
        assert data["budget"] == 30.0
        assert data["rows"]
        assert data["rows"][0]["withinBudget"] is True
        assert data["rows"][0]["payable"] <= 30.0

    def test_radar_text_output(self, capsys):
        rc = main(["--mock", "radar", "--budget", "30", "--rows", "3"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "省钱雷达" in out
        assert out.count("实付") >= 1

    def test_order_dry_run(self, capsys):
        rc = main(["--mock", "order", "巨无霸套餐"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "巨无霸套餐×1" in out
        assert "未创建订单" in out

    def test_order_confirm_submits(self, capsys):
        rc = main(["--mock", "order", "巨无霸套餐", "--confirm"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "订单已创建" in out
        assert "MOCK-ORDER-0001" in out

    def test_order_unrecognized_refused(self, capsys):
        rc = main(["--mock", "order", "不存在的东西", "--confirm"])
        assert rc == 2
        assert "拒绝下单" in capsys.readouterr().err

    def test_cancel_subcommand(self, capsys):
        rc = main(["--mock", "cancel", "MOCK-ORDER-0001"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "已申请取消订单 MOCK-ORDER-0001" in out

    def test_nutrition_by_name(self, capsys):
        rc = main(["--mock", "nutrition", "麦辣鸡腿堡套餐"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "kcal" in out

    def test_nutrition_requires_input(self, capsys):
        rc = main(["--mock", "nutrition"])
        assert rc == 2

    def test_demo_runs_end_to_end(self, capsys):
        rc = main(["demo"])
        out = capsys.readouterr().out
        assert rc == 0
        for marker in ("麦门日报", "省钱雷达", "一句话点餐", "营养把关", "积分抽奖", "演示结束"):
            assert marker in out

    def test_missing_token_falls_back_to_mock(self, capsys):
        rc = main(["tools"])
        captured = capsys.readouterr()
        # 无 token：应回退 mock 并给出提示
        assert rc == 0
        assert "离线演示模式" in captured.err


class TestModels:
    def test_pick_first_non_none(self):
        assert pick({"a": None, "b": 1}, "a", "b", default=0) == 1
        assert pick({}, "x", default="d") == "d"
        assert pick(None, "x", default="d") == "d"

    def test_meal_tolerates_alternate_field_names(self):
        meal = extract_meals([{"mealCode": "X1", "mealName": "测试堡", "currentPrice": "9.9"}])[0]
        assert meal.code == "X1" and meal.name == "测试堡" and meal.price == 9.9

    def test_nutrition_field_mapping(self):
        n = extract_nutrition([{"code": "X", "name": "测试", "热量": 450, "蛋白质": 20}])[0]
        assert n.energy_kcal == 450 and n.protein_g == 20

    def test_mock_tool_list_matches_official_count(self):
        assert len(TOOLS) == 33
