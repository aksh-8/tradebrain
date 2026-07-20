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
from bot.market_regime import (
    compute_market_regime,
    compute_sma200w_state,
    get_ticker_sector_etf,
    check_hard_blocks,
    format_regime_for_llm,
)

try:
    from ddgs import DDGS
    DDG_AVAILABLE = True
except ImportError:
    DDG_AVAILABLE = False

_S = _get_settings()
OLLAMA_URL = _S.ollama_url
MODEL_NAME = _S.ollama_model

# ---------------------------------------------------------------------------
# Trusted and blocked news sources for article fetching
# ---------------------------------------------------------------------------

TRUSTED_SOURCES = {
    "reuters.com",
    "cnbc.com",
    "marketwatch.com",
    "fool.com",
    "motleyfool.com",
    "finance.yahoo.com",
    "zacks.com",
    "benzinga.com",
    "stockanalysis.com",
    "investopedia.com",
    "thestreet.com",
    "businesswire.com",
    "prnewswire.com",   # press releases — primary source
    "globenewswire.com",
}

BLOCKED_SOURCES = {
    "bloomberg.com",
    "wsj.com",
    "ft.com",
    "barrons.com",
    "economist.com",
    "nytimes.com",
    "washingtonpost.com",
}


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

def _get_news(ticker: str, deep: bool = False) -> tuple[Optional[str], Optional[str]]:
    """
    Returns (headlines_str, article_context_str).
    headlines_str       — pipe-separated headlines for display
    article_context_str — full text from trusted articles for LLM

    deep=False: fetch top 3 articles, max 10 DDG results (default, fast)
    deep=True:  fetch top 5 articles, max 15 DDG results, longer text (thorough)
    """
    if not DDG_AVAILABLE:
        return None, None
    try:
        from bot.correlations import get_company_name
        company    = get_company_name(ticker) if len(ticker) <= 2 else None
        query      = f"{ticker} {company} stock" if company else f"{ticker} stock"
        ddg_limit  = 15 if deep else 10
        art_limit  = 5  if deep else 3
        word_limit = 600 if deep else 400

        with DDGS() as ddgs:
            results = list(ddgs.news(query, max_results=ddg_limit))

        if not results:
            return None, None

        headlines = [r.get("title", "") for r in results if r.get("title")]
        headlines_str = " | ".join(headlines[:5])

        # filter to trusted sources only
        def _is_trusted(r: dict) -> bool:
            url = (r.get("url") or r.get("link") or "").lower()
            source = (r.get("source") or "").lower()
            # block paywalled sources
            for blocked in BLOCKED_SOURCES:
                if blocked in url or blocked in source:
                    return False
            # prefer trusted sources
            for trusted in TRUSTED_SOURCES:
                if trusted in url or trusted in source:
                    return True
            # allow unknown sources — better than no articles
            return True

        trusted_results = [r for r in results if _is_trusted(r)]

        # fetch full text from top 3 trusted articles
        # fetch full text from top N trusted articles
        article_texts: list[str] = []
        for r in trusted_results[:art_limit]:
            url = r.get("url") or r.get("link") or ""
            if not url:
                continue
            text = _fetch_article_text(url, max_words=word_limit)
            if text:
                source = r.get("source") or url
                title  = r.get("title") or ""
                article_texts.append(f"[{source}] {title}:\n{text}")

        article_context = "\n\n---\n\n".join(article_texts) if article_texts else None
        return headlines_str, article_context

    except Exception:
        return None, None
    
    
# ---------------------------------------------------------------------------
# Article
# ---------------------------------------------------------------------------
    
def _fetch_article_text(url: str, max_words: int = 400) -> Optional[str]:
    """
    Fetches full article text from a URL.
    Returns first max_words words of body text, or None if fetch fails.
    """
    try:
        import urllib.request
        from html.parser import HTMLParser

        class _TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self.text_parts: list[str] = []
                self._skip = False

            def handle_starttag(self, tag, attrs):
                if tag in ("script", "style", "nav", "header", "footer"):
                    self._skip = True

            def handle_endtag(self, tag):
                if tag in ("script", "style", "nav", "header", "footer"):
                    self._skip = False

            def handle_data(self, data):
                if not self._skip:
                    cleaned = data.strip()
                    if len(cleaned) > 40:  # skip short fragments
                        self.text_parts.append(cleaned)

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            },
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        extractor = _TextExtractor()
        extractor.feed(html)

        full_text = " ".join(extractor.text_parts)
        words     = full_text.split()
        trimmed   = " ".join(words[:max_words])
        return trimmed if len(words) > 50 else None

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
# Relative strength vs sector and market
# ---------------------------------------------------------------------------

