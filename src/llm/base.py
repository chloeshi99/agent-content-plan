"""决策层：模型客户端协议与 Action 定义。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol


@dataclass
class Action:
    """模型在一步中做出的决策。

    `decided_by` 标注本次决策依据的观测字段路径，写入 trace，
    用于证明「下一步动作由上一步的真实观测驱动」。
    """

    action: Literal["tool", "finish"] = "tool"
    tool: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""
    final: dict[str, Any] = field(default_factory=dict)
    decided_by: str = ""

    def to_json(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_json(payload: str | dict) -> "Action":
        if isinstance(payload, str):
            payload = json.loads(payload)
        action = payload.get("action", "tool")
        if action not in ("tool", "finish"):
            raise ValueError(f"非法 action: {action}")
        return Action(
            action=action,
            tool=payload.get("tool"),
            args=payload.get("args") or {},
            rationale=payload.get("rationale", ""),
            final=payload.get("final") or {},
            decided_by=payload.get("decided_by", ""),
        )


class LLMClient(Protocol):
    """模型客户端协议。"""

    name: str

    def decide(self, messages: list[dict], tool_specs: list[dict]) -> Action:  # pragma: no cover
        ...
