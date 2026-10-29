"""pytest 全局夹具。

每个测试使用**独立**的临时方案库，避免用例之间互相污染断言。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import Config  # noqa: E402
from src.runner import PlanRunner  # noqa: E402


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    """指向临时方案库的配置（数据目录仍用仓库内 data/）。"""
    cfg = Config.load()
    cfg.db_path = tmp_path / "plans.sqlite3"
    cfg.offline = True
    return cfg


@pytest.fixture()
def runner(config: Config) -> PlanRunner:
    return PlanRunner(config)


@pytest.fixture()
def catalog(config: Config) -> dict:
    return config.catalog