def _compute_relative_strength(
    ticker: str,
    ticker_change_5d: Optional[float],
) -> Optional[str]:
    """
    Compares ticker 5-day performance vs sector leader and SPY.
    Returns a formatted string for display and LLM context.
    """
    if ticker_change_5d is None:
        return None

    from bot.correlations import get_sector
    from bot.chain_yf import get_price_history

    try:
        # get sector leader
        sector_info = get_sector(ticker)
        leader = sector_info.leader if sector_info else "SPY"

        # fetch leader 5d change
        leader_hist = get_price_history(leader, period="5d")
        if len(leader_hist) >= 2:
            leader_change = (
                (leader_hist[-1]["close"] - leader_hist[0]["close"])
                / leader_hist[0]["close"] * 100
            )
        else:
            leader_change = None

        # fetch SPY 5d change
        spy_hist = get_price_history("SPY", period="5d")
        if len(spy_hist) >= 2:
            spy_change = (
                (spy_hist[-1]["close"] - spy_hist[0]["close"])
                / spy_hist[0]["close"] * 100
            )
        else:
            spy_change = None

        lines = []

        # vs sector leader
        if leader_change is not None and leader != ticker:
            diff = ticker_change_5d - leader_change
            if abs(diff) < 1.0:
                vs_leader = f"IN LINE with {leader} ({leader_change:+.1f}%)"
            elif diff > 0:
                vs_leader = f"OUTPERFORMING {leader} by {diff:.1f}pts ({leader_change:+.1f}% vs {ticker_change_5d:+.1f}%)"
            else:
                vs_leader = f"UNDERPERFORMING {leader} by {abs(diff):.1f}pts ({leader_change:+.1f}% vs {ticker_change_5d:+.1f}%)"
            lines.append(f"vs sector leader: {vs_leader}")
        elif leader == ticker:
            lines.append(f"vs sector:        {ticker} IS the sector leader")

        # vs SPY
        if spy_change is not None:
            diff_spy = ticker_change_5d - spy_change
            if abs(diff_spy) < 0.5:
                vs_spy = f"IN LINE with market ({spy_change:+.1f}%)"
            elif diff_spy > 0:
                vs_spy = f"OUTPERFORMING market by {diff_spy:.1f}pts (SPY {spy_change:+.1f}%)"
            else:
                vs_spy = f"UNDERPERFORMING market by {abs(diff_spy):.1f}pts (SPY {spy_change:+.1f}%)"
            lines.append(f"vs market:        {vs_spy}")

        return "\n".join(lines) if lines else None

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
# Expected move from ATM straddle
# ---------------------------------------------------------------------------

def _compute_expected_move(ticker: str, price: float) -> Optional[str]:
    """
    Computes expected move from ATM straddle price.
    Expected move = (ATM call ask + ATM put ask) / spot * 100
    Returns human-readable string e.g. "±8.2% ($5.34) by Jun 18"
    """
    try:
        exps = get_expirations(ticker)
        if not exps:
            return None

        # find nearest expiry 7-45 DTE
        today = date.today()
        target_exp = None
        for exp in exps:
            exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
            dte = (exp_date - today).days
            if 7 <= dte <= 45:
                target_exp = exp
                break

        if not target_exp:
            return None

        chain = get_chain(ticker, target_exp)
        if not chain:
            return None

        # find ATM call and put — closest strike to current price
        calls = [c for c in chain if c.call_put == "call" and c.ask and c.ask > 0]
        puts  = [c for c in chain if c.call_put == "put"  and c.ask and c.ask > 0]

        if not calls or not puts:
            return None

        atm_call = min(calls, key=lambda c: abs(c.strike - price))
        atm_put  = min(puts,  key=lambda c: abs(c.strike - price))

        # only use if strikes are close to ATM (within 5%)
        if abs(atm_call.strike - price) / price > 0.05:
            return None
        if abs(atm_put.strike  - price) / price > 0.05:
            return None

        straddle_price = atm_call.ask + atm_put.ask
        move_pct       = round(straddle_price / price * 100, 1)
        move_dollar    = round(straddle_price, 2)

        exp_date_str = datetime.strptime(target_exp, "%Y-%m-%d").strftime("%b %d")
        dte = (datetime.strptime(target_exp, "%Y-%m-%d").date() - today).days

        return (
            f"+-{move_pct}% (${move_dollar:.2f} straddle) by {exp_date_str} "
            f"[{dte}d] — market-implied move range"
        )

    except Exception:
        return None


