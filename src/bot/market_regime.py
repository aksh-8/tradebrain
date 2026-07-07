from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

INDEXES = ["SPY", "QQQ", "SMH", "DRAM"]
INDEX_WEIGHTS = {"SPY": 1, "QQQ": 2, "SMH": 1, "DRAM": 1}  # QQQ weighted 2x

SECTORS = {
    "XLK": "tech",
    "XLV": "healthcare",
    "XLF": "financials",
    "XLE": "energy",
    "XLI": "industrials",
    "XLC": "communications",
    "XLY": "consumer discretionary",
}

# Maps bot's internal sector slugs (from sectors.json) to sector ETFs
SECTOR_MAP = {
    # Tech-adjacent → XLK
    "ai_data":         "XLK",
    "ai_infra":        "XLK",
    "ai_software":     "XLK",
    "big_tech":        "XLK",
    "cloud_cyber":     "XLK",
    "semis_compute":   "XLK",
    "semis_equipment": "XLK",
    "semis_memory":    "XLK",
    "quantum":         "XLK",
    "robotics":        "XLK",
    "china_adr":       "XLK",   # most China ADRs are tech

    # Communications → XLC
    "social_media":    "XLC",
    "entertainment":   "XLC",

    # Financials → XLF
    "banks":           "XLF",
    "fintech":         "XLF",

    # Healthcare → XLV
    "healthtech":      "XLV",

    # Energy → XLE
    "energy_infra":    "XLE",
    "energy_trad":     "XLE",

    # Industrials → XLI
    "defense_aero":    "XLI",
    "space":           "XLI",

    # Consumer discretionary → XLY
    "ev_auto":         "XLY",
    "retail":          "XLY",
    "travel":          "XLY",

    # No sector ETF match — return None
    "etfs":            None,
    "crypto_miners":   None,
}

# Sizing multipliers by regime state
SIZING_MULTIPLIERS = {
    "RISK_ON":    1.00,
    "SELECTIVE":  0.75,
    "CAUTION":    0.50,
    "RISK_OFF":   0.10,
}

# 200W SMA sizing multipliers
SMA200W_MULTIPLIERS = {
    "AT_ZONE":   1.00,   # neutral — displayed + in Gemini prompt
    "NEAR_ZONE": 1.00,   # neutral
    "ELEVATED":  1.00,   # neutral
    "STRETCHED": 1.00,   # neutral
    "EXTENDED":  1.00,   # neutral — no more block on blow-off tops
    "BROKEN":    1.00,   # neutral — no more block on below 200W
    "RECLAIM":   1.50,   # ONLY signal that boosts sizing (INTC/MU-style setup)
}

# Cache
_regime_cache: dict = {"data": None, "ts": 0}


# ---------------------------------------------------------------------------
# Cache logic — 1 hour market hours, 4 hours after hours
# ---------------------------------------------------------------------------

def _is_market_hours() -> bool:
    from datetime import datetime
    import pytz
    try:
        et = pytz.timezone("US/Eastern")
        now = datetime.now(et)
    except Exception:
        now = datetime.now()
    # 9:30 AM - 4:00 PM ET, Mon-Fri
    if now.weekday() >= 5:
        return False
    return (now.hour, now.minute) >= (9, 30) and (now.hour, now.minute) < (16, 0)


def _cache_valid() -> bool:
    if _regime_cache["data"] is None:
        return False
    age = time.time() - _regime_cache["ts"]
    max_age = 3600 if _is_market_hours() else 14400  # 1h or 4h
    return age < max_age


# ---------------------------------------------------------------------------
# Index / sector fetching
# ---------------------------------------------------------------------------

