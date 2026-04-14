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
    price: float                         # current price (live from yfinance)
    price_change_5d: Optional[float]    # % change over last 5 trading days
    price_change_1m: Optional[float]    # % change over last month
    week_52_high: Optional[float]
    week_52_low: Optional[float]

    iv_rank: Optional[float]            # 0-100, where IV sits vs past year
    avg_volume: Optional[int]           # 30d avg volume
    earnings_days_away: Optional[int]   # None if unknown

    news_summary: Optional[str]         # 2-3 sentence summary of recent news
    thesis_verdict: Optional[str]       # "supported" | "contradicted" | "neutral"
    thesis_reasoning: Optional[str]     # LLM explanation of verdict

    recommended_direction: Direction    # what the data suggests
    confidence: Literal["high", "medium", "low"]
    skip_reason: Optional[str]          # if confidence is low, why


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
    rank_score: float                    # internal ranking score
    why: Tuple[str, ...]                 # human-readable reasons
    relaxed: bool                        # True if fallback filters were used
    relax_note: str