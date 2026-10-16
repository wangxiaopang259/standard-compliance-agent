"""Skill Registry — 扫描所有已蒸馏标准，建立技能索引。

不把 800 个 SKILL.md 全读进内存，只存"名字+描述+条号+路径"；
命中后再按需读全文。
"""
from __future__ import annotations

import json
import re
from pathlib import Path


def _parse_frontmatter(txt: str) -> tuple[str, str]:
    m = re.search(r"^---\s*\n(.*?)\n---", txt, re.S)
    block = m.group(1) if m else txt[:800]
    name = ""
    mn = re.search(r"^name:\s*(.+)$", block, re.M)
    if mn:
        name = mn.group(1).strip().strip('"').strip("'")
    desc = ""
    md = re.search(r"^description:\s*\|?\s*\n?((?:[ \t]+.*\n?)*)", block, re.M)
    if md:
        desc = " ".join(l.strip() for l in md.group(1).splitlines() if l.strip())
    else:
        md2 = re.search(r"^description:\s*(.+)$", block, re.M)
        if md2:
            desc = md2.group(1).strip().strip('"').strip("'")
    return name, desc


def _extract_clauses(txt: str) -> list[str]:
    cs = set()
    for m in re.finditer(r"第\s*(\d+(?:\.\d+)+)\s*条", txt):
        cs.add(m.group(1))
    for m in re.finditer(r"(?m)^\s*#{0,4}\s*(\d+\.\d+(?:\.\d+)*)\s", txt):
        cs.add(m.group(1))
    for m in re.finditer(r"(\d+\.\d+(?:\.\d+)*)\s*条", txt):
        cs.add(m.group(1))
    return sorted(cs)


def build(specs_dir: Path, standard_of: dict | None = None) -> list[dict]:
    """扫 specs/*/skills/*/SKILL.md → 索引列表。standard_of: slug→标准号(可选)。"""
    items = []
    for spec in sorted(specs_dir.iterdir()):
        if not spec.is_dir():
            continue
        sk = spec / "skills"
        if not sk.exists():
            continue
        std_name = (standard_of or {}).get(spec.name, spec.name)
        for d in sorted(sk.iterdir()):
            f = d / "SKILL.md"
            if not f.exists():
                continue
            txt = f.read_text(encoding="utf-8")
            name, desc = _parse_frontmatter(txt)
            items.append({
                "skill": name or d.name,
                "standard": std_name,
                "spec_slug": spec.name,
                "path": str(f),
                "description": desc,
                "clauses": _extract_clauses(txt),
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
