from __future__ import annotations

import math
from typing import Optional

def _resample_to_weekly(history: list[dict]) -> list[float]:
    """
    Resamples daily OHLCV history to weekly closing prices.
    Takes the last close of each calendar week.
    Returns list of weekly closes, oldest first.
    """
    from datetime import datetime
    if not history:
        return []
    weekly: dict[str, float] = {}
    for h in history:
        try:
            d = datetime.strptime(h["date"], "%Y-%m-%d")
        except Exception:
            continue
        week_key = f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
        weekly[week_key] = h["close"]
    return [weekly[k] for k in sorted(weekly.keys())]


def compute_weekly_ema(history: list[dict], period: int) -> Optional[float]:
    """
    Computes EMA on weekly closes resampled from daily history.
    """
    weekly_closes = _resample_to_weekly(history)
    n = len(weekly_closes)
    if n < period:
        return None
    k = 2.0 / (period + 1)
    val = sum(weekly_closes[:period]) / period
    for price in weekly_closes[period:]:
        val = price * k + val * (1 - k)
    return round(val, 2)

def compute_technicals(history: list[dict]) -> dict:
    """
    Computes all standard technical indicators from OHLCV price history.

    Input: list of dicts with keys: date, open, high, low, close, volume
    Output: dict with all computed indicators

    All computations use pure Python math — no external libraries needed.
    """
    if len(history) < 20:
        return {}

    closes  = [h["close"]  for h in history]
    highs   = [h["high"]   for h in history]
    lows    = [h["low"]    for h in history]
    volumes = [h["volume"] for h in history]
    n       = len(closes)

    result: dict = {}

    # -------------------------------------------------------------------------
    # Simple Moving Averages
    # -------------------------------------------------------------------------
    def sma(period: int) -> Optional[float]:
        if n < period:
            return None
        return round(sum(closes[-period:]) / period, 2)

    result["sma_9"]   = sma(9)
    result["sma_20"]  = sma(20)
    result["sma_50"]  = sma(50)
    result["sma_100"] = sma(100)
    result["sma_200"] = sma(200)

    # -------------------------------------------------------------------------
    # Exponential Moving Averages
    # -------------------------------------------------------------------------
    def ema(period: int) -> Optional[float]:
        if n < period:
            return None
        k = 2.0 / (period + 1)
        val = sum(closes[:period]) / period
        for price in closes[period:]:
            val = price * k + val * (1 - k)
        return round(val, 2)

    result["ema_8"]  = ema(8)
    result["ema_9"]  = ema(9)
    result["ema_20"] = ema(20)
    result["ema_21"] = ema(21)
    result["ema_50"] = ema(50)

    # -------------------------------------------------------------------------
    # RSI (14)
    # -------------------------------------------------------------------------
    def rsi(period: int = 14) -> Optional[float]:
        if n < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, n):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        # initial averages
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        # smoothed averages
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100 - (100 / (1 + rs)), 1)

    result["rsi_14"] = rsi(14)

    # RSI interpretation
    rsi_val = result["rsi_14"]
    if rsi_val is not None:
        if rsi_val >= 70:
            result["rsi_note"] = f"overbought ({rsi_val})"
        elif rsi_val <= 30:
            result["rsi_note"] = f"oversold ({rsi_val})"
        else:
            result["rsi_note"] = f"neutral ({rsi_val})"

    # -------------------------------------------------------------------------
    # MACD (12, 26, 9)
    # -------------------------------------------------------------------------
    def macd() -> tuple[Optional[float], Optional[float], Optional[float]]:
        e12 = ema(12)
        e26 = ema(26)
        if e12 is None or e26 is None:
            return None, None, None
        macd_line = round(e12 - e26, 2)

        # signal line = 9-period EMA of MACD line
        # compute MACD values for each day to get signal
        if n < 35:  # need enough data for signal line
            return macd_line, None, None

        k12 = 2.0 / 13
        k26 = 2.0 / 27
        e12v = sum(closes[:12]) / 12
        e26v = sum(closes[:26]) / 26
        macd_values = []
        for i in range(26, n):
            e12v = closes[i] * k12 + e12v * (1 - k12)
            e26v = closes[i] * k26 + e26v * (1 - k26)
            macd_values.append(e12v - e26v)

        if len(macd_values) < 9:
            return macd_line, None, None

        k9 = 2.0 / 10
        signal = sum(macd_values[:9]) / 9
        for v in macd_values[9:]:
            signal = v * k9 + signal * (1 - k9)

        signal = round(signal, 2)
        histogram = round(macd_line - signal, 2)
        return macd_line, signal, histogram

    macd_line, macd_signal, macd_hist = macd()
    result["macd_line"]   = macd_line
    result["macd_signal"] = macd_signal
    result["macd_hist"]   = macd_hist

    if macd_line is not None and macd_signal is not None:
        if macd_line > macd_signal and macd_hist is not None and macd_hist > 0:
            result["macd_note"] = f"bullish (line={macd_line}, signal={macd_signal}, hist={macd_hist:+.2f})"
        elif macd_line < macd_signal:
            result["macd_note"] = f"bearish (line={macd_line}, signal={macd_signal}, hist={macd_hist:+.2f})"
        else:
            result["macd_note"] = f"neutral (line={macd_line}, signal={macd_signal}, hist={macd_hist:+.2f})"

    # -------------------------------------------------------------------------
    # Bollinger Bands (20, 2)
    # -------------------------------------------------------------------------
    def bollinger(period: int = 20, std_dev: float = 2.0):
        if n < period:
            return None, None, None
        window = closes[-period:]
        mid    = sum(window) / period
        variance = sum((x - mid) ** 2 for x in window) / period
        std    = math.sqrt(variance)
        upper  = round(mid + std_dev * std, 2)
        lower  = round(mid - std_dev * std, 2)
        return round(upper, 2), round(mid, 2), round(lower, 2)

    bb_upper, bb_mid, bb_lower = bollinger()
    result["bb_upper"] = bb_upper
    result["bb_mid"]   = bb_mid
    result["bb_lower"] = bb_lower

    current_price = closes[-1]

    # -------------------------------------------------------------------------
    # Weekly EMAs + extension signal
    # -------------------------------------------------------------------------
    weekly_closes = _resample_to_weekly(history)
    n_weekly = len(weekly_closes)

    def weekly_ema(period: int) -> Optional[float]:
        if n_weekly < period:
            return None
        k = 2.0 / (period + 1)
        val = sum(weekly_closes[:period]) / period
        for price in weekly_closes[period:]:
            val = price * k + val * (1 - k)
        return round(val, 2)

    result["weekly_ema_8"]  = weekly_ema(8)
    result["weekly_ema_21"] = weekly_ema(21)

    # extension signal — price vs weekly 8 EMA
    w8 = result.get("weekly_ema_8")
    if w8 and w8 > 0 and current_price > 0:
        pct_from_w8ema = round((current_price - w8) / w8 * 100, 1)
        result["pct_from_weekly_ema8"] = pct_from_w8ema

        if pct_from_w8ema > 25:
            result["extension_signal"] = (
                f"EXTENDED {pct_from_w8ema:+.1f}% above weekly 8 EMA (${w8:.2f}) — "
                f"DO NOT ENTER. Wait for pullback to ${w8 * 1.05:.2f}-${w8 * 1.10:.2f} entry zone."
            )
        elif pct_from_w8ema > 15:
            result["extension_signal"] = (
                f"ELEVATED {pct_from_w8ema:+.1f}% above weekly 8 EMA (${w8:.2f}) — "
                f"late-stage entry, reduce size. Better entries at ${w8 * 1.05:.2f}-${w8 * 1.08:.2f}."
            )
        elif pct_from_w8ema >= -3:
            result["extension_signal"] = (
                f"AT WEEKLY 8 EMA — {pct_from_w8ema:+.1f}% from ${w8:.2f}. "
                f"Prime entry zone. Institutional accumulation level."
            )
        elif pct_from_w8ema >= -10:
            result["extension_signal"] = (
                f"SLIGHTLY BELOW weekly 8 EMA (${w8:.2f}) — "
                f"watch for reclaim. If it holds and bounces, strong entry."
            )
        else:
            result["extension_signal"] = (
                f"BELOW weekly 8 EMA by {abs(pct_from_w8ema):.1f}% (${w8:.2f}) — "
                f"trend weakening. Avoid new longs until reclaim."
            )

    if bb_upper and bb_lower and bb_mid:
        bb_range = bb_upper - bb_lower
        if bb_range > 0:
            bb_pct = round((current_price - bb_lower) / bb_range * 100, 1)
            result["bb_pct_b"] = bb_pct  # 0=lower band, 100=upper band, 50=mid
            if bb_pct >= 80:
                result["bb_note"] = f"near upper band ({bb_pct}% — potential resistance)"
            elif bb_pct <= 20:
                result["bb_note"] = f"near lower band ({bb_pct}% — potential support)"
            else:
                result["bb_note"] = f"mid-band ({bb_pct}%)"

    # -------------------------------------------------------------------------
    # ATR (14) — Average True Range
    # -------------------------------------------------------------------------
    def atr(period: int = 14) -> Optional[float]:
        if n < period + 1:
            return None
        true_ranges = []
        for i in range(1, n):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            true_ranges.append(tr)
        if len(true_ranges) < period:
            return None
        # smoothed ATR
        atr_val = sum(true_ranges[:period]) / period
        for tr in true_ranges[period:]:
            atr_val = (atr_val * (period - 1) + tr) / period
        return round(atr_val, 2)

    result["atr_14"] = atr(14)

    # -------------------------------------------------------------------------
    # Volume analysis
    # -------------------------------------------------------------------------
    if len(volumes) >= 20:
        vol_sma20   = sum(volumes[-20:]) / 20
        current_vol = volumes[-1]
        vol_ratio   = round(current_vol / vol_sma20, 2) if vol_sma20 > 0 else None
        result["volume_sma20"] = int(vol_sma20)
        result["volume_ratio"] = vol_ratio
        if vol_ratio is not None:
            if vol_ratio >= 1.5:
                result["volume_note"] = f"{vol_ratio}x average (high volume — confirms move)"
            elif vol_ratio <= 0.7:
                result["volume_note"] = f"{vol_ratio}x average (low volume — weak conviction)"
            else:
                result["volume_note"] = f"{vol_ratio}x average (normal)"

    # -------------------------------------------------------------------------
    # Volume contraction (3-day vs 20-day avg)
    # -------------------------------------------------------------------------
    if len(volumes) >= 20:
        vol_3day  = sum(volumes[-3:]) / 3
        vol_20day = sum(volumes[-20:]) / 20
        vol_contraction = round(vol_3day / vol_20day, 2) if vol_20day > 0 else None
        result["vol_contraction_ratio"] = vol_contraction
        if vol_contraction is not None:
            if vol_contraction <= 0.6:
                result["vol_contraction_note"] = f"contracting ({vol_contraction}x avg — sellers drying up)"
            elif vol_contraction >= 1.5:
                result["vol_contraction_note"] = f"expanding ({vol_contraction}x avg)"
            else:
                result["vol_contraction_note"] = f"normal ({vol_contraction}x avg)"
    
    # -------------------------------------------------------------------------
    # EMA cluster proximity + setup signal
    # -------------------------------------------------------------------------
    ema8_val  = result.get("ema_8")
    ema21_val = result.get("ema_21")
    if ema8_val and ema21_val and current_price > 0:
        pct_from_ema8  = round((current_price - ema8_val)  / ema8_val  * 100, 1)
        pct_from_ema21 = round((current_price - ema21_val) / ema21_val * 100, 1)
        result["pct_from_ema8"]  = pct_from_ema8
        result["pct_from_ema21"] = pct_from_ema21

        at_ema_cluster    = abs(pct_from_ema8) <= 3 or abs(pct_from_ema21) <= 3
        extended_above    = pct_from_ema8 > 15
        below_ema_cluster = pct_from_ema8 < -5 and pct_from_ema21 < -5

        adx_val      = result.get("adx_14", 0) or 0
        rsi_val      = result.get("rsi_14", 50) or 50
        macd_note    = result.get("macd_note", "") or ""
        vol_contract = result.get("vol_contraction_ratio", 1.0) or 1.0
        pct_high     = result.get("pct_from_52w_high", -100) or -100
        trend_intact = adx_val >= 20
        vol_dry      = vol_contract <= 0.65
        macd_bull    = "bullish" in macd_note

        if at_ema_cluster and vol_dry and trend_intact and macd_bull:
            result["setup_signal"] = (
                "PULLBACK TO EMA — low-risk entry zone "
                "(volume contracting, trend intact, MACD bullish)"
            )
        elif at_ema_cluster and trend_intact:
            result["setup_signal"] = (
                "AT EMA SUPPORT — potential entry zone, confirm volume before entry"
            )
        elif extended_above and rsi_val >= 70:
            result["setup_signal"] = (
                f"EXTENDED + OVERBOUGHT — {pct_from_ema8:+.0f}% above 8EMA, "
                f"RSI={rsi_val} — wait for pullback to EMA cluster"
            )
        elif extended_above:
            result["setup_signal"] = (
                f"EXTENDED — {pct_from_ema8:+.0f}% above 8EMA — "
                f"wait for pullback before entry"
            )
        elif below_ema_cluster and trend_intact:
            result["setup_signal"] = (
                "BELOW EMA CLUSTER — momentum degrading, avoid new longs"
            )
        elif pct_high >= -5 and vol_dry and trend_intact:
            result["setup_signal"] = (
                "COILING NEAR HIGH — tight base building, breakout watch"
            )

    # -------------------------------------------------------------------------
    # Undercut & Rally detection
    # -------------------------------------------------------------------------
    if len(closes) >= 10 and len(history) >= 10:
        lookback_lows  = [h["low"]  for h in history[-20:-3]] if len(history) >= 23 else [h["low"] for h in history[:-3]]
        lookback_closes = closes[-20:-3] if len(closes) >= 23 else closes[:-3]
        recent_lows    = [h["low"]  for h in history[-3:]]
        recent_closes  = closes[-3:]

        if lookback_lows and lookback_closes:
            swing_low       = min(lookback_lows)
            swing_low_close = min(lookback_closes)

            # undercut: any recent low went below OR within 2% of the prior swing low
            undercut  = any(low < swing_low * 1.02 for low in recent_lows)

            # reclaim: current price is back above the swing low close
            reclaimed = current_price > swing_low_close

            # volume check
            vol_avg          = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else None
            undercut_vol     = min(volumes[-3:])  if len(volumes) >= 3  else None
            low_vol_undercut = (
                undercut_vol < vol_avg * 0.85
                if vol_avg and undercut_vol else False
            )

            # also detect: price is below swing low (undercut in progress)
            undercut_in_progress = (
                current_price < swing_low_close and
                any(low < swing_low for low in recent_lows)
            )

            if undercut and reclaimed:
                if low_vol_undercut:
                    result["unr_signal"] = (
                        f"UNDERCUT & RALLY — broke below prior low (${swing_low:.2f}) "
                        f"on low volume then reclaimed. Shorts trapped, weak hands flushed. "
                        f"High-probability bounce setup."
                    )
                else:
                    result["unr_signal"] = (
                        f"POTENTIAL U&R — undercut ${swing_low:.2f} then reclaimed. "
                        f"Watch for volume expansion to confirm next leg up."
                    )
            elif undercut_in_progress:
                result["unr_signal"] = (
                    f"UNDERCUT IN PROGRESS — below prior low (${swing_low:.2f}). "
                    f"Watch for reclaim above ${swing_low_close:.2f} — "
                    f"if it reclaims, strong U&R setup."
                )
    
    # -------------------------------------------------------------------------
    # Price structure
    # -------------------------------------------------------------------------
    week_52_high = max(highs)
    week_52_low  = min(lows)
    result["week_52_high"] = round(week_52_high, 2)
    result["week_52_low"]  = round(week_52_low, 2)

    pct_from_high = round((current_price - week_52_high) / week_52_high * 100, 1)
    pct_from_low  = round((current_price - week_52_low)  / week_52_low  * 100, 1)
    result["pct_from_52w_high"] = pct_from_high
    result["pct_from_52w_low"]  = pct_from_low

    # -------------------------------------------------------------------------
    # Trend strength — ADX (14)
    # -------------------------------------------------------------------------
    def adx(period: int = 14) -> Optional[float]:
        if n < period * 2:
            return None
        plus_dm  = []
        minus_dm = []
        true_ranges_adx = []

        for i in range(1, n):
            up   = highs[i]  - highs[i - 1]
            down = lows[i - 1] - lows[i]
            plus_dm.append(up   if up > down and up > 0   else 0)
            minus_dm.append(down if down > up and down > 0 else 0)
            tr = max(
                highs[i] - lows[i],
                abs(highs[i]  - closes[i - 1]),
                abs(lows[i]   - closes[i - 1]),
            )
            true_ranges_adx.append(tr)

        if len(true_ranges_adx) < period:
            return None

        # smoothed
        atr_s    = sum(true_ranges_adx[:period])
        plus_s   = sum(plus_dm[:period])
        minus_s  = sum(minus_dm[:period])

        dx_values = []
        for i in range(period, len(true_ranges_adx)):
            atr_s   = atr_s   - atr_s / period   + true_ranges_adx[i]
            plus_s  = plus_s  - plus_s / period   + plus_dm[i]
            minus_s = minus_s - minus_s / period  + minus_dm[i]

            plus_di  = 100 * plus_s  / atr_s if atr_s > 0 else 0
            minus_di = 100 * minus_s / atr_s if atr_s > 0 else 0
            dx_sum   = plus_di + minus_di
            dx       = 100 * abs(plus_di - minus_di) / dx_sum if dx_sum > 0 else 0
            dx_values.append(dx)

        if len(dx_values) < period:
            return None

        adx_val = sum(dx_values[-period:]) / period
        return round(adx_val, 1)

    adx_val = adx(14)
    result["adx_14"] = adx_val
    if adx_val is not None:
        if adx_val >= 25:
            result["adx_note"] = f"strong trend ({adx_val})"
        elif adx_val >= 20:
            result["adx_note"] = f"developing trend ({adx_val})"
        else:
            result["adx_note"] = f"weak/no trend ({adx_val})"

    return result


