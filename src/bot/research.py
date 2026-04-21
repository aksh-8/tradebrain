from __future__ import annotations

import json
from datetime import date, datetime, time
from typing import Optional

import requests
import yfinance as yf

from bot.chain_yf import get_spot, get_price_history, get_chain, get_expirations, ChainError
from bot.models import ResearchResult, Direction
from bot.correlations import get_correlation_context, format_context_for_llm
from bot.config import get_settings as _get_settings

try:
    from ddgs import DDGS
    DDG_AVAILABLE = True
except ImportError:
    DDG_AVAILABLE = False

_S = _get_settings()
OLLAMA_URL = _S.ollama_url
MODEL_NAME = _S.ollama_model


# ---------------------------------------------------------------------------
# Price + technicals
# ---------------------------------------------------------------------------

def _price_change_pct(history: list[dict], days: int) -> Optional[float]:
    if len(history) < days:
        return None
    old = history[-days]["close"]
    new = history[-1]["close"]
    if old <= 0:
        return None
    return round((new - old) / old * 100, 2)


def _compute_sma(history: list[dict], window: int) -> Optional[float]:
    if len(history) < window:
        return None
    closes = [h["close"] for h in history[-window:]]
    return round(sum(closes) / len(closes), 2)


# ---------------------------------------------------------------------------
# Analyst data
# ---------------------------------------------------------------------------

def _get_analyst_data(ticker: str) -> tuple[Optional[float], Optional[float], Optional[str]]:
    """
    Returns (mean_target, upside_pct, rating_string).
    """
    try:
        t = yf.Ticker(ticker)
        info = t.info

        target = info.get("targetMeanPrice")
        price  = info.get("currentPrice") or info.get("regularMarketPrice")
        rating = info.get("recommendationKey", "").capitalize() or None

        if rating:
            rating_map = {
                "Strong_buy": "Strong buy",
                "Buy":        "Buy",
                "Hold":       "Hold",
                "Sell":       "Sell",
                "Strong_sell": "Strong sell",
            }
            rating = rating_map.get(rating, rating)

        upside = None
        if target and price and price > 0:
            upside = round((target - price) / price * 100, 1)

        return (
            round(float(target), 2) if target else None,
            upside,
            rating,
        )
    except Exception:
        return None, None, None


# ---------------------------------------------------------------------------
# Unusual options activity
# ---------------------------------------------------------------------------

def _get_unusual_options(ticker: str, price: float) -> Optional[str]:
    """
    Scans the nearest expiration for contracts where volume > OI by 2x+.
    Returns a human-readable summary or None.
    """
    try:
        exps = get_expirations(ticker)
        if not exps:
            return None

        chain = get_chain(ticker, exps[0])
        unusual = []

        for c in chain:
            vol = c.volume or 0
            oi  = c.oi or 0
            if oi > 0 and vol > oi * 2 and vol > 500:
                pct_otm = abs(c.strike - price) / price * 100
                unusual.append(
                    f"{c.call_put.upper()} ${c.strike:.0f} "
                    f"vol={vol:,} OI={oi:,} ({pct_otm:.1f}% OTM)"
                )

        if not unusual:
            return None

        return "Unusual activity: " + " | ".join(unusual[:3])

    except Exception:
        return None


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------

def _get_news(ticker: str) -> Optional[str]:
    if not DDG_AVAILABLE:
        return None
    try:
        with DDGS() as ddgs:
            results = list(ddgs.news(f"{ticker} stock", max_results=6))
        if not results:
            return None
        headlines = [r.get("title", "") for r in results if r.get("title")]
        return " | ".join(headlines[:5])
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Earnings
# ---------------------------------------------------------------------------

def _get_earnings_days_away(ticker: str) -> Optional[int]:
    try:
        cal = yf.Ticker(ticker).calendar
        if cal is None:
            return None
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


# ---------------------------------------------------------------------------
# IV rank proxy
# ---------------------------------------------------------------------------

