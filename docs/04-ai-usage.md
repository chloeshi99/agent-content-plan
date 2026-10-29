# 04 AI 使用说明

## 1. 模型在整个系统中的角色

模型（LLMClient）只负责一件事：**决定下一步做什么**。

```
模型决策：下一步调用哪个工具、传什么参数、还是结束并给出结论
工具执行：返回事实（候选素材、脚本、费用明细、保存结果）或如实报错
```

模型**不**负责：
- 计算费用与耗时（由 `domain/costing.py` 确定性执行）
- 判断是否超预算（由同一模块判定）
- 搜索替代组合（由服务端给出候选，模型只做选择）
- 直接读写数据库（只能通过 `save_plan` 工具）

这个分工是刻意的：把**可验证的计算**交给确定性代码，把**需要判断的部分**
（超预算后选哪个替代方案、工具失败后换哪份素材、是否放弃）交给模型。
好处：预算判定可单测、可回归；模型幻觉不会让超预算方案被放行。

## 2. 两种决策通道

| | 离线 Mock | 真实模型 |
|---|---|---|
| 文件 | `src/llm/offline_mock.py` | `src/llm/openai_compat.py` |
| 协议 | `LLMClient.decide(messages, tool_specs) -> Action` | 同左 |
| 启用条件 | 默认（无 API Key） | `.env` 中配置 `LLM_API_KEY` |
| 用途 | CI、回归测试、流程验证 | 验证真实决策质量 |

两者共用**同一套 Action 协议**与**同一段系统提示词约束**（`agent/prompts.py`），
因此切换通道不会改变工具契约与最终字段结构。

### 2.1 离线 Mock 的边界声明

它**不是**预录日志，也不是固定分支脚本，而是一个**观测驱动的策略引擎**：
只读取「上一次工具返回的真实观测值」（`over_budget` / `error` / `status` /
`candidates` / `deadline_ok` 等），据此选择下一步动作。工具结果不同时
（估算超限 vs 在预算内、工具失败 vs 成功、检索有候选 vs 无候选），
给出的动作序列真实不同。

但它仍然**不是真实大模型**：它只能证明**流程与工具契约的正确性**，
**不能证明真实模型的决策质量**。该边界在 `03-evaluation.md` 第 7 节与本文中均已明示。

## 3. 输出契约

模型每次只输出一个 JSON 对象：

```json
{
  "action": "tool" | "finish",
  "tool": "search_materials" | "generate_script" | "estimate_cost" | "save_plan",
  "args": { ... },
  "rationale": "选择该动作的依据（简要，不含内部思维过程）",
  "decided_by": "本步决策所依据的观测字段，如 observation.over_budget",
  "final": { ... }
}
```

`finish` 时的 `final` 结构：

```json
{
  "topic": "品牌推广",
  "outcome": "completed" | "incomplete" | "failed",
  "plan_status": "saved" | "duplicate" | "not_saved" | "failed",
  "plan_id": "P-品牌推广-f840f072",
  "assets": ["A06"],
  "total_cost": 8,
  "total_duration_min": 165,
  "published": false,
  "reasoning": "..."
}
```

`decided_by` 是本项目刻意增加的字段：它把「这一步为什么这么做」显式地指向
**一个具体的观测字段**，评审可逐行核对，也便于写测试断言
（`tests/test_react.py`）。

## 4. 提示词中的硬性约束

`src/agent/prompts.py` 的 `SYSTEM_POLICY` 给模型四条硬性约束：

1. **只能在预算内产出方案**。估算超限时必须改选替代方案，不得保存超预算方案。
2. **缺预算或缺截止时间就停止**，返回未完成并说明缺口，不得臆测默认值。
3. **工具失败如实返回失败**，不得把失败步骤记为成功；可重试或改选其他素材。
4. **不得声称已发布**。系统不存在发布动作。

约束不只是提示 —— 它们在代码层也有对应的强制机制：

