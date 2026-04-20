from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import yfinance as yf

from bot.chain_yf import get_expirations, get_chain, get_spot, ChainError
from bot.models import Intake, ResearchResult, Pick
from bot.research import research_ticker
from bot.select import select_contracts
from bot.logger import log_run

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _dte(expiration: str, today: Optional[date] = None) -> int:
    t = today or date.today()
    d = datetime.strptime(expiration, "%Y-%m-%d").date()
    return max(0, (d - t).days)


def _candidate_expirations(
    exps: list[str],
    dte_min: int,
    dte_max: int,
) -> list[str]:
    target = (dte_min + dte_max) / 2.0
    scored = []
    for e in exps:
        d = _dte(e)
        if dte_min <= d <= dte_max:
            scored.append((abs(d - target), e))
    scored.sort(key=lambda x: x[0])
    return [e for _, e in scored]


def _direction_to_side(direction: str) -> Optional[str]:
    if direction == "bullish":
        return "call"
    if direction == "bearish":
        return "put"
    return None


def _dte_window(timeframe: str) -> tuple[int, int]:
    return {
        "this week":    (5,  14),
        "this month":   (14, 35),
        "1-3 months":   (30, 90),
        "unknown":      (21, 60),
    }.get(timeframe, (21, 60))


def _dict_to_pick(d: dict, side: str) -> Pick:
    c = d["contract"]
    return Pick(
        ticker     = d["ticker"],
        expiration = c.expiration,
        strike     = c.strike,
        side       = side,          # type: ignore[arg-type]
        dte        = d["dte"],
        bid        = c.bid,
        ask        = c.ask,
        mid        = d["mid"],
        cost       = d["cost"],
        breakeven  = d["breakeven"],
        otm_pct    = d["otm_pct"],
        iv         = d["iv"],
        iv_rank    = None,          # ticker-level iv_rank attached by caller
        oi         = c.oi,
        volume     = c.volume,
        spread_pct = d["spread_pct"],
        rank_score = d["rank_score"],
        why        = d["why"],
        relaxed    = d["relaxed"],
        relax_note = d["relax_note"],
    )


# ---------------------------------------------------------------------------
# Core: picks for a single ticker
# ---------------------------------------------------------------------------

