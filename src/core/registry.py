"""Skill Registry — 扫描所有已蒸馏标准，建立技能索引。

优先读取 frontmatter 的结构化字段 standard/clauses/triggers（v2.3）；
旧技能缺字段时回退到正则/启发式。
"""
from __future__ import annotations

import json
import re
from pathlib import Path


def _fm_block(txt: str) -> str:
    m = re.search(r"^---\s*\n(.*?)\n---", txt, re.S)
    return m.group(1) if m else ""


def _parse_name_desc(block: str) -> tuple[str, str]:
    name = ""
    mn = re.search(r"^name:\s*(.+)$", block, re.M)
    if mn:
        name = mn.group(1).strip().strip('"').strip("'")
    desc = ""
    md = re.search(r"^description:\s*\|?\s*\n?((?:[ \t]+.*\n?)*)", block, re.M)
    if md and md.group(1).strip():
        desc = " ".join(l.strip() for l in md.group(1).splitlines() if l.strip())
    else:
        md2 = re.search(r"^description:\s*(.+)$", block, re.M)
        if md2:
            desc = md2.group(1).strip().strip('"').strip("'")
    return name, desc


def _parse_structured(block: str) -> tuple[str, list, list]:
    """返回 (standard, clauses, triggers)；缺失则为空。"""
    standard = ""
    m = re.search(r'^standard:\s*["\']?(.+?)["\']?\s*$', block, re.M)
    if m:
        standard = m.group(1).strip()

    clauses: list = []
    mc = re.search(r"^clauses:\s*(\[[^\]]*\])", block, re.M)
    if mc:
        try:
            clauses = [str(x) for x in json.loads(mc.group(1))]
        except Exception:
            clauses = re.findall(r"[\"']?(\d+\.\d+(?:\.\d+)*)[\"']?", mc.group(1))
    if not clauses:  # yaml 列表形式，仅取纯数字项
        mt = re.search(r"^clauses:\s*\n((?:[ \t]*-[ \t]*.+\n?)+)", block, re.M)
        if mt:
            clauses = re.findall(r"-[ \t]*[\"']?(\d+\.\d+(?:\.\d+)*)", mt.group(1))

    triggers: list = []
    mtr = re.search(r"^triggers:\s*(\[[^\]]*\])", block, re.M)
    if mtr:
        try:
            triggers = [str(x) for x in json.loads(mtr.group(1))]
        except Exception:
            triggers = re.findall(r"[\"']([^\"']+)[\"']", mtr.group(1))
    if not triggers:
        mtt = re.search(r"^triggers:\s*\n((?:[ \t]*-[ \t]*.+\n?)+)", block, re.M)
        if mtt:
            triggers = [re.sub(r'^[ \t]*-[ \t]*["\']?|["\']?[ \t]*$', "", l)
                        for l in mtt.group(1).splitlines() if l.strip()]
    return standard, clauses, triggers


def _extract_clauses(txt: str) -> list[str]:
    cs = set()
    for m in re.finditer(r"第\s*(\d+(?:\.\d+)+)\s*条", txt):
        cs.add(m.group(1))
    for m in re.finditer(r"(\d+\.\d+(?:\.\d+)*)\s*条", txt):
        cs.add(m.group(1))
    return sorted(cs)


def build(specs_dir: Path, standard_of: dict | None = None) -> list[dict]:
    items = []
    for spec in sorted(specs_dir.iterdir()):
        if not spec.is_dir():
            continue
        sk = spec / "skills"
        if not sk.exists():
            continue
        std_fallback = (standard_of or {}).get(spec.name, spec.name)
        for d in sorted(sk.iterdir()):
            f = d / "SKILL.md"
            if not f.exists():
                continue
            txt = f.read_text(encoding="utf-8")
            block = _fm_block(txt)
            name, desc = _parse_name_desc(block)
            standard, clauses, triggers = _parse_structured(block)
            items.append({
                "skill": name or d.name,
                "standard": standard or std_fallback,
                "spec_slug": spec.name,
                "path": str(f),
                "description": desc,
                "clauses": clauses or _extract_clauses(txt),   # 回退：正则抽
                "triggers": triggers,
            })
    return items


def load_or_build(specs_dir: Path, cache: Path, standard_of: dict | None = None) -> list[dict]:
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data:
                return data
        except Exception:
            pass
    items = build(specs_dir, standard_of)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return items
