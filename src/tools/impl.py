"""工具实现层。

职责边界：
- 只做事实获取与受控写入
- 失败时抛出 `ToolError`，携带明确原因（不静默降级、不伪造成功）
- 记录每个工具调用的次数，供状态恢复（续跑）使用

失败注入
--------
`failure_injection` 用于复现工具故障：
    "A04": {"generate_script": {"message": "...", "fail_times": 0}}
`fail_times=0` 表示永久失败；`fail_times=1` 表示第一次失败、重试即成功
（用于验证「失败步骤续跑」）。
"""

from __future__ import annotations

import json
from typing import Any

from ..domain import costing


class ToolError(Exception):
    """工具调用失败。必须如实暴露给模型与最终结果。"""

    def __init__(self, message: str, detail: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}

    def to_dict(self) -> dict[str, Any]:
        return {"error": True, "message": self.message, "detail": self.detail}


class ToolContext:
    """一次执行内所有工具共享的上下文。

    包含失败注入计数与已生成的脚本缓存（续跑时复用成功步骤结果）。
    """

    def __init__(self, catalog: dict[str, Any], store: Any = None, now: str = "2026-10-29T09:00:00"):
        self.catalog = catalog
        self.store = store
        self.now = now
        self.failure_injection = catalog.get("failure_injection") or {}
        # 记录每个 (asset_id, tool) 已失败次数
        self.failure_counts: dict[str, int] = {}
        # 生成成功的脚本缓存：key = (topic, assets) -> script
        self.script_cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    def load_failure_counts(self, counts: dict[str, int]) -> None:
        """从持久化状态恢复失败计数（续跑时使用）。

        瞬时故障（fail_times=N）在一次执行内失败 N 次后恢复；
        续跑时必须继承上次的计数，否则会无限重试同一个必然失败的步骤。
        """
        self.failure_counts.update(counts or {})

    # ------------------------------------------------------------------
    def materials(self) -> list[dict[str, Any]]:
        return self.catalog.get("materials", [])

    def material(self, asset_id: str) -> dict[str, Any] | None:
        for m in self.materials():
            if m["id"] == asset_id:
                return m
        return None

    def _injected_failure(self, asset_id: str | None, tool: str) -> str | None:
        """检查是否应注入故障，返回失败消息（None 表示不注入）。

        `fail_times` 语义：
        - **省略** 或 `null` -> **永久失败**（每次调用都失败）
        - `0` -> 永久失败（同上，便于显式书写）
        - `N > 0` -> 前 N 次调用失败，之后成功（用于验证瞬时故障与续跑）
        """
        if not asset_id:
            return None
        entry = self.failure_injection.get(asset_id) or {}
        spec = entry.get(tool)
        if not spec:
            return None
        if isinstance(spec, str):
            spec = {"message": spec}
        key = f"{asset_id}:{tool}"
        used = self.failure_counts.get(key, 0)
        if "fail_times" in spec and spec["fail_times"] is not None:
            remaining = int(spec["fail_times"]) - used
            if remaining <= 0:
                return None  # 失败次数已用完 -> 本次成功（瞬时故障已恢复）
        self.failure_counts[key] = used + 1
        return spec.get("message", f"{tool} 调用失败（注入故障）")


