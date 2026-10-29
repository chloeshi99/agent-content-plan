# 02 方案设计（调用流程、动态决策、技术取舍）

## 1. 总体架构

```
topic + budget + deadline
          │
          v
┌────────────────────────────────────────────────────────┐
│  src/runner.py  装配层                                  │
│  前置校验（缺预算/截止时间）→ 建工具上下文 → 建 Agent → 跑循环 │
└──────────────────────────┬─────────────────────────────┘
                           │
                           v
┌────────────────────────────────────────────────────────┐
│  src/agent/loop.py  ReAct 循环                          │
│                                                        │
│  state ──> _build_messages ──> client.decide()         │
│                                     │                  │
│                    action=tool ─────┤───── action=finish│
│                                     v                  v   │
│                     registry.call()          finalize + 保护 │
│                                     │                  │   │
│                     observation 回灌 ──────────────────┘   │
└──────────────────────────┬─────────────────────────────┘
                           │
   ┌───────────────────────┼───────────────────────┐
   v                       v                       v
┌─────────┐        ┌────────────┐        ┌────────────────┐
│ llm/    │        │ tools/     │        │ domain/        │
│ 决策层  │        │ 工具层     │        │ 费用/时限计算   │
│         │        │ 只做事实   │        │ 确定性          │
└─────────┘        └─────┬──────┘        └────────────────┘
                        v
              ┌──────────────────┐
              │ storage/         │
              │ SQLite 方案库     │
              │ plans/steps/run_meta │
              └──────────────────┘
```

### 分层职责

| 层 | 位置 | 职责 | 明确不做 |
|---|---|---|---|
| 循环层 | `src/agent/loop.py` | 编排模型与工具、步数上限、步骤状态、trace | 不理解业务语义 |
| 决策层 | `src/llm/` | **决定下一步做什么** | 不直接读写数据、不做算术 |
| 工具层 | `src/tools/impl.py` | 提供事实、暴露失败、受控写入 | **不做任何决策** |
| 领域层 | `src/domain/costing.py` | 费用、耗时、时限、替代组合计算 | 不涉及 I/O |
| 存储层 | `src/storage/db.py` | 方案持久化、幂等、步骤状态 | 不含业务判断 |
| 装配层 | `src/runner.py` | 组装各层、统一入口 | 不含业务判断 |

**核心分工**：把**可验证的计算**（费用、耗时、时限、替代组合、幂等）交给确定性代码，
把**需要判断的部分**（超预算后改选哪个方案、工具失败后换哪份素材、是否停止）交给模型。
好处：预算判定可单测、可回归；模型幻觉不会让超预算方案被放行。

## 2. ReAct 决策循环

### 2.1 循环骨架

```python
while True:
    if tool_calls >= max_tool_calls:
        return incomplete()                        # 步数上限 -> 明确未完成

    action = client.decide(messages, TOOL_SPECS)  # ① 模型选择动作

    if action.action == "finish":
        return finalize(action)                    # ③ 模型决定结束

    observation = registry.call(action.tool, action.args)  # ② 执行工具
    state = absorb(state, action, observation)     # 观察结果并更新状态
    messages.append(observation)                   # 观察结果回灌给模型
```

三个环节 —— **模型选择动作 → 执行工具 → 观察结果并调整下一步或结束** —— 全部由
`src/agent/loop.py` 实现，与决策层无关。切换决策层（离线 Mock ↔ 真实模型）不改变循环结构。

### 2.2 状态传递

状态包含：已选素材 `assets`、脚本 `script`、最近一次估算 `estimate`、
失败素材 `failed_asset`、重规划次数 `attempts`、已成功步骤 `successful_steps`。

模型每一步都必须给出 `decided_by`（这一步依据哪个观测字段做决策），例如：

- `observation.over_budget:true` → 改选替代组合
- `observation.error:script_generate_failed` → 换素材重规划
- `observation.status:saved` → 结束

这条字段会写进 trace，评审可逐行核对「是不是真被观测驱动的」。

### 2.3 动态决策的三种触发

| 触发 | 观测字段 | 模型的动作 | 结果保留 |
|---|---|---|---|
| **成本超限** | `estimate.over_budget=true` + `alternatives` | 改选服务端给出的可行替代组合，重新 `estimate_cost` | 已成功的检索、脚本结果保留 |
| **工具错误** | 工具抛错，`error` 观测 | 先重试一次；仍失败则换素材重规划 | 同上 |
| **部分成功** | 部分步骤 `succeeded`、部分 `failed` | 只重做失败步骤 | 成功步骤结果直接复用 |

## 3. 费用与时限模型

### 3.1 费用

一个完整方案的固定工具开销：

```
素材检索(1) + 脚本生成(2) + 成本估算(0) + 方案保存(0) = 3 个模拟单位
方案总费用 = 3 + Σ 素材费用
```

对应参考实例：

| 组合 | 工具 | 素材 | 总费用 | 判定 |
|---|---|---|---|---|
| `[A05, A06]` | 3 | 4 + 5 = 9 | **12** | 超预算 10 |
| `[A06]` | 3 | 5 | **8** | 在预算内 ✅ |

### 3.2 时限

```
方案总耗时 = 工具固定耗时(55 分钟) + Σ 素材耗时
可行 = 总耗时 ≤ (截止时间 - 当前时间)
```

