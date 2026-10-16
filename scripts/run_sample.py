"""对贯标样例跑核验并计算命中率。

用法：
  APP_ENV=dev PYTHONPATH=src python3 scripts/run_sample.py --sample GB16994.1-2021
  APP_ENV=dev PYTHONPATH=src python3 scripts/run_sample.py --all      # 全部（慢）
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from core.config import settings           # noqa: E402
from core import store, compliance         # noqa: E402
from core.ai_client import get_llm          # noqa: E402
from infrastructure import mineru_client    # noqa: E402

SAMPLE_DIR = Path("/Users/wangshang/Documents/99-reviewagent/10000-强标实施责任清单/07-贯标样例")


def find_standard(code: str) -> Path | None:
    code_dash = code.replace(".", ".")  # GB16994.1-2021
    for p in settings.standards_dir.glob("*.pdf"):
        if p.name.replace(" ", "").startswith(code_dash.replace("GBT", "GBT").replace("GB", "GB")):
            return p
    # 宽松匹配：去空格后前缀
    norm = code.replace("/", "").replace(" ", "")
    for p in settings.standards_dir.glob("*.pdf"):
        if p.name.replace(" ", "").startswith(norm):
            return p
    return None


def parse_answer(answer_md: Path) -> list[tuple[str, str]]:
    """从预期答案中抽 (标准条号, 判定)。"""
    pairs = []
    for line in answer_md.read_text(encoding="utf-8").splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 4 and re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", cells[2] or ""):
            verdict = cells[3]
            if any(k in verdict for k in ("合规", "违规", "缺项", "不完整")):
                pairs.append((cells[2], verdict))
    return pairs


def run_one(code: str) -> dict:
    sdir = SAMPLE_DIR / code
    draft = next(iter(sdir.glob("待审查文件_*.md")), None)
    answer = next(iter(sdir.glob("贯标预期答案_*.md")), None)
    if not draft or not answer:
        return {"code": code, "error": "缺 草稿/答案"}
    std = find_standard(code)
    if not std:
        return {"code": code, "error": "未找到标准 PDF"}

    llm = get_llm()
    draft_text = mineru_client.parse_any(draft)
    std_text = mineru_client.parse_file(std)
    clauses = compliance.extract_std_clauses(std_text, llm, settings.max_std_clauses)
    data = compliance.judge(code, clauses, "", draft_text, llm)

    def norm(c):
        return (c or "").strip().replace(" ", "")
    judged = {}
    for r in data.get("rows", []):
        c = norm(r.get("std_clause"))
        if c:
            judged[c] = r.get("verdict", "")
    for m in data.get("missing", []):          # 缺项并入
        c = norm(m.get("std_clause"))
        if c:
            judged.setdefault(c, "缺项")
    expect = parse_answer(answer)

    mism = [(c, v, judged.get(norm(c), "—")) for c, v in expect if judged.get(norm(c)) != v]
    hit = len(expect) - len(mism)
    Path(settings.output_dir / f"judge_{code}.json").write_text(
        __import__("json").dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"code": code, "expected": len(expect), "hit": hit,
            "rate": round(hit / len(expect), 3) if expect else 0.0,
            "mismatch": [f"{c}: 期望{v}/实判{j}" for c, v, j in mism][:12]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    store.init_db()

    if args.sample:
        codes = [args.sample]
    else:
        codes = [d.name for d in sorted(SAMPLE_DIR.iterdir())
                 if d.is_dir() and d.name not in ("_cache", "GB16994.1")]
    if args.limit:
        codes = codes[:args.limit]

    tot_e = tot_h = n = 0
    for c in codes:
        try:
            r = run_one(c)
            print(r, flush=True)
            if "rate" in r:
                tot_e += r["expected"]; tot_h += r["hit"]; n += 1
        except Exception as e:
            print({"code": c, "error": str(e)[:120]}, flush=True)
    if n:
        print(f"\n=== 平均命中率：{tot_h}/{tot_e} = {round(tot_h/tot_e,3)} （{n} 份）===", flush=True)


if __name__ == "__main__":
    main()
