# tradebrain — Next Steps
> Current state, active work, and prioritized task list.
> Last updated: May 2026

---

## What Was Just Working On

### 1. Technical Indicators Module (`technicals.py`) ✅ JUST COMPLETED
Built a full pure-Python technical analysis engine:
- SMA 9/20/50/100/200, EMA 9/20/50
- RSI(14), MACD(12,26,9), Bollinger Bands(20,2), ATR(14), ADX(14)
- Volume ratio vs 20D average
- `format_technicals_for_llm()` renders all indicators into LLM prompt

Changed `get_price_history()` in `chain_yf.py` to return full OHLCV (was close+volume only).
Changed `research_ticker()` to use `period="1y"` (was `"6mo"`) to enable 200D SMA.

**Status**: Built and verified. Wired into `_check_thesis()` LLM prompt. **Commit pending.**

### 2. LLM Prompt Technical Analysis Rules ✅ JUST COMPLETED
Added explicit technical analysis evaluation rules to `_check_thesis()` prompt:
- Instructs LLM to address SMA confluences, price targets, support/resistance mentioned in thesis
- CRITICAL ticker anchor rule: ignore other companies in article context (fixes ARM-for-TSLA hallucination)
- Both `with thesis` and `without thesis` prompts updated

**Status**: Built. Verified with TSLA 200D SMA confluence test. **Commit pending.**

### 3. `--bankroll` CLI flag ✅ JUST COMPLETED
Allows overriding the bankroll for Kelly sizing without changing `.env`:
```bash
tradebrain "NVDA bullish" --budget 1000 --bankroll 20000
```
Shows how Kelly allocation changes with different capital levels.

**Status**: Built and verified. **Commit pending.**

### 4. `tradebrain contract` command ⚠️ PARTIALLY BUILT
Built `_cmd_contract()` in `cli.py` — analyzes a specific contract:
- Parses spec: `"INTC $150c 2026-06-18"`
- Fetches live contract price, Greeks, 2x/3x/5x/10x targets
- Runs `research_ticker()` for momentum context
- Kelly sizing

**Status**: Code written. **NOT YET TESTED.** Needs market hours testing.

---

## Commits Needed Right Now

```bash
git add .
git commit -m "feat: full technical indicators (SMA/EMA/RSI/MACD/BB/ATR/ADX), LLM technical analysis prompt rules, --bankroll flag, tradebrain contract command"
git push origin dev
git checkout main
git merge dev
git push origin main
git checkout dev
```

---

## Active Issues / Blockers

### Issue 1 — `tradebrain contract` needs market hours testing
The contract command parses the spec and fetches chain data, but bid/ask will be zero off-hours. Test during market hours:
```bash
tradebrain contract "NVDA $230c 2026-06-18" --budget 500
tradebrain contract "INTC $35c 2026-06-18" --budget 200
```

### Issue 2 — LLM reliability on Windows (qwen2.5:7b)
The 7b model occasionally confuses tickers when article context is long. The CRITICAL anchor rule in the prompt helps but doesn't fully solve it. The real fix is switching to Claude API as the LLM provider.

### Issue 3 — MP Materials sector classification
MP was auto-discovered and placed in `semis_compute` but should be in `energy_infra`. Manual fix needed in `sectors.json` — remove from `semis_compute`, add to `energy_infra`.

---

## Prioritized Next Tasks

### Tier 1 — High impact, build next

**1. Claude API as LLM provider**
Replace Ollama HTTP calls with Anthropic API for thesis evaluation. Keep Ollama as fallback.
- Add `LLM_PROVIDER=claude|ollama` to `.env`
- Add `ANTHROPIC_API_KEY=` to `.env`
- In `research.py` `_check_thesis()`: route to Claude if `LLM_PROVIDER=claude`
- Claude Sonnet — ~$0.003/run, no cold start, no hallucinations, 200k context
- Mac uses Claude API, Windows keeps Ollama 7b as fallback

**2. `tradebrain flow` command**
Parse institutional flow and scale to user budget:
```bash
tradebrain flow "APLD $35c 470k 0DTE" --budget 500
```
- Parse: ticker, strike, side, notional, expiry
- Assess: is this directional flow or hedge? (check for offsetting put flow)
- Scale: institutional $470k → user $500
- Research: why would someone put $470k on this?
- Output: full contract analysis + Kelly sizing

**3. Paper trading — log-trade command**
```bash
tradebrain log-trade NVDA --strike 220 --expiry 2026-06-18 --side call --cost 540 --quantity 1
```
- Add `paper_trades` table to SQLite
- Track: entry price, current price, P&L, DTE remaining
- `tradebrain portfolio` — view all open paper trades with live P&L

