"""Router — 为一份待审查文件挑选相关的 skill 子集。

多路召回（并集）→ LLM 精排 → 返回选中子集 + 理由 + 可能遗漏。
召回优先（宁多勿漏）：漏选会致漏审，误选只是噪音。
"""
from __future__ import annotations

import re

from core import llmjson

_STD_RE = re.compile(r"(GB/?T?\s*\d+(?:\.\d+)*[-—]\d{4}|JTS?/?T?\s*\d+[-—]\d{4}|TB\s*\d+[-—]\d{4})")
_CLAUSE_RE = re.compile(r"第?\s*(\d+\.\d+(?:\.\d+)*)\s*条?")

_KW_SYS = ("从给定文件中抽取**用于检索标准条款**的关键词/术语，只输出 JSON 字符串数组，"
           "15 个以内，不要解释。")
_RERANK_SYS = ("你是技能路由精排器。依据待审查文件，从候选 skill 中挑出**确实相关**的，"
               "只输出 JSON：{\"selected\":[{\"skill\":\"名\",\"reason\":\"一句\",\"confidence\":0-1}],"
               "\"maybe_missing\":[\"可能相关但你未选的 skill 名\"]}，不要围栏、不要解释。")


def _norm_code(c: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", c).upper()


def extract_std_codes(text: str) -> list[str]:
    return sorted({_norm_code(m) for m in _STD_RE.findall(text)})


def extract_cited_clauses(text: str) -> list[str]:
    return sorted({m for m in _CLAUSE_RE.findall(text)})


def _bigrams(s: str) -> set:
    s = s or ""
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else ({s} if s else set())


def _kw_hits(registry: list[dict], kws: list[str], limit: int) -> list[dict]:
    """关键词召回：按二元组重叠计分（对中文短语更友好）。"""
    kw_bg = [(_bigrams(k), k) for k in kws if len(k) >= 2]
    scored = []
    for s in registry:
        blob = (s.get("description", "") + " " + s.get("skill", "") + " " + " ".join(s.get("clauses", [])))
        bg = _bigrams(blob)
        sc = sum(1 for kg, _ in kw_bg if kg & bg)
        if sc:
            scored.append((sc, s))
    scored.sort(key=lambda x: -x[0])
    return [s for _, s in scored[:limit]]


def route(draft_text: str, registry: list[dict], llm, pool_cap: int = 40) -> dict:
    codes = extract_std_codes(draft_text)
    clauses = extract_cited_clauses(draft_text)

    std_hits = [s for s in registry if any(c and (c in _norm_code(s["standard"]) or c in _norm_code(s["spec_slug"])) for c in codes)]
    clause_hits = [s for s in registry if set(s.get("clauses", [])) & set(clauses)]

    try:
        kw_raw = llm.chat([{"role": "system", "content": _KW_SYS},
                           {"role": "user", "content": draft_text[:8000]}], temperature=0.0)
        kws = [k for k in (llmjson.loads(kw_raw, default=[]) or []) if isinstance(k, str)]
    except Exception:
        kws = []
    kw_hits = _kw_hits(registry, kws, pool_cap)

    # 并集去重
    pool, seen = [], set()
    for s in std_hits + clause_hits + kw_hits:
        k = s["skill"]
        if k not in seen:
            seen.add(k)
            pool.append(s)
    pool = pool[:pool_cap]

    # LLM 精排
    selected, maybe = [], []
    if pool:
        brief = "\n".join(f"- {s['skill']}（{s['standard']}）：{s.get('description','')[:120]}" for s in pool)
        prompt = (f"# 待审查文件（节选）\n{draft_text[:3000]}\n\n# 候选 skill\n{brief}\n\n"
                  f"挑出真正相关的 skill。")
        out = llm.chat([{"role": "system", "content": _RERANK_SYS}, {"role": "user", "content": prompt}],
                       temperature=0.0)
        data = llmjson.loads(out, default={}) or {}
        sel_names = {x.get("skill") for x in (data.get("selected") or []) if isinstance(x, dict)}
        selected = [s for s in pool if s["skill"] in sel_names]
        maybe = data.get("maybe_missing") or []
        if not selected:  # 精排失败 → 回退：召回全保留（召回优先）
            selected = pool

    return {
        "basis": {"standard_codes": codes, "cited_clauses": clauses, "keywords": kws},
        "counts": {"std": len(std_hits), "clause": len(clause_hits), "kw": len(kw_hits), "pool": len(pool)},
        "selected": [s["skill"] for s in selected],
        "maybe_missing": maybe,
        "selected_items": selected,
    }


def load_selected_text(selected_items: list[dict], registry: list[dict]) -> str:
    """把选中 skill 的全文 + 其所属标准的 EXECUTION.md 拼成判据文本。"""
    from pathlib import Path
    seen_spec, blob = set(), ""
    for s in selected_items:
        p = Path(s["path"])
        if p.exists():
            blob += f"\n\n===== {s['skill']}（{s['standard']}）=====\n" + p.read_text(encoding="utf-8")
        slug = s.get("spec_slug")
        if slug and slug not in seen_spec:
            seen_spec.add(slug)
            ex = p.parents[2] / "EXECUTION.md"  # specs/<slug>/skills/<skill>/SKILL.md → specs/<slug>
            if ex.exists():
                blob += f"\n\n##### EXECUTION {slug} #####\n" + ex.read_text(encoding="utf-8")[:15000]
    return blob
