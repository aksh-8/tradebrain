from __future__ import annotations

import json
import re
from typing import Optional

import requests

from bot.models import Intake, Direction, Timeframe

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "qwen2.5:7b"

TICKER_RE = re.compile(r"\$([A-Z]{1,6})\b")
PLAIN_TICKER_RE = re.compile(r"\b([A-Z]{2,6})\b")

DIRECTION_KEYWORDS = {
    "bullish": ["bullish", "calls", "call", "long", "buy", "breakout",
                "upside", "squeeze", "green", "push", "moon", "surge"],
    "bearish": ["bearish", "puts", "put", "short", "sell", "breakdown",
                "downside", "drop", "red", "crash", "dump", "fall"],
}

TIMEFRAME_KEYWORDS = {
    "this week":  ["today", "tomorrow", "this week", "eod", "intraday", "0dte"],
    "this month": ["this month", "monthly", "end of month", "few weeks"],
    "1-3 months": ["months", "quarter", "q1", "q2", "q3", "q4", "swing"],
}

KNOWN_TICKERS = {
    "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "GOOGL", "GOOG",
    "META", "AMZN", "PLTR", "CRWD", "PANW", "IWM", "SPY", "QQQ",
    "INTC", "AVGO", "NFLX", "UBER", "COIN", "MSTR", "RKLB",
}


# ---------------------------------------------------------------------------
# Fallback: pure regex parser (no Ollama needed)
# ---------------------------------------------------------------------------

def _regex_parse(raw: str, budget: float) -> Intake:
    upper = raw.upper()

    # tickers — prefer $TICKER format, fall back to known list
    tickers = TICKER_RE.findall(raw)
    if not tickers:
        tickers = [t for t in PLAIN_TICKER_RE.findall(upper) if t in KNOWN_TICKERS]
    tickers = list(dict.fromkeys(tickers))  # deduplicate, preserve order

    # direction
    direction: Direction = "unknown"
    lower = raw.lower()
    for d, keywords in DIRECTION_KEYWORDS.items():
        if any(k in lower for k in keywords):
            direction = d  # type: ignore[assignment]
            break

    # timeframe
    timeframe: Timeframe = "unknown"
    for tf, keywords in TIMEFRAME_KEYWORDS.items():
        if any(k in lower for k in keywords):
            timeframe = tf  # type: ignore[assignment]
            break

    # thesis — anything beyond the ticker is the thesis
    thesis: Optional[str] = raw.strip() if len(raw.split()) > 2 else None

    return Intake(
        raw_text  = raw,
        tickers   = tuple(tickers),
        direction = direction,
        thesis    = thesis,
        timeframe = timeframe,
        budget    = budget,
    )


# ---------------------------------------------------------------------------
# LLM parser (richer extraction when Ollama is running)
# ---------------------------------------------------------------------------

def _ollama_available() -> bool:
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def _llm_parse(raw: str, budget: float) -> Optional[Intake]:
    prompt = f"""You are a trading assistant parsing user input into structured data.

Input: "{raw}"

Extract and return ONLY valid JSON — no explanation, no markdown:
{{
  "tickers": ["list of stock tickers mentioned, uppercase, no $ sign"],
  "direction": "bullish | bearish | unknown",
  "timeframe": "this week | this month | 1-3 months | unknown",
  "thesis": "the core trade idea in one sentence, or null if none"
}}

Rules:
- tickers: only real stock symbols, no words like CALL or PUT
- direction: bullish = calls/long/up, bearish = puts/short/down
- timeframe: infer from context clues like 'this week', 'swing trade', 'months'
- thesis: clean summary of why this trade, null if just a ticker with no reasoning
"""

    try:
        r = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL_NAME,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0},
            },
            timeout=30,
        )
        raw_resp = r.json().get("response", "").strip()

        if "```" in raw_resp:
            for part in raw_resp.split("```"):
                part = part.strip().lstrip("json").strip()
                if part.startswith("{"):
                    raw_resp = part
                    break

        parsed = json.loads(raw_resp)

        tickers   = [str(t).upper().strip() for t in (parsed.get("tickers") or []) if t]
        direction = parsed.get("direction", "unknown")
        timeframe = parsed.get("timeframe", "unknown")
        thesis    = parsed.get("thesis") or None

        if direction not in ("bullish", "bearish", "unknown"):
            direction = "unknown"
        if timeframe not in ("this week", "this month", "1-3 months", "unknown"):
            timeframe = "unknown"

        return Intake(
            raw_text  = raw,
            tickers   = tuple(tickers),
            direction = direction,   # type: ignore[arg-type]
            thesis    = thesis,
            timeframe = timeframe,   # type: ignore[arg-type]
            budget    = budget,
        )

    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_intake(raw: str, budget: float) -> Intake:
    """
    Parses anything the user types into a structured Intake.
    Uses LLM if Ollama is running, falls back to regex if not.
    Works with:
      - just a ticker:       "AMD"
      - ticker + direction:  "AMD calls"
      - full thesis:         "$AMD has seized the opportunity..."
      - investor paraphrase: "Ark bullish on TSLA long term"
    """
    raw = raw.strip()

    if _ollama_available():
        result = _llm_parse(raw, budget)
        if result and result.tickers:
            return result

    # fallback to regex
    return _regex_parse(raw, budget)