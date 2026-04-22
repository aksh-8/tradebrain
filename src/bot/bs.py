from __future__ import annotations

import math
from typing import Optional


def _norm_cdf(x: float) -> float:
    """
    Standard normal CDF — Abramowitz and Stegun approximation.
    Accurate to 7 decimal places. No scipy needed.
    """
    if x < 0:
        return 1.0 - _norm_cdf(-x)
    k = 1.0 / (1.0 + 0.2316419 * x)
    poly = k * (0.319381530
              + k * (-0.356563782
              + k * (1.781477937
              + k * (-1.821255978
              + k * 1.330274429))))
    return 1.0 - (1.0 / math.sqrt(2 * math.pi)) * math.exp(-0.5 * x * x) * poly


def _norm_pdf(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def compute_greeks(
    spot: float,
    strike: float,
    dte: int,
    iv: float,
    side: str,
    premium: float,
    risk_free_rate: float = 0.05,
) -> dict:
    """
    Computes full Black-Scholes Greeks for a single option contract.

    Returns dict with:
      delta      — directional exposure per $1 stock move
      gamma      — rate of delta change per $1 stock move
      theta      — daily time decay in dollars (negative = cost per day)
      vega       — dollar change per 1-point IV move
      prob_itm   — probability of expiring in the money (approx = |delta|)
      prob_profit — probability of profit at expiry (accounts for premium paid)

    Returns empty dict if inputs are invalid.

    Args:
      spot    — current stock price
      strike  — option strike price
      dte     — days to expiration
      iv      — implied volatility as decimal (e.g. 0.65 for 65%)
      side    — "call" | "put"
      premium — option price (mid) per share, not per contract
      risk_free_rate — annualised risk-free rate (default 5%)
    """
    if dte <= 0 or iv <= 0 or spot <= 0 or strike <= 0 or premium < 0:
        return {}

    T = dte / 365.0

    try:
        d1 = (
            math.log(spot / strike)
            + (risk_free_rate + 0.5 * iv ** 2) * T
        ) / (iv * math.sqrt(T))
        d2 = d1 - iv * math.sqrt(T)
    except (ValueError, ZeroDivisionError):
        return {}

    nd1  = _norm_cdf(d1)
    nd2  = _norm_cdf(d2)
    npd1 = _norm_pdf(d1)

    # --- delta ---
    if side == "call":
        delta = nd1
    else:
        delta = nd1 - 1.0

    # --- gamma (same for calls and puts) ---
    gamma = npd1 / (spot * iv * math.sqrt(T))

    # --- theta (annualised → convert to per-day, in dollars per contract) ---
    discount = math.exp(-risk_free_rate * T)
    if side == "call":
        theta_annual = (
            -(spot * npd1 * iv) / (2 * math.sqrt(T))
            - risk_free_rate * strike * discount * nd2
        )
    else:
        theta_annual = (
            -(spot * npd1 * iv) / (2 * math.sqrt(T))
            + risk_free_rate * strike * discount * (1 - nd2)
        )
    theta_per_day = (theta_annual / 365.0) * 100  # per contract (×100 shares)

    # --- vega (per 1% IV move, in dollars per contract) ---
    vega_per_point = (spot * npd1 * math.sqrt(T)) / 100 * 100  # per contract

    # --- prob ITM (probability of expiring in the money) ---
    if side == "call":
        prob_itm = nd2
    else:
        prob_itm = 1.0 - nd2

    # --- prob profit (probability stock clears breakeven at expiry) ---
    if side == "call":
        breakeven = strike + premium
        try:
            d_be = (
                math.log(spot / breakeven)
                + (risk_free_rate - 0.5 * iv ** 2) * T
            ) / (iv * math.sqrt(T))
            prob_profit = _norm_cdf(d_be)
        except (ValueError, ZeroDivisionError):
            prob_profit = 0.0
    else:
        breakeven = strike - premium
        if breakeven <= 0:
            prob_profit = 0.99
        else:
            try:
                d_be = (
                    math.log(spot / breakeven)
                    + (risk_free_rate - 0.5 * iv ** 2) * T
                ) / (iv * math.sqrt(T))
                prob_profit = 1.0 - _norm_cdf(d_be)
            except (ValueError, ZeroDivisionError):
                prob_profit = 0.0

    return {
        "delta":       round(delta, 3),
        "gamma":       round(gamma, 4),
        "theta":       round(theta_per_day, 2),   # $ per day per contract
        "vega":        round(vega_per_point, 2),  # $ per 1pt IV move per contract
        "prob_itm":    round(prob_itm, 3),
        "prob_profit": round(prob_profit, 3),
    }