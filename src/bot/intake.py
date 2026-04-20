from __future__ import annotations

import json
import re
from typing import Optional

import requests

from bot.models import Intake, Direction, Timeframe
from bot.correlations import (
    resolve_company_name,
    find_ticker_in_text,
    detect_direction,
    detect_timeframe,
)

from bot.config import get_settings as _get_settings
_S = _get_settings()
OLLAMA_URL = _S.ollama_url
MODEL_NAME = _S.ollama_model

TICKER_RE       = re.compile(r"\$([A-Z]{1,6})\b")
PLAIN_TICKER_RE = re.compile(r"\b([A-Z]{2,6})\b")

from bot.config import get_known_tickers as _get_known_tickers
KNOWN_TICKERS = _get_known_tickers()


# ---------------------------------------------------------------------------
# Fallback: regex + correlations (no Ollama needed)
# ---------------------------------------------------------------------------

def _regex_parse(raw: str, budget: float) -> Intake:
    upper = raw.lower()

    # --- ticker extraction (3 passes) ---
    # Pass 1: explicit $TICKER format
    tickers = TICKER_RE.findall(raw)

    # Pass 2: company name lookup via correlations
    if not tickers:
        resolved = find_ticker_in_text(raw)
        if resolved:
            tickers = [resolved]

    # Pass 3: plain uppercase token in known ticker set
    if not tickers:
        tickers = [t for t in PLAIN_TICKER_RE.findall(raw.upper()) if t in KNOWN_TICKERS]

    tickers = list(dict.fromkeys(tickers))  # deduplicate, preserve order

    # --- direction via correlations (replaces inline keyword dict) ---
    direction: Direction = detect_direction(raw)  # type: ignore[assignment]

    # --- timeframe via correlations ---
    timeframe: Timeframe = detect_timeframe(raw)  # type: ignore[assignment]

    # --- thesis: full text if more than 2 words ---
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
- tickers: only real stock symbols. If a company name is mentioned (e.g. Microsoft, Palantir), convert to ticker (MSFT, PLTR)
- direction: bullish = calls/long/up/breakout/above SMA. bearish = puts/short/down/breakdown/below SMA
- timeframe: infer from context — 'this week', 'swing trade', 'months', 'quarter'
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

        parsed    = json.loads(raw_resp)
        tickers   = [str(t).upper().strip() for t in (parsed.get("tickers") or []) if t]
        direction = parsed.get("direction", "unknown")
        timeframe = parsed.get("timeframe", "unknown")
        thesis    = parsed.get("thesis") or None

        if direction not in ("bullish", "bearish", "unknown"):
            direction = "unknown"
        if timeframe not in ("this week", "this month", "1-3 months", "unknown"):
            timeframe = "unknown"

        # fallback: if LLM missed direction, use correlations scoring
        if direction == "unknown":
            direction = detect_direction(raw)

        # validate tickers — if LLM returned something not in known set,
        # try correlations lookup as correction
        from bot.correlations import find_ticker_in_text
        from bot.config import get_known_tickers
        known = get_known_tickers()
        validated = []
        for t in tickers:
            if t in known:
                validated.append(t)
            else:
                # LLM hallucinated a ticker — try company name lookup
                resolved = find_ticker_in_text(raw)
                if resolved and resolved not in validated:
                    validated.append(resolved)
        if not validated:
            validated = tickers  # fallback to LLM output if nothing resolved

        return Intake(
            raw_text  = raw,
            tickers   = tuple(validated),
            direction = direction,
            thesis    = thesis,
            timeframe = timeframe,
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
    Uses LLM if Ollama is running, falls back to regex + correlations if not.

    Handles:
      - ticker only:          "AMD"
      - ticker + direction:   "AMD calls"
      - company name:         "Microsoft is above 50SMA"
      - full thesis:          "$AMD has seized the opportunity..."
      - investor paraphrase:  "Ark bullish on TSLA long term"
    """
    raw = raw.strip()

    if _ollama_available():
        result = _llm_parse(raw, budget)
        if result and result.tickers:
            return result

    return _regex_parse(raw, budget)