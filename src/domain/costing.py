"""内容活动方案的成本与时间估算模型。

**所有算术都在这里确定性完成**，模型不参与任何费用计算。
这样预算判定可单测、可回归，模型幻觉不会导致超预算方案被放行。

费用模型
--------
一个完整方案的固定工具开销为：

    素材检索(1) + 脚本生成(2) + 成本估算(0) + 方案保存(0) = 3 个模拟单位

方案总费用 = 工具固定开销(3) + Σ 素材费用
方案总耗时 = 工具固定耗时(55 分钟) + Σ 素材耗时

例如（对应题目参考实例）：
    初始方案 [A06, A05]  -> 3 + (5 + 4) = 12  -> 超出预算 10
    替代方案 [A06]       -> 3 + (5)      = 8   -> 满足预算
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

TOOL_SET_COST_TOTAL = 3
TOOL_SET_DURATION_TOTAL = 55  # 10 + 30 + 5 + 10


@dataclass
class Estimate:
    """一次成本估算的结果。"""

    assets: list[str]
    tool_cost: int
    asset_cost: int
    total_cost: int
    total_duration_min: int
    budget: int | None = None
    over_budget: bool = False
    over_budget_by: int = 0
    breakdown: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "assets": self.assets,
            "tool_cost": self.tool_cost,
            "asset_cost": self.asset_cost,
            "total_cost": self.total_cost,
            "total_duration_min": self.total_duration_min,
            "budget": self.budget,
            "over_budget": self.over_budget,
            "over_budget_by": self.over_budget_by,
            "breakdown": self.breakdown,
            "notes": self.notes,
        }


def estimate_cost(
    catalog: dict[str, Any],
    assets: list[str],
    budget: int | None = None,
) -> Estimate:
    """按确定性规则计算方案费用与耗时。

    未知素材 id 会被跳过并记入 notes（不静默忽略）。
    """
    asset_costs = catalog.get("asset_costs") or {
        m["id"]: m["cost"] for m in catalog.get("materials", [])
    }
    durations = catalog.get("duration_minutes") or {
        m["id"]: m["duration_min"] for m in catalog.get("materials", [])
    }
    tool_costs = catalog.get("tool_costs") or {}
    tool_durations = catalog.get("tool_durations") or {}

    tool_cost = sum(tool_costs.get(t, 0) for t in catalog.get("plan_tool_set", []))
    tool_duration = sum(tool_durations.get(t, 0) for t in catalog.get("plan_tool_set", []))

    breakdown: list[dict[str, Any]] = []
    notes: list[str] = []
    asset_cost = 0
    asset_duration = 0

    for aid in assets:
        if aid not in asset_costs:
            notes.append(f"未知素材 {aid}，已跳过")
            continue
        cost = int(asset_costs[aid])
        dur = int(durations.get(aid, 0))
        asset_cost += cost
        asset_duration += dur
        breakdown.append({
            "asset_id": aid,
            "cost": cost,
            "duration_min": dur,
            "title": _title_of(catalog, aid),
        })

    total_cost = tool_cost + asset_cost
    total_duration = tool_duration + asset_duration

    over_budget = False
    over_by = 0
    if budget is not None:
        over_by = total_cost - budget
        over_budget = over_by > 0

    return Estimate(
        assets=list(assets),
        tool_cost=tool_cost,
        asset_cost=asset_cost,
        total_cost=total_cost,
        total_duration_min=total_duration,
        budget=budget,
        over_budget=over_budget,
        over_budget_by=max(0, over_by),
        breakdown=breakdown,
        notes=notes,
    )


def _title_of(catalog: dict[str, Any], asset_id: str) -> str:
    for m in catalog.get("materials", []):
        if m["id"] == asset_id:
            return m.get("title", asset_id)
    return asset_id


def find_alternatives(
    catalog: dict[str, Any],
    current: list[str],
    budget: int | None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """为超预算的当前方案寻找替代组合。

    策略（确定性，按优先级）：
    1. 逐个移除素材（保留最有价值的部分），从大到小试
    2. 用更便宜的素材替换最贵的素材
    3. 若素材为空仍超预算 -> 无可行方案

    返回按总费用升序排列的候选列表，每个候选含估算结果。
    """
    if budget is None:
        return []

    current_assets = [a for a in current]
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()

    def _add(assets: list[str]) -> None:
        key = tuple(sorted(assets))
        if key in seen:
            return
        seen.add(key)
        est = estimate_cost(catalog, assets, budget)
        if not est.over_budget:
            current_cost = estimate_cost(catalog, current, budget).total_cost if current else 0
            asset_costs_local = catalog.get("asset_costs") or {
                m["id"]: m["cost"] for m in catalog.get("materials", [])
            }
            retained_value = sum(asset_costs_local.get(a, 0) for a in set(assets) & set(current))
            candidates.append({
                "assets": assets,
                "total_cost": est.total_cost,
                "total_duration_min": est.total_duration_min,
                # 保留了多少原方案素材
                "retained": len(set(assets) & set(current)),
                # 保留素材的价值合计：用于优先保住内容投入最高的资产
                "retained_value": retained_value,
                "saved": (current_cost - est.total_cost) if current else est.total_cost,
            })

    # 1. 逐步移除
    for i in range(len(current_assets)):
        candidate = current_assets[:i] + current_assets[i + 1:]
        _add(candidate)

    # 2. 用更便宜的素材替换最贵的
    asset_costs = catalog.get("asset_costs") or {
        m["id"]: m["cost"] for m in catalog.get("materials", [])
    }
    all_assets = list(asset_costs.keys())
    if current_assets:
        most_expensive = max(current_assets, key=lambda a: asset_costs.get(a, 0))
        base_cost = asset_costs.get(most_expensive, 0)
        for alt in all_assets:
            if alt in current_assets:
                continue
            if asset_costs.get(alt, 0) < base_cost:
                _add([alt if a == most_expensive else a for a in current_assets])

    # 3. 单一素材方案
    if len(current_assets) > 1:
        for aid in all_assets:
            _add([aid])

    candidates.sort(key=lambda c: (
        -c["retained_value"],  # 优先保住价值最高的原素材（内容投入不浪费）
        -c["retained"],        # 其次保留更多素材
        c["total_cost"],       # 其次费用更低
        -c["total_duration_min"],
    ))
    return candidates[:limit]


def fits_deadline(
    catalog: dict[str, Any],
    estimate: Estimate,
    deadline: str | None,
    now: str,
) -> tuple[bool, str]:
    """判断方案耗时是否能在截止时间前完成。

    返回 (是否可行, 说明)。截止时间格式 YYYY-MM-DD 或 YYYY-MM-DDTHH:MM。
    """
    if not deadline:
        return True, "未提供截止时间，不做时限判定"

    from datetime import datetime

    def _parse(value: str) -> datetime:
        value = value.strip()
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        raise ValueError(f"无法解析时间: {value}")

    try:
        now_dt = _parse(now)
        deadline_dt = _parse(deadline)
    except ValueError as exc:
        return False, str(exc)

    if deadline_dt <= now_dt:
        return False, f"截止时间 {deadline} 已早于当前时间 {now}"

    available_min = int((deadline_dt - now_dt).total_seconds() // 60)
    if estimate.total_duration_min > available_min:
        return False, (
            f"方案耗时 {estimate.total_duration_min} 分钟，超过截止时间前可用 "
            f"{available_min} 分钟"
        )
    return True, f"方案耗时 {estimate.total_duration_min} 分钟，可在截止时间前完成（可用 {available_min} 分钟）"
