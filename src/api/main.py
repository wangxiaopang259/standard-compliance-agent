"""FastAPI — 贯标核验智能体（端口 6002）。"""
from __future__ import annotations

import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
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


app = FastAPI(title="贯标核验智能体 API", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "standard-compliance-agent",
            "mineru": settings.mineru_url, "llm": settings.llm_model, "env": settings.env}


@app.post("/api/v1/compliance")
async def compliance(
    draft: UploadFile = File(...),
    standard: Optional[UploadFile] = File(None),
    spec_slug: str = Form(""),
    std_name: str = Form(""),
):
    fn = (draft.filename or "").lower()
    if not fn.endswith(_ALLOWED):
        raise HTTPException(400, f"不支持的文件类型，仅支持 {_ALLOWED}")
    # 不给标准也不给 spec_slug → 自动路由（从全部已蒸馏标准挑 skill）
    if standard is None and not spec_slug:
        spec_slug = "auto"

    job_id = new_job_id()
    jd = settings.uploads_dir / job_id
    jd.mkdir(parents=True, exist_ok=True)
    draft_path = jd / draft.filename
    with open(draft_path, "wb") as f:
        shutil.copyfileobj(draft.file, f)
    std_path = None
    if standard is not None:
        std_path = jd / standard.filename
        with open(std_path, "wb") as f:
            shutil.copyfileobj(standard.file, f)

    store.create_job(job_id, {"draft": draft.filename, "standard": standard.filename if standard else None})
    _EXECUTOR.submit(run_job, job_id, draft_path, std_path, spec_slug, std_name)
    return JSONResponse({"job_id": job_id, "status": "queued"}, status_code=202)


@app.get("/api/v1/compliance/status/{job_id}")
async def status(job_id: str):
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job


@app.get("/api/v1/compliance/result/{job_id}")
async def result(job_id: str):
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if job["status"] != "completed":
        return JSONResponse({"job_id": job_id, "status": job["status"], "message": "尚未完成"}, status_code=202)
    jf = Path(job["result_path"]) / "compliance.json"
    data = json.loads(jf.read_text(encoding="utf-8")) if jf.exists() else {}
    report = Path(job["result_path"]) / "贯标核查报告.md"
    return {"job_id": job_id, "status": "completed",
            "report_md": report.read_text(encoding="utf-8") if report.exists() else "",
            "compliance": data}


@app.get("/api/v1/compliance/download/{job_id}")
async def download(job_id: str):
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    z = Path(job["result_path"]) / "贯标核查报告.md"
    if not z.exists():
        raise HTTPException(404, "产物未就绪")
    return FileResponse(z, filename=f"贯标核查报告_{job_id}.md", media_type="text/markdown")


if __name__ == "__main__":
    uvicorn.run("api.main:app", host=settings.host, port=settings.port, reload=False)
