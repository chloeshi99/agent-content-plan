# 内容任务规划 Agent

一个基于 ReAct 循环的内容活动方案规划助手：在**给定预算**与**截止时间**内，
完成「检索素材 → 生成脚本 → 成本估算 → 方案保存」，并在**超预算 / 工具失败 / 部分成功**时
动态调整计划、保留已成功步骤的结果。

> **系统不具备真实投放能力。** 工具集中不存在发布工具，所有落库产物的 `published` 恒为 `false`，
> 费用单位为「模拟单位」（合成数据，不涉及真实货币）。

**题号**：`项目2` · 内容任务规划 Agent

**技术选择**：原生实现（Python 3.10+ 标准库），未使用 LangGraph / ADK / AgentScope。
取舍理由见 [docs/02-solution.md](docs/02-solution.md) 第 6 节。

## 评审入口（建议从这里开始）

| 我想看什么 | 去哪里 |
|---|---|
| **一键跑通全部验收点** | `python -m src.cli --case all` |
| **实际输出汇总表** | [examples/outputs/summary.md](examples/outputs/summary.md) |
| **运行记录（步骤/参数/返回摘要/决策依据）** | [examples/traces/](examples/traces) |
| **ReAct 决策要求的验证** | [docs/03-evaluation.md](docs/03-evaluation.md) 第 5 节 |
| **输入样例** | [examples/inputs/cases.json](examples/inputs/cases.json) |
| **需求与验收标准** | [docs/01-requirements.md](docs/01-requirements.md) |
| **设计取舍** | [docs/02-solution.md](docs/02-solution.md) |
| **已知限制（重要）** | [docs/03-evaluation.md](docs/03-evaluation.md) 第 7 节 |
| **AI 辅助开发说明** | [docs/04-ai-usage.md](docs/04-ai-usage.md) 第 8 节 |
| **我能否一键核对全部验收点？** | `python -m scripts.check_delivery`（逐条自检，输出 OK/FAIL） |
| **复盘与下一步** | [docs/05-retrospective.md](docs/05-retrospective.md) |

## 快速开始

无需任何环境变量、无需安装第三方依赖，离线即可完整运行。

```bash
# 1. 环境（Python 3.10+）
pip install -r requirements.txt

# 2. 运行全部业务用例（覆盖必须验收的 5 项 + 加分项）
python -m src.cli --case all

# 3. 运行测试套件（70 passed）
python -m pytest tests -q

# 4. 重新生成实际输出与运行记录
python -m scripts.gen_outputs

# 5. 提交前密钥/隐私自查
python -m scripts.check_secrets

# 6. 交付自检（逐条核对题目要求）
python -m scripts.check_delivery
```

自由请求：

```bash
python -m src.cli --topic "品牌推广" --budget 10 --deadline 2026-11-30
python -m src.cli --topic "校园开放日" --budget 20 --deadline 2026-11-20
python -m src.cli --list-plans                       # 查看已保存方案
python -m src.cli --case all --trace-dir examples/traces   # 生成运行记录
python -m scripts.show_trace examples/traces/budget_overrun.jsonl
```

> `.env` **无需配置**。不设置任何变量即可跑通全部流程（离线 Mock 决策通道）。
> 需要真实模型时再复制 `.env.example` 并填入密钥。

## 实际运行结果

```
[normal_complete]    outcome=completed  plan=saved     assets=[A08] cost=7
[budget_overrun]     outcome=completed  plan=saved     assets=[A06] cost=8   (12→8 重规划)
[missing_budget]     outcome=incomplete plan=not_saved  cost=None  tool_calls=0
[missing_deadline]   outcome=incomplete plan=not_saved  cost=None  tool_calls=0
[tool_failure]       outcome=completed  plan=saved     assets=[A06] cost=8
[no_feasible_plan]   outcome=incomplete plan=not_saved  gap=budget(over_by=9)
[duplicate_execution] outcome=completed plan=duplicate assets=[A08] cost=7
[resume_after_failure] outcome=completed plan=saved   assets=[A09] cost=5  (复用检索步骤)
```

对照题目要求的验收点：

