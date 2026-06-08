from __future__ import annotations

from datetime import date, datetime
from typing import Optional


MACRO_EVENTS = [
    # ── June 2026 ──
    {"date": "2026-06-11", "event": "CPI (May)",
     "impact": "very_high",
     "note": "Hot CPI = rate hike fears, growth selloff. Cool CPI = risk-on rally."},
    {"date": "2026-06-12", "event": "PPI (May)",
     "impact": "high",
     "note": "Inflation pipeline signal. Moves bonds and rate-sensitive names."},
    {"date": "2026-06-17", "event": "FOMC begins",
     "impact": "very_high",
     "note": "Fed meeting starts. Markets often drift up into FOMC then sell the news."},
    {"date": "2026-06-18", "event": "FOMC Rate Decision + Press Conference",
     "impact": "very_high",
     "note": "Rate decision + Powell press conference. IV spikes then crushes. Biggest single day risk."},
    {"date": "2026-06-20", "event": "OpEx — June monthly",
     "impact": "high",
     "note": "June monthly options expiration. Max pain pinning, gamma unwind post-expiry."},
    {"date": "2026-06-27", "event": "PCE Inflation (May)",
     "impact": "high",
     "note": "Fed preferred inflation measure. Can override FOMC tone if data diverges."},

    # ── July 2026 ──
    {"date": "2026-07-03", "event": "NFP (June)",
     "impact": "very_high",
     "note": "Jobs report. Strong jobs = rates stay high. Weak jobs = rate cut hopes."},
    {"date": "2026-07-10", "event": "CPI (June)",
     "impact": "very_high",
     "note": "Inflation data mid-cycle. Critical for Fed path going into Q3."},
    {"date": "2026-07-18", "event": "OpEx — July monthly",
     "impact": "high",
     "note": "July monthly options expiration."},
    {"date": "2026-07-29", "event": "FOMC begins",
     "impact": "very_high",
     "note": "July FOMC. Market pricing rate cut probability will drive pre-meeting drift."},
    {"date": "2026-07-30", "event": "FOMC Rate Decision",
     "impact": "very_high",
     "note": "July rate decision. First potential cut meeting — huge volatility event."},

    # ── August 2026 ──
    {"date": "2026-08-07", "event": "NFP (July)",
     "impact": "very_high",
     "note": "Jobs report. Summer liquidity thin — moves can be exaggerated."},
    {"date": "2026-08-12", "event": "CPI (July)",
     "impact": "very_high",
     "note": "Inflation print. August historically low liquidity amplifies reaction."},
    {"date": "2026-08-15", "event": "OpEx — August monthly",
     "impact": "high",
     "note": "August monthly expiration. Thin summer liquidity makes this choppier."},
    {"date": "2026-08-22", "event": "Jackson Hole Symposium begins",
     "impact": "very_high",
     "note": "Fed chair speech at Jackson Hole often sets tone for rest of year. Major market mover."},
]


def get_upcoming_events(days_ahead: int = 21) -> list[dict]:
    today = date.today()
    upcoming = []
    for event in MACRO_EVENTS:
        try:
            event_date = datetime.strptime(event["date"], "%Y-%m-%d").date()
        except Exception:
            continue
        days_away = (event_date - today).days
        if 0 <= days_away <= days_ahead:
            upcoming.append({**event, "days_away": days_away})
    return sorted(upcoming, key=lambda x: x["days_away"])


def format_macro_for_llm(events: list[dict]) -> Optional[str]:
    if not events:
        return None

    lines = ["MACRO CALENDAR — upcoming events:"]
    for e in events:
        impact_flag = "!!" if e["impact"] == "very_high" else "!"
        lines.append(
            f"  {impact_flag} {e['event']} in {e['days_away']}d "
            f"({e['date']}) — {e['note']}"
        )

    nearest_high = next(
        (e for e in events if e["impact"] in ("very_high", "high")), None
    )
    if nearest_high:
        if nearest_high["days_away"] <= 3:
            lines.append(
                "\nMACRO RISK: Major event in 3 days or less. "
                "DO NOT buy new options. IV will spike and crush. "
                "Existing positions: consider closing before the event."
            )
        elif nearest_high["days_away"] <= 7:
            lines.append(
                "\nMACRO RISK: Major event within 1 week. "
                "Size at 50% of normal. Prefer expirations AFTER the event. "
                "Avoid 0-7 DTE contracts."
            )
        elif nearest_high["days_away"] <= 14:
            lines.append(
                "\nMACRO NOTE: Major event in 1-2 weeks. "
                "Factor elevated macro risk into sizing. "
                "Prefer 30+ DTE to survive the event."
            )

    return "\n".join(lines)


def get_macro_display_lines() -> list[str]:
    events = get_upcoming_events(days_ahead=21)
    if not events:
        return []

    lines = []
    for e in events:
        days = e["days_away"]
        if days <= 3:
            color = "bold red"
        elif days <= 7:
            color = "red"
        elif days <= 14:
            color = "yellow"
        else:
            color = "dim"

        icon = "🔴" if e["impact"] == "very_high" else "⚠"
        lines.append(
            f"[{color}]{icon} {e['event']} — {days}d ({e['date']})[/{color}]"
        )
    return lines