# ---------------------------------------------------------------------------
# IV skew detection
# ---------------------------------------------------------------------------

def _compute_iv_skew(ticker: str, price: float) -> Optional[str]:
    """
    Computes put/call IV skew at ~25 delta.
    Skew ratio = avg OTM put IV / avg OTM call IV
    > 1.2  = put skew (fear premium — market pricing downside)
    < 0.85 = call skew (squeeze/bullish premium elevated)
    ~1.0   = neutral
    """
    try:
        exps = get_expirations(ticker)
        if not exps:
            return None

        # target 21-45 DTE for skew measurement
        today = date.today()
        target_exp = None
        for exp in exps:
            exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
            dte = (exp_date - today).days
            if 21 <= dte <= 45:
                target_exp = exp
                break

        if not target_exp:
            # fallback to any expiry
            target_exp = exps[0]

        chain = get_chain(ticker, target_exp)
        if not chain:
            return None

        # OTM puts: strike 5-20% below price, IV available
        otm_puts = [
            c for c in chain
            if c.call_put == "put"
            and c.iv and c.iv > 0
            and 0.05 <= (price - c.strike) / price <= 0.20
        ]

        # OTM calls: strike 5-20% above price, IV available
        otm_calls = [
            c for c in chain
            if c.call_put == "call"
            and c.iv and c.iv > 0
            and 0.05 <= (c.strike - price) / price <= 0.20
        ]

        if not otm_puts or not otm_calls:
            return None

        avg_put_iv  = sum(c.iv for c in otm_puts)  / len(otm_puts)
        avg_call_iv = sum(c.iv for c in otm_calls) / len(otm_calls)

        if avg_call_iv <= 0:
            return None

        skew_ratio = round(avg_put_iv / avg_call_iv, 2)

        if skew_ratio >= 1.3:
            label = f"strong put skew {skew_ratio:.2f} — market pricing significant downside risk"
        elif skew_ratio >= 1.15:
            label = f"put skew {skew_ratio:.2f} — mild fear premium, market cautious"
        elif skew_ratio <= 0.85:
            label = f"call skew {skew_ratio:.2f} — calls elevated, bullish/squeeze premium"
        else:
            label = f"neutral skew {skew_ratio:.2f} — balanced put/call premium"

        return label

    except Exception:
        return None


# ---------------------------------------------------------------------------
# Term structure (near vs far IV)
# ---------------------------------------------------------------------------

def _compute_term_structure(ticker: str, price: float) -> Optional[str]:
    """
    Compares near-term IV vs medium-term IV.
    Term ratio = near_IV / far_IV
    > 1.3  = inverted (event risk priced into near-term)
    < 0.8  = steep contango (calm near-term, far premium elevated)
    ~1.0   = flat normal
    """
    try:
        exps = get_expirations(ticker)
        if len(exps) < 2:
            return None

        today = date.today()

        # near: 7-21 DTE
        near_exp = None
        for exp in exps:
            dte = (datetime.strptime(exp, "%Y-%m-%d").date() - today).days
            if 7 <= dte <= 21:
                near_exp = exp
                break

        # far: 35-75 DTE
        far_exp = None
        for exp in exps:
            dte = (datetime.strptime(exp, "%Y-%m-%d").date() - today).days
            if 35 <= dte <= 75:
                far_exp = exp
                break

        if not near_exp or not far_exp:
            return None

        near_chain = get_chain(ticker, near_exp)
        far_chain  = get_chain(ticker, far_exp)

        if not near_chain or not far_chain:
            return None

        # ATM contracts only for IV comparison
        def _atm_iv(chain, spot):
            atm = min(
                [c for c in chain if c.iv and c.iv > 0],
                key=lambda c: abs(c.strike - spot),
                default=None,
            )
            return atm.iv if atm else None

        near_iv = _atm_iv(near_chain, price)
        far_iv  = _atm_iv(far_chain,  price)

        if not near_iv or not far_iv or far_iv <= 0:
            return None

        ratio = round(near_iv / far_iv, 2)

        near_dte = (datetime.strptime(near_exp, "%Y-%m-%d").date() - today).days
        far_dte  = (datetime.strptime(far_exp,  "%Y-%m-%d").date() - today).days

        if ratio >= 1.3:
            label = (
                f"inverted {ratio:.2f} ({near_dte}d IV={near_iv:.0%} vs "
                f"{far_dte}d IV={far_iv:.0%}) — near-term event risk priced in, "
                f"avoid buying short-dated premium"
            )
        elif ratio <= 0.8:
            label = (
                f"steep contango {ratio:.2f} ({near_dte}d IV={near_iv:.0%} vs "
                f"{far_dte}d IV={far_iv:.0%}) — calm near-term, "
                f"longer-dated options relatively expensive"
            )
        else:
            label = (
                f"normal {ratio:.2f} ({near_dte}d IV={near_iv:.0%} vs "
                f"{far_dte}d IV={far_iv:.0%}) — no unusual event risk detected"
            )

        return label

    except Exception:
        return None


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------