def compute_ema_exit_signal(history: list[dict], current_price: float) -> dict:
    """
    Computes EMA-based exit signals for an open options position.
    Uses underlying stock price vs 8/21/50 EMA.

    Exit strategy (from momentum trading playbook):
    - Stock closes below 8 EMA  → trim 25%
    - Stock closes below 21 EMA → trim another 25%
    - Stock closes below 50 EMA → sell full position
    """
    if len(history) < 52 or current_price <= 0:
        return {}

    closes = [h["close"] for h in history]
    n      = len(closes)

    def _ema(period: int) -> Optional[float]:
        if n < period:
            return None
        k   = 2.0 / (period + 1)
        val = sum(closes[:period]) / period
        for price in closes[period:]:
            val = price * k + val * (1 - k)
        return round(val, 2)

    ema8  = _ema(8)
    ema21 = _ema(21)
    ema50 = _ema(50)

    if not all([ema8, ema21, ema50]):
        return {}

    above_ema8  = current_price >= ema8
    above_ema21 = current_price >= ema21
    above_ema50 = current_price >= ema50

    if not above_ema50:
        action      = "SELL"
        action_note = "below 50 EMA — sell full position"
        color       = "red"
    elif not above_ema21:
        action      = "TRIM"
        action_note = "below 21 EMA — trim 25%"
        color       = "yellow"
    elif not above_ema8:
        action      = "TRIM"
        action_note = "below 8 EMA — trim 25%"
        color       = "yellow"
    else:
        action      = "HOLD"
        action_note = "above all EMAs"
        color       = "green"

    return {
        "ema8":        ema8,
        "ema21":       ema21,
        "ema50":       ema50,
        "above_ema8":  above_ema8,
        "above_ema21": above_ema21,
        "above_ema50": above_ema50,
        "action":      action,
        "action_note": action_note,
        "color":       color,
    }


