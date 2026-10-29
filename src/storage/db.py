"""SQLite 方案存储 + 步骤状态持久化。

两张表：
1. `plans` —— 已保存的内容活动方案（幂等键防重复）
2. `steps` —— 每次执行的步骤状态（支持失败步骤续跑）

幂等
----
`plan_key = topic|assets|cost` 唯一。同样的方案重复保存只产生一份。
「重复执行」验收点依赖此约束。

步骤状态
--------
步骤以 (run_key, step_name) 唯一，记录 status/result/updated_at。
续跑时读取已有成功步骤的结果直接复用，失败步骤重试。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id           TEXT UNIQUE NOT NULL,
    plan_key          TEXT UNIQUE NOT NULL,
    topic             TEXT NOT NULL,
    assets            TEXT NOT NULL,
    script            TEXT,
    total_cost        INTEGER NOT NULL,
    total_duration_min INTEGER NOT NULL,
    payload           TEXT,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS steps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_key     TEXT NOT NULL,
    step_name   TEXT NOT NULL,
    status      TEXT NOT NULL,
    result      TEXT,
    updated_at  TEXT NOT NULL,
    UNIQUE(run_key, step_name)
);

CREATE TABLE IF NOT EXISTS run_meta (
    run_key     TEXT PRIMARY KEY,
    meta        TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


def plan_key(topic: str, assets: list[str], total_cost: int) -> str:
    raw = "|".join([topic.strip(), ",".join(sorted(a.strip() for a in assets)), str(total_cost)])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class PlanStore:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _cursor(self):
        """打开连接并在退出时显式关闭。

        注意：`with sqlite3.connect(...) as conn` 只管理事务，不关闭连接，
        长期运行会泄漏文件句柄（Windows 上导致库文件无法删除）。
        """
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._cursor() as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    # ------------------------------------------------------------------
    # 方案
    # ------------------------------------------------------------------
    def save_plan(self, record: dict[str, Any]) -> dict[str, Any]:
        """保存方案。重复保存相同方案返回 duplicate，不产生第二份。"""
        key = plan_key(record["topic"], record["assets"], record["total_cost"])
        existing = self.get_by_key(key)
        if existing is not None:
            return {
                "status": "duplicate",
                "plan_id": existing["plan_id"],
                "detail": "相同方案已存在，未重复创建",
            }

        payload = {
            "topic": record["topic"],
            "assets": record["assets"],
            "script": record.get("script", ""),
            "total_cost": record["total_cost"],
            "total_duration_min": record.get("total_duration_min", 0),
            "published": False,  # 永不发布
        }
        pid = f"P-{record['topic'][:12]}-{key[:8]}"

        with self._cursor() as conn:
            try:
                conn.execute(
                    """INSERT INTO plans
                       (plan_id, plan_key, topic, assets, script, total_cost,
                        total_duration_min, payload, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        pid,
                        key,
                        record["topic"],
                        ",".join(record["assets"]),
                        record.get("script", ""),
                        int(record["total_cost"]),
                        int(record.get("total_duration_min") or 0),
                        json.dumps(payload, ensure_ascii=False),
                        record.get("created_at", ""),
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                row = self.get_by_key(key)
                if row is not None:
                    return {
                        "status": "duplicate",
                        "plan_id": row["plan_id"],
                        "detail": f"唯一约束拦截重复写入: {exc}",
                    }
                raise

        return {"status": "saved", "plan_id": pid, "detail": "方案已写入本地方案库"}

    def get_by_key(self, key: str) -> dict | None:
        with self._cursor() as conn:
            row = conn.execute("SELECT * FROM plans WHERE plan_key = ?", (key,)).fetchone()
        return dict(row) if row else None

    def list_plans(self) -> list[dict]:
        with self._cursor() as conn:
            rows = conn.execute("SELECT * FROM plans ORDER BY id").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["assets"] = d["assets"].split(",") if d["assets"] else []
            out.append(d)
        return out

    def count(self) -> int:
        with self._cursor() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0])

    # ------------------------------------------------------------------
    # 步骤状态
    # ------------------------------------------------------------------
    def save_step(self, run_key: str, step_name: str, status: str, result: Any = None) -> None:
        """记录步骤状态。status: pending / running / succeeded / failed / skipped"""
        payload = json.dumps(result, ensure_ascii=False, default=str) if result is not None else None
        with self._cursor() as conn:
            conn.execute(
                """INSERT INTO steps (run_key, step_name, status, result, updated_at)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(run_key, step_name) DO UPDATE SET
                     status=excluded.status, result=excluded.result, updated_at=excluded.updated_at""",
                (run_key, step_name, status, payload, _now_iso()),
            )
            conn.commit()

    def get_steps(self, run_key: str) -> list[dict]:
        with self._cursor() as conn:
            rows = conn.execute(
                "SELECT * FROM steps WHERE run_key = ? ORDER BY id", (run_key,)
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["result"] = json.loads(d["result"]) if d["result"] else None
            out.append(d)
        return out

    def get_step(self, run_key: str, step_name: str) -> dict | None:
        with self._cursor() as conn:
            row = conn.execute(
                "SELECT * FROM steps WHERE run_key = ? AND step_name = ?", (run_key, step_name)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["result"] = json.loads(d["result"]) if d["result"] else None
        return d

    def clear_steps(self, run_key: str) -> None:
        with self._cursor() as conn:
            conn.execute("DELETE FROM steps WHERE run_key = ?", (run_key,))
            conn.commit()

    # ------------------------------------------------------------------
    # 运行级元数据（续跑时需要继承的状态）
    # ------------------------------------------------------------------
    def save_meta(self, run_key: str, meta: dict[str, Any]) -> None:
        """保存运行级元数据（如失败计数），供续跑时恢复。"""
        payload = json.dumps(meta, ensure_ascii=False, default=str)
        with self._cursor() as conn:
            conn.execute(
                """INSERT INTO run_meta (run_key, meta, updated_at) VALUES (?,?,?)
                   ON CONFLICT(run_key) DO UPDATE SET
                     meta=excluded.meta, updated_at=excluded.updated_at""",
                (run_key, payload, _now_iso()),
            )
            conn.commit()

    def get_meta(self, run_key: str) -> dict[str, Any]:
        with self._cursor() as conn:
            row = conn.execute("SELECT meta FROM run_meta WHERE run_key = ?", (run_key,)).fetchone()
        if not row:
            return {}
        try:
            return json.loads(row["meta"])
        except (json.JSONDecodeError, TypeError):
            return {}


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