def _get_hv_rank(history: list[dict]) -> Optional[float]:
    """
    Computes Historical Volatility rank (0-100) from price history.
    
    HV rank = (current_30d_HV - min_HV_period) / (max_HV_period - min_HV_period) * 100
    
    This is a legitimate proxy for IV rank used when historical IV data
    is unavailable. Stable across market hours and off-hours because it
    uses daily closes, not real-time options prices.
    
    Replaces the broken _get_iv_rank() proxy which averaged raw chain IV
    and returned 100 during market hours, 23 off-hours.
    """
    import math

    if len(history) < 31:
        return None

    closes = [h["close"] for h in history if h["close"] > 0]
    if len(closes) < 31:
        return None

    # compute daily log returns
    log_returns = [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes))
    ]

    # compute rolling 30-day HV (annualized)
    window = 30
    hvs = []
    for i in range(window, len(log_returns) + 1):
        window_returns = log_returns[i - window:i]
        mean = sum(window_returns) / window
        variance = sum((r - mean) ** 2 for r in window_returns) / (window - 1)
        hv = math.sqrt(variance * 252)  # annualized
        hvs.append(hv)

    if len(hvs) < 2:
        return None

    current_hv = hvs[-1]
    min_hv     = min(hvs)
    max_hv     = max(hvs)

    if max_hv == min_hv:
        return 50.0  # flat volatility — return neutral rank

    rank = (current_hv - min_hv) / (max_hv - min_hv) * 100
    return round(min(100.0, max(0.0, rank)), 1)


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------

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
    sma50: Optional[float],
    sma200: Optional[float],
    above_sma50: Optional[bool],
    above_sma200: Optional[bool],
    iv_rank: Optional[float],
    earnings_days_away: Optional[int],
    analyst_target: Optional[float],
    analyst_upside: Optional[float],
    analyst_rating: Optional[str],
    unusual_activity: Optional[str],
    news_summary: Optional[str],
    thesis: Optional[str],
    context_tickers: Optional[list[str]] = None,
) -> tuple[Optional[str], Optional[str], Direction, str]:

    if not _ollama_available():
        return None, "Ollama not running — thesis check skipped", "unknown", "low"

    # sector context
    try:
        ctx = get_correlation_context(ticker)
        sector_context = format_context_for_llm(ctx)
    except Exception:
        sector_context = f"{ticker}: sector data unavailable"

    # earnings warning
    earnings_warning = ""
    if earnings_days_away is not None and earnings_days_away <= 14:
        earnings_warning = (
            f"\nCRITICAL: Earnings in {earnings_days_away} days. "
            f"IV will spike into the event then crush after. "
            f"Buying calls now = fighting IV expansion AND post-earnings IV crush. "
            f"This trade needs to work BEFORE earnings or be closed before the report."
        )

    # IV environment note
    iv_note = ""
    if iv_rank is not None:
        if iv_rank >= 70:
            iv_note = f"IV rank {iv_rank:.0f}/100 — options are EXPENSIVE. Premium is elevated. Favor defined-risk spreads over naked long calls."
        elif iv_rank <= 30:
            iv_note = f"IV rank {iv_rank:.0f}/100 — options are CHEAP. Good environment for buying premium."
        else:
            iv_note = f"IV rank {iv_rank:.0f}/100 — moderate IV environment."

    # technicals block
    sma_note = ""
    if above_sma50 is not None:
        sma_note += f"Price {'ABOVE' if above_sma50 else 'BELOW'} 50SMA (${sma50:.2f}). "
    if above_sma200 is not None:
        sma_note += f"Price {'ABOVE' if above_sma200 else 'BELOW'} 200SMA (${sma200:.2f})."

    # analyst block
    analyst_note = ""
    if analyst_target:
        analyst_note = (
            f"Analyst consensus: {analyst_rating or 'N/A'} | "
            f"Mean target ${analyst_target:.2f} "
            f"({f'+{analyst_upside:.1f}%' if analyst_upside and analyst_upside > 0 else f'{analyst_upside:.1f}%' if analyst_upside else 'N/A'} upside)"
        )
    
    context_note = ""
    if context_tickers:
        context_note = f"\nContext tickers mentioned in thesis (supporting context only, not the trade): {', '.join(context_tickers)}"

    data_block = f"""
- Price:          ${price:.2f}
- 5-day change:   {f'{price_change_5d:+.1f}%' if price_change_5d is not None else 'unknown'}
- 1-month change: {f'{price_change_1m:+.1f}%' if price_change_1m is not None else 'unknown'}
- Technicals:     {sma_note or 'unavailable'}
- IV environment: {iv_note or 'unknown'}
- Earnings:       {f'in {earnings_days_away} days' if earnings_days_away is not None else 'unknown'}
- Analyst view:   {analyst_note or 'unavailable'}
- Options flow:   {unusual_activity or 'nothing unusual'}
- News:           {news_summary or 'none'}
{context_note}
{earnings_warning}

Sector context:
{sector_context}
""".strip()

    if thesis:
        prompt = f"""You are a senior options trading analyst at a quantitative hedge fund.
A trader has submitted a thesis. Evaluate it rigorously using all available data.

Thesis: "{thesis}"

Data:
{data_block}

Your job:
1. Does the data support or contradict the thesis?
2. What is the directional bias based on ALL evidence?
3. What are the top 1-2 risks to this trade right now?
4. Is the IV environment favorable for buying options?

Rules:
- Be direct and specific. No generic statements.
- Always address earnings risk if within 21 days.
- Always comment on IV environment.
- Reference sector context if relevant.
- If thesis is contradicted by data, say so clearly.

Reply with ONLY valid JSON:
{{
  "verdict": "supported" | "contradicted" | "neutral",
  "direction": "bullish" | "bearish" | "unknown",
  "confidence": "high" | "medium" | "low",
  "reasoning": "3-4 sentence analysis covering thesis alignment, key risks, IV environment, and sector context"
}}"""
    else:
        prompt = f"""You are a senior options trading analyst at a quantitative hedge fund.
Analyze {ticker} based on all available data and give a clear directional view.

Data:
{data_block}

Your job:
1. What is the directional bias based on price action, technicals, and sector context?
2. What are the top 1-2 risks right now?
3. Is the IV environment favorable for buying options?

Rules:
- Be direct. No hedging every sentence.
- Always address earnings risk if within 21 days.
- Always comment on IV environment.
- Reference the sector leader and beta if relevant.

Reply with ONLY valid JSON:
{{
  "direction": "bullish" | "bearish" | "unknown",
  "confidence": "high" | "medium" | "low",
  "reasoning": "3-4 sentence analysis covering price action, key risks, IV environment, and sector context"
}}"""

    import time
    for attempt in range(2):
        try:
            r = requests.post(
                OLLAMA_URL,
                json={
                    "model":   MODEL_NAME,
                    "prompt":  prompt,
                    "stream":  False,
                    "options": {"temperature": 0, "top_p": 1},
                },
                timeout=_S.ollama_timeout,
            )
            raw = r.json().get("response", "").strip()

            # retry once on cold start empty response
            if not raw and attempt == 0:
                time.sleep(2)
                continue

            if "```" in raw:
                for part in raw.split("```"):
                    part = part.strip()
                    if part.startswith("json"):
                        part = part[4:].strip()
                    if part.startswith("{"):
                        raw = part
                        break

            parsed     = json.loads(raw)
            direction  = parsed.get("direction",  "unknown")
            confidence = parsed.get("confidence", "low")
            verdict    = parsed.get("verdict")
            reasoning  = parsed.get("reasoning")

            if direction not in ("bullish", "bearish", "unknown"):
                direction = "unknown"
            if confidence not in ("high", "medium", "low"):
                confidence = "low"

            return verdict, reasoning, direction, confidence  # type: ignore[return-value]

        except Exception as e:
            if attempt == 0:
                time.sleep(2)
                continue
            return None, f"LLM parse error: {e}", "unknown", "low"

    return None, "LLM returned empty response after retry", "unknown", "low"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def research_ticker(
    ticker: str,
    thesis: Optional[str],
    budget: float,
    context_tickers: Optional[list[str]] = None,
) -> ResearchResult:
    ticker = ticker.upper().strip()

    # price + history
    try:
        price = get_spot(ticker)
    except ChainError:
        price = 0.0

    history         = get_price_history(ticker, period="6mo")
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

    # technicals
    sma50        = _compute_sma(history, 50)
    sma200       = _compute_sma(history, 200)
    above_sma50  = (price > sma50)  if sma50  and price > 0 else None
    above_sma200 = (price > sma200) if sma200 and price > 0 else None

    # options data
    iv_rank            = _get_hv_rank(history)   # HV rank proxy — stable on/off hours
    unusual_activity   = _get_unusual_options(ticker, price)
    earnings_days_away = _get_earnings_days_away(ticker)

    # analyst
    analyst_target, analyst_upside, analyst_rating = _get_analyst_data(ticker)

    # news
    news_summary = _get_news(ticker)

    # LLM thesis check
    verdict, reasoning, direction, confidence = _check_thesis(
        ticker             = ticker,
        price              = price,
        price_change_5d    = price_change_5d,
        price_change_1m    = price_change_1m,
        sma50              = sma50,
        sma200             = sma200,
        above_sma50        = above_sma50,
        above_sma200       = above_sma200,
        iv_rank            = iv_rank,
        earnings_days_away = earnings_days_away,
        analyst_target     = analyst_target,
        analyst_upside     = analyst_upside,
        analyst_rating     = analyst_rating,
        unusual_activity   = unusual_activity,
        news_summary       = news_summary,
        thesis             = thesis,
        context_tickers    = context_tickers or [],
    )

    skip_reason: Optional[str] = None
    if confidence == "low":
        skip_reason = "data mixed or insufficient — no clear edge detected"

    return ResearchResult(
        ticker               = ticker,
        price                = price,
        price_change_5d      = price_change_5d,
        price_change_1m      = price_change_1m,
        week_52_high         = week_52_high,
        week_52_low          = week_52_low,
        sma50                = sma50,
        sma200               = sma200,
        above_sma50          = above_sma50,
        above_sma200         = above_sma200,
        iv_rank              = iv_rank,
        unusual_options_activity = unusual_activity,
        analyst_target       = analyst_target,
        analyst_upside       = analyst_upside,
        analyst_rating       = analyst_rating,
        avg_volume           = avg_volume,
        earnings_days_away   = earnings_days_away,
        news_summary         = news_summary,
        thesis_verdict       = verdict,
        thesis_reasoning     = reasoning,
        recommended_direction= direction,
        confidence           = confidence,
        skip_reason          = skip_reason,
    )