from __future__ import annotations

import json
from datetime import date, datetime
from typing import Optional

import requests
import yfinance as yf

from bot.chain_yf import get_spot, get_price_history, get_chain, get_expirations, ChainError
from bot.models import ResearchResult, Direction
from bot.correlations import get_correlation_context, format_context_for_llm

try:
    from ddgs import DDGS
    DDG_AVAILABLE = True
except ImportError:
    DDG_AVAILABLE = False

OLLAMA_URL  = "http://localhost:11434/api/generate"
MODEL_NAME = "qwen2.5:14b"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _price_change_pct(history: list[dict], days: int) -> Optional[float]:
    if len(history) < days:
        return None
    old = history[-days]["close"]
    new = history[-1]["close"]
    if old <= 0:
        return None
    return round((new - old) / old * 100, 2)


def _get_earnings_days_away(ticker: str) -> Optional[int]:
    try:
        cal = yf.Ticker(ticker).calendar
        if cal is None:
            return None
        # yfinance returns a dict or DataFrame depending on version
        if isinstance(cal, dict):
            val = cal.get("Earnings Date")
            if val is None:
                return None
            if isinstance(val, (list, tuple)):
                val = val[0]
        else:
            if cal.empty:
                return None
            if "Earnings Date" in cal.index:
                val = cal.loc["Earnings Date"].iloc[0]
            else:
                return None
        if hasattr(val, "date"):
            d = val.date()
        else:
            d = datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
        days_away = (d - date.today()).days
        return days_away if days_away >= 0 else None
    except Exception:
        return None


def _get_iv_rank(ticker: str) -> Optional[float]:
    """
    Approximates IV rank using current options chain.
    True IV rank needs a year of daily IV history (paid data).
    We return current average IV normalised to 0-100 as a proxy.
    """
    try:
        exps = get_expirations(ticker)
        if not exps:
            return None
        chain = get_chain(ticker, exps[0])
        ivs = [c.iv for c in chain if c.iv is not None and c.iv > 0]
        if not ivs:
            return None
        avg_iv = sum(ivs) / len(ivs)
        # IV of 0.20 → ~20, 0.80 → ~80, clamped 0-100
        return round(min(100.0, max(0.0, avg_iv * 100)), 1)
    except Exception:
        return None


def _get_news(ticker: str) -> Optional[str]:
    if not DDG_AVAILABLE:
        return None
    try:
        with DDGS() as ddgs:
            results = list(ddgs.news(f"{ticker} stock", max_results=5))
        if not results:
            return None
        headlines = [r.get("title", "") for r in results if r.get("title")]
        return " | ".join(headlines[:4])
    except Exception:
        return None