def _ollama_available() -> bool:
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False

# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------

def _gemini_available() -> bool:
    from bot.config import get_settings
    s = get_settings()
    return s.llm_provider == "gemini" and bool(s.gemini_api_key)


def _call_gemini(prompt: str) -> str:
    """
    Calls Gemini with automatic fallback on 503.
    Primary: gemini-2.5-pro. Fallback: gemini-2.5-flash.
    """
    import time
    from google import genai
    from bot.config import get_settings
    s = get_settings()
    client = genai.Client(api_key=s.gemini_api_key)

    models = [
        s.gemini_model,           # gemini-2.5-pro (primary)
        s.gemini_model_fallback,  # gemini-2.5-flash (fallback)
    ]

    last_err = None
    for model in models:
        try:
            response = client.models.generate_content(
                model    = model,
                contents = prompt,
            )
            if model != s.gemini_model:
                print(f"  [dim]⚡ Fallback: {model}[/dim]")
            return response.text
        except Exception as e:
            if "503" in str(e) or "UNAVAILABLE" in str(e):
                last_err = e
                if model == s.gemini_model:
                    print(f"  [dim]Gemini Pro busy — trying fallback...[/dim]")
                    time.sleep(2)
                    continue
            raise

    raise last_err if last_err else Exception("Gemini call failed without specific error")

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
    article_context: Optional[str] = None,
    technicals_block: Optional[str] = None,
    relative_strength_note: Optional[str] = None,
    expected_move:          Optional[str] = None,
    iv_skew:                Optional[str] = None,
    term_structure:         Optional[str] = None,
    macro_context:          Optional[str] = None,
    regime_context:         Optional[str] = None,
    hard_block:             Optional[str] = None,
) -> tuple[Optional[str], Optional[str], Direction, str]:

    use_gemini = _gemini_available()
    use_ollama = not use_gemini and _ollama_available()
    if not use_gemini and not use_ollama:
        return None, "No LLM available — set LLM_PROVIDER=gemini + GEMINI_API_KEY or run Ollama", "unknown", "low"

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
- Technicals:     {technicals_block or sma_note or 'unavailable'}
- IV environment: {iv_note or 'unknown'}
- Earnings:       {f'in {earnings_days_away} days' if earnings_days_away is not None else 'unknown'}
- Analyst view:   {analyst_note or 'unavailable'}
- Options flow:   {unusual_activity or 'nothing unusual'}
- Relative strength (5d): {relative_strength_note or 'not available'}
- Expected move:  {expected_move or 'not available'}
- IV skew:        {iv_skew or 'not available'}
- Term structure: {term_structure or 'not available'}
- Macro calendar: {macro_context or 'no major events in next 21 days'}

{regime_context or 'Market regime: unavailable'}
{f'HARD BLOCK: {hard_block}' if hard_block else ''}
- News headlines: {news_summary or 'none'}
- News detail:    {article_context or 'headlines only — no full articles available'}
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

TECHNICAL ANALYSIS RULES — follow these if the thesis contains chart language:
- If thesis mentions a moving average (50D, 200D, SMA, EMA), verify against the data provided and comment on it specifically.
- If thesis mentions a price target, evaluate whether it is realistic given current price, momentum, and analyst consensus.
- If thesis mentions support/resistance, confluence, breakout, or psychological levels, treat these as primary thesis drivers and evaluate them directly.
- If thesis mentions previous ATH, all-time high, or historical levels, acknowledge them as valid technical reference points.
- "Confluence" means multiple signals aligning at the same price — treat this as a stronger signal than any single indicator.
- Never ignore specific price levels mentioned in the thesis. Always comment on whether the data supports reaching those levels.
- If the thesis is purely technical (chart-based) and fundamental data is mixed, weight the technical evidence appropriately — do not default to fundamental analysis.

