"""营养把关：热量 / 钠 / 脂肪的阈值评估与替换建议。

阈值参考《中国居民膳食指南》常用建议值（按单份/单餐保守取）：
- 单餐能量 > 650 kcal          → 偏高
- 单餐钠   > 800 mg           → 偏高（成人每日推荐 ≤ 2000 mg）
- 单餐脂肪 > 30 g             → 偏高
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence

from .models import Meal, Nutrition, pick

THRESHOLDS = {
    "energy_kcal": 650.0,
    "sodium_mg": 800.0,
    "fat_g": 30.0,
}

_LABELS = {
    "energy_kcal": "能量",
    "sodium_mg": "钠",
    "fat_g": "脂肪",
}
_UNITS = {
    "energy_kcal": "kcal",
    "sodium_mg": "mg",
    "fat_g": "g",
}


@dataclass
class Issue:
    field: str
    value: float
    threshold: float

    @property
    def text(self) -> str:
        return (
            f"{_LABELS[self.field]} {self.value:.0f}{_UNITS[self.field]} "
            f"超出单餐参考值 {self.threshold:.0f}{_UNITS[self.field]}"
        )


def assess(nutrition: Nutrition) -> List[Issue]:
    """返回超阈值项列表（空 = 相对健康）。"""
    issues: List[Issue] = []
    for key, threshold in THRESHOLDS.items():
        value = float(getattr(nutrition, key, 0.0) or 0.0)
        if value > threshold:
            issues.append(Issue(field=key, value=value, threshold=threshold))
    return issues


def combo_nutrition(codes: Sequence[str], by_code: Dict[str, Nutrition]) -> Nutrition:
    """把多份餐品营养相加（未登记的餐品跳过）。"""
    total = Nutrition()
    for code in codes:
        nutrition = by_code.get(code)
        if nutrition is not None:
            total = total + nutrition
    return total


def swap_suggestions(
    current: Nutrition,
    alternatives: Sequence[Nutrition],
    *,
    limit: int = 3,
) -> List[Nutrition]:
    """找同类中能量更低、且钠不升高的替代项。"""
    better = [
        n
        for n in alternatives
        if n.code != current.code
        and n.energy_kcal > 0
        and n.energy_kcal <= current.energy_kcal
        and n.sodium_mg <= current.sodium_mg
    ]
    better.sort(key=lambda n: n.energy_kcal)
    return better[:limit]


def summarize(label: str, nutrition: Nutrition) -> str:
    issues = assess(nutrition)
    head = (
        f"{label}：能量 {nutrition.energy_kcal:.0f} kcal | 蛋白质 {nutrition.protein_g:.1f} g | "
        f"脂肪 {nutrition.fat_g:.1f} g | 碳水 {nutrition.carb_g:.1f} g | "
        f"钠 {nutrition.sodium_mg:.0f} mg"
    )
    if not issues:
        return head + " ✅（未超单餐参考值）"
    return head + " ⚠️ " + "；".join(i.text for i in issues)
