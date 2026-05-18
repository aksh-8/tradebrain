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

def kelly_size(
    pop: float,
    cost: float,
    bankroll: float,
    target_multiple: float = 3.0,
) -> dict:
    """
    Computes Kelly position sizing for a long options trade.

    Args:
      pop            — probability of profit (0.0-1.0) from Black-Scholes
      cost           — contract cost in dollars (mid * 100)
      bankroll       — total trading capital in dollars
      target_multiple — expected return multiple if trade wins (default 2x)
                        conservative estimate — options can return much more

    Returns dict with:
      full_kelly_pct  — full Kelly fraction (too aggressive, shown for reference)
      half_kelly_pct  — half Kelly fraction (recommended for retail)
      suggested_usd   — dollar amount to risk (half Kelly × bankroll)
      max_contracts   — how many contracts fit in suggested_usd
      verdict         — plain English sizing recommendation
    """
    if pop <= 0 or pop >= 1 or cost <= 0 or bankroll <= 0:
        return {}

    win_prob  = pop
    loss_prob = 1.0 - pop

    # reward ratio: how much you win vs how much you lose
    # if you risk $250 and target 2x, you make $250 on a win
    reward_ratio = target_multiple - 1.0  # net gain per dollar risked

    # full Kelly formula
    full_kelly = (win_prob * reward_ratio - loss_prob) / reward_ratio
    full_kelly = max(0.0, full_kelly)  # never negative

    # half Kelly — standard retail recommendation
    # reduces variance significantly while keeping most of the edge
    half_kelly = full_kelly / 2.0

    suggested_usd  = round(half_kelly * bankroll, 2)
    max_contracts  = int(suggested_usd // cost) if cost > 0 else 0

    half_kelly_pct = round(half_kelly * 100, 1)
    full_kelly_pct = round(full_kelly * 100, 1)

    # how many times over Kelly is one contract
    one_contract_pct = (cost / bankroll) * 100
    kelly_multiple = round(one_contract_pct / half_kelly_pct, 1) if half_kelly_pct > 0 else 999

    # verdict
    if full_kelly <= 0:
        verdict = (
            f"No mathematical edge at this PoP with 3x target. "
            f"One contract = {one_contract_pct:.1f}% of bankroll. "
            f"Skip or treat as speculative lottery — size at $50 max."
        )
    elif suggested_usd < cost:
        verdict = (
            f"Kelly suggests ${suggested_usd:.0f} but one contract costs ${cost:.0f} "
            f"({one_contract_pct:.1f}% of bankroll = {kelly_multiple}x your Kelly limit). "
            f"If you trade this, you are over-betting. 1 contract maximum, high conviction only."
        )
    elif max_contracts == 1:
        verdict = (
            f"1 contract. Kelly allocation ${suggested_usd:.0f} "
            f"({half_kelly_pct:.1f}% of bankroll). Sized correctly."
        )
    else:
        verdict = (
            f"Kelly allows up to {max_contracts} contracts (${suggested_usd:.0f}). "
            f"Start with 1 — scale only after consistent wins."
        )

    return {
        "full_kelly_pct": full_kelly_pct,
        "half_kelly_pct": half_kelly_pct,
        "suggested_usd":  suggested_usd,
        "max_contracts":  max_contracts,
        "verdict":        verdict,
    }

def _standard_normal() -> float:
    """
    Box-Muller transform — standard normal sample.
    No numpy needed, pure Python math + random.
    """
    import random
    while True:
        u1 = random.random()
        u2 = random.random()
        if u1 > 0:
            break
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def monte_carlo_probs(
    spot: float,
    strike: float,
    dte: int,
    iv: float,
    side: str,
    premium: float,
    n_sims: int = 1000,
    risk_free_rate: float = 0.05,
) -> dict:
    """
    Monte Carlo simulation using Geometric Brownian Motion.

    Simulates n_sims possible stock price paths to expiry and computes
    the probability of achieving 2x, 3x, 5x, 10x contract returns.

    Args:
      spot     — current stock price
      strike   — option strike price
      dte      — days to expiration
      iv       — annualized volatility as decimal (e.g. 0.45 for 45%)
      side     — "call" | "put"
      premium  — option mid price per share (not per contract)
      n_sims   — number of simulations (default 1000, fast enough)
      risk_free_rate — annualized risk-free rate (default 5%)

    Returns dict with:
      p2x, p3x, p5x, p10x — probabilities as floats (0.0-1.0)
      target2x ... target10x — stock price needed for each multiple
      n_sims — number of simulations run
    """
    if dte <= 0 or iv <= 0 or spot <= 0 or strike <= 0 or premium <= 0:
        return {}

    T = dte / 365.0
    drift = (risk_free_rate - 0.5 * iv ** 2) * T
    diffusion = iv * math.sqrt(T)

    # thresholds — contract value needed for each multiple
    threshold_2x  = premium * 2.0
    threshold_3x  = premium * 3.0
    threshold_5x  = premium * 5.0
    threshold_10x = premium * 10.0

    count_2x = count_3x = count_5x = count_10x = 0

    for _ in range(n_sims):
        z = _standard_normal()
        # GBM: S(T) = S(0) * exp(drift + diffusion * Z)
        s_T = spot * math.exp(drift + diffusion * z)

        # option value at expiry
        if side == "call":
            value = max(0.0, s_T - strike)
        else:
            value = max(0.0, strike - s_T)

        if value >= threshold_2x:
            count_2x += 1
        if value >= threshold_3x:
            count_3x += 1
        if value >= threshold_5x:
            count_5x += 1
        if value >= threshold_10x:
            count_10x += 1

    # stock price targets for each multiple (at expiry, ignoring time value)
    if side == "call":
        target_2x  = strike + threshold_2x
        target_3x  = strike + threshold_3x
        target_5x  = strike + threshold_5x
        target_10x = strike + threshold_10x
    else:
        target_2x  = strike - threshold_2x
        target_3x  = strike - threshold_3x
        target_5x  = strike - threshold_5x
        target_10x = strike - threshold_10x

    return {
        "p2x":      round(count_2x  / n_sims, 3),
        "p3x":      round(count_3x  / n_sims, 3),
        "p5x":      round(count_5x  / n_sims, 3),
        "p10x":     round(count_10x / n_sims, 3),
        "target2x":  round(target_2x,  2),
        "target3x":  round(target_3x,  2),
        "target5x":  round(target_5x,  2),
        "target10x": round(target_10x, 2),
        "n_sims":   n_sims,
    }