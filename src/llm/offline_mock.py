"""离线决策引擎（Offline Mock Client）。

重要边界声明
------------
本模块**不是**预录日志，也不是固定分支脚本。它是一个**观测驱动的策略引擎**：
只读取「上一次工具返回的真实观测值」（estimate.over_budget / error /
status / candidates 等），据此选择下一步动作。当工具结果不同时
（估算超限 vs 在预算内、工具失败 vs 成功、检索有候选 vs 无候选），
给出的动作序列真实不同。

但它仍然**不是真实大模型**：它只能证明「流程与工具契约正确」，
**不能证明真实模型的决策质量**。该边界在 docs/03-evaluation.md 与
docs/04-ai-usage.md 中均已明示。

真实模型通道见 `openai_compat.OpenAICompatClient`，两者共用同一套
Action 协议与同一段系统提示词约束（见 agent/prompts.py）。

决策图（由观测驱动，不依赖步号）
--------------------------------
    (无观测) --search_materials--> 无候选? --是--> --search_materials(换关键词)--> 仍无 -> finish(incomplete)
                                                |否
                                                v
                                        --generate_script--> 失败? --是--> --generate_script(重试)
                                                |否                        |仍失败 -> 换素材(重规划)
                                                v
                                        --estimate_cost--> 超预算?
                                                          |是 -> 选替代组合 -> --estimate_cost(重估)
                                                          |否 -> 时限不满足? -> 换更省的组合
                                                          |否 -> --save_plan--> 成功/重复/失败
"""

from __future__ import annotations

import json
from typing import Any

from .base import Action


