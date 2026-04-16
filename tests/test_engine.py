from bot.models import Intake
from bot.engine import run

intake = Intake(
    raw_text="AMD bullish breakout",
    tickers=("AMD",),
    direction="bullish",
    thesis="5 weeks green, strength building",
    timeframe="1-3 months",
    budget=500.0,
)

research, picks, reason = run(intake)
print("price:", research.price)
print("picks:", len(picks))
for p in picks:
    print(f"  {p.strike} {p.side} exp={p.expiration} cost=${p.cost:.0f} breakeven=${p.breakeven:.2f}")
if reason:
    print("reason:", reason)