def get_picks(
    *,
    ticker: str,
    side: str,
    underlying: float,
    budget: float,
    dte_min: int = 21,
    dte_max: int = 60,
    top_n: int = 3,
) -> tuple[list[Pick], str]:
    """
    Fetches option chain and returns ranked Pick objects.
    Returns (picks, failure_reason).
    """
    try:
        exps = get_expirations(ticker)
    except ChainError as e:
        return [], str(e)

    candidates = _candidate_expirations(exps, dte_min, dte_max)
    if not candidates:
        return [], f"no expirations in DTE window [{dte_min}, {dte_max}]"

    last_reason = ""
    for exp in candidates[:6]:
        try:
            chain = get_chain(ticker, exp)
        except ChainError as e:
            last_reason = str(e)
            continue

        raw_picks, reason = select_contracts(
            contracts  = chain,
            ticker     = ticker,
            side       = side,          # type: ignore[arg-type]
            underlying = underlying,
            budget     = budget,
            dte_min    = dte_min,
            dte_max    = dte_max,
            top_n      = top_n,
        )

        if raw_picks:
            return [_dict_to_pick(p, side) for p in raw_picks], ""
        last_reason = f"{exp}: {reason}"

    return [], f"no valid contracts found; last={last_reason}"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(intake: Intake) -> tuple[ResearchResult, list[Pick], str, Optional[str]]:
    """
    Full pipeline:
      1. Research every ticker in intake
      2. Use the best-supported ticker + direction
      3. Fetch + rank contracts
      4. Return (research, picks, failure_reason)
    """
    # Step 1 — research primary ticker
    primary_ticker = intake.tickers[0] if intake.tickers else None
    if not primary_ticker:
        return _empty_research("unknown"), [], "no ticker provided", None

    research = research_ticker(
        ticker          = primary_ticker,
        thesis          = intake.thesis,
        budget          = intake.budget,
        context_tickers = list(intake.context_tickers),
    )

    # Step 2 — resolve direction
    # intake direction wins if explicit; else use what research found
    # resolve direction with LLM override logic
    user_direction = intake.direction
    llm_direction  = research.recommended_direction
    llm_verdict    = research.thesis_verdict
    llm_confidence = research.confidence

    direction_note: Optional[str] = None

    if user_direction != "unknown":
        if llm_verdict == "contradicted" and llm_confidence == "high":
            direction = llm_direction if llm_direction != "unknown" else user_direction
            direction_note = (
                f"[yellow]⚠ THESIS OVERRIDE[/yellow] — You said "
                f"[bold]{user_direction}[/bold] but data says "
                f"[bold]{direction}[/bold] with high confidence. "
                f"Showing {direction} contracts. Review reasoning above."
            )
        elif llm_verdict == "contradicted" and llm_confidence == "medium":
            direction = user_direction
            direction_note = (
                f"[yellow]⚠ THESIS WARNING[/yellow] — Data partially contradicts "
                f"your {user_direction} thesis. Proceeding with {user_direction} "
                f"contracts but review the reasoning carefully before trading."
            )
        elif llm_verdict == "neutral" and llm_confidence == "low":
            direction = user_direction
            direction_note = (
                f"[dim]⚠ No strong signal detected. Proceeding with your "
                f"{user_direction} direction — low confidence trade.[/dim]"
            )
        else:
            direction = user_direction
    else:
        direction = llm_direction

    side = _direction_to_side(direction)
    if side is None:
        return research, [], (
            f"direction is '{direction}' — need bullish or bearish to select contracts"
        ), direction_note

    # Step 3 — confidence gate
    # Low confidence = warn but still proceed (user decides, not the bot)
    dte_min, dte_max = _dte_window(intake.timeframe)

    # Step 4 — fetch live spot (research already has it, reuse)
    underlying = research.price
    if underlying <= 0:
        return research, [], f"could not get live price for {primary_ticker}"

    # Step 5 — get picks
    picks, reason = get_picks(
        ticker     = primary_ticker,
        side       = side,
        underlying = underlying,
        budget     = intake.budget,
        dte_min    = dte_min,
        dte_max    = dte_max,
        top_n      = 3,
    )

    # Attach ticker-level iv_rank to each pick
    if research.iv_rank is not None:
        picks = [
            Pick(**{**p.__dict__, "iv_rank": research.iv_rank})
            for p in picks
        ]
    
    # log every run regardless of outcome
    try:
        log_run(intake, research, picks)
    except Exception:
        pass  # never let logging break the main pipeline

    return research, picks, reason, direction_note



def _empty_research(ticker: str) -> ResearchResult:
    return ResearchResult(
        ticker               = ticker,
        price                = 0.0,
        price_change_5d      = None,
        price_change_1m      = None,
        week_52_high         = None,
        week_52_low          = None,
        iv_rank              = None,
        avg_volume           = None,
        earnings_days_away   = None,
        news_summary         = None,
        thesis_verdict       = None,
        thesis_reasoning     = None,
        recommended_direction= "unknown",
        confidence           = "low",
        skip_reason          = "no ticker provided",
    )

def run_multi(
    intake: Intake,
) -> list[tuple[ResearchResult, list[Pick], str, Optional[str]]]:
    """
    Runs the full pipeline for every ticker in intake.
    Returns results sorted by confidence then price action.
    Each element is (research, picks, failure_reason).
    """
    if not intake.tickers:
        return []

    results = []
    for ticker in intake.tickers:
        single_intake = Intake(
            raw_text  = intake.raw_text,
            tickers   = (ticker,),
            direction = intake.direction,
            thesis    = intake.thesis,
            timeframe = intake.timeframe,
            budget    = intake.budget,
        )
        research, picks, reason, direction_note = run(single_intake)
        results.append((research, picks, reason, direction_note))

    # sort: confidence high > medium > low, then by picks count
    conf_order = {"high": 0, "medium": 1, "low": 2}
    results.sort(key=lambda x: (
        conf_order.get(x[0].confidence, 9),
        -len(x[1]),
    ))

    return results