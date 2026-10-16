"""MinerU（天枢）解析客户端 — 真实接口。

天枢 http://10.100.100.99:8000，无需鉴权；「提交 → 轮询 → 取结果」三步：
  POST /api/v1/tasks/submit            (multipart) → {task_id}
  GET  /api/v1/tasks/{id}              状态 pending/processing/completed/failed
  GET  /api/v1/tasks/{id}/data         {"data":{"markdown":{"content":...},"content_list":{"content":[...]}}}
  GET  /api/v1/health
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import httpx

from core.config import settings

_CTYPES = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".txt": "text/plain", ".html": "text/html", ".htm": "text/html",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".bmp": "image/bmp", ".tif": "image/tiff", ".tiff": "image/tiff",
    ".wps": "application/octet-stream",
}


class MinerUError(RuntimeError):
    pass


def _ctype(path: str | Path) -> str:
    return _CTYPES.get(Path(path).suffix.lower(), "application/octet-stream")


def _prepare_path(path: str | Path) -> tuple[str, str | None]:
    """MinerU 不支持老式 .doc/.wps，用 macOS textutil 转 .docx。"""
    ext = Path(path).suffix.lower()
    if ext not in (".doc", ".wps") or not shutil.which("textutil"):
        return str(path), None
    tmp = tempfile.mktemp(suffix=".docx")
    try:
        subprocess.run(["textutil", "-convert", "docx", "-output", tmp, str(path)],
                       capture_output=True, timeout=180)
        if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
            return tmp, tmp
    except Exception:
        pass
    return str(path), (tmp if os.path.exists(tmp) else None)


def available(timeout: int = 5) -> bool:
    if not settings.mineru_enabled:
        return False
    try:
        r = httpx.get(f"{settings.mineru_url}/api/v1/health", timeout=timeout)
        return r.status_code == 200 and r.json().get("status") in ("healthy", "ok")
    except Exception:
        return False


def _submit(path: str | Path, tries: int = 3) -> str:
    last = None
    for _ in range(tries):
        try:
            with open(path, "rb") as fh:
                files = {"file": (os.path.basename(path), fh, _ctype(path))}
                data = {"backend": "pipeline", "lang": "ch", "method": "auto",
                        "formula_enable": "true", "table_enable": "true", "priority": "100"}
                r = httpx.post(f"{settings.mineru_url}/api/v1/tasks/submit",
                               files=files, data=data, timeout=900)
            r.raise_for_status()
            return r.json()["task_id"]
        except Exception as e:
            last = e
            time.sleep(8)
    raise MinerUError(f"MinerU 提交失败: {last}")


def _wait(task_id: str, timeout: int | None = None) -> dict:
    timeout = timeout or settings.mineru_timeout
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            j = httpx.get(f"{settings.mineru_url}/api/v1/tasks/{task_id}", timeout=30).json()
        except Exception:
            time.sleep(3)
            continue
        if j.get("status") in ("completed", "failed"):
            return j
        time.sleep(3)
    return {"status": "timeout", "task_id": task_id}


def convert_to_md(path: str | Path) -> tuple[str, list, dict]:
    """返回 (markdown, content_list, meta)。失败抛 MinerUError。"""
    submit_path, tmp = _prepare_path(path)
    try:
        tid = _submit(submit_path)
        j = _wait(tid)
        if j.get("status") != "completed":
            raise MinerUError(f"MinerU 解析未完成: {j.get('status')}")
        d = httpx.get(f"{settings.mineru_url}/api/v1/tasks/{tid}/data",
                      timeout=180).json().get("data", {})
        md = (d.get("markdown") or {}).get("content", "") or ""
        cl = (d.get("content_list") or {}).get("content", []) or []
        pages = (max([x.get("page_idx", 0) for x in cl], default=0) + 1) if cl else 0
        meta = {"task_id": tid, "pages": pages, "blocks": len(cl),
                "md_chars": len(md), "engine": "mineru"}
        return md, cl, meta
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


def parse_file(path: str | Path, timeout: int | None = None) -> str:
    """便捷方法：只要 Markdown。"""
    md, _cl, _meta = convert_to_md(path)
    return md


def quality_check(markdown: str) -> dict:
    """粗校验：字符数 + 条文号命中（两种体例：'第X.X条' 与 'X.X.X 标题'）。"""
    md = markdown or ""
    chars = len(md)
    clauses = (len(re.findall(r"第\s*\d+(?:\.\d+)*\s*条", md))
               + len(re.findall(r"(?m)^\s*#{0,6}\s*\d+\.\d+(?:\.\d+)*\s", md)))
    return {"chars": chars, "clause_hits": clauses, "ok": chars > 500 and clauses > 0}


def parse_any(path) -> str:
    """文本类(.md/.txt)直读；其余走 MinerU。"""
    p = Path(path)
    if p.suffix.lower() in (".md", ".txt"):
        return p.read_text(encoding="utf-8")
    return parse_file(p)