| 验收点 | 用例 | 结果 |
|---|---|---|
| 正常完成 | `normal_complete` | ✅ 选材→脚本→估算→保存，`cost=7` |
| 缺预算 | `missing_budget` | ✅ `incomplete`，`tool_calls=0`，缺口列出 `budget` |
| 缺截止时间 | `missing_deadline` | ✅ `incomplete`，`tool_calls=0`，缺口列出 `deadline` |
| 工具失败 | `tool_failure` | ✅ 失败步骤记 `failed`，换素材重规划并保存 |
| 预算超限 | `budget_overrun` | ✅ 估算 12 → 改选 → 估算 8 → 保存（参考实例） |
| 无可行方案 | `no_feasible_plan` | ✅ `incomplete` + 预算缺口 `over_by=9` |
| 重复执行 | `duplicate_execution` | ✅ 第二次 `duplicate`，方案表 1 行 |
| 失败步骤续跑（加分） | `resume_after_failure` | ✅ 复用检索结果，最终 `saved` |

**所有用例 `published` 均为 `false`** —— 没有任何一个用例声称已发布。

## 它能做什么

| 能力 | 实现方式 |
|---|---|
| 检索候选素材（按相关度排序） | `search_materials` 工具 |
| 生成方案脚本 | `generate_script` 工具（含素材分段与缓存） |
| 确定性费用/耗时估算与时限判定 | `estimate_cost` 工具 + `domain/costing.py` |
| 超预算时给出可行替代组合 | 服务端 `alternatives`，模型只做选择 |
| 保存方案（幂等） | `save_plan` 工具 + SQLite 唯一约束 |
| 缺预算/缺截止时间 → 明确未完成 | 装配层前置校验，不进入工具循环 |
| 工具失败 → 重试 → 换素材重规划 | 错误带 `failed_asset_id`，状态驱动下一步 |
| 失败步骤续跑（加分） | `steps` 表复用成功结果 + `run_meta` 继承失败计数 |
| 步数上限 | `MAX_TOOL_CALLS=10`，达到返回 `outcome=incomplete` |

### 交付结果字段（题目「交付结果」逐项对应）

| 交付项 | 字段 |
|---|---|
| 任务计划 | `plan_script` —— 方案脚本文本 |
| 步骤状态 | `steps[]` —— 每步 `{step, status}` |
| 工具结果 | `tool_results[]` —— 每次的 `tool/args/status/result_summary/error` |
| 费用合计 | `cost_breakdown` —— `tool_cost + asset_cost = total_cost`，含 `breakdown[]` 明细 |
| 已保存方案 | `plan_status` + `plan_id` |
| 未完成原因 | `outcome` + `incomplete_reason` + `gap` + `reasoning` |

## ReAct 决策（题目核心要求）

模型在运行时选择动作、执行工具、观察结果、调整下一步：

```
① 模型选择动作  action = client.decide(messages, tool_specs)
② 执行工具      observation = registry.call(action.tool, action.args)
③ 观察结果并调整 observation 回灌 messages → 下一轮 decide() 读新状态
                或 action.action == "finish" 时结束
```

每步决策都记录**它依据哪个观测字段**（trace 的 `decided_by` 列），可逐行核对：

| 步骤 | 工具 | 观测 | 决策依据 |
|---|---|---|---|
| 1 | `search_materials` | 候选列表 | `state.step:1(无观测，从检索素材开始)` |
| 2 | `generate_script` | 脚本 | `observation.candidates` |
| 3 | `estimate_cost` | **`total_cost=12, over_budget=true`** | `observation.script` |
| 4 | `estimate_cost` | **`total_cost=8, over_budget=false`** | **`observation.over_budget`** → 改选 `[A06]` |
| 5 | `save_plan` | `status=saved` | `observation.over_budget:false+deadline_ok:true` |
| 6 | **finish** | — | `observation.status:saved` |

**最强证据**：第 4 步的依据是第 3 步的**真实观测字段** `over_budget`，
而不是「现在是第 4 步」。同样的机制在 `tool_failure` 里表现为
第 4 步依据 `observation.error.detail.failed_asset_id` 换素材，
而**检索只发生了一次**（成功结果被保留）。

## 费用模型（可单测、可核对）

```
方案总费用 = 工具固定开销(3) + Σ 素材费用
工具固定开销 = 检索(1) + 脚本生成(2) + 估算(0) + 保存(0) = 3
方案总耗时 = 工具固定耗时(55 分钟) + Σ 素材耗时
```