def _get_ticker_regime(ticker: str) -> Optional[dict]:
    """
    Fetches EMA position + 5d change for a single ticker.
    """
    try:
        from bot.research import get_price_history
        from bot.technicals import compute_technicals
    except Exception:
        return None

    try:
        history = get_price_history(ticker, period="6mo")
    except Exception:
        return None
    if not history or len(history) < 60:
        return None

    tech = compute_technicals(history)
    if not tech:
        return None

    closes = [h["close"] for h in history]
    price  = closes[-1]

    ema8  = tech.get("ema_8")
    ema21 = tech.get("ema_21")
    ema50 = tech.get("ema_50")

    if not all([ema8, ema21, ema50]):
        return None

    above_8  = price >= ema8
    above_21 = price >= ema21
    above_50 = price >= ema50

    pct_5d = round((closes[-1] - closes[-6]) / closes[-6] * 100, 1) if len(closes) >= 6 else None

    if above_8 and above_21 and above_50:
        state = "above_all"
    elif above_21 and above_50:
        state = "above_21_50"
    elif above_50:
        state = "above_50_only"
    else:
        state = "below_all"

    return {
        "ticker":   ticker,
        "price":    price,
        "ema_8":    ema8,
        "ema_21":   ema21,
        "ema_50":   ema50,
        "above_8":  above_8,
        "above_21": above_21,
        "above_50": above_50,
        "state":    state,
        "pct_5d":   pct_5d,
    }


def _get_vix() -> Optional[float]:
    try:
        from bot.research import get_price_history
        history = get_price_history("^VIX", period="1mo")
        if history:
            return round(history[-1]["close"], 1)
    except Exception:
        return None
    return None


def _get_naaim() -> Optional[float]:
    """
    NAAIM sentiment — best effort weekly CSV fetch.
    Returns None on any failure (graceful degradation).
    """
    try:
        import urllib.request, csv, io
        url = "https://www.naaim.org/programs/naaim-exposure-index/csv/"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = r.read().decode("utf-8", errors="ignore")
        reader = csv.reader(io.StringIO(data))
        rows = list(reader)
        # last data row, second column typically is the index value
        for row in reversed(rows):
            if len(row) >= 2:
                try:
                    val = float(row[1])
                    if 0 <= val <= 200:
                        return round(val, 1)
                except Exception:
                    continue
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Regime computation
# ---------------------------------------------------------------------------

