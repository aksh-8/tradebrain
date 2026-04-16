from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

_CONFIG_DIR = Path(__file__).parent.parent.parent / "config"
_UNIVERSE_PATH = _CONFIG_DIR / "universe.json"

_universe_cache: Optional[dict] = None


def _load_universe() -> dict:
    global _universe_cache
    if _universe_cache is None:
        with open(_UNIVERSE_PATH, "r") as f:
            _universe_cache = json.load(f)
    return _universe_cache


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Settings:
    default_budget_usd: float
    default_dte_min: int
    default_dte_max: int
    default_top_n: int
    ollama_url: str
    ollama_model: str
    ollama_timeout: int


def get_settings() -> Settings:
    return Settings(
        default_budget_usd = float(os.getenv("DEFAULT_BUDGET_USD", "300")),
        default_dte_min    = int(os.getenv("DEFAULT_DTE_MIN", "21")),
        default_dte_max    = int(os.getenv("DEFAULT_DTE_MAX", "60")),
        default_top_n      = int(os.getenv("DEFAULT_TOP_N", "3")),
        ollama_url         = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate"),
        ollama_model       = os.getenv("OLLAMA_MODEL", "qwen2.5:14b"),
        ollama_timeout     = int(os.getenv("OLLAMA_TIMEOUT", "120")),
    )


# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------

def get_known_tickers() -> set[str]:
    """
    All tickers the bot knows about — from universe.json.
    Used by intake for plain-text ticker detection.
    """
    d = _load_universe()
    return set(t.upper() for t in d.get("tickers", []))


def get_proxies() -> dict[str, str]:
    """
    Proxy map — if signal ticker has no liquid options, trade this instead.
    e.g. {"SPY": "IWM", "NVDA": "AMD"}
    """
    d = _load_universe()
    return {k.upper(): v.upper() for k, v in d.get("proxies", {}).items()}


def resolve_proxy(ticker: str) -> str:
    """
    Returns the proxy ticker if one exists, otherwise the original ticker.
    """
    return get_proxies().get(ticker.upper(), ticker.upper())