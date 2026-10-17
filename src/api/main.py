"""FastAPI — 贯标核验智能体（端口 5013）。

接口一览
--------
上传 / 启动
  POST /api/v1/compliance/upload            上传待审查文件（+可选标准），返回 upload_id
  POST /api/v1/compliance/jobs              用 upload_id 或直接文件启动贯标（异步，返回 job_id）
  POST /api/v1/compliance                   兼容旧用法：上传+启动一步完成

查询
  GET  /api/v1/compliance/status/{job_id}   进度（stage / percent / logs / tokens / duration）
  GET  /api/v1/compliance/result/{job_id}   结果（report_md + compliance + trace 摘要）
  GET  /api/v1/compliance/jobs              历史记录（分页 / 状态过滤 / 关键词）
  GET  /api/v1/compliance/jobs/{job_id}     单条记录详情
  GET  /api/v1/compliance/history           历史记录别名

下载
  GET  /api/v1/compliance/download/{job_id}/report     贯标核查报告（.md）
  GET  /api/v1/compliance/download/{job_id}/result     结构化结果（compliance.json）
  GET  /api/v1/compliance/download/{job_id}/trace      完整 trace（trace.json）
  GET  /api/v1/compliance/download/{job_id}/original   上传的待审查原文
  GET  /api/v1/compliance/download/{job_id}/standard   上传的标准原文（如有）
  GET  /api/v1/compliance/download/{job_id}            兼容旧用法：报告
"""
from __future__ import annotations

import json
import mimetypes
import shutil
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import settings
from core import store
from core.orchestrator import run_job, new_job_id

_EXECUTOR = ThreadPoolExecutor(max_workers=4)
_ALLOWED = (".pdf", ".doc", ".docx", ".md", ".txt", ".png", ".jpg", ".jpeg", ".tif", ".tiff")


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init_db()
    yield


app = FastAPI(title="贯标核验智能体 API", version="0.2.0", lifespan=lifespan)


# ── 前端静态页（与后端同端口）─────────────────────────────
_STATIC_DIR = Path(__file__).resolve().parents[2] / "static"
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
async def index():
    p = _STATIC_DIR / "index.html"
    if p.exists():
        return FileResponse(str(p))
    return {"service": "standard-compliance-agent", "docs": "/docs"}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "standard-compliance-agent",
            "mineru": settings.mineru_url, "llm": settings.llm_model, "env": settings.env}


# ------------------------------------------------------------------ helpers
def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _check_ext(f: UploadFile, label: str = "文件") -> None:
    fn = (f.filename or "").lower()
    if not fn.endswith(_ALLOWED):
        raise HTTPException(400, f"{label}类型不支持，仅支持 {_ALLOWED}")


