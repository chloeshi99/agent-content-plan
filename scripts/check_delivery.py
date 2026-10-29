"""交付自检：逐条核对题目要求。

对照题目「项目要求 / 交付结果 / 动态决策 / 必须验收 / 运行方式 / 模型与工具 /
提交方式 / Agent 决策要求 / GitHub 交付规范」逐条检查当前仓库状态。

用法：
    python -m scripts.check_delivery
退出码：0 = 全部满足；1 = 存在未满足项。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))


def has(rel: str) -> bool:
    return (ROOT / rel).exists()


def load(rel: str) -> dict | None:
    p = ROOT / rel
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


# ---------------- 项目结构（题目「GitHub 交付规范」） ----------------
for rel, label in [
    ("README.md", "README.md"),
    ("src", "src/"),
    ("tests", "tests/"),
    ("examples", "examples/"),
    ("data", "data/"),
    ("docs", "docs/"),
    ("requirements.txt", "依赖文件 requirements.txt"),
    (".env.example", ".env.example"),
]:
    check(f"结构：{label}", has(rel))

for doc in ["01-requirements.md", "02-solution.md", "03-evaluation.md", "04-ai-usage.md", "05-retrospective.md"]:
    check(f"文档：docs/{doc}", has(f"docs/{doc}"))

# ---------------- 验证材料 ----------------
check("验证材料：examples/inputs（输入）", has("examples/inputs/cases.json"))
check("验证材料：examples/outputs（实际输出）", has("examples/outputs/summary.md"))
traces = list((ROOT / "examples" / "traces").glob("*.jsonl")) if has("examples/traces") else []
check("验证材料：examples/traces（运行记录）", len(traces) >= 3, f"{len(traces)} 个 trace 文件")

# ---------------- 必须验收的五个场景 ----------------
ACCEPT = {
    "normal_complete": ("正常完成", "completed"),
    "missing_budget": ("缺预算", "incomplete"),
    "missing_deadline": ("缺截止时间", "incomplete"),
    "tool_failure": ("工具失败并重规划", "completed"),
    "budget_overrun": ("预算超限改选替代方案", "completed"),
}
for case, (label, expect) in ACCEPT.items():
    d = load(f"examples/outputs/{case}.json")
    ok = False
    detail = "缺少输出文件"
    if d:
        a = d["actual"]
        ok = a["outcome"] == expect and a["published"] is False
        detail = f"outcome={a['outcome']} plan={a['plan_status']} cost={a['total_cost']}"
    check(f"验收：{label}（{case}）", ok, detail)

# 缺预算/缺截止时间不得进入工具循环
for case in ("missing_budget", "missing_deadline"):
    d = load(f"examples/outputs/{case}.json")
    if d:
        check(f"验收：{case} 未调用任何工具", d["actual"]["tool_calls"] == 0,
              f"tool_calls={d['actual']['tool_calls']}")
        gap = (d["actual"].get("gap") or {}).get("missing") or []
        check(f"验收：{case} 明确列出缺口", bool(gap), f"missing={gap}")

# 预算超限必须改选并落在预算内（参考实例 12 -> 8）
d = load("examples/outputs/budget_overrun.json")
if d:
    check("验收：预算超限后总费用在预算内", d["actual"]["total_cost"] is not None
          and d["actual"]["total_cost"] <= 10, f"cost={d['actual']['total_cost']}")
    check("验收：预算超限采用替代方案（参考实例 12→8）", d["actual"]["total_cost"] == 8,
          f"cost={d['actual']['total_cost']}")
    tools = []
    tp = ROOT / "examples" / "traces" / "budget_overrun.jsonl"
    if tp.exists():
        tools = [json.loads(x)["tool"] for x in tp.read_text(encoding="utf-8").splitlines() if x.strip()]
    check("验收：超限后重新估算", tools.count("estimate_cost") >= 2, f"estimate_cost×{tools.count('estimate_cost')}")
    check("验收：超限后保存", "save_plan" in tools)

# 工具失败必须有失败步骤且保留成功步骤
d = load("examples/outputs/tool_failure.json")
if d:
    steps = d["runs"][0]["steps"]
    check("验收：工具失败步骤记为 failed", any(s["status"] == "failed" for s in steps),
          f"steps={[s['status'] for s in steps]}")
    check("验收：保留已成功步骤", any(s["status"] == "succeeded" for s in steps))

# 无可行方案 -> 未完成 + 缺口
d = load("examples/outputs/no_feasible_plan.json")
if d:
    check("验收：无可行方案返回未完成", d["actual"]["outcome"] == "incomplete")
    check("验收：无可行方案给出预算缺口", (d["actual"].get("gap") or {}).get("type") == "budget",
          json.dumps(d["actual"].get("gap"), ensure_ascii=False))
    check("验收：无可行方案未保存方案", d["plan_count_after"] == 0, f"方案数={d['plan_count_after']}")

# 重复执行只产生一份
d = load("examples/outputs/duplicate_execution.json")
if d:
    check("验收：重复执行方案表只有一份", d["plan_count_after"] == 1, f"方案数={d['plan_count_after']}")
    check("验收：重复执行第二次为 duplicate", d["runs"][-1]["plan_status"] == "duplicate",
          f"{d['runs'][-1]['plan_status']}")

# 续跑（加分项）
d = load("examples/outputs/resume_after_failure.json")
if d:
    check("加分：失败步骤续跑最终完成", d["actual"]["outcome"] == "completed")
    check("加分：续跑复用已成功步骤", bool(d["actual"].get("reused_steps")),
          f"reused={d['actual'].get('reused_steps')}")
    check("加分：首次执行如实记录失败", any(s["status"] == "failed" for s in d["runs"][0]["steps"]))

# ---------------- 所有用例都不声称已发布 ----------------
all_ok = True
detail = ""
for f in (ROOT / "examples" / "outputs").glob("*.json"):
    d = json.loads(f.read_text(encoding="utf-8"))
    if "actual" not in d:
        continue
    if d["actual"].get("published") is not False:
        all_ok = False
        detail = f.name
check("边界：所有用例 published=false", all_ok, detail)

# ---------------- ReAct 决策可核对性 ----------------
decided_ok = True
decided_detail = ""
for f in traces:
    entries = [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
    for e in entries:
        if e.get("action") == "tool" and not e.get("decided_by"):
            decided_ok = False
            decided_detail = f.name
            break
check("ReAct：每个工具步骤都有决策依据 decided_by", decided_ok, decided_detail)

# 不同观测 -> 不同动作：超限用例必须出现两次 estimate_cost
if traces:
    tp = ROOT / "examples" / "traces" / "budget_overrun.jsonl"
    if tp.exists():
        entries = [json.loads(x) for x in tp.read_text(encoding="utf-8").splitlines() if x.strip()]
        bys = [e.get("decided_by") or "" for e in entries]
        check("ReAct：后续步骤决策依据互不相同（观测驱动而非步号驱动）",
              len(set(bys)) >= len(bys) - 1, f"{bys}")

# ---------------- 测试 ----------------
try:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=300,
    )
    tail = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout else ""
    check("测试：pytest 全部通过", proc.returncode == 0, tail)
except Exception as exc:  # pragma: no cover
    check("测试：pytest 全部通过", False, str(exc))

# ---------------- 无密钥 ----------------
check("安全：仓库不包含 .env 文件", not has(".env"))

# ---------------- 输出 ----------------
width = max(len(n) for n, _, _ in results) + 2
failed = 0
def main() -> int:
    failed = 0
    for name, ok, detail in results:
        mark = "OK  " if ok else "FAIL"
        if not ok:
            failed += 1
        line = f"[{mark}] {name.ljust(width)}"
        if detail:
            line += f"  ({detail})"
        print(line)

    print()
    print(f"共 {len(results)} 项检查，通过 {len(results) - failed} 项，失败 {failed} 项。")
    if failed:
        print("存在未满足项，请对照上表 FAIL 行修复。")
    else:
        print("全部检查通过。")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