`当前时间` 由 `AGENT_NOW` 固定（默认 `2026-10-29T09:00:00`），保证可复现。

### 3.3 替代组合搜索

`costing.find_alternatives()` 是确定性搜索，按优先级：

1. 逐个移除素材（从最贵的开始）
2. 用更便宜的素材替换最贵的素材
3. 单素材方案

排序键：`retained_value`（保留的原素材价值之和）→ `retained`（保留个数）→ `total_cost`（更便宜）。
**不是简单取最便宜的** —— 优先保住内容投入最高的资产，避免「为了省钱把好素材全删了」。

## 4. 状态与失败处理

### 4.1 三张表

| 表 | 主键 | 用途 |
|---|---|---|
| `plans` | `plan_key`（topic+assets+cost 的哈希）唯一 | 幂等：相同方案只存一份 |
| `steps` | `(run_key, step_name)` 唯一 | 步骤状态与结果，支撑续跑 |
| `run_meta` | `run_key` | 运行级元数据（失败计数），供续跑继承 |

### 4.2 失败注入语义

`data/catalog.json` 的 `failure_injection`：

```jsonc
"A04": { "generate_script": { "message": "..." } },          // 永久失败
"A09": { "generate_script": { "message": "...", "fail_times": 2 } }  // 前 2 次失败，之后成功
```

- **省略 / `null` / `0`** → 永久失败（每次都失败）
- **`N > 0`** → 前 N 次失败，之后成功（模拟瞬时故障）

### 4.3 续跑（加分项）

```
第一次执行：search_materials ✓ → generate_script ✗ → 结束（outcome=failed）
第二次续跑：读 steps 表，复用 search_materials 结果 → 重试 generate_script ✓ → 估算 ✓ → 保存 ✓
```

关键点：续跑时**继承上次的失败计数**，否则瞬时故障会在每次续跑时重新失败，陷入无限重试。
实现见 `ToolContext.load_failure_counts()` 与 `runner.run(resume=True)`。

## 5. 保护与边界

| 约束 | 代码层强制 |
|---|---|
| 不得声称已发布 | `published` 无条件 `false`；工具集无发布能力 |
| 不得超预算保存 | `save_plan` 前重新校验；`costing` 判定为准 |
| 缺预算/截止时间不得进入循环 | `runner.run()` 前置校验，直接返回 `incomplete` |
| 失败步骤不得记为成功 | 工具抛错 → 步骤状态写 `failed`，结果体不含成功字段 |
| 步数上限 | `MAX_TOOL_CALLS=10`，达到返回 `outcome=incomplete` |

## 6. 技术取舍

### 6.1 为什么原生实现，不用 LangGraph / ADK / AgentScope

题目明示「框架名称不加分，能解释取舍与修改结果才有价值」，且框架非必需。

| 考量 | 本项目的判断 |
|---|---|
| **决策循环只有一层** | 标准的 ReAct 单循环用 LangGraph 的 StateGraph 表达略显重（需定义状态、节点、边、条件路由），而本项目的循环骨架只有数十行（`src/agent/loop.py`） |
| **需要的核心是「观测驱动」** | 循环本身已经是骨架；框架能提供的是编排抽象，本项目缺的不是编排，是「每步决策依据」的可核对性 —— 这靠 `decided_by` 字段与断言测试，不靠框架 |
| **离线 Mock 是硬需求** | 题目要求「模型不可用时提供离线 Mock 入口并标明边界」。框架的状态图会引入额外心智负担，却不带来离线能力；本项目的离线通道是 40 行策略引擎 |
| **续跑需要自己控制状态持久化** | LangGraph 有 checkpoint，但本项目的续跑粒度是**工具步骤**（`(run_key, step_name)`），与框架的节点级 checkpoint 不完全对齐，自己实现反而更直接 |
| **可解释性是评分项** | 「高分依据是合理重规划」——重规划逻辑要能被逐行读懂并单独单测。原生实现让 `tests/test_react.py` 能直接断言「超预算观测 → 下一步是重新估算」 |

**代价与应对**：没有现成框架的 checkpoint / 可视化 / 追踪 UI。

- 续跑自己实现：`steps` 表 + `run_meta` 表，约 40 行（`src/storage/db.py` + `src/agent/loop.py`）。
- 可视化自己实现：`examples/traces/*.jsonl` 的 `decided_by` 列 + `scripts/show_trace.py`。
- 迁移触发点：如果需求演进到多 Agent 协作、需要条件边可视化、或要支持「从任意节点恢复」，
  会迁移到 LangGraph；当前单主题串行规模下，框架的抽象成本高于收益。

### 6.2 其他取舍

| 取舍 | 决定 | 理由 |
|---|---|---|
| 费用计算 | **确定性代码**，不让模型算 | 预算判定是硬约束，必须可单测、可复现 |
| 决策通道 | 默认离线 Mock + 可切真实模型 | 评审无密钥也能完整跑通全部验收点 |
| 存储 | SQLite 三张表 | 标准库自带，支持幂等约束与续跑状态 |
| 入口 | CLI | 题目三选一，CLI 最易复现与截图 |
| 数据 | 合成素材与模拟单位 | 题目允许，避免隐私与合规问题 |
| 错误信息 | 错误携带 `failed_asset_id` 等结构化详情 | 失败信息的设计直接决定模型能否重规划（见 `05-retrospective.md` 1.1） |
