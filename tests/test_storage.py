"""方案库与步骤状态持久化单元测试。"""

from __future__ import annotations

from pathlib import Path

from src.storage.db import PlanStore, plan_key


def test_save_plan_idempotent(tmp_path: Path) -> None:
    store = PlanStore(tmp_path / "plans.sqlite3")
    record = {
        "topic": "校园开放日",
        "assets": ["A08"],
        "script": "s",
        "total_cost": 7,
        "total_duration_min": 135,
    }
    first = store.save_plan(record)
    second = store.save_plan(record)
    assert first["status"] == "saved"
    assert second["status"] == "duplicate"
    assert first["plan_id"] == second["plan_id"]
    assert store.count() == 1


def test_plan_key_is_order_insensitive() -> None:
    a = plan_key("主题", ["A05", "A06"], 12)
    b = plan_key("主题", ["A06", "A05"], 12)
    assert a == b


def test_steps_persist_and_resume(tmp_path: Path) -> None:
    store = PlanStore(tmp_path / "plans.sqlite3")
    run_key = "resume:demo"
    store.save_step(run_key, "search_materials", "succeeded", {"candidates": [{"asset_id": "A09"}]})
    store.save_step(run_key, "generate_script", "failed", {"error": True})

    steps = store.get_steps(run_key)
    assert len(steps) == 2
    search = store.get_step(run_key, "search_materials")
    assert search is not None
    assert search["status"] == "succeeded"
    assert search["result"]["candidates"][0]["asset_id"] == "A09"

    failed = store.get_step(run_key, "generate_script")
    assert failed is not None
    assert failed["status"] == "failed"


def test_run_meta_failure_counts(tmp_path: Path) -> None:
    store = PlanStore(tmp_path / "plans.sqlite3")
    store.save_meta("resume:A09", {"failure_counts": {"A09:generate_script": 2}})
    meta = store.get_meta("resume:A09")
    assert meta["failure_counts"]["A09:generate_script"] == 2
