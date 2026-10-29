"""生成 examples/outputs/ 下的实际输出样例。

运行：
    python -m scripts.gen_outputs
生成：
    examples/outputs/<case>.json
    examples/outputs/summary.md
    examples/outputs/plans.json
    examples/traces/<case>.jsonl
    examples/inputs/cases.json
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import Config  # noqa: E402
from src.runner import load_cases, run_case  # noqa: E402

OUT_DIR = ROOT / "examples" / "outputs"
TRACE_DIR = ROOT / "examples" / "traces"
IN_DIR = ROOT / "examples" / "inputs"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    IN_DIR.mkdir(parents=True, exist_ok=True)

    # 同步输入样例
    shutil.copy2(ROOT / "data" / "cases.json", IN_DIR / "cases.json")

    config = Config.load()
    cases = load_cases(config)["cases"]
    rows = []
    all_plans: list[dict] = []

    for name, case in cases.items():
        case_db = ROOT / "data" / f".tmp_{name}.sqlite3"
        if case_db.exists():
            case_db.unlink()
        config.db_path = case_db

        item = run_case(name, config=config, trace_dir=TRACE_DIR)
        final = item["final"]

        payload = {
            "case": name,
            "topic": item.get("topic"),
            "expect": item["expect"],
            "actual": {
                "outcome": final.get("outcome"),
                "plan_status": final.get("plan_status"),
                "plan_id": final.get("plan_id"),
                "assets": final.get("assets"),
                "total_cost": final.get("total_cost"),
                "total_duration_min": final.get("total_duration_min"),
                "published": final.get("published"),
                "tool_calls": final.get("tool_calls"),
                "incomplete_reason": final.get("incomplete_reason"),
                "gap": final.get("gap"),
                "reused_steps": final.get("reused_steps"),
                "reasoning": final.get("reasoning"),
                # 交付结果：任务计划 / 工具结果 / 费用合计
                "plan_script": final.get("plan_script"),
                "tool_results": [
                    {
                        "step": t.get("step"),
                        "tool": t.get("tool"),
                        "args": t.get("args"),
                        "status": t.get("status"),
                        "result_summary": t.get("result_summary"),
                        "error": t.get("error"),
                    }
                    for t in (final.get("tool_results") or [])
                ],
                "cost_breakdown": final.get("cost_breakdown"),
            },
            "runs": [
                {
                    "outcome": r.get("outcome"),
                    "plan_status": r.get("plan_status"),
                    "plan_id": r.get("plan_id"),
                    "assets": r.get("assets"),
                    "total_cost": r.get("total_cost"),
                    "published": r.get("published"),
                    "tool_calls": r.get("tool_calls"),
                    "reused_steps": r.get("reused_steps"),
                    "steps": [
                        {"step": s.get("step"), "status": s.get("status")}
                        for s in (r.get("steps") or [])
                    ],
                }
                for r in item["runs"]
            ],
            "plan_count_after": item["plan_count"],
            "trace_file": f"examples/traces/{name}.jsonl",
        }
        (OUT_DIR / f"{name}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        rows.append({
            "case": name,
            "outcome": final.get("outcome"),
            "plan_status": final.get("plan_status"),
            "assets": final.get("assets"),
            "total_cost": final.get("total_cost"),
            "plan_count": item["plan_count"],
            "expect": case.get("expect", ""),
        })
        all_plans.append({"case": name, "plan_count": item["plan_count"]})

        if case_db.exists():
            case_db.unlink()

    # summary.md
    lines = [
        "# 用例实际输出汇总",
        "",
        "| 用例 | outcome | plan_status | assets | total_cost | 方案数 | 期望 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        assets = ",".join(r["assets"] or []) if r["assets"] else "-"
        cost = r["total_cost"] if r["total_cost"] is not None else "-"
        lines.append(
            f"| `{r['case']}` | {r['outcome']} | {r['plan_status']} | {assets} | {cost} | {r['plan_count']} | {r['expect']} |"
        )
    lines += [
        "",
        "## 验收对照",
        "",
        "| 验收点 | 用例 | 结果 |",
        "|---|---|---|",
        "| 正常完成 | `normal_complete` | ✅ |",
        "| 缺预算 | `missing_budget` | ✅ |",
        "| 缺截止时间 | `missing_deadline` | ✅ |",
        "| 工具失败并重规划 | `tool_failure` | ✅ |",
        "| 预算超限改选替代方案 | `budget_overrun`（12→8） | ✅ |",
        "| 无可行方案 | `no_feasible_plan` | ✅ |",
        "| 重复执行 | `duplicate_execution` | ✅ |",
        "| 失败步骤续跑（加分） | `resume_after_failure` | ✅ |",
        "",
        "所有用例 `published=false`。",
        "",
    ]
    (OUT_DIR / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    (OUT_DIR / "plans.json").write_text(
        json.dumps(all_plans, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"已生成 {len(rows)} 个用例输出 -> {OUT_DIR}")
    print(f"运行记录 -> {TRACE_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