| 提示词约束 | 代码层强制 |
|---|---|
| 不得超预算保存 | `estimate_cost` 的 `over_budget` 由确定性代码判定；`save_plan` 前校验费用 |
| 缺输入就停止 | `runner.run()` 前置校验，直接返回 `incomplete`，不进入循环 |
| 不得把失败记为成功 | 工具抛错 → 步骤状态写 `failed`；`_finalize` 对「声称 saved 但无 plan_id」改判 `failed` |
| 不得声称已发布 | `published` 无条件 `false`；工具集无发布能力；`tests/test_react.py` 断言工具层无 publish 动作 |
| 不得无限重试 | `MAX_TOOL_CALLS=10`、`MAX_PLAN_ATTEMPTS=3` |

## 5. 工具契约

| 工具 | 入参 | 出参 | 失败语义 |
|---|---|---|---|
| `search_materials` | `topic`, `limit` | `candidates[]`（含 id/费用/耗时/相关度） | 空结果时 `note` 说明未检索到 |
| `generate_script` | `topic`, `assets[]` | `script`, `sections[]` | 素材不存在 / 注入故障 → 抛 `ToolError`，含 `failed_asset_id` |
| `estimate_cost` | `assets[]`, `budget`, `deadline` | `total_cost`, `over_budget`, `breakdown[]`, `deadline_ok`, `alternatives[]` | 参数非法 → 抛错 |
| `save_plan` | `topic`, `assets[]`, `script`, `total_cost` | `status`(`saved`/`duplicate`), `plan_id` | 重复 → `duplicate`（不是错误）；写入故障 → 抛错 |

工具层**不做任何决策**：它只提供事实、暴露失败、受控写入。

## 6. 决策可核对性

每一步都会写入 trace：

```jsonc
{
  "step": 4,
  "action": "tool",
  "tool": "estimate_cost",
  "args": {"assets": ["A06"], "budget": 10, "deadline": "2026-11-30"},
  "result_summary": "{\"total_cost\": 8, \"over_budget\": false, ...}",
  "next_action": "由模型根据观测决定下一步",
  "rationale": "改选更省的组合后重新估算",
  "decided_by": "observation.over_budget",
  "tool_error": null
}
```

`decided_by` 的三种典型取值：

| 取值 | 含义 |
|---|---|
| `state.step:1(无观测，从检索素材开始)` | 首个动作，无观测可依据 |
| `observation.candidates` | 依据检索结果选材 |
| `observation.over_budget` | 依据超预算观测改选替代方案 |
| `observation.error.detail.failed_asset_id` | 依据失败详情换素材 |
| `observation.status:saved` | 依据保存结果结束 |

## 7. 接入真实模型

```bash
cp .env.example .env
# 编辑 .env 填入 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL
python -m src.cli --topic "品牌推广" --budget 10 --deadline 2026-11-30
```

不配置任何变量时使用离线 Mock 通道，**全部验收点依然可跑通**。

## 8. AI 辅助开发说明

本项目的代码与文档由 AI 辅助生成。说明如下：

1. **AI 参与了什么**：需求拆解、架构设计、代码实现、测试编写、文档撰写、缺陷修复。
2. **AI 没有做什么**：没有虚构测试结果 —— 本文所有「实际结果」均由
   `python -m scripts.gen_outputs` 与 `python -m pytest tests -q` 现场生成。
3. **人工校验过的关键点**：
   - 费用模型的算术（12 / 8 / 7 / 5）与题目参考实例逐项核对；
   - 失败步骤是否被如实记为 `failed`（逐条检查 `examples/outputs/*.json`）；
   - 续跑是否真的复用了成功步骤（检查 `reused_steps` 与检索只发生一次）；
   - 重复执行是否只产生一份方案（检查方案表行数）。
4. **AI 曾犯的错误与修复**（见 `05-retrospective.md`）：
   - 重试计数 off-by-one；
   - `fail_times=0` 被误判为成功；
   - 替代方案按「最便宜」排序，导致有价值的素材被丢弃；
   - 重规划后素材重复；
   - 状态写回覆盖了模型自己的选材决定；
   - 工具契约被一个空列表影子覆盖（Mock 不用该字段，真实模型通道会失效）。
5. **可复现性**：全部结论可用仓库内命令复现，不依赖任何外部服务。
