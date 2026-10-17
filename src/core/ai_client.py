"""OpenAI 兼容客户端 — LLM 与 VL 共用（内网 qwen3.8-27b vLLM）。

特性：
- chat / chat_vision
- 健康检查 health()
- 连接失败自动**重试 + 尝试拉起隧道**（TUNNEL_CMD，最多一次/客户端）
"""
from __future__ import annotations

import base64
import os
import subprocess
import time

import httpx

from core.config import settings


class AIClient:
    def __init__(self, base_url: str = None, api_key: str = None, model: str = None,
                 timeout: float = 600.0, disable_thinking: bool = None,
                 max_retries: int = None, tunnel_cmd: str = None):
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.llm_api_key
        self.model = model or settings.llm_model
        self.timeout = timeout
        self.disable_thinking = (settings.llm_disable_thinking
                                 if disable_thinking is None else disable_thinking)
        self.max_retries = int(os.environ.get(
            "LLM_MAX_RETRIES", max_retries if max_retries is not None else 3))
        self.tunnel_cmd = os.environ.get(
            "TUNNEL_CMD", tunnel_cmd if tunnel_cmd is not None else getattr(settings, "llm_tunnel_cmd", ""))
        self._tunnel_started = False

    # ---- 基础 ----
    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def health(self, timeout: float = 5.0) -> bool:
        try:
            r = httpx.get(f"{self.base_url}/models", headers=self._headers(),
                          timeout=timeout, trust_env=False)
            return r.status_code == 200
        except Exception:
            return False

    def _ensure_tunnel(self) -> None:
        """连接失败时尝试拉起隧道（最多一次），随后最多等 10s。"""
        if self._tunnel_started or not self.tunnel_cmd:
            return
        self._tunnel_started = True
        try:
            subprocess.Popen(self.tunnel_cmd, shell=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            return
        for _ in range(10):
            if self.health():
                return
            time.sleep(1)

    # ---- 请求 ----
    def _payload(self, messages, temperature, response_format, max_tokens) -> dict:
        p = {"model": self.model, "messages": messages, "temperature": temperature}
        if self.disable_thinking:
            p["chat_template_kwargs"] = {"enable_thinking": False}
        if response_format:
            p["response_format"] = response_format
        if max_tokens:
            p["max_tokens"] = max_tokens
        return p

    def _chat_once(self, messages, temperature, response_format, max_tokens) -> str:
        with httpx.Client(timeout=self.timeout, trust_env=False) as c:
            r = c.post(f"{self.base_url}/chat/completions", headers=self._headers(),
                       json=self._payload(messages, temperature, response_format, max_tokens))
            r.raise_for_status()
            data = r.json()
        return data["choices"][0]["message"].get("content") or ""

    def chat(self, messages: list, temperature: float = 0.0,
             response_format: dict | None = None, max_tokens: int | None = None) -> str:
        last = None
        for attempt in range(self.max_retries + 1):
            try:
                return self._chat_once(messages, temperature, response_format, max_tokens)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
                    httpx.RemoteProtocolError) as e:
                last = e
                self._tunnel_started = False          # 允许下次重试时再次拉起
                if attempt < self.max_retries:
                    time.sleep(min(2 * attempt + 2, 10))
                    self._ensure_tunnel()
                continue
            except httpx.HTTPStatusError as e:
                if e.response.status_code >= 500 and attempt < self.max_retries:
                    time.sleep(min(2 * attempt + 2, 10))
                    continue
                raise
        raise last  # type: ignore

    def chat_vision(self, prompt: str, image_paths: list[str], temperature: float = 0.0) -> str:
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