### Tier 2 — Important, build after Tier 1

**4. Monte Carlo simulation**
For each recommended contract, run 10,000 price path simulations:
- P(2x return), P(3x return), P(5x return)
- Expected value
- Best case (95th percentile), worst case (5th percentile)
- All from Geometric Brownian Motion — pure Python, no scipy needed

**5. SEC EDGAR 8-K fetch**
Fetch latest material event filings before research:
- Free API: `https://data.sec.gov/submissions/CIK{cik}.json`
- Surface: earnings surprises, contract wins, acquisitions filed in last 7 days
- Add `recent_filings` block to `data_block` in `_check_thesis()`

**6. Relative strength vs sector**
Compare ticker price action to sector leader and SPY:
- "NVDA down 4% but sector down 6% — NVDA showing relative strength"
- All data available from `get_price_history()`
- Add `relative_strength_note` to `ResearchResult` and display in research panel

**7. Macro calendar awareness**
FOMC dates, CPI/NFP, Mag 7 earnings cross-check:
- New file: `src/bot/macro_calendar.py`
- Returns upcoming macro events in next 14 days
- Injected into LLM prompt and shown in watchlist table as `Macro risk` column

### Tier 3 — Infrastructure

**8. Tests**
Fill `tests/` with proper pytest tests:
- `test_select.py` — two-pass relaxation, Greeks computation
- `test_intake.py` — direction detection, ticker extraction
- `test_correlations.py` — sector lookup, company name map
- `test_bs.py` — Greeks math validation against known values
- `test_technicals.py` — RSI, MACD, SMA against hand-computed values

**9. Auto beta recomputation**
Weekly background update for sector leader betas and cross-sector signal correlation matrix.

**10. Tradier IV rank upgrade**
Replace `_get_hv_rank()` with real IV percentile from Tradier API.
Requires: Tradier brokerage account (free API with account).

---

## Feature Ideas Backlog (Not Yet Scoped)

- **Comparable company valuation (comps)** — EV/Revenue, P/S vs sector peers. Validates "undervalued" thesis claims. Data from `yf.Ticker().info`.
- **Implied move for earnings** — what options are pricing as expected move vs historical move. Missing piece for earnings event play (Structure 2).
- **Volume profile** — flag unusual volume days. `volume_ratio` already in technicals but not surfaced as a standalone signal.
- **Short interest** — from `yf.Ticker().info["shortPercentOfFloat"]`. Squeeze potential signal.
- **Auto sector leader update** — recompute sector leader when threshold met (3 weeks consecutive outperformance).
- **Cross-sector signal auto-generation** — correlation matrix from price history across sectors.
- **Paper trading auto P&L tracking** — background check on open paper positions each run.

---

## Architecture Notes for Next Developer

### LLM Provider Upgrade Path
The current Ollama setup is a prototype constraint. The right production path:
1. Keep Ollama for local/offline use
2. Add Claude API as primary provider (most reliable, best reasoning)
3. Route: `if LLM_PROVIDER == "claude": call_anthropic_api() else: call_ollama()`
4. Same prompt, same JSON response format, same downstream parsing

### Contract Selection Limitation
`select.py` uses OTM% window (5-15% strict, 0-20% relaxed). For TSLA at $392, this gives strikes $411-$451 strict. When liquidity is thin there (off-hours or low-volume strikes), it relaxes to $465+ which may not match the thesis targets. This is a known limitation — the selection is liquidity-driven, not thesis-driven. A future improvement would weight strikes closer to LLM-identified price targets.

### Two-Machine Development
- Mac (primary): all feature development, 14b model, Metal GPU
- Windows (work): testing only, 7b model, CPU
- `.env` is machine-specific and gitignored — each machine has its own bankroll, model settings
- Both branches (`dev` and `main`) should always be in sync before switching machines

---

## Quick Health Check Commands

```bash
# Verify all modules load
python -c "from bot.engine import run, run_multi; from bot.cli import main; from bot.technicals import compute_technicals; from bot.ticker_discovery import discover_and_add; print('all OK')"

# Run engine test
python tests/test_engine.py

# Test technical indicators
python -c "
from bot.chain_yf import get_price_history
from bot.technicals import compute_technicals, format_technicals_for_llm
history = get_price_history('NVDA', period='1y')
t = compute_technicals(history)
print(format_technicals_for_llm(t))
"

# Test auto-discovery
python -c "
from bot.ticker_discovery import discover_and_add
r = discover_and_add('RKLB')
print(r['message'])
"

# Live test (market hours)
tradebrain "AMD bullish breakout" --budget 3000
tradebrain watch
```