def _save(f: UploadFile, d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / (f.filename or f"upload_{uuid.uuid4().hex[:6]}")
    with open(p, "wb") as out:
        shutil.copyfileobj(f.file, out)
    return p


def _read_meta(d: Path) -> dict:
    f = d / "meta.json"
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _percent(status: str, stage: str) -> int:
    table = {"queued": 0, "parsing": 15, "parse_draft": 15,
             "loading_standard": 35, "standard": 35,
             "judging": 65, "judge": 65, "done": 100, "completed": 100}
    return table.get(stage or "", table.get(status or "", 0))


def _job_view(job: dict) -> dict:
    d = dict(job)
    d["progress"] = {"stage": d.get("stage", ""),
                     "percent": _percent(d.get("status", ""), d.get("stage", "")),
                     "status": d.get("status", "")}
    return d


def _start(job_id: str, draft_path: Path, std_path: Optional[Path],
           spec_slug: str, std_name: str, source: dict) -> None:
    store.create_job(job_id, source)
    if std_name:
        store.update_job(job_id, std_name=std_name)
    _EXECUTOR.submit(run_job, job_id, draft_path, std_path, spec_slug, std_name)


# ------------------------------------------------------------------ upload
@app.post("/api/v1/compliance/upload", status_code=201)
async def upload(
    draft: UploadFile = File(...),
    standard: Optional[UploadFile] = File(None),
):
    """第一步：上传待审查文件（如设计方案），返回 upload_id，供后续启动使用。"""
    _check_ext(draft, "待审查文件")
    if standard is not None:
        _check_ext(standard, "标准文件")
    upload_id = "u-" + uuid.uuid4().hex[:12]
    d = settings.uploads_dir / upload_id
    dp = _save(draft, d)
    sp = _save(standard, d) if standard is not None else None
    meta = {"upload_id": upload_id, "draft": dp.name,
            "standard": sp.name if sp else None, "created_at": _now()}
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


# ------------------------------------------------------------------ start job
@app.post("/api/v1/compliance/jobs", status_code=202)
async def create_job(
    draft: Optional[UploadFile] = File(None),
    standard: Optional[UploadFile] = File(None),
    upload_id: str = Form(""),
    standard_upload_id: str = Form(""),
    spec_slug: str = Form(""),
    std_name: str = Form(""),
):
    """第二步：启动贯标（异步）。传 upload_id，或直接传 draft 文件。返回 job_id。"""
    if upload_id:
        ud = settings.uploads_dir / upload_id
        meta = _read_meta(ud)
        if not meta:
            raise HTTPException(404, f"upload_id 不存在: {upload_id}")
        draft_name = meta["draft"]
        std_name_file = meta.get("standard")
        if standard is not None:
            _check_ext(standard, "标准文件")
            std_name_file = None  # 用新上传的
    else:
        if draft is None:
            raise HTTPException(400, "需要 draft 文件或 upload_id")
        _check_ext(draft, "待审查文件")
        draft_name = None
        std_name_file = None

    job_id = new_job_id()
    jd = settings.uploads_dir / job_id
    jd.mkdir(parents=True, exist_ok=True)

    # 待审查文件
    if upload_id:
        draft_path = jd / draft_name
        shutil.copy2(settings.uploads_dir / upload_id / draft_name, draft_path)
    else:
        draft_path = _save(draft, jd)

    # 标准文件（优先级：显式上传 > standard_upload_id > upload 自带的 standard）
    std_path = None
    if standard is not None:
        std_path = _save(standard, jd)
    elif standard_upload_id:
        sm = _read_meta(settings.uploads_dir / standard_upload_id)
        if not sm:
            raise HTTPException(404, f"standard_upload_id 不存在: {standard_upload_id}")
        sname = sm.get("standard") or sm.get("draft")
        std_path = jd / sname
        shutil.copy2(settings.uploads_dir / standard_upload_id / sname, std_path)
    elif upload_id and std_name_file:
        std_path = jd / std_name_file
        shutil.copy2(settings.uploads_dir / upload_id / std_name_file, std_path)

    # 不给标准也不给 spec_slug → 自动路由
    if std_path is None and not spec_slug:
        spec_slug = "auto"

    source = {"draft": draft_path.name,
              "standard": std_path.name if std_path else None,
              "upload_id": upload_id or None}
    _start(job_id, draft_path, std_path, spec_slug, std_name, source)
    return JSONResponse({"job_id": job_id, "status": "queued", "spec_slug": spec_slug}, status_code=202)


@app.post("/api/v1/compliance", status_code=202)
async def compliance_compat(
    draft: UploadFile = File(...),
    standard: Optional[UploadFile] = File(None),
    spec_slug: str = Form(""),
    std_name: str = Form(""),
):
    """兼容旧用法：上传 + 启动一步完成。"""
    _check_ext(draft, "待审查文件")
    if standard is not None:
        _check_ext(standard, "标准文件")
    if standard is None and not spec_slug:
        spec_slug = "auto"
    job_id = new_job_id()
    jd = settings.uploads_dir / job_id
    dp = _save(draft, jd)
    sp = _save(standard, jd) if standard is not None else None
    source = {"draft": dp.name, "standard": sp.name if sp else None}
    _start(job_id, dp, sp, spec_slug, std_name, source)
    return JSONResponse({"job_id": job_id, "status": "queued"}, status_code=202)


# ------------------------------------------------------------------ query
@app.get("/api/v1/compliance/status/{job_id}")
async def status(job_id: str):
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return _job_view(job)


@app.get("/api/v1/compliance/result/{job_id}")
async def result(job_id: str):
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if job["status"] != "completed":
        return JSONResponse({"job_id": job_id, "status": job["status"],
                             "progress": _job_view(job)["progress"],
                             "message": "尚未完成"}, status_code=202)
    out = Path(job["result_path"]) if job.get("result_path") else settings.output_dir / job_id
    jf = out / "compliance.json"
    data = json.loads(jf.read_text(encoding="utf-8")) if jf.exists() else {}
    rf = out / "贯标核查报告.md"
    return {"job_id": job_id, "status": "completed",
            "std_name": job.get("std_name", ""),
            "duration_ms": job.get("duration_ms", 0),
            "tokens": job.get("tokens", {}),
            "report_md": rf.read_text(encoding="utf-8") if rf.exists() else "",
            "compliance": data}


@app.get("/api/v1/compliance/jobs")
async def list_jobs(limit: int = 20, offset: int = 0,
                    status: str = "", q: str = ""):
    return store.list_jobs(limit=max(1, min(limit, 200)), offset=max(0, offset),
                           status=status or None, q=q or None)


@app.get("/api/v1/compliance/history")
async def history(limit: int = 20, offset: int = 0, status: str = "", q: str = ""):
    return await list_jobs(limit=limit, offset=offset, status=status, q=q)


@app.get("/api/v1/compliance/jobs/{job_id}")
async def job_detail(job_id: str):
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    view = _job_view(job)
    # trace 摘要（不塞全量调用链，避免响应过大；全量走 /download/trace）
    tp = Path(job["trace_path"]) if job.get("trace_path") else None
    if tp and tp.exists():
        try:
            td = json.loads(tp.read_text(encoding="utf-8"))
            view["trace_summary"] = {
                "duration_ms": td.get("duration_ms"),
                "totals": td.get("totals"),
                "spans": [{"name": s.get("name"), "duration_ms": s.get("duration_ms")}
                          for s in td.get("spans", [])],
                "llm_calls": len(td.get("llm_calls", [])),
            }
        except Exception:
            pass
    return view


@app.get("/api/v1/compliance/trace/{job_id}")
async def trace_inline(job_id: str):
    """在线查看完整 trace（与 /download/trace 内容一致）。"""
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    p = Path(job["trace_path"]) if job.get("trace_path") else settings.output_dir / job_id / "trace.json"
    if not p.exists():
        raise HTTPException(404, "trace 未就绪")
    return JSONResponse(json.loads(p.read_text(encoding="utf-8")))


# ------------------------------------------------------------------ download
_MEDIA = {
    ".md": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".tif": "image/tiff", ".tiff": "image/tiff",
}


def _media_for(name: str) -> str:
    return _MEDIA.get(Path(name).suffix.lower()) or mimetypes.guess_type(name)[0] or "application/octet-stream"


def _resolve(job: dict, kind: str) -> tuple[Path, str]:
    out = Path(job["result_path"]) if job.get("result_path") else settings.output_dir / job["job_id"]
    src = job.get("source") or {}
    if isinstance(src, str):
        try:
            src = json.loads(src)
        except Exception:
            src = {}
    if kind == "report":
        return out / "贯标核查报告.md", "text/markdown; charset=utf-8"
    if kind == "result":
        return out / "compliance.json", "application/json; charset=utf-8"
    if kind == "trace":
        p = Path(job["trace_path"]) if job.get("trace_path") else out / "trace.json"
        return p, "application/json; charset=utf-8"
    if kind == "original":
        name = src.get("draft") or ""
        p = out / name
        if not p.exists():
            p = settings.uploads_dir / job["job_id"] / name
        return p, _media_for(name)
    if kind == "standard":
        name = src.get("standard") or ""
        p = out / name
        if not p.exists():
            p = settings.uploads_dir / job["job_id"] / name
        return p, _media_for(name)
    raise HTTPException(400, f"未知下载类型: {kind}（可选 report|result|trace|original|standard）")


@app.get("/api/v1/compliance/download/{job_id}/{kind}")
async def download_kind(job_id: str, kind: str):
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    p, media = _resolve(job, kind)
    if not p.exists():
        raise HTTPException(404, f"文件未就绪: {kind}")
    return FileResponse(p, filename=p.name, media_type=media)


@app.get("/api/v1/compliance/download/{job_id}")
async def download_report_compat(job_id: str):
    """兼容旧用法：下载报告。"""
    return await download_kind(job_id, "report")


if __name__ == "__main__":
    uvicorn.run("api.main:app", host=settings.host, port=settings.port, reload=False)
