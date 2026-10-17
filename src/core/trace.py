"""作业 trace 记录器：阶段耗时、LLM 调用链、token 消耗。

产物是一个可直接落盘/下载的 dict：
{
  job_id, meta, started_at, finished_at, duration_ms,
  totals: {calls, prompt_tokens, completion_tokens, total_tokens},
  spans: [{name, start, end, duration_ms, attrs, error?}],
  llm_calls: [{ts, tag, model, duration_ms, attempt, ok,
               prompt_tokens, completion_tokens, total_tokens,
               request_chars, response_chars, finish_reason, error?}],
  events: [{ts, name, ...}]
}
"""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


class TraceRecorder:
    def __init__(self, job_id: str, meta: dict | None = None) -> None:
        self.job_id = job_id
        self.meta = meta or {}
        self.started_at = _now_iso()
        self._t0 = time.time()
        self.spans: list[dict] = []
        self.llm_calls: list[dict] = []
        self.events: list[dict] = []
        self._stack: list[dict] = []

    @contextmanager
    def span(self, name: str, **attrs):
        """记录一个阶段/子步骤的起止与耗时。"""
        s = {"name": name, "start": _now_iso(), "_t": time.time(), "attrs": attrs}
        self._stack.append(s)
        try:
            yield s
        except Exception as e:  # noqa: BLE001
            s["error"] = f"{type(e).__name__}: {e}"
            raise
        finally:
            s["end"] = _now_iso()
            s["duration_ms"] = round((time.time() - s["_t"]) * 1000, 1)
            s.pop("_t", None)
            rec = {"name": s["name"], "start": s["start"], "end": s["end"],
                   "duration_ms": s["duration_ms"]}
            if s.get("attrs"):
                rec["attrs"] = s["attrs"]
            if s.get("error"):
                rec["error"] = s["error"]
            self.spans.append(rec)
            self._stack.pop()

    def event(self, name: str, **attrs) -> None:
        self.events.append({"ts": _now_iso(), "name": name, **attrs})

    def llm_call(self, rec: dict) -> None:
        """由 AIClient 在每次 LLM 调用（含重试）后回调。"""
        item = dict(rec)
        item["ts"] = _now_iso()
        self.llm_calls.append(item)

    @property
    def totals(self) -> dict:
        prompt = sum(int(c.get("prompt_tokens") or 0) for c in self.llm_calls)
        completion = sum(int(c.get("completion_tokens") or 0) for c in self.llm_calls)
        return {
            "calls": len(self.llm_calls),
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        }

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "meta": self.meta,
            "started_at": self.started_at,
            "finished_at": _now_iso(),
            "duration_ms": round((time.time() - self._t0) * 1000, 1),
            "totals": self.totals,
            "spans": self.spans,
            "llm_calls": self.llm_calls,
            "events": self.events,
        }

    def write(self, path: str | Path) -> str:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return str(p)
