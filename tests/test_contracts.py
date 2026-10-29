"""工具契约测试：模型只能通过这四个契约与系统交互。"""

from __future__ import annotations

from src.agent.loop import TOOL_SPECS
from src.tools.contracts import TOOL_NAMES


def test_tool_specs_expose_four_tools() -> None:
    """四个工具必须完整暴露给决策层（否则模型无从选择动作）。"""
    assert TOOL_NAMES == [
        "search_materials",
        "generate_script",
        "estimate_cost",
        "save_plan",
    ]
    assert [t["name"] for t in TOOL_SPECS] == TOOL_NAMES


def test_every_tool_spec_has_schema() -> None:
    for spec in TOOL_SPECS:
        assert spec.get("description"), spec["name"]
        schema = spec.get("input_schema") or {}
        assert schema.get("type") == "object", spec["name"]
        assert schema.get("required"), spec["name"]
        for prop in (schema.get("properties") or {}).values():
            assert prop.get("type"), spec["name"]
            assert prop.get("description"), spec["name"]


def test_no_publish_tool_in_contract() -> None:
    """契约里不允许出现发布/投放类工具。"""
    for spec in TOOL_SPECS:
        assert "publish" not in spec["name"]
        assert "deliver" not in spec["name"]
        assert "release" not in spec["name"]
