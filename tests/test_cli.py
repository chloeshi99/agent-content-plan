"""CLI 入口冒烟测试。"""

from __future__ import annotations

from src.cli import main


def test_cli_case_missing_budget(capsys) -> None:
    code = main(["--case", "missing_budget", "--json"])
    assert code == 0
    out = capsys.readouterr().out
    assert "incomplete" in out


def test_cli_list_plans(capsys) -> None:
    code = main(["--list-plans"])
    assert code == 0
    out = capsys.readouterr().out
    assert "方案" in out
