from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Tuple


Direction = Literal["bullish", "bearish", "unknown"]
Timeframe = Literal["this week", "this month", "1-3 months", "unknown"]


@dataclass(frozen=True)
class Intake:
    """
    Structured result of parsing anything the user throws at us —
    a ticker, a tweet, a thesis paragraph, or all three.
    """
    raw_text: str                        # exactly what the user typed
    tickers: Tuple[str, ...]             # extracted tickers, primary first
    context_tickers: Tuple[str, ...]   # mentioned in thesis but not the trade target
    direction: Direction                 # bullish | bearish | unknown
    thesis: Optional[str]               # cleaned thesis text, if any
    timeframe: Timeframe                # hint at how long the trade should run
    budget: float                        # USD, per trade


@dataclass(frozen=True)
class ResearchResult:
    """
    Everything the research agent found about a ticker.
    Feeds directly into contract selection.
    """
    ticker: str
    price: float
    price_change_5d: Optional[float]
    price_change_1m: Optional[float]
    week_52_high: Optional[float]
    week_52_low: Optional[float]

    # technicals
    sma50: Optional[float]
    sma200: Optional[float]
    above_sma50: Optional[bool]
    above_sma200: Optional[bool]

    # options
    iv_rank: Optional[float]
    unusual_options_activity: Optional[str]  # human-readable summary if detected

    # analyst
    analyst_target: Optional[float]         # mean price target
    analyst_upside: Optional[float]         # % upside from current price
    analyst_rating: Optional[str]           # "Buy" | "Hold" | "Sell" | None

    # fundamentals
    avg_volume: Optional[int]
    earnings_days_away: Optional[int]

    # research
    news_summary: Optional[str]

    # LLM output
    thesis_verdict: Optional[str]
    thesis_reasoning: Optional[str]
    recommended_direction: Direction
    confidence: Literal["high", "medium", "low"]
    skip_reason: Optional[str]
    relative_strength_note:   Optional[str] = None
    expected_move: Optional[str] = None   # "±8.2% by Jun 18"
    iv_skew: Optional[str] = None   # "put skew 1.31 — market pricing downside"
    term_structure: Optional[str] = None   # "inverted 1.42 — event risk priced in"
    extension_signal:         Optional[str] = None
    macro_context:            Optional[str] = None
    unr_signal:               Optional[str] = None

@dataclass(frozen=True)
class Pick:
    """
    A specific options contract the engine is recommending.
    """
    ticker: str
    expiration: str                      # YYYY-MM-DD
    strike: float
    side: Literal["call", "put"]
    dte: int
    bid: Optional[float]
    ask: Optional[float]
    mid: float
    cost: float                          # mid * 100, what you actually pay
    breakeven: float                     # strike +/- mid
    otm_pct: float                       # % out of the money
    iv: Optional[float]                  # implied volatility
    iv_rank: Optional[float]            # contract IV vs ticker's iv_rank
    oi: Optional[int]                    # open interest
    volume: Optional[int]
    spread_pct: float                    # (ask - bid) / mid
    # Black-Scholes Greeks
    delta:       Optional[float]   # directional exposure per $1 move
    gamma:       Optional[float]   # rate of delta change
    theta:       Optional[float]   # daily time decay $ per contract
    vega:        Optional[float]   # $ change per 1-point IV move
    prob_itm:    Optional[float]   # probability of expiring ITM
    prob_profit: Optional[float]   # probability of profit at expiry
    rank_score: float                    # internal ranking score
    why: Tuple[str, ...]                 # human-readable reasons
    relaxed: bool                        # True if fallback filters were used
    relax_note: str