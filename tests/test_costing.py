"""成本与时限模型单元测试。

参考实例：预算 10；初始方案估算 12，替代方案估算 8。
"""

from __future__ import annotations

from src.domain import costing


def test_estimate_reference_instance(catalog) -> None:
    """参考实例：A06+A05 = 12，A06 = 8。"""
    over = costing.estimate_cost(catalog, ["A06", "A05"], budget=10)
    assert over.total_cost == 12
    assert over.over_budget is True
    assert over.over_budget_by == 2

    ok = costing.estimate_cost(catalog, ["A06"], budget=10)
    assert ok.total_cost == 8
    assert ok.over_budget is False


def test_tool_cost_is_fixed(catalog) -> None:
    """工具固定开销始终为 3（检索 1 + 脚本 2 + 估算 0 + 保存 0）。"""
    est = costing.estimate_cost(catalog, ["A08"], budget=20)
    assert est.tool_cost == 3
    assert est.asset_cost == 4
    assert est.total_cost == 7


def test_find_alternatives_prefers_retained_value(catalog) -> None:
    """替代方案优先保住价值最高的原素材（内容投入不浪费）。"""
    alts = costing.find_alternatives(catalog, ["A05", "A06"], budget=10)
    assert alts
    # 首选应为 A06（价值 5，总费用 8），而非更便宜但价值更低的 A05
    assert alts[0]["assets"] == ["A06"]
    assert alts[0]["total_cost"] == 8


def test_find_alternatives_empty_when_budget_too_low(catalog) -> None:
    """预算过低时没有可行替代。"""
    alts = costing.find_alternatives(catalog, ["A08", "A07"], budget=2)
    assert alts == []


def test_fits_deadline_ok(catalog) -> None:
    est = costing.estimate_cost(catalog, ["A08"], budget=20)
    ok, reason = costing.fits_deadline(catalog, est, "2026-11-20", "2026-10-29T09:00:00")
    assert ok is True


def test_fits_deadline_too_tight(catalog) -> None:
    est = costing.estimate_cost(catalog, ["A01", "A06"], budget=50)
    ok, reason = costing.fits_deadline(catalog, est, "2026-10-29T10:00:00", "2026-10-29T09:00:00")
    assert ok is False
    assert "超过" in reason


def test_unknown_asset_is_noted(catalog) -> None:
    est = costing.estimate_cost(catalog, ["A99"], budget=20)
    assert any("未知素材" in n for n in est.notes)
    assert est.asset_cost == 0