def format_technicals_for_llm(t: dict) -> str:
    """
    Formats computed technicals into a clean string for LLM consumption.
    Only includes available values.
    """
    if not t:
        return "unavailable"

    lines = []

    # Moving averages
    sma_parts = []
    for period in [9, 20, 50, 100, 200]:
        val = t.get(f"sma_{period}")
        if val is not None:
            sma_parts.append(f"{period}D=${val:.2f}")
    if sma_parts:
        lines.append(f"SMA:    {', '.join(sma_parts)}")

    ema_parts = []
    for period in [9, 20, 50]:
        val = t.get(f"ema_{period}")
        if val is not None:
            ema_parts.append(f"{period}D=${val:.2f}")
    if ema_parts:
        lines.append(f"EMA:    {', '.join(ema_parts)}")

    # RSI
    if t.get("rsi_note"):
        lines.append(f"RSI:    {t['rsi_note']}")

    # MACD
    if t.get("macd_note"):
        lines.append(f"MACD:   {t['macd_note']}")

    # Bollinger Bands
    bb_u, bb_m, bb_l = t.get("bb_upper"), t.get("bb_mid"), t.get("bb_lower")
    if bb_u and bb_m and bb_l:
        lines.append(f"BB(20): upper=${bb_u:.2f}  mid=${bb_m:.2f}  lower=${bb_l:.2f}")
    if t.get("bb_note"):
        lines.append(f"        {t['bb_note']}")

    # ATR
    if t.get("atr_14"):
        lines.append(f"ATR:    ${t['atr_14']:.2f}/day (expected daily range)")

    # Volume
    if t.get("volume_note"):
        lines.append(f"Volume: {t['volume_note']}")

    # EMA proximity
    p8  = t.get("pct_from_ema8")
    p21 = t.get("pct_from_ema21")
    if p8 is not None and p21 is not None:
        lines.append(f"vs EMA: {p8:+.1f}% from 8EMA  {p21:+.1f}% from 21EMA")

    # Volume contraction
    if t.get("vol_contraction_note"):
        lines.append(f"Vol 3d: {t['vol_contraction_note']}")

    # Setup signal — highest value line for LLM
    if t.get("setup_signal"):
        lines.append(f"Setup:  *** {t['setup_signal']} ***")
    
    # U&R signal
    if t.get("unr_signal"):
        lines.append(f"U&R:    *** {t['unr_signal']} ***")
    
    # Weekly EMA extension
    w8  = t.get("weekly_ema_8")
    pw8 = t.get("pct_from_weekly_ema8")
    if w8 and pw8 is not None:
        lines.append(f"Wkly8E: ${w8:.2f}  ({pw8:+.1f}% from weekly 8 EMA)")
    if t.get("extension_signal"):
        lines.append(f"Ext:    *** {t['extension_signal']} ***")

    # ADX
    if t.get("adx_note"):
        lines.append(f"ADX:    {t['adx_note']}")

    # 52w structure
    h52 = t.get("week_52_high")
    l52 = t.get("week_52_low")
    ph  = t.get("pct_from_52w_high")
    pl  = t.get("pct_from_52w_low")
    if h52 and l52 and ph is not None and pl is not None:
        lines.append(f"52w:    high=${h52:.2f} ({ph:+.1f}%)  low=${l52:.2f} ({pl:+.1f}%)")

    return "\n".join(lines) if lines else "unavailable"