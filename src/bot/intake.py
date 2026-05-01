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

def _extract_anchor(raw: str) -> Optional[str]:
    """If first token is a known ticker, treat it as the primary trade target."""
    first = raw.split()[0].upper().strip("$.,") if raw else ""
    return first if first in KNOWN_TICKERS else None

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

    # anchor — first ticker is primary, rest are context
    anchor = _extract_anchor(raw)
    if anchor and tickers and tickers[0] != anchor:
        context = [t for t in tickers if t != anchor]
        primary = [anchor]
    else:
        primary = tickers[:1] if tickers else []
        context = tickers[1:] if len(tickers) > 1 else []

    # auto-discover unknown tickers before proceeding
    if primary and primary[0] not in KNOWN_TICKERS:
        from bot.ticker_discovery import discover_and_add
        discovery = discover_and_add(primary[0])
        if discovery["success"]:
            if discovery.get("new"):
                print(f"\n  [auto-discovery] Added {discovery['ticker']} "
                      f"({discovery.get('company_name', '')}) → "
                      f"{discovery.get('sector_name', '')} | "
                      f"beta={discovery.get('beta', '')}\n")
        else:
            print(f"\n  [auto-discovery] {discovery['message']}\n")
    return Intake(
        raw_text        = raw,
        tickers         = tuple(primary),
        context_tickers = tuple(context),
        direction       = direction,
        thesis          = thesis,
        timeframe       = timeframe,
        budget          = budget,
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
  "primary_ticker": "the ONE ticker the user wants to trade, uppercase, no $ sign",
  "context_tickers": ["other tickers mentioned as context only, not the trade target"],
  "direction": "bullish | bearish | unknown",
  "timeframe": "this week | this month | 1-3 months | unknown",
  "thesis": "the core trade idea in one sentence, or null if none"
}}
Rules:
- primary_ticker: the subject of the trade — usually the first ticker mentioned
- context_tickers: supporting tickers mentioned to explain the thesis (e.g. NVDA mentioned to explain why QBTS is interesting — NVDA is context, QBTS is primary)
- direction: bullish = calls/long/up/breakout/above SMA. bearish = puts/short/down/breakdown/below SMA
- If a company name is mentioned (e.g. Microsoft, Palantir), convert to ticker (MSFT, PLTR)
- timeframe: infer from context — 'this week', 'swing trade', 'months', 'quarter'
- thesis: clean summary of why this trade, null if just a ticker with no reasoning
"""

    import time
    for attempt in range(2):
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

            # retry once on cold start empty response
            if not raw_resp and attempt == 0:
                time.sleep(2)
                continue

            if "```" in raw_resp:
                for part in raw_resp.split("```"):
                    part = part.strip().lstrip("json").strip()
                    if part.startswith("{"):
                        raw_resp = part
                        break

            parsed       = json.loads(raw_resp)
            primary_raw  = str(parsed.get("primary_ticker") or "").upper().strip()
            context_raw  = [str(t).upper().strip() for t in (parsed.get("context_tickers") or []) if t]
            direction    = parsed.get("direction", "unknown")
            timeframe    = parsed.get("timeframe", "unknown")
            thesis       = parsed.get("thesis") or None

            if direction not in ("bullish", "bearish", "unknown"):
                direction = "unknown"
            if timeframe not in ("this week", "this month", "1-3 months", "unknown"):
                timeframe = "unknown"

            # fallback: if LLM missed direction, use correlations scoring
            if direction == "unknown":
                direction = detect_direction(raw)

            # validate primary ticker — if not in known set, try correlations lookup
            from bot.correlations import find_ticker_in_text
            from bot.config import get_known_tickers
            known = get_known_tickers()
            if primary_raw not in known:
                resolved = find_ticker_in_text(raw)
                primary_raw = resolved or primary_raw

            # anchor override — if user started with a known ticker, trust that
            anchor = _extract_anchor(raw)
            if anchor and primary_raw != anchor:
                context_raw = [primary_raw] + [t for t in context_raw if t != anchor]
                primary_raw = anchor

            # clean context — remove primary from context list, validate
            context_clean = [t for t in context_raw if t != primary_raw and t in known]

            if not primary_raw:
                return None
            
            # auto-discover unknown tickers before proceeding
            if primary_raw and primary_raw not in known:
                from bot.ticker_discovery import discover_and_add
                discovery = discover_and_add(primary_raw)
                if discovery["success"]:
                    if discovery.get("new"):
                        print(f"\n  [auto-discovery] Added {discovery['ticker']} "
                              f"({discovery.get('company_name', '')}) → "
                              f"{discovery.get('sector_name', '')} | "
                              f"beta={discovery.get('beta', '')}\n")
                else:
                    print(f"\n  [auto-discovery] {discovery['message']}\n")

            return Intake(
                raw_text        = raw,
                tickers         = (primary_raw,),
                context_tickers = tuple(context_clean),
                direction       = direction,   # type: ignore[arg-type]
                thesis          = thesis,
                timeframe       = timeframe,   # type: ignore[arg-type]
                budget          = budget,
            )

        except Exception:
            if attempt == 0:
                time.sleep(2)
                continue
            return None

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