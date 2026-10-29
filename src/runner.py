"""装配层：把配置、数据、工具、决策层组装成一次可执行的任务。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .agent.loop import ReActAgent, write_trace
from .config import Config
from .llm.offline_mock import OfflineMockClient
from .storage.db import PlanStore
from .tools.impl import ToolContext, ToolRegistry


class PlanRunner:
    """一次规划任务的执行器。"""

    def __init__(self, config: Config | None = None):
        self.config = config or Config.load()
        self.catalog = self.config.catalog
        self.store = PlanStore(self.config.db_path)
        self.client = self._make_client()
        self.registry: ToolRegistry | None = None

    # ------------------------------------------------------------------
    def _make_client(self) -> Any:
        if not self.config.offline and self.config.llm_api_key:
            from .llm.openai_compat import OpenAICompatClient
            return OpenAICompatClient(
                api_key=self.config.llm_api_key,
                base_url=self.config.llm_base_url,
                model=self.config.llm_model,
                temperature=self.config.llm_temperature,
            )
        return OfflineMockClient()

    # ------------------------------------------------------------------
    def run(
        self,
        topic: str | None = None,
        budget: int | None = None,
        deadline: str | None = None,
        run_key: str = "default",
        trace_path: Path | None = None,
        resume: bool = False,
    ) -> dict[str, Any]:
        """执行一次规划任务。

        缺预算或缺截止时间时直接返回明确的未完成，不进入工具循环。
        """
        # ---- 前置校验：缺预算/截止时间 ----
        missing: list[str] = []
        if topic is None or not str(topic).strip():
            missing.append("topic")
        if budget is None:
            missing.append("budget")
        if deadline is None or not str(deadline).strip():
            missing.append("deadline")
        if missing:
            return {
                "topic": topic,
                "outcome": "incomplete",
                "plan_status": "not_saved",
                "assets": [],
                "total_cost": None,
                "total_duration_min": None,
                "published": False,
                "reasoning": (
                    f"缺少必要输入（{', '.join(missing)}），无法开始规划。"
                    "预算与截止时间是硬性约束，不能用默认值替代。"
                ),
                "incomplete_reason": f"missing_input:{','.join(missing)}",
                "gap": {"type": "missing_input", "missing": missing},
                "tool_calls": 0,
                "max_tool_calls": self.config.max_tool_calls,
                "steps": [],
                "reused_steps": [],
            }

        # ---- 工具上下文 ----
        ctx = ToolContext(self.catalog, store=self.store, now=self.config.now)
        self.registry = ToolRegistry(ctx)

        # ---- 续跑：读取已保存的步骤与运行级元数据 ----
        prior_steps: dict[str, Any] = {}
        if resume:
            for s in self.store.get_steps(run_key):
                prior_steps[s["step_name"]] = s
            # 继承失败计数：否则瞬时故障会在续跑时再次失败，陷入无限重试
            meta = self.store.get_meta(run_key)
            ctx.load_failure_counts(meta.get("failure_counts") or {})

        writer = None
        if trace_path is not None:
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            writer = lambda entry: write_trace(trace_path, entry)  # noqa: E731

        agent = ReActAgent(
            client=self.client,
            registry=self.registry,
            max_tool_calls=self.config.max_tool_calls,
            max_plan_attempts=self.config.max_plan_attempts,
            trace_writer=writer,
            store=self.store,
            run_key=run_key,
        )
        result = agent.run(
            topic=topic,
            budget=budget,
            deadline=deadline,
            resume=resume,
            prior_steps=prior_steps,
        )
        # 持久化运行级元数据（失败计数），供续跑继承
        try:
            self.store.save_meta(run_key, {"failure_counts": ctx.failure_counts})
        except Exception:
            pass
        result["tool_calls"] = result.get("tool_calls", 0)
        result["run_key"] = run_key
        return result

    # ------------------------------------------------------------------
    def list_plans(self) -> list[dict]:
        return self.store.list_plans()

    def plan_count(self) -> int:
        return self.store.count()


def load_cases(config: Config | None = None) -> dict[str, Any]:
    config = config or Config.load()
    return config.load_json("cases.json")


def run_case(
    name: str,
    config: Config | None = None,
    runner: PlanRunner | None = None,
    trace_dir: Path | None = None,
) -> dict[str, Any]:
    """执行一个具名用例，支持 repeat（验证幂等）与 resume（验证续跑）。"""
    config = config or Config.load()
    cases = load_cases(config)["cases"]
    if name not in cases:
        raise KeyError(f"未知用例: {name}，可选: {', '.join(cases)}")
    case = cases[name]

    runner = runner or PlanRunner(config)
    repeat = int(case.get("repeat", 1))
    resume = bool(case.get("resume", False))
    run_key = case.get("run_key") or f"case:{name}"

    trace_path = None
    if trace_dir is not None:
        trace_path = Path(trace_dir) / f"{name}.jsonl"
        if trace_path.exists():
            trace_path.unlink()

    runs: list[dict[str, Any]] = []
    for i in range(repeat):
        runs.append(runner.run(
            topic=case.get("topic"),
            budget=case.get("budget"),
            deadline=case.get("deadline"),
            run_key=run_key,
            trace_path=trace_path,
            resume=resume and i > 0,
        ))

    return {
        "case": name,
        "expect": case.get("expect"),
        "topic": case.get("topic"),
        "runs": runs,
        "final": runs[-1],
        "plan_count": runner.plan_count(),
    }


def summarize(result: dict[str, Any]) -> str:
    return json.dumps({
        "outcome": result.get("outcome"),
        "plan_status": result.get("plan_status"),
        "plan_id": result.get("plan_id"),
        "assets": result.get("assets"),
        "total_cost": result.get("total_cost"),
        "duration_min": result.get("total_duration_min"),
        "tool_calls": result.get("tool_calls"),
        "published": result.get("published"),
    }, ensure_ascii=False)
