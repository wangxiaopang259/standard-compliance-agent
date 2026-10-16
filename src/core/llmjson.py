"""从 LLM 输出里稳健抽取 JSON。"""
from __future__ import annotations

import json
import re


def loads(text: str, default=None):
    if not text:
        return default
    t = text.strip()
    # 去 ```json ... ``` 围栏
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    # 找第一个 {...} 或 [...]
    for pat in (r"\{.*\}", r"\[.*\]"):
        m = re.search(pat, t, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                continue
    return default
