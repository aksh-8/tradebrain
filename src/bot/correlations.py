from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_SECTORS_PATH = Path(__file__).parent.parent.parent / "config" / "sectors.json"
_data: Optional[dict] = None


def _load() -> dict:
    global _data
    if _data is None:
        with open(_SECTORS_PATH, "r", encoding="utf-8") as f:
            _data = json.load(f)
    return _data


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SectorInfo:
    name: str
    slug: str
    leader: str
    leader_note: str
    tickers: tuple[str, ...]
    correlated_sectors: tuple[str, ...]
    macro_sensitivity: str
    risk_note: str
    beta_to_leader: dict[str, float]


@dataclass(frozen=True)
class CorrelationContext:
    """
    Everything the bot knows about a ticker's sector context.
    Used to enrich research and surface related trade ideas.
    """
    ticker: str
    sector: Optional[SectorInfo]
    is_sector_leader: bool
    beta_to_leader: Optional[float]
    correlated_tickers: tuple[str, ...]  # tickers that move with this one
    implied_by: tuple[str, ...]          # if these move, this ticker likely follows
    risk_note: Optional[str]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve_company_name(name: str) -> Optional[str]:
    """
    "Microsoft" -> "MSFT", "Palantir" -> "PLTR", etc.
    Returns None if no match found.
    """
    d = _load()
    name_map = d.get("company_name_map", {})
    return name_map.get(name.lower().strip())

def find_ticker_in_text(text: str) -> Optional[str]:
    """
    Scans free-form text for any known company name and returns its ticker.
    Unlike resolve_company_name(), this searches inside the text, not exact match.
    Checks longest names first to avoid 'amd' matching inside 'advanced micro devices'.
    """
    d = _load()
    name_map = d.get("company_name_map", {})
    lower = text.lower()
    for name in sorted(name_map, key=len, reverse=True):
        if name in lower:
            return name_map[name]
    return None

def get_company_name(ticker: str) -> Optional[str]:
    """
    Returns the most common company name for a ticker, or None.
    e.g. "S" -> "SentinelOne", "MSFT" -> "Microsoft"
    """
    d = _load()
    name_map = d.get("company_name_map", {})
    # reverse lookup — find first name that maps to this ticker
    ticker = ticker.upper().strip()
    for name, t in name_map.items():
        if t == ticker:
            # capitalise first letter of each word
            return " ".join(w.capitalize() for w in name.split())
    return None

def get_sector(ticker: str) -> Optional[SectorInfo]:
    """
    Returns the sector a ticker belongs to, or None.
    """
    d = _load()
    ticker = ticker.upper().strip()
    for s in d.get("sectors", []):
        if ticker in [t.upper() for t in s.get("tickers", [])]:
            return SectorInfo(
                name               = s["name"],
                slug               = s["slug"],
                leader             = s["leader"],
                leader_note        = s["leader_note"],
                tickers            = tuple(s["tickers"]),
                correlated_sectors = tuple(s.get("correlated_sectors", [])),
                macro_sensitivity  = s.get("macro_sensitivity", "medium"),
                risk_note          = s.get("risk_note", ""),
                beta_to_leader     = s.get("beta_to_leader", {}),
            )
    return None


def get_correlation_context(ticker: str) -> CorrelationContext:
    """
    Full correlation picture for a ticker.
    What moves with it, what leads it, what it implies.
    """
    d    = _load()
    ticker = ticker.upper().strip()
    sector = get_sector(ticker)

    is_leader    = sector is not None and sector.leader == ticker
    beta         = sector.beta_to_leader.get(ticker) if sector else None

    # correlated tickers = others in same sector (excluding self)
    correlated: list[str] = []
    if sector:
        correlated = [t for t in sector.tickers if t != ticker]

    # implied_by = cross-sector signals where this ticker appears in 'implies'
    implied_by: list[str] = []
    for sig in d.get("cross_sector_signals", []):
        if ticker in [t.upper() for t in sig.get("implies", [])]:
            implied_by.append(sig["trigger_ticker"].upper())

    return CorrelationContext(
        ticker             = ticker,
        sector             = sector,
        is_sector_leader   = is_leader,
        beta_to_leader     = beta,
        correlated_tickers = tuple(correlated),
        implied_by         = tuple(implied_by),
        risk_note          = sector.risk_note if sector else None,
    )


def get_implied_tickers(ticker: str, direction: str) -> list[str]:
    """
    If NVDA is bullish, what other tickers does that imply?
    Returns list of tickers sorted by relevance.
    """
    d      = _load()
    ticker = ticker.upper().strip()
    result: list[str] = []

    for sig in d.get("cross_sector_signals", []):
        if (sig["trigger_ticker"].upper() == ticker and
                sig.get("trigger_direction", "").lower() == direction.lower()):
            result.extend([t.upper() for t in sig.get("implies", [])])

    return list(dict.fromkeys(result))  # deduplicate, preserve order


