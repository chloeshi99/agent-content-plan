"""循环层测试：步数上限、未完成状态、越权保护、trace 完整性。

题目要求：「项目须设置执行步数或工具调用次数上限，达到上限时返回明确的未完成状态。」
本文件对这条要求给出可执行断言。
"""

from __future__ import annotations

import json
from pathlib import Path

from src.agent.loop import ReActAgent
from src.llm.base import Action
from src.storage.db import PlanStore
from src.tools.impl import ToolContext, ToolRegistry


class _LoopingClient:
    """永远返回工具调用的客户端，用于触发步数上限。"""

    name = "looping"

    def __init__(self) -> None:
        self.calls = 0

    def decide(self, messages, tool_specs) -> Action:
        self.calls += 1
        return Action(
            action="tool",
            tool="search_materials",
            args={"topic": "校园开放日"},
            rationale="故意继续调用以触发上限",
            decided_by="test.loop",
        )


class _EmptyToolClient:
    """返回空工具名的客户端。"""

    name = "empty-tool"

    def decide(self, messages, tool_specs) -> Action:
        return Action(action="tool", tool=None, rationale="故意给出空工具名")


def _build(tmp_path: Path, client, max_tool_calls: int = 3) -> ReActAgent:
    catalog = json.loads((Path(__file__).resolve().parents[1] / "data" / "catalog.json")
                         .read_text(encoding="utf-8"))
    store = PlanStore(tmp_path / "plans.sqlite3")
    ctx = ToolContext(catalog, store=store)
    return ReActAgent(
        client=client,
        registry=ToolRegistry(ctx),
        max_tool_calls=max_tool_calls,
        max_plan_attempts=3,
        store=store,
        run_key="test:loop",
    )


def test_tool_call_cap_returns_incomplete(tmp_path: Path) -> None:
    """达到工具调用上限时，必须返回明确的未完成状态。"""
    client = _LoopingClient()
    agent = _build(tmp_path, client, max_tool_calls=3)
    final = agent.run(topic="校园开放日", budget=20, deadline="2026-11-20")

    assert final["outcome"] == "incomplete"
    assert final["incomplete_reason"] == "max_tool_calls_exceeded"
    assert final["tool_calls"] == 3
    assert final["max_tool_calls"] == 3
    assert final["plan_status"] == "not_saved"
    assert final["published"] is False


def test_cap_is_not_exceeded(tmp_path: Path) -> None:
    """工具调用次数不得超过上限。"""
    client = _LoopingClient()
    agent = _build(tmp_path, client, max_tool_calls=2)
    final = agent.run(topic="校园开放日", budget=20, deadline="2026-11-20")
    assert final["tool_calls"] <= 2


def test_incomplete_state_explains_reason(tmp_path: Path) -> None:
    """未完成时必须说明原因，而不是静默结束。"""
    client = _LoopingClient()
    agent = _build(tmp_path, client, max_tool_calls=2)
    final = agent.run(topic="校园开放日", budget=20, deadline="2026-11-20")
    assert "未完成" in final["reasoning"]
    assert "max_tool_calls_exceeded" in final["incomplete_reason"]


def test_empty_tool_name_returns_incomplete(tmp_path: Path) -> None:
    """模型给出空工具名时不得崩溃，返回未完成。"""
    agent = _build(tmp_path, _EmptyToolClient())
    final = agent.run(topic="校园开放日", budget=20, deadline="2026-11-20")
    assert final["outcome"] == "incomplete"
    assert final["incomplete_reason"]


def test_published_always_false(tmp_path: Path) -> None:
    """任何路径下 published 都是 false。"""
    agent = _build(tmp_path, _LoopingClient(), max_tool_calls=2)
    final = agent.run(topic="校园开放日", budget=20, deadline="2026-11-20")
    assert final["published"] is False


def test_trace_entries_have_required_fields(tmp_path: Path) -> None:
    """trace 必须含题目要求的五个字段：步骤/工具参数/返回摘要/后续动作/决策依据。"""
    client = _LoopingClient()
    agent = _build(tmp_path, client, max_tool_calls=2)
    agent.run(topic="校园开放日", budget=20, deadline="2026-11-20")

    assert agent.trace
    for e in agent.trace:
        assert "step" in e
        assert "args" in e
        assert "result_summary" in e
        assert "next_action" in e
        assert "decided_by" in e


def test_trace_written_to_file(tmp_path: Path) -> None:
    """trace 可落盘为 jsonl。"""
    from src.agent.loop import write_trace

    path = tmp_path / "traces" / "t.jsonl"
    write_trace(path, {"step": 1, "action": "tool"})
    write_trace(path, {"step": 2, "action": "finish"})
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == 2
    assert lines[0]["step"] == 1
