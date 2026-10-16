"""贯标核验编排：解析草案 + 加载标准判据 → 判定 → 报告。"""
from __future__ import annotations

import json
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from core.config import settings
from core import store
from core.ai_client import get_llm
from core import compliance, registry as registry_mod, router
from infrastructure import mineru_client


@dataclass
class Ctx:
    job_id: str
    draft_path: Path
    standard_path: Path | None = None
    spec_slug: str = ""
    std_name: str = ""
    draft_text: str = ""
    std_text: str = ""
    clauses: list = field(default_factory=list)
    skills_blob: str = ""
    out_dir: Path = field(default_factory=Path)


def new_job_id() -> str:
    return "c-" + uuid.uuid4().hex[:12]


def run_job(job_id: str, draft_path, standard_path=None, spec_slug: str = "",
            std_name: str = "") -> None:
    if not store.get_job(job_id):
        store.create_job(job_id, {"draft": Path(draft_path).name})
    out = settings.output_dir / job_id
    out.mkdir(parents=True, exist_ok=True)
    ctx = Ctx(job_id=job_id, draft_path=Path(draft_path),
              standard_path=Path(standard_path) if standard_path else None,
              spec_slug=spec_slug, std_name=std_name or (Path(standard_path).stem if standard_path else spec_slug),
              out_dir=out)
    try:
        store.update_job(job_id, status="parsing", stage="parse_draft")
        ctx.draft_text = mineru_client.parse_any(ctx.draft_path)
        store.append_log(job_id, f"草案解析 {len(ctx.draft_text)} 字")

        store.update_job(job_id, status="loading_standard", stage="standard")
        auto = (ctx.spec_slug in ("", "auto", "__auto__")) and ctx.standard_path is None
        if ctx.spec_slug and ctx.spec_slug not in ("auto", "__auto__"):
            spec_dir = settings.specs_dir / ctx.spec_slug
            if spec_dir.exists():
                ctx.clauses, ctx.skills_blob = compliance.load_std_from_spec(spec_dir)
                store.append_log(job_id, f"判据=已蒸馏目录 {spec_dir.name}（条 {len(ctx.clauses)}）")
        if auto:
            # 自动路由：从全部已蒸馏标准里挑相关 skill
            reg = registry_mod.load_or_build(settings.specs_dir, settings.data_dir / "registry.json")
            routing = router.route(ctx.draft_text, reg, get_llm())
            selected = routing["selected_items"]
            ctx.skills_blob = router.load_selected_text(selected, reg)
            ctx.clauses = [{"clause": c, "text": "", "keyword": s["skill"]}
                           for s in selected for c in s.get("clauses", [])]
            (out / "routing.json").write_text(
                json.dumps({k: v for k, v in routing.items() if k != "selected_items"},
                           ensure_ascii=False, indent=2), encoding="utf-8")
            store.append_log(job_id, f"自动路由：选中 {len(selected)} 个 skill "
                                     f"（库 {len(reg)}，召回 {routing['counts']}）")
        if not ctx.clauses:
            if not ctx.standard_path:
                raise ValueError("缺少标准（standard 文件 / spec_slug / auto 路由无命中）")
            ctx.std_text = mineru_client.parse_file(ctx.standard_path)
            llm = get_llm()
            ctx.clauses = compliance.extract_std_clauses(ctx.std_text, llm, settings.max_std_clauses)
            store.append_log(job_id, f"标准解析 {len(ctx.std_text)} 字；抽条款 {len(ctx.clauses)}")

        store.update_job(job_id, status="judging", stage="judge")
        data = compliance.judge(ctx.std_name, ctx.clauses, ctx.skills_blob, ctx.draft_text, get_llm())

        (out / "compliance.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "贯标核查报告.md").write_text(compliance.build_report_md(data), encoding="utf-8")
        s = data.get("summary") or {}
        store.append_log(job_id, f"完成：合规{s.get('合规','?')}/违规{s.get('违规','?')}/缺项{s.get('缺项','?')}")
        store.update_job(job_id, status="completed", stage="done", result_path=str(out))
    except Exception as e:
        store.append_log(job_id, f"失败：{e}\n{traceback.format_exc()[:1500]}")
        store.update_job(job_id, status="failed")
