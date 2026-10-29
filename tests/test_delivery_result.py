"""交付结果字段测试：题目要求的 5 项交付内容都要出现在最终结果里。

题目原文：「交付结果：任务计划、步骤状态、工具结果、费用合计、已保存方案或未完成原因。」
"""

from __future__ import annotations

from src.runner import run_case


def test_delivery_result_contains_all_required_fields(config) -> None:
    """正常完成时：任务计划 / 步骤状态 / 工具结果 / 费用合计 / 已保存方案 五项齐全。"""
    final = run_case("normal_complete", config=config)["final"]

    # 任务计划（脚本正文）
    assert final.get("plan_script"), "缺少任务计划（脚本正文）"
    assert "内容活动方案" in final["plan_script"]

    # 步骤状态
    steps = final.get("steps") or []
    assert steps, "缺少步骤状态"
    assert all(s.get("status") in ("succeeded", "failed", "pending", "running")
               for s in steps)

    # 工具结果
    tool_results = final.get("tool_results") or []
    assert len(tool_results) == final["tool_calls"], "工具结果条数应等于工具调用次数"
    for t in tool_results:
        assert t.get("tool")
        assert t.get("args") is not None
        assert t.get("result_summary")
        assert t.get("status") in ("succeeded", "failed")

    # 费用合计
    cb = final.get("cost_breakdown") or {}
    assert cb.get("total_cost") == final["total_cost"]
    assert cb.get("tool_cost") == 3          # 工具固定开销
    assert cb.get("asset_cost") is not None
    assert cb.get("breakdown"), "缺少费用明细"
    # 费用合计 = 工具开销 + 素材费用
    assert cb["total_cost"] == cb["tool_cost"] + cb["asset_cost"]

    # 已保存方案
    assert final.get("plan_status") == "saved"
    assert final.get("plan_id")
    assert final.get("published") is False


def test_incomplete_result_contains_reason(config) -> None:
    """未完成时：给出未完成原因与缺口，而不是空结果。"""
    final = run_case("missing_budget", config=config)["final"]
    assert final["outcome"] == "incomplete"
    assert final.get("incomplete_reason")
    assert final.get("reasoning")
    gap = final.get("gap") or {}
    assert gap.get("missing"), "缺少缺口说明"
    assert final.get("plan_status") == "not_saved"
    assert final.get("plan_id") is None


def test_failed_step_appears_in_tool_results(config) -> None:
    """工具结果里失败的调用必须标记为 failed，并带错误信息。"""
    final = run_case("tool_failure", config=config)["final"]
    failed = [t for t in final["tool_results"] if t["status"] == "failed"]
    assert failed, "工具结果中应有失败项"
    for t in failed:
        assert t.get("error"), "失败项必须带错误信息"


def test_cost_breakdown_matches_reference_instance(config) -> None:
    """预算超限用例：最终费用在预算内，且构成可核对。"""
    final = run_case("budget_overrun", config=config)["final"]
    cb = final["cost_breakdown"]
    assert final["total_cost"] == 8          # 参考实例：替代方案 8
    assert cb["total_cost"] == 8
    assert cb["tool_cost"] == 3
    assert cb["asset_cost"] == 5
    assert cb["over_budget"] is False
    assert cb["budget"] == 10
