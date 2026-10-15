"""营养把关测试。"""

from mcd_strategist.models import Nutrition
from mcd_strategist.nutrition import assess, combo_nutrition, summarize, swap_suggestions


def _n(code, kcal=500, sodium=500, fat=20):
    return Nutrition(code=code, name=code, energy_kcal=kcal, sodium_mg=sodium, fat_g=fat)


class TestAssess:
    def test_healthy_no_issues(self):
        assert assess(_n("A", kcal=400, sodium=400, fat=15)) == []

    def test_high_energy(self):
        issues = assess(_n("A", kcal=900))
        assert [i.field for i in issues] == ["energy_kcal"]

    def test_multiple_issues(self):
        issues = assess(_n("A", kcal=900, sodium=1200, fat=40))
        assert {i.field for i in issues} == {"energy_kcal", "sodium_mg", "fat_g"}

    def test_issue_text_contains_value(self):
        issues = assess(_n("A", sodium=1000))
        assert "1000" in issues[0].text and "钠" in issues[0].text


class TestCombo:
    def test_sum(self):
        total = combo_nutrition(["A", "B"], {"A": _n("A", kcal=300), "B": _n("B", kcal=400)})
        assert total.energy_kcal == 700

    def test_missing_code_ignored(self):
        total = combo_nutrition(["A", "Z"], {"A": _n("A", kcal=300)})
        assert total.energy_kcal == 300


class TestSwap:
    def test_suggests_lower_kcal(self):
        current = _n("CUR", kcal=800)
        pool = [_n("CUR", kcal=800), _n("NEW1", kcal=600), _n("NEW2", kcal=700)]
        sug = swap_suggestions(current, pool, limit=3)
        assert [s.code for s in sug] == ["NEW1", "NEW2"]

    def test_excludes_higher_kcal(self):
        current = _n("CUR", kcal=600)
        pool = [_n("CUR", kcal=600), _n("BAD", kcal=900)]
        assert swap_suggestions(current, pool) == []

    def test_excludes_higher_sodium(self):
        current = Nutrition(code="CUR", energy_kcal=600, sodium_mg=500)
        pool = [current, Nutrition(code="X", energy_kcal=400, sodium_mg=900)]
        assert swap_suggestions(current, pool) == []


class TestSummarize:
    def test_ok_flag(self):
        text = summarize("测试餐", _n("A", kcal=400, sodium=300, fat=10))
        assert "✅" in text

    def test_warn_flag(self):
        text = summarize("测试餐", _n("A", kcal=900))
        assert "⚠️" in text
