"""LLM 输出后处理：去代码围栏、规范化日期/占位符。

在所有产物落盘前统一调用（见 orchestrator.JobContext.write）。
"""
from __future__ import annotations

import re
from datetime import datetime

# 会被系统注入的占位符
_PLACEHOLDERS = ("{{DATE}}", "{{TIMESTAMP}}")

# 幻觉日期的常见字段名 + 紧随其后的具体日期（YYYY-MM-DD / YYYY年M月D日 等）
_HALLUCINATED_DATE = re.compile(
    r"(蒸馏时间|生成时间|处理时间|编制时间|产出时间|下载时间)"
    r"(\*{0,2}\s*[:：]\s*\*{0,2})"        # 兼容 **字段**: 的加粗写法
    r"\d{4}\s*[-/年.]\s*\d{1,2}\s*[-/月.]\s*\d{1,2}\s*日?",
)


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def strip_code_fence(text: str) -> str:
    """剥掉整段被 ```lang ... ``` 包裹的围栏（LLM 常干）。"""
    if not text:
        return text
    s = text.strip()
    m = re.match(r"^```[a-zA-Z0-9_-]*\s*\n(.*)\n```$", s, re.S)
    if m:
        return m.group(1).strip() + "\n"
    return text


def normalize(text: str) -> str:
    if not text:
        return text
    t = strip_code_fence(text)
    # 注入真实日期/时间
    t = t.replace("{{DATE}}", today()).replace("{{TIMESTAMP}}", now())
    # 纠正 LLM 编造的具体日期（如"蒸馏时间: 2023-10-27"）
    t = _HALLUCINATED_DATE.sub(lambda m: f"{m.group(1)}{m.group(2)}{today()}", t)
    return t
