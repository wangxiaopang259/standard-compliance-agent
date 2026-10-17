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
    updated_at    TEXT DEFAULT '',
    std_name      TEXT DEFAULT '',
    trace_path    TEXT DEFAULT '',
    tokens        TEXT DEFAULT '{}',
    duration_ms   INTEGER DEFAULT 0,
    finished_at   TEXT DEFAULT ''
);
"""

# 老库补列（CREATE TABLE IF NOT EXISTS 不会给已存在的表加列）
_MIGRATE_COLS = [
    ("std_name", "TEXT DEFAULT ''"),
    ("trace_path", "TEXT DEFAULT ''"),
    ("tokens", "TEXT DEFAULT '{}'"),
    ("duration_ms", "INTEGER DEFAULT 0"),
    ("finished_at", "TEXT DEFAULT ''"),
]


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(settings.db_path, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with _LOCK, _conn() as c:
        c.executescript(_SCHEMA)
        # 老库补列
        existing = {r["name"] for r in c.execute("PRAGMA table_info(jobs)").fetchall()}
        for col, typ in _MIGRATE_COLS:
            if col not in existing:
                c.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typ}")
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
    for k in ("regime", "source", "logs", "tokens"):
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
    for k in ("regime", "logs", "source", "tokens"):
        try:
            if k == "logs":
                d[k] = json.loads(d[k]) if d[k] else []
            else:
                d[k] = json.loads(d[k]) if d[k] else {}
        except Exception:
            pass
    return d


def list_jobs(limit: int = 50, offset: int = 0, status: str | None = None,
              q: str | None = None) -> dict:
    """历史记录分页查询。返回 {total, items}。"""
    where, params = [], []
    if status:
        where.append("status=?")
        params.append(status)
    if q:
        where.append("(std_name LIKE ? OR source LIKE ? OR job_id LIKE ?)")
        params.extend([f"%{q}%"] * 3)
    w = ("WHERE " + " AND ".join(where)) if where else ""
    with _LOCK, _conn() as c:
        total = c.execute(f"SELECT COUNT(*) AS n FROM jobs {w}", params).fetchone()["n"]
        rows = c.execute(
            "SELECT job_id,status,stage,std_name,slug,duration_ms,tokens,"
            "created_at,updated_at,finished_at,result_path,source "
            f"FROM jobs {w} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [int(limit), int(offset)]).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        for k in ("tokens", "source"):
            try:
                d[k] = json.loads(d[k]) if d[k] else {}
            except Exception:
                pass
        items.append(d)
    return {"total": total, "items": items}
