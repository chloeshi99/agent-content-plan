"""命令行入口。

运行方式（题目要求 CLI / HTTP API / Notebook 三选一 -> 本项目选 CLI）：

    python -m src.cli --case all
    python -m src.cli --case budget_overrun --json
    python -m src.cli --topic "校园开放日" --budget 20 --deadline 2026-11-20
    python -m src.cli --list-plans
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import Config
from src.runner import PlanRunner, load_cases, run_case, summarize

TRACE_DIR = Path(__file__).resolve().parents[1] / "examples" / "traces"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.cli",
        description="内容任务规划 Agent —— 在预算与截止时间内完成一份内容活动方案",
    )
    p.add_argument("--case", help="运行具名用例；`all` 运行全部用例")
    p.add_argument("--topic", help="活动主题")
    p.add_argument("--budget", type=int, help="预算（模拟单位）")
    p.add_argument("--deadline", help="截止时间，如 2026-11-20 或 2026-11-20T18:00")
    p.add_argument("--resume", action="store_true", help="续跑：复用已成功的步骤")
    p.add_argument("--run-key", default="cli", help="运行标识，用于步骤状态隔离")
    p.add_argument("--list-plans", action="store_true", help="列出已保存的方案")
    p.add_argument("--trace-dir", help="把运行记录写入该目录")
    p.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config = Config.load()

    # ---- 查看已保存方案 ----
    if args.list_plans:
        runner = PlanRunner(config)
        plans = runner.list_plans()
        if args.json:
            print(json.dumps(plans, ensure_ascii=False, indent=2))
        else:
            if not plans:
                print("方案库为空")
            for p in plans:
                print(f"[{p['plan_id']}] topic={p['topic']} assets={p['assets']} "
                      f"cost={p['total_cost']} duration={p['total_duration_min']}min")
            print(f"\n方案总数: {len(plans)}")
        return 0

    # ---- 具名用例 ----
    if args.case:
        trace_dir = Path(args.trace_dir) if args.trace_dir else TRACE_DIR
        if args.case == "all":
            cases = load_cases(config)["cases"]
            results = []
            for name in cases:
                # 每个用例独立方案库，避免用例间污染（尤其是 resume / duplicate）
                case_db = config.data_dir / f".tmp_cli_{name}.sqlite3"
                if case_db.exists():
                    case_db.unlink()
                case_cfg = Config.load()
                case_cfg.db_path = case_db
                item = run_case(name, config=case_cfg, trace_dir=trace_dir)
                results.append(item)
                final = item["final"]
                print(f"[{name}] {summarize(final)}")
                print(f"    期望: {item['expect']}")
                if case_db.exists():
                    case_db.unlink()
            saved = sum(1 for r in results if r["final"].get("plan_status") in ("saved", "duplicate"))
            print(f"\n完成用例: {len(results)}；其中成功保存过方案的用例: {saved}")
            if args.json:
                print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
            return 0

        item = run_case(args.case, config=config, trace_dir=trace_dir)
        final = item["final"]
        if args.json:
            print(json.dumps(item, ensure_ascii=False, indent=2, default=str))
        else:
            print(json.dumps(final, ensure_ascii=False, indent=2, default=str))
        return 0

    # ---- 自由请求 ----
    if args.topic:
        runner = PlanRunner(config)
        trace_path = None
        if args.trace_dir:
            trace_path = Path(args.trace_dir) / "custom.jsonl"
            if trace_path.exists():
                trace_path.unlink()
        result = runner.run(
            topic=args.topic,
            budget=args.budget,
            deadline=args.deadline,
            run_key=args.run_key,
            trace_path=trace_path,
            resume=args.resume,
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0

    _build_parser().print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
