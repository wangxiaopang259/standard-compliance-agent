"""作业状态存储（SQLite）。

表 jobs：job_id / status / stage / stage_index / total_stages /
regime(json) / logs(json) / result_path / slug / source(json) / 时间戳。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone

from core.config import settings

_LOCK = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id        TEXT PRIMARY KEY,
    status        TEXT DEFAULT 'queued',
    stage         TEXT DEFAULT '',
    stage_index   INTEGER DEFAULT 0,
    total_stages  INTEGER DEFAULT 7,
    regime        TEXT DEFAULT '{}',
    logs          TEXT DEFAULT '[]',
    result_path   TEXT DEFAULT '',
    slug          TEXT DEFAULT '',
    source        TEXT DEFAULT '{}',
    created_at    TEXT DEFAULT '',
    updated_at    TEXT DEFAULT ''
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(settings.db_path, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with _LOCK, _conn() as c:
        c.executescript(_SCHEMA)
        # 启动时把未完成任务标记中断（断点续跑另见 orchestrator）
        c.execute("UPDATE jobs SET status='interrupted' "
                  "WHERE status NOT IN ('completed','failed','interrupted')")
        c.commit()


def create_job(job_id: str, source: dict) -> None:
    with _LOCK, _conn() as c:
        c.execute("INSERT INTO jobs (job_id, status, stage, source, created_at, updated_at) "
                  "VALUES (?,?,?,?,?,?)",
                  (job_id, "queued", "", json.dumps(source, ensure_ascii=False), _now(), _now()))
        c.commit()


def update_job(job_id: str, **fields) -> None:
    if not fields:
        return
    for k in ("regime", "source", "logs"):
        if k in fields and not isinstance(fields[k], str):
            fields[k] = json.dumps(fields[k], ensure_ascii=False)
    fields["updated_at"] = _now()
    sets = ", ".join(f"{k}=?" for k in fields)
    with _LOCK, _conn() as c:
        c.execute(f"UPDATE jobs SET {sets} WHERE job_id=?", list(fields.values()) + [job_id])
        c.commit()


def append_log(job_id: str, message: str) -> None:
    with _LOCK, _conn() as c:
        row = c.execute("SELECT logs FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        logs = json.loads(row["logs"]) if row else []
        logs.append({"ts": _now(), "msg": message})
        c.execute("UPDATE jobs SET logs=?, updated_at=? WHERE job_id=?",
                  (json.dumps(logs, ensure_ascii=False), _now(), job_id))
        c.commit()


def get_job(job_id: str) -> dict | None:
    with _LOCK, _conn() as c:
        row = c.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    for k in ("regime", "logs", "source"):
        try:
            d[k] = json.loads(d[k]) if d[k] else ({} if k != "logs" else [])
        except Exception:
            pass
    return d


def list_jobs(limit: int = 50) -> list[dict]:
    with _LOCK, _conn() as c:
        rows = c.execute("SELECT job_id,status,stage,slug,created_at,updated_at "
                         "FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]
