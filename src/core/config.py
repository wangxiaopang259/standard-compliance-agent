"""配置加载（分环境）。优先级：环境变量 > configs/app.<APP_ENV>.json > configs/app.json。"""
from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "configs"


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _deep_merge(base: dict, over: dict) -> dict:
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def _upfind(rel: str, start: Path) -> Path | None:
    """从 start 向上逐级找 rel，命中即返回（应对目录重组/移动）。"""
    relp = Path(rel)
    for base in [start, *start.parents]:
        cand = base / relp
        if cand.exists():
            return cand.resolve()
    return None


def _load_config() -> tuple[dict, str]:
    env = os.environ.get("APP_ENV", "prod").strip() or "prod"
    base = _read_json(CONFIG_DIR / "app.json")
    overlay = _read_json(CONFIG_DIR / f"app.{env}.json")
    return _deep_merge(deepcopy(base), overlay), env


class Settings:
    def __init__(self) -> None:
        cfg, env = _load_config()
        self.env = env
        server = cfg.get("server", {})
        llm = cfg.get("llm", {})
        vl = cfg.get("vl", {})
        mineru = cfg.get("mineru", {})
        comp = cfg.get("compliance", {})

        self.host = os.environ.get("HOST", server.get("host", "0.0.0.0"))
        self.port = int(os.environ.get("PORT", server.get("port", 5013)))

        llm_prov = (llm.get("providers") or {}).get("qwen38", {})
        self.llm_base_url = os.environ.get("LLM_BASE_URL", llm_prov.get("base_url", "")).rstrip("/")
        self.llm_api_key = os.environ.get("LLM_API_KEY", llm_prov.get("api_key", ""))
        self.llm_model = os.environ.get("LLM_MODEL",
                                         llm.get("default_model",
                                                 (llm_prov.get("models") or ["qwen3.8-27b"])[0]))
        self.llm_disable_thinking = str(
            os.environ.get("LLM_DISABLE_THINKING", llm_prov.get("disable_thinking", True))
        ).lower() in ("1", "true", "yes")
        self.llm_tunnel_cmd = os.environ.get("TUNNEL_CMD", llm.get("tunnel_cmd", ""))

        self.vl_base_url = os.environ.get("VL_BASE_URL", vl.get("base_url", self.llm_base_url)).rstrip("/")
        self.vl_api_key = os.environ.get("VL_API_KEY", vl.get("api_key", self.llm_api_key))
        self.vl_model = os.environ.get("VL_MODEL", vl.get("default_model", self.llm_model))

        self.mineru_url = os.environ.get("MINERU_URL", mineru.get("url", "")).rstrip("/")
        self.mineru_enabled = str(os.environ.get("MINERU_ENABLED",
                                                 mineru.get("enabled", True))).lower() in ("1", "true", "yes")
        self.mineru_timeout = int(os.environ.get("MINERU_TIMEOUT", mineru.get("timeout", 1800)))

        # 用"向上查找"解析外部依赖目录，避免目录重组后相对路径失效
        specs_rel = os.environ.get("SPECS_DIR", comp.get("specs_dir", "../standard-distill-agent/specs"))
        std_rel = os.environ.get("STANDARDS_DIR", comp.get("standards_dir", "10000-强标实施责任清单/05-强标文本"))
        self.specs_dir = _upfind(specs_rel, PROJECT_ROOT) or (PROJECT_ROOT / specs_rel).resolve()
        self.standards_dir = (_upfind(std_rel, PROJECT_ROOT)
                              or _upfind("10000-强标实施责任清单/05-强标文本", PROJECT_ROOT)
                              or (PROJECT_ROOT / std_rel).resolve())
        self.max_std_clauses = int(comp.get("max_std_clauses", 120))

        self.uploads_dir = PROJECT_ROOT / "uploads"
        self.output_dir = PROJECT_ROOT / "output"
        self.data_dir = PROJECT_ROOT / "data"
        self.logs_dir = PROJECT_ROOT / "logs"
        for d in (self.uploads_dir, self.output_dir, self.data_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jobs.sqlite"

    def summary(self) -> dict:
        return {"env": self.env, "port": self.port, "llm_base_url": self.llm_base_url,
                "llm_model": self.llm_model, "mineru_url": self.mineru_url}


settings = Settings()