def compute_market_regime(force: bool = False) -> dict:
    """
    Computes full market regime — indexes, sectors, sentiment.
    Cached per hour (market hours) or 4 hours (after hours).
    """
    if not force and _cache_valid():
        return _regime_cache["data"]

    indexes = {}
    for ticker in INDEXES:
        r = _get_ticker_regime(ticker)
        if r:
            indexes[ticker] = r

    sectors = {}
    for etf in SECTORS.keys():
        r = _get_ticker_regime(etf)
        if r:
            sectors[etf] = r

    vix   = _get_vix()
    naaim = _get_naaim()

    # Compute weighted score for indexes
    score = 0
    max_score = 0
    for ticker, data in indexes.items():
        weight = INDEX_WEIGHTS.get(ticker, 1)
        max_score += weight * 3  # max 3 points per ticker (above 8, 21, 50)
        if data["above_50"]: score += weight
        if data["above_21"]: score += weight
        if data["above_8"]:  score += weight

    pct_score = score / max_score if max_score > 0 else 0

    # Determine regime state
    # First check sentiment extreme overrides
    sentiment_extreme = False
    if naaim is not None and vix is not None:
        if naaim > 95 and vix < 15:
            sentiment_extreme = True

    if pct_score >= 0.85 and not sentiment_extreme:
        state = "RISK_ON"
    elif pct_score >= 0.60:
        state = "SELECTIVE"
    elif pct_score >= 0.35:
        state = "CAUTION"
    else:
        state = "RISK_OFF"

    # Sentiment can downgrade
    if sentiment_extreme and state == "RISK_ON":
        state = "SELECTIVE"

    # Sector rankings by 5-day performance
    sector_ranked = sorted(
        [(etf, data.get("pct_5d") or 0, data) for etf, data in sectors.items()],
        key=lambda x: x[1],
        reverse=True,
    )
    leaders  = [s[0] for s in sector_ranked[:3]]
    laggards = [s[0] for s in sector_ranked[-3:]]

    # Rotation warnings — which sectors are being rotated OUT of
    rotating_out = []
    total_sectors = len(sectors)
    if total_sectors > 0:
        bottom_third_cutoff = total_sectors - max(1, total_sectors // 3)
        for i, (etf, pct, data) in enumerate(sector_ranked):
            if i >= bottom_third_cutoff and not data["above_21"]:
                rotating_out.append(etf)

    result = {
        "state":         state,
        "score":         round(pct_score * 100, 1),
        "indexes":       indexes,
        "sectors":       sectors,
        "vix":           vix,
        "naaim":         naaim,
        "sentiment_extreme": sentiment_extreme,
        "leaders":       leaders,
        "laggards":      laggards,
        "rotating_out":  rotating_out,
        "sizing_mult":   SIZING_MULTIPLIERS.get(state, 0.5),
        "computed_at":   datetime.now().isoformat(),
    }

    _regime_cache["data"] = result
    _regime_cache["ts"]   = time.time()
    return result


# ---------------------------------------------------------------------------
# 200W SMA analysis for individual tickers
# ---------------------------------------------------------------------------

def compute_sma200w_state(history: list[dict], current_price: float) -> Optional[dict]:
    """
    Computes 200-week SMA state for an individual ticker.
    Returns dict with state, price vs SMA, and sizing multiplier.
    """
    if not history or len(history) < 100 or current_price <= 0:
        return None

    # Resample daily to weekly closes
    from datetime import datetime
    weekly: dict = {}
    for h in history:
        try:
            d = datetime.strptime(h["date"], "%Y-%m-%d")
            week_key = f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
            weekly[week_key] = h["close"]
        except Exception:
            continue

    weekly_closes = [weekly[k] for k in sorted(weekly.keys())]
    if len(weekly_closes) < 200:
        # Not enough data for 200w — fall back to available window
        if len(weekly_closes) < 50:
            return None
        sma200 = sum(weekly_closes) / len(weekly_closes)
        approximated = True
    else:
        sma200 = sum(weekly_closes[-200:]) / 200
        approximated = False

    pct_from_sma = round((current_price - sma200) / sma200 * 100, 1)

    # Detect RECLAIM — was below within last 20 weeks, now above
    reclaim = False
    if len(weekly_closes) >= 20 and current_price > sma200:
        recent_below = any(
            c < (sum(weekly_closes[max(0, i-199):i+1]) / min(200, i+1))
            for i, c in enumerate(weekly_closes[-20:], start=len(weekly_closes)-20)
        )
        if recent_below:
            reclaim = True

    if current_price < sma200:
        state = "BROKEN"
        note  = f"Price below 200W SMA (${sma200:.2f}) — trend broken. Watchlist only until reclaim."
    elif reclaim:
        state = "RECLAIM"
        note  = f"Just RECLAIMED 200W SMA (${sma200:.2f}) — highest conviction long. Historical: MU +1400%, INTC +300%."
    elif abs(pct_from_sma) <= 3:
        state = "AT_ZONE"
        note  = f"AT 200W SMA (${sma200:.2f}) — institutional accumulation zone. Historical: NVDA +1700%, AMAT +400%."
    elif pct_from_sma <= 8:
        state = "NEAR_ZONE"
        note  = f"Near 200W SMA (${sma200:.2f}, +{pct_from_sma}%) — good entry."
    elif pct_from_sma <= 50:
        state = "ELEVATED"
        note  = f"ELEVATED +{pct_from_sma}% above 200W SMA (${sma200:.2f}) — reduce size 25%."
    elif pct_from_sma <= 100:
        state = "STRETCHED"
        note  = f"STRETCHED +{pct_from_sma}% above 200W SMA (${sma200:.2f}) — reduce size 50%, better entries on pullback."
    else:
        state = "EXTENDED"
        note  = f"EXTENDED +{pct_from_sma}% above 200W SMA (${sma200:.2f}) — do NOT enter, wait for pullback."

    return {
        "state":         state,
        "sma_200w":      round(sma200, 2),
        "pct_from_sma":  pct_from_sma,
        "sizing_mult":   SMA200W_MULTIPLIERS.get(state, 0.5),
        "note":          note,
        "approximated":  approximated,
    }


# ---------------------------------------------------------------------------
# Sector detection for a ticker
# ---------------------------------------------------------------------------

def get_ticker_sector_etf(ticker: str, ticker_sector: Optional[str] = None) -> Optional[str]:
    if not ticker_sector:
        return None
    return SECTOR_MAP.get(ticker_sector.lower())


# ---------------------------------------------------------------------------
# Hard block check
# ---------------------------------------------------------------------------

def check_hard_blocks(
    regime: dict,
    sma200w: Optional[dict],
    ticker_sector_etf: Optional[str],
    direction: str,
) -> Optional[str]:
    """
    Returns block reason string if trade should be blocked, else None.
    Only blocks bullish trades — bearish trades allowed in RISK_OFF.
    """
    if direction != "bullish":
        return None

    # Block 1: sector rotating out AND regime risk off
    if regime["state"] == "RISK_OFF" and ticker_sector_etf:
        if ticker_sector_etf in regime["rotating_out"]:
            return (
                f"HARD BLOCK: {ticker_sector_etf} is rotating out AND market regime is RISK OFF. "
                f"Bullish premium blocked. Override with --force."
            )

    # Block 2: sentiment extreme
    if regime.get("sentiment_extreme"):
        return (
            f"HARD BLOCK: NAAIM > 95 AND VIX < 15 — extreme sentiment, flush risk. "
            f"No aggressive bullish sizing. Override with --force."
        )

    return None


# ---------------------------------------------------------------------------
# Format for LLM injection
# ---------------------------------------------------------------------------

def format_regime_for_llm(regime: dict, sma200w: Optional[dict], sector_etf: Optional[str]) -> str:
    """
    Formats regime + 200W + sector context into LLM prompt block.
    """
    lines = ["MARKET REGIME (mandatory rules):"]
    lines.append(f"  State: {regime['state']} (score {regime['score']}/100)")
    lines.append(f"  Sizing multiplier: {regime['sizing_mult']:.2f}x normal")

    if regime['state'] == "RISK_OFF":
        lines.append("  RULE: Cap confidence at LOW. Warn user against new bullish premium.")
    elif regime['state'] == "CAUTION":
        lines.append("  RULE: Cap confidence at MEDIUM. Best setups only.")
    elif regime['state'] == "SELECTIVE":
        lines.append("  RULE: Reduce sizing to 75%. Confirm high-conviction setups only.")

    # Index summary
    idx_summary = []
    for ticker, data in regime['indexes'].items():
        pos = "✓" if data['above_21'] else "✗"
        idx_summary.append(f"{ticker}{pos}")
    lines.append(f"  Indexes vs 21EMA: {' '.join(idx_summary)}")

    # Sector context
    if sector_etf:
        sector_data = regime['sectors'].get(sector_etf)
        if sector_data:
            pct = sector_data.get('pct_5d', 0)
            in_out = "ROTATING OUT" if sector_etf in regime['rotating_out'] else "in favor"
            lines.append(f"  Ticker's sector ({sector_etf}): {pct:+.1f}% 5d — {in_out}")
            if sector_etf in regime['rotating_out']:
                lines.append(f"  RULE: Sector rotation warning — cap confidence at MEDIUM.")

    # Sentiment
    if regime.get('vix') is not None:
        vix_note = " (extreme fear)" if regime['vix'] > 25 else " (complacency)" if regime['vix'] < 15 else ""
        lines.append(f"  VIX: {regime['vix']}{vix_note}")
    if regime.get('naaim') is not None:
        naaim_note = " (extreme greed)" if regime['naaim'] > 90 else " (bearish)" if regime['naaim'] < 40 else ""
        lines.append(f"  NAAIM: {regime['naaim']}{naaim_note}")

    # 200W SMA
    if sma200w:
        lines.append("")
        lines.append("200-WEEK SMA (long-term regime):")
        lines.append(f"  State: {sma200w['state']}")
        lines.append(f"  {sma200w['note']}")
        lines.append(f"  Sizing multiplier: {sma200w['sizing_mult']:.2f}x")

    return "\n".join(lines)