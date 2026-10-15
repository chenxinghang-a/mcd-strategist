"""积分抽奖测试：信息读取、dry-run 安全、确认后真实调用、容错降级。"""

import json

from mcd_strategist.cli import main
from mcd_strategist.lottery import draw_once, lottery_status, my_prizes
from mcd_strategist.mockserver import MockMcpClient


class TestLotteryModule:
    def test_status_reads_info_and_points(self):
        status = lottery_status(MockMcpClient())
        assert status.status == "进行中"
        assert status.cost == 100
        assert "巨无霸免费兑换券" in status.prizes
        assert status.available_points == 2860
        assert status.affordable()

    def test_my_prizes_empty_by_default(self):
        assert my_prizes(MockMcpClient()) == []

    def test_draw_once_normalizes_and_calls_tool(self):
        client = MockMcpClient()
        result = draw_once(client)
        assert result["win"] is False  # mock 固定未中奖，保证测试可复现
        assert result["costPaid"] == 100
        assert any(c[0] == "draw-lottery" for c in client.calls)

    def test_status_tolerates_account_failure(self):
        class NoAccount(MockMcpClient):
            def _tool_query_my_account(self, args):
                raise RuntimeError("账户服务暂不可用")

        status = lottery_status(NoAccount())
        assert status.cost == 100  # 活动信息仍可读
        assert status.account_ok is False
        assert status.affordable()  # 余额未知时不拦截

    def test_prizes_tolerate_tool_failure(self):
        class FlakyPrize(MockMcpClient):
            def _tool_query_my_prizes(self, args):
                raise RuntimeError("暂不可用")

        assert my_prizes(FlakyPrize()) == []


class TestLotteryCli:
    def test_info_only(self, capsys):
        rc = main(["--mock", "lottery"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "积分抽奖" in out
        assert "100" in out
        assert "巨无霸免费兑换券" in out

    def test_draw_default_is_dry_run(self, capsys):
        rc = main(["--mock", "lottery", "--draw"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "dry-run" in out
        assert "未中奖" not in out  # dry-run 不执行抽奖

    def test_draw_confirm_executes(self, capsys):
        rc = main(["--mock", "lottery", "--draw", "--confirm"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "未中奖" in out

    def test_confirm_without_draw_refused(self, capsys):
        rc = main(["--mock", "lottery", "--confirm"])
        assert rc == 2
        assert "--draw" in capsys.readouterr().err

    def test_json_output(self, capsys):
        rc = main(["--mock", "--json", "lottery"])
        out = capsys.readouterr().out
        assert rc == 0
        data = json.loads(out)
        assert data["lottery"]["cost"] == 100
        assert data["myPrizes"] == []
        assert data["dryRun"] is False
