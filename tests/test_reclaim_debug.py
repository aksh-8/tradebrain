"""
Diagnose why MSFT is flagged RECLAIM.

Prints how many of the last 20 weekly closes were below the 200W SMA.
The patched rule fires RECLAIM only when that count is >= 4.

Run from repo root:
    python tests/test_reclaim_debug.py
"""

from datetime import datetime
from bot.chain_yf import get_price_history


def build_weekly(history):
    weekly = {}
    for h in history:
        try:
            d = datetime.strptime(h["date"], "%Y-%m-%d")
            key = f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
            weekly[key] = h["close"]
        except Exception:
            continue
    return [weekly[k] for k in sorted(weekly.keys())]


for ticker in ["MSFT"]:
    history = get_price_history(ticker, period="5y")
    spot = history[-1]["close"]
    weekly_closes = build_weekly(history)

    sma200 = sum(weekly_closes[-200:]) / 200
    last20 = weekly_closes[-20:]
    weeks_below = sum(1 for c in last20 if c < sma200)

    print(f"{ticker}")
    print(f"  spot           = ${spot:.2f}")
    print(f"  200W SMA       = ${sma200:.2f}")
    print(f"  current > SMA? = {spot > sma200}")
    print(f"  weeks_below (of last 20) = {weeks_below}")
    print(f"  RECLAIM should fire?     = {spot > sma200 and weeks_below >= 4}")
    print()
    print("  last 20 weekly closes vs SMA:")
    for i, c in enumerate(last20, 1):
        mark = "BELOW" if c < sma200 else "above"
        print(f"    wk -{20 - i + 1:2}: ${c:8.2f}  {mark}")