def _ollama_available() -> bool:
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def _check_thesis(
    ticker: str,
    price: float,
    price_change_5d: Optional[float],
    price_change_1m: Optional[float],
    iv_rank: Optional[float],
    earnings_days_away: Optional[int],
    news_summary: Optional[str],
    thesis: Optional[str],
) -> tuple[Optional[str], Optional[str], Direction, str]:
    if not _ollama_available():
        return None, "Ollama not running — thesis check skipped", "unknown", "low"

    # --- sector context from correlations ---
    try:
        ctx = get_correlation_context(ticker)
        sector_context = format_context_for_llm(ctx)
    except Exception:
        sector_context = f"{ticker}: sector data unavailable"

    # --- earnings risk flag ---
    earnings_warning = ""
    if earnings_days_away is not None and earnings_days_away <= 14:
        earnings_warning = (
            f"\nWARNING: Earnings in {earnings_days_away} days. "
            f"IV will spike into the event and crush after. "
            f"Buying calls now means fighting IV expansion AND post-earnings IV crush."
        )

    data_block = f"""
- Current price:  ${price:.2f}
- 5-day change:   {f'{price_change_5d:+.1f}%' if price_change_5d is not None else 'unknown'}
- 1-month change: {f'{price_change_1m:+.1f}%' if price_change_1m is not None else 'unknown'}
- IV rank:        {f'{iv_rank:.0f}/100' if iv_rank is not None else 'unknown'}
- Earnings in:    {f'{earnings_days_away} days' if earnings_days_away is not None else 'unknown'}
- Recent news:    {news_summary or 'none available'}
{earnings_warning}

Sector context:
{sector_context}
""".strip()

    if thesis:
        prompt = f"""You are a senior options trading research analyst.
A trader has a thesis about {ticker}. Your job is to evaluate it against real data and sector context.

Thesis: "{thesis}"

Data:
{data_block}

Rules:
- If earnings are within 14 days, always flag the IV crush risk in your reasoning.
- If IV rank is above 70, note that buying premium is expensive.
- If the sector leader is weak, note the headwind even if this ticker looks strong.
- Be direct. Do not hedge every sentence.

Reply with ONLY valid JSON — no explanation outside the JSON:
{{
  "verdict": "supported" | "contradicted" | "neutral",
  "direction": "bullish" | "bearish" | "unknown",
  "confidence": "high" | "medium" | "low",
  "reasoning": "2-3 sentence explanation covering thesis, data, sector context, and any risks"
}}"""
    else:
        prompt = f"""You are a senior options trading research analyst.
Based on the following data about {ticker}, determine the directional bias and key risks.

Data:
{data_block}

Rules:
- If earnings are within 14 days, flag the IV crush risk.
- If IV rank is above 70, note that buying premium is expensive.
- Consider the sector context — is the leader strong or weak?
- Be direct. Do not hedge every sentence.

Reply with ONLY valid JSON — no explanation outside the JSON:
{{
  "direction": "bullish" | "bearish" | "unknown",
  "confidence": "high" | "medium" | "low",
  "reasoning": "2-3 sentence explanation covering price action, sector context, and key risks"
}}"""

    try:
        r = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL_NAME,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0, "top_p": 1},
            },
            timeout=120,
        )
        raw = r.json().get("response", "").strip()

        if "```" in raw:
            for part in raw.split("```"):
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                if part.startswith("{"):
                    raw = part
                    break

        parsed     = json.loads(raw)
        direction  = parsed.get("direction", "unknown")
        confidence = parsed.get("confidence", "low")
        verdict    = parsed.get("verdict")
        reasoning  = parsed.get("reasoning")

        if direction  not in ("bullish", "bearish", "unknown"):
            direction = "unknown"
        if confidence not in ("high", "medium", "low"):
            confidence = "low"

        return verdict, reasoning, direction, confidence  # type: ignore[return-value]

    except Exception as e:
        return None, f"LLM parse error: {e}", "unknown", "low"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def research_ticker(
    ticker: str,
    thesis: Optional[str],
    budget: float,
) -> ResearchResult:
    """
    Layer 2 entry point.
    Pulls all free data, runs thesis check, returns ResearchResult.
    """
    ticker = ticker.upper().strip()

    # --- price + history ---
    try:
        price = get_spot(ticker)
    except ChainError:
        price = 0.0

    history        = get_price_history(ticker, period="3mo")
    price_change_5d = _price_change_pct(history, 5)
    price_change_1m = _price_change_pct(history, 21)

    week_52_high: Optional[float] = None
    week_52_low:  Optional[float] = None
    avg_volume:   Optional[int]   = None

    if history:
        closes       = [h["close"] for h in history]
        week_52_high = round(max(closes), 2)
        week_52_low  = round(min(closes), 2)
        vols         = [h["volume"] for h in history if h["volume"] > 0]
        avg_volume   = int(sum(vols) / len(vols)) if vols else None

    # --- options data ---
    iv_rank            = _get_iv_rank(ticker)
    earnings_days_away = _get_earnings_days_away(ticker)

    # --- news ---
    news_summary = _get_news(ticker)

    # --- LLM thesis check ---
    verdict, reasoning, direction, confidence = _check_thesis(
        ticker=ticker,
        price=price,
        price_change_5d=price_change_5d,
        price_change_1m=price_change_1m,
        iv_rank=iv_rank,
        earnings_days_away=earnings_days_away,
        news_summary=news_summary,
        thesis=thesis,
    )

    skip_reason: Optional[str] = None
    if confidence == "low":
        skip_reason = "data mixed or insufficient — no clear edge detected"

    return ResearchResult(
        ticker=ticker,
        price=price,
        price_change_5d=price_change_5d,
        price_change_1m=price_change_1m,
        week_52_high=week_52_high,
        week_52_low=week_52_low,
        iv_rank=iv_rank,
        avg_volume=avg_volume,
        earnings_days_away=earnings_days_away,
        news_summary=news_summary,
        thesis_verdict=verdict,
        thesis_reasoning=reasoning,
        recommended_direction=direction,
        confidence=confidence,
        skip_reason=skip_reason,
    )