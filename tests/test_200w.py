"""
Test the 200W SMA 5-year fetch fix.

Run from your repo root (where you normally run tradebrain):
    python test_200w.py

PASS criteria:
  - approximated == False   (the whole point of the 5y fetch)
  - weeks >= 200            (enough real weekly closes)
  - MSFT 200W is well BELOW the old broken ~$453 value
    (a true 4-year average of a rising stock sits below its 1-year average)
"""

from datetime import datetime
from bot.chain_yf import get_price_history
from bot.market_regime import compute_sma200w_state


def weekly_count(history):
    weeks = set()
    for h in history:
        d = datetime.strptime(h["date"], "%Y-%m-%d")
        iso = d.isocalendar()
        weeks.add((iso[0], iso[1]))
    return len(weeks)


for ticker in ["MSFT", "NVDA", "AAPL"]:
    history = get_price_history(ticker, period="5y")

    if not history:
        print(f"{ticker}: no history returned (network or ticker issue)")
        continue

    spot = history[-1]["close"]
    weeks = weekly_count(history)
    result = compute_sma200w_state(history, spot)

    if result is None:
        print(f"{ticker}: None — not enough data (weeks={weeks})")
        continue

    verdict = "PASS" if (result["approximated"] is False and weeks >= 200) else "FAIL"

    print(
        f"{ticker:5} | days={len(history):4} | weeks={weeks:3} | "
        f"approximated={str(result['approximated']):5} | "
        f"200W=${result['sma_200w']:8.2f} | "
        f"spot=${spot:8.2f} | "
        f"state={result['state']:9} | "
        f"{result['pct_from_sma']:+.1f}% | {verdict}"
    )