# ----------------------------------------------------------------------
class ToolRegistry:
    """四个工具的统一入口。"""

    def __init__(self, ctx: ToolContext):
        self.ctx = ctx
        self.calls: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        handler = {
            "search_materials": self.search_materials,
            "generate_script": self.generate_script,
            "estimate_cost": self.estimate_cost,
            "save_plan": self.save_plan,
        }.get(name)
        if handler is None:
            raise ToolError(f"未知工具: {name}", {"tool": name, "available": list(handler or {})})
        try:
            result = handler(args)
        except ToolError as exc:
            self.calls.append({"tool": name, "args": args, "ok": False, "error": exc.message})
            raise
        self.calls.append({"tool": name, "args": args, "ok": True})
        return result

    # ------------------------------------------------------------------
    # search_materials
    # ------------------------------------------------------------------
    def search_materials(self, args: dict[str, Any]) -> dict[str, Any]:
        topic = (args.get("topic") or "").strip()
        if not topic:
            raise ToolError("检索素材需要 topic 参数", {"required": ["topic"]})

        limit = int(args.get("limit") or 5)
        materials = self.ctx.materials()
        keywords = [w for w in topic.replace("，", " ").replace(",", " ").split() if w]

        scored: list[tuple[int, dict[str, Any]]] = []
        for m in materials:
            haystack = " ".join([
                m.get("title", ""),
                m.get("summary", ""),
                " ".join(m.get("tags", [])),
                " ".join(m.get("keywords", [])),
            ])
            score = 0
            for w in keywords:
                if w in haystack:
                    score += 2
            for kw in m.get("keywords", []):
                if kw in topic:
                    score += 3
            for tag in m.get("tags", []):
                if tag in topic:
                    score += 1
            if score > 0:
                scored.append((score, m))

        scored.sort(key=lambda x: (-x[0], x[1].get("cost", 0)))
        hits = scored[:limit]
        candidates = [
            {
                "asset_id": m["id"],
                "title": m["title"],
                "type": m["type"],
                "tags": m.get("tags", []),
                "summary": m.get("summary", ""),
                "cost": m.get("cost"),
                "duration_min": m.get("duration_min"),
                "relevance": score,
            }
            for score, m in hits
        ]
        return {
            "topic": topic,
            "candidates": candidates,
            "candidate_count": len(candidates),
            "total_materials": len(materials),
            "note": "按相关度排序返回候选素材，费用由服务端给出" if candidates
                    else f"未检索到与主题「{topic}」相关的素材",
        }

    # ------------------------------------------------------------------
    # generate_script
    # ------------------------------------------------------------------
    def generate_script(self, args: dict[str, Any]) -> dict[str, Any]:
        topic = (args.get("topic") or "").strip()
        assets = list(args.get("assets") or [])
        if not topic:
            raise ToolError("生成脚本需要 topic 参数", {"required": ["topic"]})
        if not assets:
            raise ToolError("生成脚本需要至少一个素材", {"required": ["assets"]})

        for aid in assets:
            if self.ctx.material(aid) is None:
                raise ToolError(f"素材不存在: {aid}", {"asset_id": aid})

        # 故障注入：按素材逐个检查
        for aid in assets:
            injected = self.ctx._injected_failure(aid, "generate_script")
            if injected:
                raise ToolError(
                    f"脚本生成失败（素材 {aid}）: {injected}",
                    {
                        "asset_id": aid,
                        "failed_asset_id": aid,
                        "injected": True,
                        "retryable": True,
                        "other_assets_ok": [a for a in assets if a != aid],
                    },
                )

        cache_key = json.dumps([topic, sorted(assets)], ensure_ascii=False)
        if cache_key in self.ctx.script_cache:
            return {
                "topic": topic,
                "assets": assets,
                "script": self.ctx.script_cache[cache_key],
                "cached": True,
                "sections": self._sections(topic, assets),
            }

        script = self._render_script(topic, assets)
        self.ctx.script_cache[cache_key] = script
        return {
            "topic": topic,
            "assets": assets,
            "script": script,
            "cached": False,
            "sections": self._sections(topic, assets),
        }

    def _sections(self, topic: str, assets: list[str]) -> list[dict[str, Any]]:
        out = []
        for aid in assets:
            m = self.ctx.material(aid) or {}
            out.append({
                "asset_id": aid,
                "title": m.get("title", aid),
                "type": m.get("type", ""),
                "summary": m.get("summary", ""),
            })
        return out

    def _render_script(self, topic: str, assets: list[str]) -> str:
        titles = []
        for aid in assets:
            m = self.ctx.material(aid) or {}
            titles.append(f"{m.get('title', aid)}（{aid}）")
        lines = [
            f"# {topic} 内容活动方案",
            "",
            "## 一、目标",
            f"围绕「{topic}」完成一次可投放的内容活动，覆盖多形态素材组合。",
            "",
            "## 二、选用的内容资产",
        ]
        for aid in assets:
            m = self.ctx.material(aid) or {}
            lines.append(f"- **{m.get('title', aid)}**（{aid} / {m.get('type', '')}）：{m.get('summary', '')}")
        lines += [
            "",
            "## 三、投放节奏",
            "1. 预热期：以短视频脚本切入，建立话题",
            "2. 引爆期：主视觉与图文承接搜索与推荐流量",
            "3. 长尾期：长文沉淀内容资产，承接深度阅读",
            "",
            "## 四、组合说明",
            f"本方案共选用 {len(assets)} 项内容资产：" + "、".join(titles) + "。",
            "各资产承担不同角色，形成从吸引到转化的完整链路。",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # estimate_cost
    # ------------------------------------------------------------------
    def estimate_cost(self, args: dict[str, Any]) -> dict[str, Any]:
        assets = list(args.get("assets") or [])
        if not assets:
            raise ToolError("成本估算需要至少一个素材", {"required": ["assets"]})
        budget = args.get("budget")
        if budget is not None:
            try:
                budget = int(budget)
            except (TypeError, ValueError):
                raise ToolError(f"预算必须是整数: {budget!r}", {"budget": budget}) from None

        deadline = args.get("deadline")
        est = costing.estimate_cost(self.ctx.catalog, assets, budget)
        result = est.to_dict()

        # 截止时间可行性（确定性计算，不让模型算时间）
        ok, reason = costing.fits_deadline(self.ctx.catalog, est, deadline, self.ctx.now)
        result["deadline_ok"] = ok
        result["deadline_reason"] = reason
        result["deadline"] = deadline
        result["now"] = self.ctx.now

        # 超预算时给出可行的替代组合（确定性计算），模型据此重规划
        if est.over_budget:
            alts = costing.find_alternatives(self.ctx.catalog, assets, budget)
            result["alternatives"] = alts
            result["alternatives_hint"] = (
                f"超出预算 {est.over_budget_by} 个单位。服务端已给出 {len(alts)} 个可行的替代组合，"
                "请选择其一重新估算。"
                if alts else
                f"超出预算 {est.over_budget_by} 个单位，且没有可行的替代组合。"
            )
        else:
            result["alternatives"] = []
            result["alternatives_hint"] = "在预算内" + ("" if ok else f"，但时限不满足：{reason}")
        return result

    # ------------------------------------------------------------------
    # save_plan
    # ------------------------------------------------------------------
    def save_plan(self, args: dict[str, Any]) -> dict[str, Any]:
        topic = (args.get("topic") or "").strip()
        assets = list(args.get("assets") or [])
        if not topic:
            raise ToolError("保存方案需要 topic 参数", {"required": ["topic"]})
        if not assets:
            raise ToolError("保存方案需要至少一个素材", {"required": ["assets"]})
        for aid in assets:
            if self.ctx.material(aid) is None:
                raise ToolError(f"素材不存在: {aid}", {"asset_id": aid})
        if args.get("total_cost") is None:
            raise ToolError("保存方案需要 total_cost 参数", {"required": ["total_cost"]})

        # 故障注入
        injected = self.ctx._injected_failure(None, "save_plan")
        if injected:
            raise ToolError(f"方案保存失败: {injected}", {"injected": True})

        if self.ctx.store is None:
            raise ToolError("方案保存失败: 存储未初始化", {"injected": False})

        record = {
            "topic": topic,
            "assets": assets,
            "script": args.get("script", ""),
            "total_cost": int(args["total_cost"]),
            "total_duration_min": int(args.get("total_duration_min") or 0),
        }
        saved = self.ctx.store.save_plan(record)
        return {
            "status": saved["status"],
            "plan_id": saved["plan_id"],
            "detail": saved["detail"],
            "topic": topic,
            "assets": assets,
            "total_cost": record["total_cost"],
            "total_duration_min": record["total_duration_min"],
        }
