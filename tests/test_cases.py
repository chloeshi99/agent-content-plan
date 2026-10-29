"""用例级端到端测试：直接驱动 data/cases.json 中定义的每个场景。

这些测试是「业务契约」的可执行版本：cases.json 里的 expect 描述对应
assert_* 断言字段，行为一旦回退立即失败。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.runner import load_cases, run_case


def _case_names() -> list[str]:
    return list(load_cases()["cases"])


@pytest.mark.parametrize("name", _case_names())
def test_case_outcome(name: str, config) -> None:
    """每个用例的最终结果必须与 cases.json 中的断言一致。"""
    item = run_case(name, config=config)
    case = load_cases(config)["cases"][name]
    final = item["final"]

    assert final["outcome"] == case["assert_outcome"], final.get("reasoning")
    assert final["published"] is False
    if "assert_plan_status" in case:
        assert final["plan_status"] == case["assert_plan_status"]
    if "assert_incomplete_reason" in case:
        assert case["assert_incomplete_reason"] in (final.get("incomplete_reason") or "")


@pytest.mark.parametrize("name", _case_names())
def test_case_never_publishes(name: str, config) -> None:
    """任何路径都不得标记为已发布。"""
    item = run_case(name, config=config)
    for run in item["runs"]:
        assert run["published"] is False
        assert run.get("outcome") != "published"


def test_budget_overrun_replans(config) -> None:
    """预算超限 -> 改选替代方案 -> 重新估算 -> 保存。

    对应参考实例：预算 10；初始方案估算 12，替代方案估算 8。
    """
    item = run_case("budget_overrun", config=config)
    final = item["final"]
    assert final["outcome"] == "completed"
    assert final["plan_status"] == "saved"
    assert final["total_cost"] is not None
    assert final["total_cost"] <= 10
    assert final["total_cost"] == 8  # 参考实例：替代方案估算 8


def test_missing_budget_does_not_enter_loop(config) -> None:
    """缺预算 -> 明确返回未完成，且不进入工具循环。"""
    item = run_case("missing_budget", config=config)
    final = item["final"]
    assert final["outcome"] == "incomplete"
    assert final["tool_calls"] == 0
    assert "budget" in (final.get("gap") or {}).get("missing", [])


def test_missing_deadline_does_not_enter_loop(config) -> None:
    """缺截止时间 -> 明确返回未完成，且不进入工具循环。"""
    item = run_case("missing_deadline", config=config)
    final = item["final"]
    assert final["outcome"] == "incomplete"
    assert final["tool_calls"] == 0
    assert "deadline" in (final.get("gap") or {}).get("missing", [])


def test_tool_failure_replans_and_preserves_success(config) -> None:
    """工具失败 -> 改选替代素材重规划，且不得把失败步骤记为成功。"""
    item = run_case("tool_failure", config=config)
    final = item["final"]
    assert final["outcome"] == "completed"
    assert final["plan_status"] == "saved"
    # 步骤状态：至少有一个 failed 与多个 succeeded
    statuses = [s["status"] for s in final.get("steps") or []]
    assert "failed" in statuses, "必须有失败步骤被如实记录"
    assert "succeeded" in statuses, "必须保留成功步骤"
    # 失败步骤不得被标记为成功
    for s in final.get("steps") or []:
        if "generate_script" in s["step"] and s["status"] == "failed":
            break
    else:
        pytest.fail("脚本生成的失败步骤未被记录为 failed")


def test_no_feasible_plan_returns_gap(config) -> None:
    """预算过低且无可行方案 -> 返回未完成并给出预算缺口。"""
    item = run_case("no_feasible_plan", config=config)
    final = item["final"]
    assert final["outcome"] == "incomplete"
    assert final["plan_status"] == "not_saved"
    gap = final.get("gap") or {}
    assert gap.get("type") == "budget"


def test_duplicate_execution_creates_single_plan(config) -> None:
    """相同方案重复执行 -> 方案表只有一份，第二次返回 duplicate。"""
    item = run_case("duplicate_execution", config=config)
    assert item["plan_count"] == 1
    first, second = item["runs"]
    assert first["plan_status"] == "saved"
    assert second["plan_status"] == "duplicate"
    assert first["plan_id"] == second["plan_id"]


def test_resume_after_failure_reuses_successful_steps(config) -> None:
    """失败步骤续跑（加分项）：复用已成功的检索步骤，重试失败的脚本生成。"""
    item = run_case("resume_after_failure", config=config)
    assert len(item["runs"]) == 2
    first, second = item["runs"]

    # 第一次：脚本生成失败后如实报告失败（未把失败记为成功）
    assert first["outcome"] == "failed"
    first_statuses = [s["status"] for s in first.get("steps") or []]
    assert "failed" in first_statuses
    assert "succeeded" in first_statuses  # 检索成功被保留

    # 第二次：续跑复用成功步骤，最终完成
    assert second["outcome"] == "completed"
    assert second["plan_status"] == "saved"
    reused = second.get("reused_steps") or []
    assert reused, "续跑必须复用已成功的步骤"
    assert any("search_materials" in r for r in reused)


def test_budget_overrun_trace_shows_replan(config, tmp_path) -> None:
    """预算超限的运行记录必须体现「估算超限 -> 改选 -> 重估 -> 保存」。"""
    trace_dir = tmp_path / "traces"
    run_case("budget_overrun", config=config, trace_dir=trace_dir)
    path = trace_dir / "budget_overrun.jsonl"
    assert path.exists()
    entries = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    tools = [e.get("tool") for e in entries if e["action"] == "tool"]
    # 必须有两次 estimate_cost：一次超限，一次替代方案
    assert tools.count("estimate_cost") >= 2
    # 必须有 save_plan
    assert "save_plan" in tools
    # 决策依据必须包含 over_budget
    decided = [e.get("decided_by") for e in entries]
    assert any("over_budget" in (d or "") for d in decided)
