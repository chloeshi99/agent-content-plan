# 用例实际输出汇总

| 用例 | outcome | plan_status | assets | total_cost | 方案数 | 期望 |
|---|---|---|---|---|---|---|
| `normal_complete` | completed | saved | A08 | 7 | 1 | 预算充足且时限宽裕，选材->生成脚本->估算->保存全部成功 |
| `budget_overrun` | completed | saved | A06 | 8 | 1 | 初始方案估算超出预算 10，模型改选更省的替代组合后重新估算并保存（对应参考实例：12 -> 8） |
| `missing_budget` | incomplete | not_saved | - | - | 0 | 缺少预算，明确返回未完成并说明缺口，不进入工具循环 |
| `missing_deadline` | incomplete | not_saved | - | - | 0 | 缺少截止时间，明确返回未完成并说明缺口 |
| `tool_failure` | completed | saved | A06 | 8 | 1 | 脚本生成工具注入永久故障，重试后改选其他素材重规划，保留检索成功结果，最终完成 |
| `no_feasible_plan` | incomplete | not_saved | A08,A07 | 11 | 0 | 预算过低且无任何可行组合，返回未完成并给出预算缺口 |
| `duplicate_execution` | completed | duplicate | A08 | 7 | 1 | 相同方案重复执行，第二次保存返回 duplicate，方案表只有一份 |
| `resume_after_failure` | completed | saved | A09 | 5 | 1 | 首次执行脚本生成瞬时故障连续失败，第二次续跑复用已成功的检索步骤并重试失败步骤，最终完成（加分项） |

## 验收对照

| 验收点 | 用例 | 结果 |
|---|---|---|
| 正常完成 | `normal_complete` | ✅ |
| 缺预算 | `missing_budget` | ✅ |
| 缺截止时间 | `missing_deadline` | ✅ |
| 工具失败并重规划 | `tool_failure` | ✅ |
| 预算超限改选替代方案 | `budget_overrun`（12→8） | ✅ |
| 无可行方案 | `no_feasible_plan` | ✅ |
| 重复执行 | `duplicate_execution` | ✅ |
| 失败步骤续跑（加分） | `resume_after_failure` | ✅ |

所有用例 `published=false`。
