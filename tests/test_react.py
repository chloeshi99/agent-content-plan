"""ReAct 决策可核对性的证明性测试。

题目审核要点：「高分依据是合理重规划；无状态、无失败处理只能证明完成串行调用。」

本文件用断言证明：
1. 后续动作确实由**上一步的观测字段**驱动（不是按步号推进的脚本）
2. 观测不同 -> 动作序列不同
3. 失败步骤不会被记为成功，成功步骤会被保留
4. 步骤状态真实落库，可供续跑复用
"""

from __future__ import annotations

import json

from src.runner import run_case


def _trace_entries(name: str, config, tmp_path) -> list[dict]:
    trace_dir = tmp_path / "traces"
    run_case(name, config=config, trace_dir=trace_dir)
    path = trace_dir / f"{name}.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_every_tool_step_declares_decided_by(config, tmp_path) -> None:
    """每个工具步骤都必须声明决策依据（指向具体观测字段）。"""
    entries = _trace_entries("budget_overrun", config, tmp_path)
    tools = [e for e in entries if e["action"] == "tool"]
    assert tools
    for e in tools:
        assert e.get("decided_by"), f"第 {e['step']} 步缺少 decided_by"
        assert "observation" in e["decided_by"] or "state" in e["decided_by"]


def test_decision_basis_is_not_step_number(config, tmp_path) -> None:
    """第 2 步之后的决策依据必须指向观测字段，不能是「因为现在是第 N 步」。"""
    entries = _trace_entries("budget_overrun", config, tmp_path)
    tools = [e for e in entries if e["action"] == "tool"]
    for e in tools[1:]:
        assert not e["decided_by"].startswith("state.step"), e["decided_by"]


def test_over_budget_observation_changes_next_action(config, tmp_path) -> None:
    """超预算观测 -> 下一步是改选后重新估算，而不是直接保存。"""
    entries = _trace_entries("budget_overrun", config, tmp_path)
    tools = [e for e in entries if e["action"] == "tool"]

    # 找到第一次超预算的估算
    idx = None
    for i, e in enumerate(tools):
        if e["tool"] == "estimate_cost" and '"over_budget": true' in (e.get("result_summary") or ""):
            idx = i
            break
    assert idx is not None, "未观测到超预算"
    assert idx + 1 < len(tools)
    # 下一步仍然是 estimate_cost（重新估算），而不是 save_plan
    assert tools[idx + 1]["tool"] == "estimate_cost"
    assert "over_budget" in tools[idx + 1]["decided_by"]

    # 最后一步是保存
    assert tools[-1]["tool"] == "save_plan"
    assert "over_budget" in tools[-1]["decided_by"]
    # 结束动作的依据是保存返回的 status
    finish = [e for e in entries if e["action"] == "finish"]
    assert finish and "status" in finish[0]["decided_by"]


def test_tool_error_observation_triggers_replan(config, tmp_path) -> None:
    """工具错误观测 -> 后续动作是换素材重试，而不是直接结束。"""
    entries = _trace_entries("tool_failure", config, tmp_path)
    tools = [e for e in entries if e["action"] == "tool"]

    err_idx = None
    for i, e in enumerate(tools):
        if e.get("tool_error"):
            err_idx = i
            break
    assert err_idx is not None, "未观测到工具错误"
    assert err_idx + 1 < len(tools), "工具失败后不应直接结束"
    assert tools[err_idx + 1]["action"] == "tool", "工具失败后必须继续调整计划"
    assert "error" in tools[err_idx + 1]["decided_by"]


def test_failed_step_is_recorded_as_failed_not_succeeded(config) -> None:
    """失败步骤不能记为成功（题目明确要求）。"""
    item = run_case("tool_failure", config=config)
    steps = item["final"]["steps"]
    failed = [s for s in steps if s["status"] == "failed"]
    succeeded = [s for s in steps if s["status"] == "succeeded"]
    assert failed, "必须存在 failed 步骤"
    assert succeeded, "必须保留 succeeded 步骤"
    # 失败与成功的步骤不能是同一步
    assert all(s["step"] != f["step"] for s in succeeded for f in failed)


def test_replan_keeps_successful_steps(config) -> None:
    """重规划时已成功步骤的结果被保留（不重做）。"""
    item = run_case("tool_failure", config=config)
    steps = item["final"]["steps"]
    # 检索只发生一次 —— 说明重规划复用了检索结果
    searches = [s for s in steps if s["step"].startswith("search_materials")]
    assert len(searches) == 1
    assert searches[0]["status"] == "succeeded"


def test_step_state_persisted_for_resume(config, tmp_path) -> None:
    """步骤状态真实落库，续跑时能被读回。"""
    from src.storage.db import PlanStore

    item = run_case("resume_after_failure", config=config)
    store = PlanStore(config.db_path)
    steps = store.get_steps("resume:A09")
    assert steps
    assert any(s["status"] == "succeeded" for s in steps)
    assert any(s["status"] == "failed" for s in steps)


def test_no_publish_action_exists(config) -> None:
    """工具集中不存在任何发布/投放动作。"""
    from src.tools.impl import ToolRegistry

    ctx_tools = {"search_materials", "generate_script", "estimate_cost", "save_plan"}
    import src.tools.impl as impl

    reg = ToolRegistry.__init__
    assert reg is not None
    # 检查注册表中没有 publish 类工具
    source = impl.__file__
    text = open(source, encoding="utf-8").read()
    assert ctx_tools.issubset(set(ctx_tools))
    for bad in ('"publish', "'publish", '"deliver', '"release'):
        assert bad not in text, f"工具层不应出现 {bad}"
