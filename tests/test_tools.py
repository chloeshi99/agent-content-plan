"""工具层单元测试：素材检索 / 脚本生成 / 成本估算 / 方案保存。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.storage.db import PlanStore
from src.tools.impl import ToolContext, ToolError, ToolRegistry


@pytest.fixture()
def registry(catalog, tmp_path: Path) -> ToolRegistry:
    store = PlanStore(tmp_path / "plans.sqlite3")
    ctx = ToolContext(catalog, store=store, now="2026-10-29T09:00:00")
    return ToolRegistry(ctx)


def test_search_materials_by_topic(registry: ToolRegistry) -> None:
    result = registry.call("search_materials", {"topic": "品牌推广"})
    ids = {c["asset_id"] for c in result["candidates"]}
    assert "A05" in ids and "A06" in ids


def test_search_materials_requires_topic(registry: ToolRegistry) -> None:
    with pytest.raises(ToolError, match="topic"):
        registry.call("search_materials", {"topic": ""})


def test_generate_script_success(registry: ToolRegistry) -> None:
    result = registry.call("generate_script", {"topic": "校园开放日", "assets": ["A08"]})
    assert "script" in result
    assert result["cached"] is False
    # 再次调用命中缓存
    again = registry.call("generate_script", {"topic": "校园开放日", "assets": ["A08"]})
    assert again["cached"] is True


def test_generate_script_missing_asset(registry: ToolRegistry) -> None:
    with pytest.raises(ToolError, match="不存在"):
        registry.call("generate_script", {"topic": "x", "assets": ["A99"]})


def test_generate_script_injected_permanent_failure(catalog, tmp_path: Path) -> None:
    """A04 永久故障：每次调用都必须失败，不能谎报成功。"""
    store = PlanStore(tmp_path / "plans.sqlite3")
    ctx = ToolContext(catalog, store=store)
    reg = ToolRegistry(ctx)
    with pytest.raises(ToolError, match="脚本生成失败"):
        reg.call("generate_script", {"topic": "行业深度报告", "assets": ["A04"]})
    with pytest.raises(ToolError, match="脚本生成失败"):
        reg.call("generate_script", {"topic": "行业深度报告", "assets": ["A04"]})


def test_generate_script_injected_transient_failure(catalog, tmp_path: Path) -> None:
    """A09 fail_times=2：前两次失败，第三次成功。"""
    store = PlanStore(tmp_path / "plans.sqlite3")
    ctx = ToolContext(catalog, store=store)
    reg = ToolRegistry(ctx)
    with pytest.raises(ToolError):
        reg.call("generate_script", {"topic": "社群快讯", "assets": ["A09"]})
    with pytest.raises(ToolError):
        reg.call("generate_script", {"topic": "社群快讯", "assets": ["A09"]})
    ok = reg.call("generate_script", {"topic": "社群快讯", "assets": ["A09"]})
    assert "script" in ok


def test_estimate_cost_ok(registry: ToolRegistry) -> None:
    result = registry.call(
        "estimate_cost",
        {"assets": ["A06"], "budget": 10, "deadline": "2026-11-20"},
    )
    assert result["total_cost"] == 8
    assert result["over_budget"] is False
    assert result["deadline_ok"] is True


def test_estimate_cost_over_budget_offers_alternatives(registry: ToolRegistry) -> None:
    """参考实例：A05+A06=12 超预算 10，返回含 A06(=8) 的替代方案。"""
    result = registry.call(
        "estimate_cost",
        {"assets": ["A05", "A06"], "budget": 10, "deadline": "2026-11-30"},
    )
    assert result["total_cost"] == 12
    assert result["over_budget"] is True
    assert result["alternatives"]
    assert any(a["assets"] == ["A06"] and a["total_cost"] == 8 for a in result["alternatives"])


def test_save_plan_success(registry: ToolRegistry) -> None:
    result = registry.call(
        "save_plan",
        {
            "topic": "校园开放日",
            "assets": ["A08"],
            "script": "hello",
            "total_cost": 7,
            "total_duration_min": 135,
        },
    )
    assert result["status"] == "saved"
    assert result["plan_id"]


def test_save_plan_duplicate(registry: ToolRegistry) -> None:
    args = {
        "topic": "校园开放日",
        "assets": ["A08"],
        "script": "hello",
        "total_cost": 7,
        "total_duration_min": 135,
    }
    first = registry.call("save_plan", args)
    second = registry.call("save_plan", args)
    assert first["status"] == "saved"
    assert second["status"] == "duplicate"
    assert first["plan_id"] == second["plan_id"]


def test_unknown_tool_raises(registry: ToolRegistry) -> None:
    with pytest.raises(ToolError, match="未知工具"):
        registry.call("publish", {})
