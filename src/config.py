"""配置加载。

配置优先级：环境变量 > .env 文件 > 默认值。
不配置任何变量也能完整运行（离线 Mock 决策通道）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"

# 固定的「当前时间」，保证耗时与截止时间计算可复现
DEFAULT_NOW = "2026-10-29T09:00:00"


def _load_dotenv(path: Path) -> dict[str, str]:
    """极简 .env 解析（不引入第三方依赖）。"""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip("\"'")
    return env


@dataclass
class Config:
    root: Path = ROOT
    data_dir: Path = DATA_DIR
    db_path: Path = DATA_DIR / "plans.sqlite3"
    trace_dir: Path = ROOT / "examples" / "traces"

    # 决策通道
    offline: bool = True
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"
    llm_temperature: float = 0.0

    # 执行约束
    max_tool_calls: int = 10
    max_plan_attempts: int = 3

    # 时间基准（固定值，保证可复现）
    now: str = DEFAULT_NOW

    extra: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    @classmethod
    def load(cls) -> "Config":
        dotenv = _load_dotenv(ROOT / ".env")

        def get(key: str, default: str = "") -> str:
            return os.environ.get(key) or dotenv.get(key, default)

        api_key = get("LLM_API_KEY")
        offline_raw = get("AGENT_OFFLINE", "").strip().lower()
        if offline_raw in ("1", "true", "yes"):
            offline = True
        elif offline_raw in ("0", "false", "no"):
            offline = False
        else:
            offline = not bool(api_key)

        return cls(
            db_path=Path(get("PLAN_DB_PATH") or (DATA_DIR / "plans.sqlite3")),
            trace_dir=Path(get("TRACE_DIR") or (ROOT / "examples" / "traces")),
            offline=offline,
            llm_api_key=api_key,
            llm_base_url=get("LLM_BASE_URL", "https://api.deepseek.com/v1"),
            llm_model=get("LLM_MODEL", "deepseek-chat"),
            llm_temperature=float(get("LLM_TEMPERATURE", "0") or 0),
            max_tool_calls=int(get("MAX_TOOL_CALLS", "10") or 10),
            max_plan_attempts=int(get("MAX_PLAN_ATTEMPTS", "3") or 3),
            now=get("AGENT_NOW", DEFAULT_NOW),
        )

    # ------------------------------------------------------------------
    def load_json(self, name: str) -> dict[str, Any]:
        path = self.data_dir / name
        if not path.exists():
            raise FileNotFoundError(f"数据文件不存在: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    @property
    def catalog(self) -> dict[str, Any]:
        return self.load_json("catalog.json")
