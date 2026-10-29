# 01 需求说明（用户、目标、范围与成功标准）

## 1. 用户与目标

**用户**：内容活动的运营人员。他要在**给定预算**和**给定截止时间**内，
为某个主题产出一份可执行的内容活动方案（选哪些素材、脚本长什么样、花多少钱、能不能按时交付）。

**目标**：让运营人员在预算与截止时间内完成一份内容活动方案。

拆成四件可验收的事：

1. **选材与成稿** —— 检索候选素材，生成方案脚本。
2. **费用可控** —— 每次估算都给出费用明细；**超预算时必须改方案**，不能硬上。
3. **时限可控** —— 估算同时给出总耗时，与截止时间比对；不可行就换更省的组合。
4. **诚实与幂等** —— 没有可行方案就明确返回未完成及缺口；相同方案重复执行只产生一份。

## 2. 输入与材料

| 材料 | 来源 | 内容 |
|---|---|---|
| 活动主题 | CLI `--topic` | 自然语言主题，如「品牌推广」 |
| 预算 | CLI `--budget` | 整数，模拟单位 |
| 截止时间 | CLI `--deadline` | `YYYY-MM-DD` 或 `YYYY-MM-DDTHH:MM` |
| 素材库 | `data/catalog.json` | id、标题、类型、标签、关键词、摘要、费用、耗时 |
| 工具费用表 | `data/catalog.json` | 检索 1 / 脚本 2 / 估算 0 / 保存 0 |
| 工具耗时表 | `data/catalog.json` | 检索 10 / 脚本 30 / 估算 5 / 保存 10（分钟） |
| 故障注入 | `data/catalog.json` | `failure_injection`：指定素材的工具失败行为 |

工具层提供四个工具：**素材检索、脚本生成、成本估算、方案保存**。

## 3. 范围

### 在范围内

- 单主题的内容活动方案规划（CLI 入口）
- ReAct 决策循环：模型选择动作 → 执行工具 → 观察结果 → 模型调整计划或停止
- 费用与耗时的**确定性**估算（模型不参与算术）
- 超预算 / 工具失败 / 部分成功时的**重规划**，并保留已成功步骤结果
- 缺预算或缺截止时间时的明确未完成返回
- 离线 Mock 决策通道（无密钥可运行）与真实模型通道（OpenAI 兼容）
- SQLite 方案库 + 步骤状态表（幂等与续跑）
- Trace 记录（步骤 / 工具参数 / 返回摘要 / 后续动作 / 决策依据）

### 明确不在范围内

- **真实投放/发布**。系统只产出方案，不存在发布工具，`published` 恒为 `false`。
- 界面、线上部署、多 Agent、生产级 Runtime（题目明示非必需）。
- 多用户隔离与鉴权。
- 真实业务数据与真实货币（素材与费用均为合成数据，单位为「模拟单位」）。

## 4. 成功标准

### 4.1 必须验收的五项（题目明示）

| 验收点 | 判定标准 | 对应用例 |
|---|---|---|
| **正常完成** | 选材 → 脚本 → 估算 → 保存全部成功，`plan_status=saved` | `normal_complete` |
| **缺预算 / 缺截止时间** | 明确返回未完成并列出缺口，**不进入工具循环** | `missing_budget`、`missing_deadline` |
| **工具失败** | 失败步骤如实记为 `failed`，改选替代素材重规划，保留成功步骤 | `tool_failure` |
| **预算超限** | 初始估算超预算 → 改选替代方案 → 重新估算 → 保存 | `budget_overrun` |
| **重复执行** | 相同方案第二次返回 `duplicate`，方案表只有一份 | `duplicate_execution` |

加分项：**失败步骤续跑** —— 复用已成功步骤、重试失败步骤，对应 `resume_after_failure`。

### 4.2 参考实例的逐条对应

> 「预算 10 个模拟单位；初始方案估算 12，替代方案估算 8。模型观察到超预算后改选 8 的方案，
> 再估算和保存。若没有可行方案，返回未完成及缺口；不能把失败步骤记为成功。」

| 参考实例要求 | 本项目实现 | 验证位置 |
|---|---|---|
| 预算 10 | `budget_overrun` 用例 `budget=10` | `examples/inputs/cases.json` |
| 初始方案估算 12 | `[A05, A06]` → 3 + (4+5) = 12 | `examples/traces/budget_overrun.jsonl` 第 3 步 |
| 替代方案估算 8 | `[A06]` → 3 + 5 = 8 | 同上第 4 步 |
| 观察到超预算后改选 | 观测 `over_budget=true` 触发改选，`decided_by` 记录依据 | 同上 |
| 再估算和保存 | 第 4 步 `estimate_cost`、第 5 步 `save_plan` | 同上 |
| 无可行方案返回未完成及缺口 | `no_feasible_plan` → `outcome=incomplete`，`gap.type=budget` | `examples/outputs/no_feasible_plan.json` |
| 失败步骤不能记为成功 | 失败步骤 `status=failed`，成功步骤 `status=succeeded` | `examples/outputs/tool_failure.json` 的 `runs[].steps` |

### 4.3 审核要点（题目原文）

> 核对预算计算、步骤依赖与实际状态；注入一次失败，观察是否调整计划且保留成功结果。
> 高分依据是合理重规划；无状态、无失败处理只能证明完成串行调用。

本项目的对应机制：

| 审核要点 | 机制 | 可核对位置 |
|---|---|---|
| 预算计算 | 全部算术在 `src/domain/costing.py`，确定性可单测 | `tests/test_costing.py` |
| 步骤依赖 | 步骤按 (run_key, step_name) 持久化，成功结果可复用 | `src/storage/db.py`、`tests/test_storage.py` |
| 实际状态 | 每步 `succeeded`/`failed` 如实落库，不美化 | `examples/outputs/*.json` 的 `runs[].steps` |
| 注入失败 | `data/catalog.json` 的 `failure_injection` | `tool_failure`、`resume_after_failure` |
| 调整计划且保留成功结果 | `decided_by` + `reused_steps` | `examples/traces/*.jsonl` |
