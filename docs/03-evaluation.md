# 03 评测报告（测试预期、实际结果、失败与限制）

> 本文档中所有「实际结果」均由 `python -m scripts.gen_outputs` 与
> `python -m pytest tests -q` 生成，与仓库代码保持同步。
> 对应文件：[examples/outputs/summary.md](../examples/outputs/summary.md)、
> [examples/outputs/*.json](../examples/outputs)、
> [examples/traces/*.jsonl](../examples/traces)。

## 1. 评测目标

本项目要验证的不是「模型能不能写出一份方案」，而是**流程与工具契约在各种边界条件下是否正确**。
具体回答六个问题：

1. 预算充足时，能否走完 选材 → 脚本 → 估算 → 保存 并保存成功？
2. 缺预算或缺截止时间时，会不会有**臆测默认值**继续往下跑？
3. 估算超预算时，会不会**硬上超预算方案**，还是改选替代方案？
4. 工具失败时，会不会**把失败步骤记为成功**？
5. 重复执行时，会不会**产生多份方案**？
6. 模型的**下一步动作**是否真的由上一步的**工具观测结果**驱动？

第 6 点对应题目对 ReAct 的硬性要求，单独由 `tests/test_react.py` 覆盖（见第 5 节）。

## 2. 测试执行结果

```
$ python -m pytest tests -q
........................................................                 [100%]
```

**59 个测试全部通过，0 失败。**

| 测试文件 | 数量 | 覆盖内容 |
|---|---|---|
| `tests/test_costing.py` | 7 | 费用模型、参考实例 12→8、替代组合排序、时限判定 |
| `tests/test_tools.py` | 11 | 工具契约、错误暴露、故障注入（永久/瞬时）、幂等保存 |
| `tests/test_storage.py` | 4 | 方案幂等、步骤状态持久化、运行级元数据 |
| `tests/test_contracts.py` | 3 | 四个工具契约完整、无发布类工具 |
| `tests/test_cases.py` | 24 | 端到端用例断言（对应必须验收的 5 项 + 加分项） |
| `tests/test_react.py` | 8 | **观测驱动决策证明**、失败不记成功、成功结果保留 |
| `tests/test_cli.py` | 2 | CLI 入口冒烟 |

## 3. 必须验收的五项 —— 预期 vs 实际

数据来源：[examples/outputs/summary.md](../examples/outputs/summary.md)（自动生成）。

| # | 验收点 | 用例 | 预期 | **实际结果** | 判定 |
|---|---|---|---|---|---|
| 1 | **正常完成** | `normal_complete` | 选材→脚本→估算→保存全成功 | `outcome=completed`，`plan_status=saved`，`assets=[A08]`，`total_cost=7`，`published=false` | ✅ |
| 2 | **缺预算** | `missing_budget` | 明确未完成并说明缺口 | `outcome=incomplete`，`tool_calls=0`，`gap.missing=["budget"]`，方案数 0 | ✅ |
| 3 | **缺截止时间** | `missing_deadline` | 明确未完成并说明缺口 | `outcome=incomplete`，`tool_calls=0`，`gap.missing=["deadline"]`，方案数 0 | ✅ |
| 4 | **工具失败** | `tool_failure` | 失败记 `failed`，改选素材重规划，保留成功步骤 | `outcome=completed`，步骤含 2 个 `failed` + 4 个 `succeeded`，检索只发生 1 次（结果被复用） | ✅ |
| 5 | **预算超限** | `budget_overrun` | 改选替代方案 → 重估 → 保存 | 首次估算 `total_cost=12`（超 10），改选 `[A06]` 后 `total_cost=8`，`plan_status=saved` | ✅ |
| 6 | **重复执行** | `duplicate_execution` | 第二次 `duplicate`，方案表一份 | 第 1 次 `saved`，第 2 次 `duplicate`，方案数 **1** | ✅ |

加分项：**失败步骤续跑** —— `resume_after_failure`：首次 `outcome=failed` 且如实记录失败步骤；
续跑时 `reused_steps=["search_materials:1"]`，最终 `outcome=completed`、`plan_status=saved`。

补充场景：**无可行方案** —— `no_feasible_plan`（预算 2）：
`outcome=incomplete`，`gap={"type":"budget","over_by":9,"required_budget":11,"given_budget":2}`，方案数 0。

全部用例的 `published` 均为 `false` —— **没有任何一个用例声称已发布**。

### 3.1 参考实例逐条对照

> 「预算 10 个模拟单位；初始方案估算 12，替代方案估算 8。模型观察到超预算后改选 8 的方案，
> 再估算和保存。若没有可行方案，返回未完成及缺口；不能把失败步骤记为成功。」

| 参考实例要求 | 实际输出 | 验证位置 |
|---|---|---|
| 预算 10 | `budget_overrun` 用例 `budget=10` | `examples/inputs/cases.json` |
| 初始方案估算 12 | `[A05,A06]` → `tool_cost=3, asset_cost=9, total_cost=12` | `examples/traces/budget_overrun.jsonl` 第 3 步 |
| 替代方案估算 8 | `[A06]` → `total_cost=8` | 同上第 4 步 |
| 观察到超预算后改选 | 第 4 步 `decided_by="observation.over_budget"` | 同上 |
| 再估算和保存 | 第 4 步 `estimate_cost`、第 5 步 `save_plan` | 同上 |
| 无可行方案返回未完成及缺口 | `gap.type=budget`，含 `over_by` / `required_budget` / `given_budget` | `examples/outputs/no_feasible_plan.json` |
| 失败步骤不能记为成功 | 步骤状态 `failed`/`succeeded` 分明 | `examples/outputs/tool_failure.json` 的 `runs[].steps` |

### 3.2 预算计算的核对方式

费用模型的每一步都可独立核对（`src/domain/costing.py`）：

```
方案总费用 = 工具固定开销(3) + Σ 素材费用
工具固定开销 = 检索(1) + 脚本生成(2) + 估算(0) + 保存(0) = 3
```

| 组合 | 工具 | 素材 | 总费用 | 判定（预算 10） |
|---|---|---|---|---|
| `[A05, A06]` | 3 | 4+5=9 | 12 | ❌ 超 2 |
| `[A06]` | 3 | 5 | 8 | ✅ |
| `[A05]` | 3 | 4 | 7 | ✅ |
| `[A09]` | 3 | 2 | 5 | ✅ |

`tests/test_costing.py::test_estimate_reference_instance` 固化了 12 与 8 两个数字。

## 4. 多组输入 / 实际输出 / 运行记录

题目要求「至少提交 3 组输入、实际输出及运行记录，覆盖正常、信息或资料不足、工具失败场景」。
本项目提交 **8 组**，场景全覆盖。

| 场景类别 | 用例 | 实际输出 | 运行记录 |
|---|---|---|---|
| **正常** | `normal_complete` | `examples/outputs/normal_complete.json` | `examples/traces/normal_complete.jsonl` |
| **预算超限** | `budget_overrun` | `examples/outputs/budget_overrun.jsonl` → `.json` | `examples/traces/budget_overrun.jsonl` |
| **信息不足（缺预算）** | `missing_budget` | `examples/outputs/missing_budget.json` | `examples/traces/missing_budget.jsonl` |
| **信息不足（缺截止时间）** | `missing_deadline` | `examples/outputs/missing_deadline.json` | `examples/traces/missing_deadline.jsonl` |
| **工具失败** | `tool_failure` | `examples/outputs/tool_failure.json` | `examples/traces/tool_failure.jsonl` |
| **无可行方案** | `no_feasible_plan` | `examples/outputs/no_feasible_plan.json` | `examples/traces/no_feasible_plan.jsonl` |
| **重复执行** | `duplicate_execution` | `examples/outputs/duplicate_execution.json` | `examples/traces/duplicate_execution.jsonl` |
| **失败续跑（加分）** | `resume_after_failure` | `examples/outputs/resume_after_failure.json` | `examples/traces/resume_after_failure.jsonl` |

输入统一在 [examples/inputs/cases.json](../examples/inputs/cases.json)（与 `data/cases.json` 同步）。

### 4.1 运行记录格式

每条 trace 记录含题目要求的字段：

```jsonc
{
  "step": 3,                    // 步骤
  "action": "tool",
  "tool": "estimate_cost",
  "args": {"assets": ["A05","A06"], "budget": 10, "deadline": "2026-11-30"},  // 工具参数
  "result_summary": "{\"total_cost\": 12, \"over_budget\": true, ...}",       // 返回摘要
  "next_action": "由模型根据观测决定下一步",
  "rationale": "...",            // 模型给的理由
  "decided_by": "observation.over_budget",   // 本步决策依据的观测字段
  "tool_error": null             // 失败原因（成功时为 null）
}
```

可读查看：`python -m scripts.show_trace examples/traces/budget_overrun.jsonl`。

## 5. ReAct 决策要求的验证

题目原文：「高分依据是合理重规划；无状态、无失败处理只能证明完成串行调用。」

`tests/test_react.py` 用断言而非叙述来证明：

| 测试 | 证明什么 |
|---|---|
| `test_every_tool_step_declares_decided_by` | 每一步都声明了决策依据 |
| `test_decision_basis_is_not_step_number` | 第 2 步之后的依据不是「因为是第 N 步」 |
| `test_over_budget_observation_changes_next_action` | 超预算观测 → 下一步是重新估算而非保存 |
| `test_tool_error_observation_triggers_replan` | 工具错误观测 → 继续调整计划而非结束 |
| `test_failed_step_is_recorded_as_failed_not_succeeded` | 失败步骤不记为成功 |
| `test_replan_keeps_successful_steps` | 重规划时检索只发生一次（成功结果被保留） |
| `test_step_state_persisted_for_resume` | 步骤状态真实落库，可被续跑读回 |
| `test_no_publish_action_exists` | 工具层不存在发布/投放动作 |

`budget_overrun` 的完整动作序列（来自 trace）：

```
1 search_materials   decided_by = state.step:1(无观测，从检索素材开始)
2 generate_script    decided_by = observation.candidates
3 estimate_cost      decided_by = observation.script            -> total_cost=12, over_budget=true
4 estimate_cost      decided_by = observation.over_budget       -> 改选 [A06]，total_cost=8
5 save_plan          decided_by = observation.over_budget:false+deadline_ok:true
6 finish             decided_by = observation.status:saved
```

第 4 步的依据是第 3 步的**真实观测字段** `over_budget`，而不是「第 4 步该做什么」。

## 6. 失败注入与续跑的证据

`data/catalog.json` 的 `failure_injection` 提供两种失败：

| 素材 | 配置 | 语义 | 用例 |
|---|---|---|---|
| `A04` | 无 `fail_times` | 永久失败 | `tool_failure` |
| `A09` | `fail_times: 2` | 前 2 次失败，之后成功 | `resume_after_failure` |

`tool_failure` 的动作序列：

```
1 search_materials   检索成功
2 generate_script    失败（A04 永久故障）
3 generate_script    重试，仍失败
4 generate_script    换素材（A06）—— 决策依据 observation.error.detail.failed_asset_id
5 estimate_cost      费用 8，在预算内
6 save_plan          保存成功
```

检索只发生一次 —— 说明重规划**复用了已成功步骤的结果**，而不是从头再来。

`resume_after_failure` 的两次执行：

| 次 | 结果 | 步骤状态 | `reused_steps` |
|---|---|---|---|
| 第 1 次 | `outcome=failed` | `search ✓ / generate ✗ / generate ✗` | — |
| 第 2 次（续跑） | `outcome=completed`，`plan_status=saved` | `search ✓(复用) / generate ✓ / estimate ✓ / save ✓` | `["search_materials:1"]` |

## 7. 已知限制

1. **离线 Mock 是确定性策略引擎，不是真实大模型。**
   它证明「流程与工具契约正确」，**不证明真实模型的决策质量**。
   真实模型通道（`src/llm/openai_compat.py`）代码完整但本次提交未执行（仓库不含密钥）。
   这一点不用 Mock 结果冒充模型效果。

2. **费用是模拟单位，不是真实货币。**
   素材与工具费用均为合成数据，符合题目「合成素材、工具费用与耗时表」的要求。

3. **不涉及真实投放。** 工具集无发布能力，`published` 恒为 `false`。

4. **时限判定依赖固定「当前时间」**（`AGENT_NOW`，默认 `2026-10-29T09:00:00`），
   以保证结果可复现。真实场景需替换为系统时钟。

5. **替代组合是启发式搜索**，不是全局最优求解。排序优先「保留价值最高的原素材」，
   在「内容投入不被浪费」与「绝对最便宜」之间做了取舍。

6. **单主题串行流程**，没有多 Agent、并行检索或分布式调度（题目明示非必需）。

## 8. AI 辅助开发说明

见 [docs/04-ai-usage.md](04-ai-usage.md) 第 8 节。
