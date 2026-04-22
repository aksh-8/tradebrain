from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from math import log
from typing import List, Optional, Tuple, Literal

from bot.chain_yf import YFContract
from bot.bs import compute_greeks


CallPut = Literal["call", "put"]


def _parse_exp(expiration: str) -> date:
    return datetime.strptime(expiration, "%Y-%m-%d").date()


def _dte(expiration: str, today: Optional[date] = None) -> int:
    t = today or date.today()
    return max(0, (_parse_exp(expiration) - t).days)


def _mid(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    if bid is None or ask is None:
        return None
    if bid <= 0 or ask <= 0:
        return None
    m = (bid + ask) / 2.0
    return m if m > 0 else None


def _effective_mid(
    bid: Optional[float],
    ask: Optional[float],
    last: Optional[float],
    allow_last_price_fallback: bool,
) -> Tuple[Optional[float], bool]:
    m = _mid(bid, ask)
    if m is not None:
        return m, False
    if allow_last_price_fallback and last is not None and last > 0:
        return float(last), True
    return None, False


def _spread_pct(bid: float, ask: float, mid: float) -> float:
    return (ask - bid) / mid if mid > 0 else 999.0


def _otm_pct(underlying: float, strike: float, side: CallPut) -> float:
    if underlying <= 0:
        return 999.0
    if side == "call":
        return (strike - underlying) / underlying
    return (underlying - strike) / underlying


def _breakeven(strike: float, mid: float, side: CallPut) -> float:
    if side == "call":
        return strike + mid
    return strike - mid


def select_contracts(
    *,
    contracts: List[YFContract],
    ticker: str,
    side: CallPut,
    underlying: float,
    budget: float,
    dte_min: int = 21,
    dte_max: int = 60,
    otm_min: float = 0.03,
    otm_max: float = 0.15,
    max_spread_pct: float = 0.20,
    min_oi: int = 100,
    min_volume: int = 10,
    top_n: int = 5,
    allow_last_price_fallback: bool = True,
) -> Tuple[List[dict], str]:
    """
    Filters and ranks contracts. Returns (picks, failure_reason).
    Each pick is a dict ready to build a Pick dataclass upstream.
    """
    if underlying <= 0:
        return [], "underlying price unavailable"

    strict_otm_min,  strict_otm_max  = otm_min, otm_max
    relaxed_otm_min, relaxed_otm_max = 0.00, 0.20

    def build_candidates(
        otm_lo: float,
        otm_hi: float,
        relaxed: bool,
        relax_note: str,
    ) -> Tuple[List[dict], int, int, int, int]:

        # BUG FIX: counters live inside build_candidates, returned explicitly
        # (v1 had these in outer scope where they were never updated)
        rb = rl = rm = rd = 0
        out: List[dict] = []

        for c in contracts:
            if c.call_put != side:
                continue

            d = _dte(c.expiration)
            if d < dte_min or d > dte_max:
                rd += 1
                continue

            last = c.last
            m, used_last = _effective_mid(c.bid, c.ask, last, allow_last_price_fallback)
            if m is None:
                rb += 1
                continue

            cost = m * 100.0
            if cost > budget:
                rb += 1
                continue

            sp_valid = (
                c.bid is not None and c.ask is not None
                and c.bid > 0 and c.ask > 0
                and not used_last
            )
            sp = _spread_pct(c.bid, c.ask, m) if sp_valid else 999.0  # type: ignore[arg-type]

            oi  = c.oi     or 0
            vol = c.volume or 0

            if (sp_valid and sp > max_spread_pct) or oi < min_oi or vol < min_volume:
                rl += 1
                continue

            otm = _otm_pct(underlying, c.strike, side)

            if otm < 0:
                rm += 1
                continue

            if otm < otm_lo or otm > otm_hi:
                rm += 1
                continue

            target_otm  = (otm_lo + otm_hi) / 2.0
            otm_penalty = abs(otm - target_otm)

            # Scoring — IV now included (penalise very high IV)
            iv = c.iv or 0.0
            score = 0.0
            score += (max_spread_pct - sp) * 100.0 * 3.0 if sp_valid else 0.0
            score += log(oi + 1)  * 2.0
            score += log(vol + 1) * 2.0
            score -= otm_penalty  * 100.0 * 1.5
            score -= (cost / budget) * 3.0
            score -= iv * 10.0      # high IV = expensive premium = penalise

            spread_str = f"spread={sp*100:.1f}%" if sp_valid else "spread=unknown"
            price_note = (
                f"mid=${m:.2f} (${cost:.0f})"
                if not used_last
                else f"last=${m:.2f} (${cost:.0f}) [NO BID/ASK]"
            )

            # compute Black-Scholes Greeks if IV available
            greeks = {}
            if c.iv and c.iv > 0 and m and m > 0:
                greeks = compute_greeks(
                    spot     = underlying,
                    strike   = c.strike,
                    dte      = d,
                    iv       = c.iv,
                    side     = side,
                    premium  = m,
                )

            out.append({
                "ticker":      ticker,
                "contract":    c,
                "dte":         d,
                "mid":         m,
                "cost":        cost,
                "breakeven":   _breakeven(c.strike, m, side),
                "spread_pct":  sp,
                "otm_pct":     otm,
                "iv":          c.iv,
                "delta":       greeks.get("delta"),
                "gamma":       greeks.get("gamma"),
                "theta":       greeks.get("theta"),
                "vega":        greeks.get("vega"),
                "prob_itm":    greeks.get("prob_itm"),
                "prob_profit": greeks.get("prob_profit"),
                "rank_score":  score,
                "why":         (price_note, spread_str, f"OI={oi}", f"vol={vol}", f"DTE={d}", f"OTM={otm*100:.1f}%"),
                "relaxed":     relaxed or used_last,
                "relax_note":  relax_note + (" used lastPrice" if used_last else ""),
            })

        return out, rb, rl, rm, rd

    # Pass 1 — strict window
    candidates, rb, rl, rm, rd = build_candidates(strict_otm_min, strict_otm_max, False, "")

    # Pass 2 — relaxed window if pass 1 empty
    if not candidates:
        candidates, rb, rl, rm, rd = build_candidates(
            relaxed_otm_min, relaxed_otm_max, True,
            f"relaxed OTM {strict_otm_min:.2f}-{strict_otm_max:.2f} → {relaxed_otm_min:.2f}-{relaxed_otm_max:.2f}",
        )

    if not candidates:
        return [], (
            f"no contracts passed filters "
            f"(dte={rd}, budget/quote={rb}, liquidity={rl}, moneyness={rm})"
        )

    candidates.sort(key=lambda p: p["rank_score"], reverse=True)
    return candidates[:top_n], ""