FUNDAMENTAL RULES:
- Always address earnings risk if within 21 days.
- Always comment on IV environment.
- Reference sector context if relevant.
- If thesis is contradicted by data, say so clearly.
- Be direct and specific. No generic statements.

Reply with ONLY valid JSON:
{{
  "verdict": "supported" | "contradicted" | "neutral",
  "direction": "bullish" | "bearish" | "unknown",
  "confidence": "high" | "medium" | "low",
  "reasoning": "3-4 sentence analysis. If thesis is technical, directly address the specific levels, confluences, and targets mentioned. Cover IV environment and top risk."
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
- If price is near a key technical level (50D SMA, 200D SMA, 52-week high/low), mention it explicitly.

Reply with ONLY valid JSON:
{{
  "direction": "bullish" | "bearish" | "unknown",
  "confidence": "high" | "medium" | "low",
  "reasoning": "3-4 sentence analysis covering price action, key technicals, IV environment, and sector context"
}}"""

    import time
    for attempt in range(2):
        try:
            if use_gemini:
                raw = _call_gemini(prompt)
            else:
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
    deep: bool = False,
) -> ResearchResult:
    ticker = ticker.upper().strip()

    # price + history
    try:
        price = get_spot(ticker)
    except ChainError:
        price = 0.0

    history         = get_price_history(ticker, period="1y")
    price_change_5d = _price_change_pct(history, 5)
    # --- relative strength ---
    rel_strength = _compute_relative_strength(ticker, price_change_5d)
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
    news_summary, article_context = _get_news(ticker, deep=deep)

    # compute full technical indicators
    from bot.technicals import compute_technicals, format_technicals_for_llm
    technicals      = compute_technicals(history)
    technicals_block = format_technicals_for_llm(technicals)

    # options intelligence
    expected_move  = _compute_expected_move(ticker, price)
    iv_skew        = _compute_iv_skew(ticker, price)
    term_structure = _compute_term_structure(ticker, price)

    # macro calendar
    from bot.macro_calendar import get_upcoming_events, format_macro_for_llm
    macro_events  = get_upcoming_events(days_ahead=21)
    macro_context = format_macro_for_llm(macro_events)

    # market regime + 200W SMA + sector detection
    regime  = compute_market_regime()

    # 200W SMA needs ~5y of data to build 200 real weekly closes. The shared
    # `history` above is only 1y (~52 weeks), which forced the approximated
    # branch every run. Fetch a dedicated 5y window; fall back to 1y history
    # if the longer fetch fails so this never breaks the research path.
    try:
        history_5y = get_price_history(ticker, period="5y")
        sma200w    = compute_sma200w_state(history_5y, price)
    except Exception:
        sma200w    = compute_sma200w_state(history, price)
        
    # detect sector ETF for rotation warnings
    sector_etf = None
    try:
        from bot.correlations import get_sector
        sector_info = get_sector(ticker)
        if sector_info:
            sector_etf = get_ticker_sector_etf(ticker, sector_info.slug)
    except Exception:
        sector_etf = None

    regime_context = format_regime_for_llm(regime, sma200w, sector_etf)

    # determine direction for hard block check
    _block_direction = "bullish"  # default assumption; refined by LLM later
    hard_block = check_hard_blocks(regime, sma200w, sector_etf, _block_direction)

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
        article_context    = article_context,
        technicals_block   = technicals_block,
        relative_strength_note = rel_strength,
        expected_move          = expected_move,
        iv_skew                = iv_skew,
        term_structure         = term_structure,
        macro_context          = macro_context,
        regime_context         = regime_context,
        hard_block             = hard_block,
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
        relative_strength_note = rel_strength,
        expected_move          = expected_move,
        iv_skew                = iv_skew,
        term_structure         = term_structure,
        extension_signal       = technicals.get("extension_signal"),
        macro_context          = macro_context,
        unr_signal             = technicals.get("unr_signal"),
        market_regime          = regime,
        sma200w_state          = sma200w,
        sector_etf             = sector_etf,
        hard_block             = hard_block,
        regime_sizing_mult     = regime.get('sizing_mult', 1.0) * (sma200w['sizing_mult'] if sma200w else 1.0),
    )