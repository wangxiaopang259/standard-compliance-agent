"""OpenAI 兼容客户端 — LLM 与 VL 共用（内网 qwen3.8-27b vLLM）。

- chat(messages)            纯文本
- chat_vision(prompt, imgs) 图文（看图谱）—— 复用同一端点
"""
from __future__ import annotations

import base64
import httpx

from core.config import settings


class AIClient:
    def __init__(self, base_url: str = None, api_key: str = None, model: str = None,
                 timeout: float = 600.0, disable_thinking: bool = None):
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.llm_api_key
        self.model = model or settings.llm_model
        self.timeout = timeout
        self.disable_thinking = (settings.llm_disable_thinking
                                 if disable_thinking is None else disable_thinking)

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def chat(self, messages: list, temperature: float = 0.0,
             response_format: dict | None = None, max_tokens: int | None = None) -> str:
        payload = {"model": self.model, "messages": messages, "temperature": temperature}
        if self.disable_thinking:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        if response_format:
            payload["response_format"] = response_format
        if max_tokens:
            payload["max_tokens"] = max_tokens
        url = f"{self.base_url}/chat/completions"
        with httpx.Client(timeout=self.timeout, trust_env=False) as c:
            r = c.post(url, headers=self._headers(), json=payload)
            r.raise_for_status()
            data = r.json()
        return data["choices"][0]["message"].get("content") or ""

    def chat_vision(self, prompt: str, image_paths: list[str], temperature: float = 0.0) -> str:
        """图文问答：把本地图片 base64 塞进 content（OpenAI 多模态格式）。"""
        content = [{"type": "text", "text": prompt}]
        for p in image_paths:
            b64 = base64.b64encode(open(p, "rb").read()).decode()
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"}})
        return self.chat([{"role": "user", "content": content}], temperature=temperature)


def get_llm() -> AIClient:
    return AIClient()


def get_vl() -> AIClient:
    return AIClient(base_url=settings.vl_base_url, api_key=settings.vl_api_key,
                    model=settings.vl_model)
