"""提交前自检：扫描仓库中是否包含真实密钥或隐私数据。

题目要求「不提交真实密钥或隐私数据」。本脚本用于提交前自查。

用法：
    python -m scripts.check_secrets
退出码：0 = 未发现风险；1 = 发现疑似泄漏。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 常见密钥格式
PATTERNS = [
    (r"sk-[A-Za-z0-9]{16,}", "OpenAI 风格密钥"),
    (r"ghp_[A-Za-z0-9]{20,}", "GitHub Personal Access Token"),
    (r"AKIA[0-9A-Z]{16}", "AWS Access Key"),
    (r"AIza[0-9A-Za-z_\-]{30,}", "Google API Key"),
    (r"xox[baprs]-[0-9A-Za-z\-]{10,}", "Slack Token"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "私钥"),
]

# 赋值形式的密钥（等号右侧有实际值且非空）
ASSIGN = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password|passwd|access[_-]?key)\b\s*[:=]\s*[\"']?([^\s\"'#]{8,})"
)

# 明确安全的上下文：这些写法是从环境变量取值，不是硬编码
SAFE_CONTEXT = re.compile(
    r"(?i)(env\.get|environ|getenv|os\.environ|config\.|self\.|\.llm_api_key|api_key=api_key|"
    r"api_key=self|api_key=config|api_key=client)"
)

ALLOWED = {
    "", "none", "null", "your_api_key", "changeme", "xxx", "example",
    "placeholder", "dummy", "fake", "test_key", "your-key-here",
    "sk-xxx", "sk-yourkey",
}

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules"}
SKIP_EXT = {".sqlite3", ".pyc", ".pyo", ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".ico"}


def main() -> int:
    hits: list[str] = []

    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in SKIP_EXT:
            continue
        if path.name == ".env":
            hits.append(f"{path.relative_to(ROOT)}: 存在 .env 文件（不应提交）")
            continue

        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        for pattern, label in PATTERNS:
            for m in re.finditer(pattern, text):
                hits.append(f"{path.relative_to(ROOT)}: 疑似{label}")

        for m in ASSIGN.finditer(text):
            value = m.group(2).strip().strip("\"'")
            if value.lower() in ALLOWED:
                continue
            line_start = text.rfind("\n", 0, m.start()) + 1
            line = text[line_start: text.find("\n", m.end()) if text.find("\n", m.end()) > 0 else len(text)]
            if SAFE_CONTEXT.search(line):
                continue
            hits.append(f"{path.relative_to(ROOT)}: 疑似硬编码密钥 -> {m.group(0)[:60]}")

    if hits:
        print("发现疑似密钥/隐私内容：")
        for h in hits:
            print(f"  - {h}")
        print("\n请移除后再提交。")
        return 1

    print("未发现真实密钥或隐私数据。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