class OfflineMockClient:
    """确定性的观测驱动策略客户端。"""

    name = "offline-mock"

    def __init__(self) -> None:
        # 当前方案选用的素材
        self.assets: list[str] = []
        # 已成功的步骤结果（失败步骤续跑时复用）
        self.successful: dict[str, Any] = {}
        # 续跑时从持久化状态恢复的阶段
        self.resume_stage: str = ""
        self.resumed_candidates: list[dict[str, Any]] = []
        self.resumed_script: str = ""
        self.resumed_assets: list[str] = []

    # ------------------------------------------------------------------
    def decide(self, messages: list[dict], tool_specs: list[dict]) -> Action:
        state = self._extract_state(messages)
        last_tool = state.get("last_tool")
        obs = state.get("observation") or {}

        # ---- 续跑：从上次中断的阶段继续，复用已成功的步骤结果 ----
        stage = state.get("resume_stage") or getattr(self, "resume_stage", "")
        if stage and last_tool is None:
            return self._resume_from(state, stage)

        if last_tool is None:
            return self._search(state, "先检索候选素材，为方案选材。",
                                "state.step:1(无观测，从检索素材开始)")

        if last_tool == "search_materials":
            return self._after_search(state, obs)

        if last_tool == "generate_script":
            return self._after_generate(state, obs)

        if last_tool == "estimate_cost":
            return self._after_estimate(state, obs)

        if last_tool == "save_plan":
            return self._after_save(state, obs)

        return self._finish(state, "无可用动作，结束执行。")

    # ------------------------------------------------------------------
    def _resume_from(self, state: dict, stage: str) -> Action:
        """续跑决策：跳过已成功的步骤，从失败点继续。

        - stage=generate -> 重试脚本生成（复用已成功的检索结果）
        - stage=estimate -> 直接重新估算（复用已成功的脚本）
        - stage=save     -> 直接保存（复用已成功的估算）
        - stage=done     -> 已全部完成
        """
        if stage == "done":
            return self._finish(
                state,
                "续跑检查：所有步骤此前均已成功，无需重做。",
                decided_by="state.resume_stage:done",
                outcome="completed",
                plan_status=state.get("plan_status", "not_saved"),
                assets=state.get("assets") or [],
                total_cost=state.get("total_cost"),
                total_duration_min=state.get("total_duration_min"),
            )

        assets = list(getattr(self, "resumed_assets", []) or state.get("assets") or [])

        if stage == "generate":
            if not assets:
                assets = [c["asset_id"] for c in (getattr(self, "resumed_candidates", []) or [])][:2]
            self._select_assets(assets)
            return self._act(
                "generate_script",
                {"topic": state.get("topic"), "assets": assets},
                "续跑：复用上次已成功的素材检索结果，重试失败的脚本生成步骤。",
                decided_by="state.resume_stage:generate",
            )

        if stage == "estimate":
            self._select_assets(assets)
            return self._act(
                "estimate_cost",
                {
                    "assets": assets,
                    "budget": state.get("budget"),
                    "deadline": state.get("deadline"),
                },
                "续跑：复用上次已成功的脚本生成结果，重新执行成本估算。",
                decided_by="state.resume_stage:estimate",
            )

        if stage == "save":
            self._select_assets(assets)
            return self._act(
                "save_plan",
                {
                    "topic": state.get("topic"),
                    "assets": assets,
                    "script": getattr(self, "resumed_script", "") or state.get("script") or "",
                    "total_cost": state.get("total_cost"),
                    "total_duration_min": state.get("total_duration_min"),
                },
                "续跑：复用上次已成功的估算结果，执行方案保存。",
                decided_by="state.resume_stage:save",
            )

        return self._search(state, "续跑：无已成功步骤，从检索素材开始。",
                            "state.resume_stage:none")

    # ------------------------------------------------------------------
    def _extract_state(self, messages: list[dict]) -> dict[str, Any]:
        for msg in reversed(messages):
            if msg.get("role") == "user":
                try:
                    return json.loads(msg["content"])
                except (json.JSONDecodeError, TypeError):
                    continue
        return {}

    # ------------------------------------------------------------------
    def _act(self, tool: str, args: dict, rationale: str, decided_by: str = "") -> Action:
        return Action(action="tool", tool=tool, args=args, rationale=rationale, decided_by=decided_by)

    def _select_assets(self, assets: list[str]) -> list[str]:
        """记录选材结果，供后续步骤复用。

        选材是**模型的决策**，因此以决策层为准；状态里的旧值只在决策层
        尚未给出选择时作为兜底。
        """
        if assets:
            self.assets = list(assets)
        return self.assets

    def _finish(self, state: dict, rationale: str, decided_by: str = "", **extra: Any) -> Action:
        final = {
            "topic": state.get("topic"),
            "published": False,
            "plan_status": "not_saved",
            "reasoning": rationale,
        }
        final.update(extra)
        return Action(action="finish", rationale=rationale, final=final, decided_by=decided_by)

    # ------------------------------------------------------------------
    def _search(self, state: dict, rationale: str, decided_by: str) -> Action:
        return self._act(
            "search_materials",
            {"topic": state.get("topic"), "limit": 5},
            rationale,
            decided_by=decided_by,
        )

    # ------------------------------------------------------------------
    def _after_search(self, state: dict, obs: dict) -> Action:
        candidates = obs.get("candidates") or []
        attempts = int(state.get("search_attempts") or 0)

        if not candidates:
            # search_attempts 自增：1 = 首次未命中
            if attempts <= 1:
                # 换更宽的关键词重试一次
                return self._act(
                    "search_materials",
                    {"topic": state.get("topic"), "limit": 8},
                    "检索未命中候选素材，放宽关键词再检索一次。",
                    decided_by="observation.candidates:empty",
                )
            return self._finish(
                state,
                "两次检索均未找到与主题匹配的素材，无法构建方案。",
                decided_by="observation.candidates:empty+state.search_attempts:exhausted",
                outcome="incomplete",
                incomplete_reason="no_matching_material",
                assets=[],
                plan_status="not_saved",
            )

        # 选择候选：优先挑成本低、覆盖面广的组合
        picked = self._select_assets(self._pick_assets(candidates))
        return self._act(
            "generate_script",
            {"topic": state.get("topic"), "assets": picked},
            f"检索到 {len(candidates)} 个候选素材，选定 {picked} 作为方案资产，进入脚本生成。",
            decided_by="observation.candidates",
        )

    def _pick_assets(self, candidates: list[dict]) -> list[str]:
        """选材策略：优先低成本 + 类型多样。

        取相关度最高的 2 个，若都同类型则补一个不同类型的。
        刻意避免直接选最贵的，保证初始方案常会超预算（触发重规划）。
        """
        ordered = sorted(candidates, key=lambda c: (c.get("cost", 0), -c.get("relevance", 0)))
        picked: list[str] = []
        types: set[str] = set()
        for c in ordered:
            if len(picked) >= 2:
                break
            picked.append(c["asset_id"])
            types.add(c.get("type", ""))
        if len(picked) < 2:
            for c in ordered:
                if c["asset_id"] not in picked:
                    picked.append(c["asset_id"])
                    break
        return picked

    # ------------------------------------------------------------------
    def _after_generate(self, state: dict, obs: dict) -> Action:
        if obs.get("error"):
            # generate_attempts 在工具失败时自增：1 = 首次失败，2 = 重试后仍失败
            attempts = int(state.get("generate_attempts") or 0)
            tried = list(state.get("tried_assets") or [])

            # 第 1 次失败：先用相同参数重试一次，排除瞬时故障
            if attempts <= 1:
                return self._act(
                    "generate_script",
                    {"topic": state.get("topic"), "assets": self.assets or state.get("assets") or []},
                    "脚本生成工具报错，先用相同参数重试一次以排除瞬时故障。",
                    decided_by="observation.error+state.generate_attempts",
                )

            # 重试仍失败：改用其他候选素材重规划（保留已成功的检索结果）
            detail = obs.get("detail") or {}
            failed_aid = detail.get("failed_asset_id")
            candidates = state.get("candidates") or []

            # 优先策略：把失败的那个素材替换成另一个候选，其余保留
            if failed_aid:
                remaining = [c for c in candidates
                             if c["asset_id"] != failed_aid and c["asset_id"] not in tried]
                if remaining:
                    nxt = min(remaining, key=lambda c: c.get("cost", 0))
                    # 其余素材保留（去重），再加入替换进来的新素材
                    keep = [a for a in (self.assets or []) if a != failed_aid]
                    new_assets = list(dict.fromkeys(keep + [nxt["asset_id"]]))
                    self._select_assets(new_assets)
                    return self._act(
                        "generate_script",
                        {"topic": state.get("topic"), "assets": self.assets},
                        (
                            f"素材 {failed_aid} 的脚本生成重试后仍失败，"
                            f"保留已成功的检索结果与其它素材，改用 {nxt['asset_id']} 替换后重规划。"
                        ),
                        decided_by="observation.error.detail.failed_asset_id",
                    )

            # 回退策略：完全换一批未尝试过的候选
            remaining = [c for c in candidates
                         if c["asset_id"] not in tried and c["asset_id"] != failed_aid]
            if remaining:
                nxt = min(remaining, key=lambda c: c.get("cost", 0))
                self._select_assets([nxt["asset_id"]])
                return self._act(
                    "generate_script",
                    {"topic": state.get("topic"), "assets": self.assets},
                    (
                        f"脚本生成重试后仍失败，保留已成功的素材检索结果，"
                        f"改用素材 {self.assets[0]} 重规划脚本。"
                    ),
                    decided_by="observation.error+state.generate_attempts:exhausted",
                )

            return self._finish(
                state,
                "脚本生成在所有候选素材上均连续失败，停止执行并如实报告失败，未把失败步骤记为成功。",
                decided_by="observation.error+no_alternative",
                outcome="failed",
                incomplete_reason="script_generation_failed",
                assets=self.assets,
                plan_status="not_saved",
            )

        return self._act(
            "estimate_cost",
            {
                "assets": self.assets or state.get("assets") or [],
                "budget": state.get("budget"),
                "deadline": state.get("deadline"),
            },
            "脚本生成成功，进入成本估算，检查是否超出预算与截止时间。",
            decided_by="observation.script",
        )

    # ------------------------------------------------------------------
    def _after_estimate(self, state: dict, obs: dict) -> Action:
        over = bool(obs.get("over_budget"))
        alternatives = obs.get("alternatives") or []
        deadline_ok = bool(obs.get("deadline_ok", True))
        attempts = int(state.get("plan_attempts") or 0)

        # 超预算 -> 选替代组合重新估算
        if over:
            if alternatives and attempts < int(state.get("max_plan_attempts") or 3):
                alt = alternatives[0]
                self._select_assets(list(alt["assets"]))
                return self._act(
                    "estimate_cost",
                    {
                        "assets": self.assets,
                        "budget": state.get("budget"),
                        "deadline": state.get("deadline"),
                    },
                    (
                        f"成本估算观测到超出预算 {obs.get('over_budget_by')} 个单位"
                        f"（当前 {obs.get('total_cost')} > 预算 {state.get('budget')}）；"
                        f"改选服务端给出的替代组合 {self.assets}（估算 {alt['total_cost']}），重新估算。"
                    ),
                    decided_by="observation.over_budget",
                )
            return self._finish(
                state,
                (
                    f"成本超出预算且没有可行的替代组合（已尝试 {attempts} 次重规划），"
                    "返回未完成并说明缺口。"
                ),
                decided_by="observation.over_budget+no_alternative",
                outcome="incomplete",
                incomplete_reason="no_feasible_plan_within_budget",
                assets=self.assets,
                total_cost=obs.get("total_cost"),
                budget=state.get("budget"),
                gap={
                    "type": "budget",
                    "over_by": obs.get("over_budget_by"),
                    "required_budget": obs.get("total_cost"),
                    "given_budget": state.get("budget"),
                },
                plan_status="not_saved",
            )

        # 时限不满足 -> 换更省的组合（降耗时）
        if not deadline_ok:
            alternatives = [a for a in alternatives if a.get("total_duration_min", 0)] or alternatives
            if alternatives:
                alt = min(alternatives, key=lambda c: c.get("total_duration_min", 10**9))
                self._select_assets(list(alt["assets"]))
                return self._act(
                    "estimate_cost",
                    {
                        "assets": self.assets,
                        "budget": state.get("budget"),
                        "deadline": state.get("deadline"),
                    },
                    (
                        f"估算在预算内但时限不满足：{obs.get('deadline_reason')}；"
                        f"改选耗时更短的组合 {self.assets}（{alt['total_duration_min']} 分钟），重新估算。"
                    ),
                    decided_by="observation.deadline_ok:false",
                )
            return self._finish(
                state,
                f"方案耗时超出截止时间且无更短组合：{obs.get('deadline_reason')}",
                decided_by="observation.deadline_ok:false+no_alternative",
                outcome="incomplete",
                incomplete_reason="deadline_unreachable",
                assets=self.assets,
                total_cost=obs.get("total_cost"),
                budget=state.get("budget"),
                gap={"type": "deadline", "reason": obs.get("deadline_reason")},
                plan_status="not_saved",
            )

        # 预算内且时限可行 -> 保存
        return self._act(
            "save_plan",
            {
                "topic": state.get("topic"),
                "assets": self.assets or state.get("assets") or [],
                "script": state.get("script") or "",
                "total_cost": obs.get("total_cost"),
                "total_duration_min": obs.get("total_duration_min"),
            },
            (
                f"估算在预算内（{obs.get('total_cost')} <= 预算 {state.get('budget')}）"
                f"且时限可行，保存方案草稿。"
            ),
            decided_by="observation.over_budget:false+deadline_ok:true",
        )

    # ------------------------------------------------------------------
    def _after_save(self, state: dict, obs: dict) -> Action:
        status = obs.get("status")
        if status == "saved":
            return self._finish(
                state,
                "方案保存成功（仅保存草稿，未做任何真实提交）。",
                decided_by="observation.status:saved",
                outcome="completed",
                plan_status="saved",
                plan_id=obs.get("plan_id"),
                total_cost=obs.get("total_cost"),
                total_duration_min=obs.get("total_duration_min"),
                assets=obs.get("assets"),
                save_detail=obs.get("detail"),
            )
        if status == "duplicate":
            return self._finish(
                state,
                "方案保存返回 duplicate：相同方案已存在，未重复创建。",
                decided_by="observation.status:duplicate",
                outcome="completed",
                plan_status="duplicate",
                plan_id=obs.get("plan_id"),
                total_cost=obs.get("total_cost"),
                total_duration_min=obs.get("total_duration_min"),
                assets=obs.get("assets"),
                save_detail=obs.get("detail"),
            )

        # 保存失败 -> 重试一次，仍失败则如实报告
        # save_attempts 自增：1 = 首次失败，2 = 重试后仍失败
        attempts = int(state.get("save_attempts") or 0)
        if attempts <= 1:
            return self._act(
                "save_plan",
                {
                    "topic": state.get("topic"),
                    "assets": self.assets or state.get("assets") or [],
                    "script": state.get("script") or "",
                    "total_cost": state.get("total_cost"),
                    "total_duration_min": state.get("total_duration_min"),
                },
                "方案保存失败，先原参数重试一次以排除瞬时故障。",
                decided_by="observation.error+state.save_attempts",
            )
        return self._finish(
            state,
            "重试后保存仍失败，如实返回 failed，方案未保存。",
            decided_by="observation.error+state.save_attempts:exhausted",
            outcome="failed",
            plan_status="failed",
            assets=self.assets,
            gap={"type": "save_failure", "detail": obs.get("message")},
            save_detail=obs.get("message"),
        )
