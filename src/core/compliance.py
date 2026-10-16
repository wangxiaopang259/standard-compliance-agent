"""贯标核验核心：构建标准条款库 + 判定草案合规性 + 生成报告。

判定判据来源（择一）：
  A. 已蒸馏目录 specs/<slug>/（skills/* + EXECUTION.md）—— 富判据
  B. 标准全文（MinerU 解析）—— 现场抽取条款
"""
from __future__ import annotations

import json
from pathlib import Path

from core import llmjson

_SYS = ("你是标准化贯标（合规）核验裁判。依据给定**标准条款**，逐条判定**待审查文件**"
        "是否满足要求，输出 合规/违规/缺项/不完整。\n"
        "【硬约束】\n"
        "1. 只输出 JSON，不要代码围栏、不要解释。\n"
        "2. basis_quote 必须是标准原文的**逐字摘录**，禁止编造条号。\n"
        "3. note ≤40 字，只写判定说明，严禁思考/推敲/自我对话。\n"
        "4. verdict ∈ 合规/违规/缺项/不完整。\n")

_EXTRACT_SYS = ("你是条款抽取器。从标准全文中抽取全部**可判定的要求型条款**，"
                "只输出 JSON：[{\"clause\":\"X.X\",\"text\":\"逐字条文\",\"keyword\":\"关键词\"}]，不要围栏、不要解释。")

_JUDGE_PROMPT = """# 标准：__STD__
# 标准条款库（JSON）
__CLAUSES__

# 待审查文件全文
__DRAFT__

# 任务
逐条核验：把待审查文件中的主张对到标准条款，判定 合规/违规/缺项/不完整；
并找出标准要求但文件**未覆盖**的缺项。只输出 JSON：
{
  "standard": "__STD__",
  "rows": [
    {"file_clause":"3.1","file_text":"文件要点","std_clause":"X.X","verdict":"合规|违规|缺项|不完整",
     "basis_quote":"逐字依据","note":"一句说明"}
  ],
  "missing": [ {"std_clause":"X.X","requirement":"标准要求摘要","note":"缺项说明"} ],
  "summary": {"合规":0,"违规":0,"缺项":0,"不完整":0}
}"""


def load_std_from_spec(spec_dir: Path) -> tuple[list[dict], str]:
    """从已蒸馏目录读判据（skills + EXECUTION）。返回 (clauses, skills_blob)。"""
    clauses, blob = [], ""
    ex = spec_dir / "EXECUTION.md"
    if ex.exists():
        blob += ex.read_text(encoding="utf-8")
    sk = spec_dir / "skills"
    if sk.exists():
        for d in sorted(sk.iterdir()):
            f = d / "SKILL.md"
            if f.exists():
                t = f.read_text(encoding="utf-8")
                blob += f"\n\n===== {d.name} =====\n" + t
                # 抽 R 段的条号
                import re
                for m in re.finditer(r"([\d]+\.[\d.]+)\s*条|第\s*([\d.]+)\s*条", t):
                    c = m.group(1) or m.group(2)
                    if c:
                        clauses.append({"clause": c, "text": "", "keyword": d.name})
    return clauses, blob


def extract_std_clauses(std_text: str, llm, cap: int = 120) -> list[dict]:
    out = llm.chat([{"role": "system", "content": _EXTRACT_SYS},
                    {"role": "user", "content": f"标准全文：\n{std_text[:60000]}\n\n最多 {cap} 条。"}],
                   temperature=0.0)
    data = llmjson.loads(out, default=[]) or []
    return [c for c in data if isinstance(c, dict) and c.get("clause")][:cap]


def judge(std_name: str, clauses: list[dict], skills_blob: str, draft_text: str, llm) -> dict:
    cl_text = json.dumps(clauses, ensure_ascii=False)[:30000] if clauses else "（无结构化条款，见技能判据）"
    prompt = (_JUDGE_PROMPT
              .replace("__STD__", std_name)
              .replace("__CLAUSES__", cl_text + ("\n\n# 技能判据（补充）\n" + skills_blob[:20000] if skills_blob else ""))
              .replace("__DRAFT__", draft_text[:40000]))
    out = llm.chat([{"role": "system", "content": _SYS}, {"role": "user", "content": prompt}],
                   temperature=0.0)
    data = llmjson.loads(out, default=None)
    if not isinstance(data, dict):
        return {"standard": std_name, "rows": [], "missing": [], "summary": {},
                "_raw": out[:2000]}
    return data


def build_report_md(data: dict) -> str:
    rows = data.get("rows") or []
    missing = data.get("missing") or []
    s = data.get("summary") or {}
    lines = [f"# 贯标核查报告 — {data.get('standard','')}", "",
             f"> 判定：合规/违规/缺项/不完整；依据为标准原文逐字摘录。", "",
             "## 一、逐条核验", "",
             "| 文件条款 | 文件要点 | 标准条号 | 判定 | 逐字依据 | 说明 |",
             "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append("| {fc} | {ft} | {sc} | {v} | {bq} | {n} |".format(
            fc=r.get("file_clause", ""), ft=r.get("file_text", ""), sc=r.get("std_clause", ""),
            v=r.get("verdict", ""), bq=(r.get("basis_quote", "") or "").replace("|", "/"),
            n=(r.get("note", "") or "").replace("|", "/")))
    lines += ["", "## 二、缺项（标准要求但文件未覆盖）", ""]
    if missing:
        lines += ["| 标准条号 | 要求 | 说明 |", "|---|---|---|"]
        for m in missing:
            lines.append("| {sc} | {rq} | {n} |".format(
                sc=m.get("std_clause", ""), rq=(m.get("requirement", "") or "").replace("|", "/"),
                n=(m.get("note", "") or "").replace("|", "/")))
    else:
        lines.append("（未发现缺项）")
    lines += ["", "## 三、汇总",
              f"- ✅ 合规：{s.get('合规','?')}", f"- ❌ 违规：{s.get('违规','?')}",
              f"- ❌ 缺项：{s.get('缺项', len(missing))}", f"- ⚠️ 不完整：{s.get('不完整','?')}"]
    return "\n".join(lines)