| 组合 | 工具 | 素材 | 总费用 | 判定（预算 10） |
|---|---|---|---|---|
| `[A05, A06]` | 3 | 4+5 | **12** | ❌ 超 2 → 改选 |
| `[A06]` | 3 | 5 | **8** | ✅ |
| `[A05]` | 3 | 4 | 7 | ✅ |
| `[A09]` | 3 | 2 | 5 | ✅ |

> 全部算术在 `src/domain/costing.py` 确定性完成，**模型不参与任何费用计算**。
> 参考实例 12 → 8 由 `tests/test_costing.py` 固化。

## 目录结构

```
项目2/
├─ README.md
├─ requirements.txt          # 运行时零第三方依赖
├─ .env.example              # 真实模型配置示例（.env 不提交）
├─ data/
│  ├─ catalog.json           # 合成素材 + 工具费用/耗时表 + 故障注入
│  └─ cases.json             # 8 个业务用例
├─ src/
│  ├─ cli.py                 # CLI 入口
│  ├─ runner.py              # 装配层
│  ├─ config.py              # 配置加载
│  ├─ agent/loop.py          # ReAct 循环 + 步骤状态 + trace
│  ├─ agent/prompts.py       # 系统提示词与输出契约
│  ├─ llm/offline_mock.py    # 离线观测驱动决策引擎
│  ├─ llm/openai_compat.py   # 真实模型通道（本次未执行，无密钥）
│  ├─ tools/impl.py          # 四个工具 + 故障注入
│  ├─ domain/costing.py      # 费用/时限/替代组合（确定性）
│  └─ storage/db.py          # plans / steps / run_meta
├─ tests/                    # 70 个测试
├─ examples/
│  ├─ inputs/cases.json      # 输入
│  ├─ outputs/               # 实际输出 + summary.md
│  └─ traces/                # 运行记录（jsonl）
├─ scripts/
│  ├─ gen_outputs.py         # 重新生成输出与运行记录
│  ├─ show_trace.py          # 可读查看运行记录
│  ├─ check_delivery.py      # 交付自检
│  └─ check_secrets.py       # 密钥/隐私自查
└─ docs/
   ├─ 01-requirements.md     # 需求与验收标准
   ├─ 02-solution.md         # 架构、动态决策、技术取舍
   ├─ 03-evaluation.md       # 预期 vs 实际、限制
   ├─ 04-ai-usage.md         # 模型角色、输出契约、AI 使用说明
   └─ 05-retrospective.md     # 复盘与下一步
```

## 已知限制（请一并阅读）

1. 离线 Mock 是**确定性策略引擎，不是真实大模型** —— 证明流程与工具契约正确，
   不证明真实模型的决策质量（详见 `docs/03-evaluation.md` 第 7 节）。
2. 费用是**模拟单位**，素材为合成数据，不涉及真实货币与真实投放。
3. 时限判定使用固定「当前时间」（`AGENT_NOW`）以保证结果可复现。
4. 替代组合是启发式搜索，非全局最优。
5. 实际用时约 3 小时 25 分钟；**未完成部分逐项列在
   [docs/05-retrospective.md](docs/05-retrospective.md) 第 0 节**。

## 提交信息

| 字段 | 值 |
|---|---|
| **题号** | `项目2` —— 内容任务规划 Agent |
| **GitHub URL** | https://github.com/chloeshi99/agent-content-plan |
| **最终 commit SHA** | 本仓库 `main` 分支的最新提交 —— 取 `git rev-parse HEAD`，或见 [commits/main](https://github.com/chloeshi99/agent-content-plan/commits/main) |
| **仓库可见性** | Public（公开可读，评审账号无需授权） |
| **默认分支** | `main` |

> 说明：commit SHA 无法在自身所在的提交内容中预先写入（写入即改变 SHA）。
> 因此以 `main` 分支最新提交为准，上面的链接会直接显示当前 SHA；
> 提交表单中填写 `git rev-parse HEAD` 的输出即可。

### 从零复现

```bash
git clone https://github.com/chloeshi99/agent-content-plan.git
cd agent-content-plan
pip install -r requirements.txt

python -m src.cli --case all            # 8 个用例（含全部验收点）
python -m pytest tests -q               # 70 个测试
python -m scripts.check_delivery         # 53 项交付自检
python -m scripts.check_secrets          # 密钥/隐私自查
```

无需任何环境变量、无需 API Key、无需联网 —— 默认走离线 Mock 决策通道。

仓库不含任何真实密钥或隐私数据（`python -m scripts.check_secrets` 可自检）。
