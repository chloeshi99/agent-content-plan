"""查看运行记录（trace）的可读摘要。

用法：
    python -m scripts.show_trace examples/traces/budget_overrun.jsonl
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1
    path = Path(argv[0])
    if not path.exists():
        print(f"文件不存在: {path}")
        return 1

    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        head = f"[{e.get('step')}] {e.get('action')}"
        if e.get("tool"):
            head += f" {e['tool']}"
        print(head)
        if e.get("args"):
            print(f"    args        : {json.dumps(e['args'], ensure_ascii=False)}")
        if e.get("decided_by"):
            print(f"    decided_by  : {e['decided_by']}")
        if e.get("rationale"):
            print(f"    rationale   : {e['rationale']}")
        if e.get("tool_error"):
            print(f"    tool_error  : {e['tool_error']}")
        summary = e.get("result_summary") or ""
        if summary:
            if len(summary) > 300:
                summary = summary[:300] + "...(truncated)"
            print(f"    observation : {summary}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