def get_sector_for_slug(slug: str) -> Optional[SectorInfo]:
    d = _load()
    for s in d.get("sectors", []):
        if s["slug"] == slug:
            return SectorInfo(
                name               = s["name"],
                slug               = s["slug"],
                leader             = s["leader"],
                leader_note        = s["leader_note"],
                tickers            = tuple(s["tickers"]),
                correlated_sectors = tuple(s.get("correlated_sectors", [])),
                macro_sensitivity  = s.get("macro_sensitivity", "medium"),
                risk_note          = s.get("risk_note", ""),
                beta_to_leader     = s.get("beta_to_leader", {}),
            )
    return None


def format_context_for_llm(ctx: CorrelationContext) -> str:
    """
    Formats correlation context into a string the LLM can use
    when evaluating a thesis. Injected into the research prompt.
    """
    if ctx.sector is None:
        return f"{ctx.ticker}: no sector data available."

    lines = [
        f"Sector: {ctx.sector.name}",
        f"Sector leader: {ctx.sector.leader} — {ctx.sector.leader_note}",
    ]

    if ctx.is_sector_leader:
        lines.append(f"{ctx.ticker} IS the sector leader.")
    elif ctx.beta_to_leader:
        lines.append(
            f"{ctx.ticker} has beta ~{ctx.beta_to_leader:.2f} to {ctx.sector.leader} "
            f"(moves roughly {ctx.beta_to_leader*100:.0f}% as much as the leader)."
        )

    if ctx.implied_by:
        lines.append(
            f"When these tickers move bullishly, {ctx.ticker} typically follows: "
            + ", ".join(ctx.implied_by)
        )

    if ctx.sector.risk_note:
        lines.append(f"Risk context: {ctx.sector.risk_note}")

    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Direction + timeframe detection vocabulary
# ---------------------------------------------------------------------------

BULLISH_SIGNALS: list[str] = [
    "above 50sma", "above 200sma", "above 50ma", "above 200ma",
    "reclaimed", "reclaim", "golden cross", "breakout", "breaking out",
    "broke out", "higher high", "higher low", "uptrend", "bull flag",
    "support held", "bounced off support", "above resistance",
    "cleared resistance", "making new highs", "all time high",
    "52 week high", "squeezing", "squeeze", "gamma squeeze", "short squeeze",
    "bullish", "calls", "long", "buy", "buying", "accumulating",
    "load up", "huge deal", "massive", "strength", "strong",
    "first time since", "green", "ripping", "running", "moon",
    "upside", "surge", "push", "explosive", "opportunity", "seized",
    "impressive", "outperform", "upgrade", "raised target",
    "call sweep", "call wall", "above ask", "momentum",
    "looks ready", "ready to go", "sets up", "set up", "setting up",
    "due time", "going to", "held", "held where", "above 40", "above 50",
    "discount", "pureplay", "pure play", "independent", "undervalued",
    "hidden gem", "overlooked", "breakout setup", "lotto", "capex play",
]

BEARISH_SIGNALS: list[str] = [
    "below 50sma", "below 200sma", "below 50ma", "below 200ma",
    "death cross", "breakdown", "breaking down", "broke down",
    "lower high", "lower low", "downtrend", "bear flag",
    "lost support", "below support", "rejected at resistance",
    "overbought", "distribution", "making new lows", "52 week low",
    "rolling over", "bearish", "puts", "put", "short", "sell",
    "selling", "dumping", "avoid", "weak", "weakness", "red",
    "dropping", "falling", "crash", "dump", "downside", "warning",
    "downgrade", "cut target", "lowered target", "losing share",
    "put sweep", "put wall",
]

TIMEFRAME_MAP: dict[str, str] = {
    "today":        "this week",
    "tomorrow":     "this week",
    "this week":    "this week",
    "eod":          "this week",
    "intraday":     "this week",
    "0dte":         "this week",
    "weekly":       "this week",
    "this month":   "this month",
    "monthly":      "this month",
    "end of month": "this month",
    "few weeks":    "this month",
    "next month":   "this month",
    "swing":        "1-3 months",
    "months":       "1-3 months",
    "quarter":      "1-3 months",
    "long term":    "1-3 months",
    "longer term":  "1-3 months",
}


def detect_direction(text: str) -> str:
    """
    Returns 'bullish', 'bearish', or 'unknown'.
    Scores all signals — highest count wins.
    Tie goes bullish (calls are more common retail flow).
    """
    lower = text.lower()
    bull = sum(1 for s in BULLISH_SIGNALS if s in lower)
    bear = sum(1 for s in BEARISH_SIGNALS if s in lower)
    if bull > bear:
        return "bullish"
    if bear > bull:
        return "bearish"
    if bull == bear and bull > 0:
        return "bullish"
    return "unknown"


def detect_timeframe(text: str) -> str:
    """
    Returns timeframe string or 'unknown'.
    Checks longest phrases first to avoid partial matches.
    """
    lower = text.lower()
    for phrase, tf in sorted(TIMEFRAME_MAP.items(), key=lambda x: len(x[0]), reverse=True):
        if phrase in lower:
            return tf
    return "unknown"