# tradebrain — Project Context

> **For a senior quantitative developer/software engineer joining today.**
> This document covers everything needed to understand, run, and extend tradebrain from scratch.

---

## 1. What Is tradebrain?

tradebrain is a **retail options research and contract selection CLI tool**. It ingests a free-form thesis or ticker from the user, runs quantitative research, evaluates the thesis using an LLM (Gemini 2.5 Pro primary, Ollama fallback), and surfaces ranked option contracts with full Greeks, Kelly sizing, market regime awareness, and earnings-aware trade structures.

The bot is the researcher and judge; Akash is the executor. Discipline is enforced by the tool: **one-position-per-week, bot-only entries, no manual overrides on strike selection or direction.**

---

## 2. Who Uses It and How

**Akash Biswal** — retail options trader and solo developer. Trades on Robinhood via bot-recommended contracts. Runs the bot on **Mac M4 Pro (primary)** and **Windows work PC (secondary)** via PowerShell/CLI. Repo synced via Git: **github.com/aksh-8/tradebrain**, single main branch, SSH auth on Mac.

Employed at Porsche, enrolled in DBA program at Westcliff University.

### Typical workflow

1. Ingest market intelligence (Talon weekly reports, Twitter/X collections, YouTube summaries)
2. Claude produces analysis and a formatted batch run
3. Akash pastes commands directly into terminal
4. Bot handles strike selection, DTE, IV rank filtering, regime sizing modifier, and Kelly-based sizing
5. Trades logged into paper/real accounts, exit signals monitored via `tradebrain portfolio`

### Batch run format
```bash
tradebrain "TICKER bullish/bearish thesis" --deep --llm gemini
```
The `--deep` flag enables deeper research context (article summaries pulled from web).

---

## 3. Codebase Architecture

Roughly 10,000+ lines of Python. Pure-Python numerics — no scipy dependency. Rich terminal UI. SQLite for persistence.

### Core modules

**`bot/cli.py`** — main CLI entry point. Commands:
- `tradebrain <thesis>` — main research + picks command
- `tradebrain regime` — display market regime state
- `tradebrain portfolio` — open positions + exit signals + account overview
- `tradebrain account --deposit/--withdraw/--history` — capital tracking
- `tradebrain history` — past runs
- `tradebrain watch <ticker>` — watchlist add
- `tradebrain watchlist show/add/remove` — watchlist management
- `tradebrain contract` — direct contract analysis
- `tradebrain flow` — parse flow alerts
- `tradebrain log-trade` — log a trade (paper or real)
- `tradebrain close-trade ID` — close position
- `tradebrain add-to-trade ID` — average into position
- `tradebrain trim-trade ID --contracts N --exit-cost X` — partial close
- `tradebrain delete-trade ID` — remove erroneous trade
- `tradebrain rerun ID` — rerun past thesis with fresh prices
- `tradebrain review ID` — review open position with fresh analysis
- `tradebrain batch <file>` — batch run from thesis file
- `tradebrain reset --paper/--real --confirm` — nuke trades, reset to bankroll

Key display functions:
- `_print_picks()` — main contracts table with **Delta, Theta/day, Vega, PoP** columns (all color-coded)
- `_print_pre_earnings_picks()` — Structure 1 (Pre-Earnings Run-Up Play, yellow panel, expires BEFORE earnings)
- `_print_research()` — technicals + regime + macro + sector + 200W SMA context
- `_print_kelly_sizing()` — Kelly recommendation panel
- `_print_contract_signals()` — combined EMA + P&L exit rules for open positions

**`bot/research.py`** — thesis evaluation pipeline. Builds Gemini prompt with:
- Price context (spot, 5D/1M change, 52W range)
- Technicals block (SMAs, EMAs, RSI, MACD, BB, ATR, ADX, volume)
- Weekly EMA extension signal, U&R signal, setup signal
- Expected move (from ATM straddle), IV skew, term structure
- 200W SMA state (fetches 5 years of history)
- Macro calendar (21-day horizon)
- Market regime block (state, sector rotation, sentiment)
- News summary (via DDG or deep-mode article scrape)
- Analyst target and upside %
- Earnings warning if within 14 days

**`bot/technicals.py`** — pure-Python numerics:
- SMA 9/20/50/100/200, EMA 8/9/20/21/50
- RSI 14 (with note: overbought/oversold/neutral)
- MACD line/signal/histogram (with bullish/bearish/neutral note)
- Bollinger Bands (20, 2σ) with %B and note
- ATR 14
- ADX 14 (strong/developing/weak trend classification)
- Volume ratio + volume contraction (sellers drying up detection)
- Weekly 8/21 EMA (daily-to-weekly resampled)
- Weekly EMA extension signal (five states: EXTENDED / ELEVATED / AT WEEKLY 8 EMA / SLIGHTLY BELOW / BELOW)
- U&R (Undercut & Rally) detection with 2% threshold
- Setup signal (PULLBACK TO EMA / AT EMA SUPPORT / EXTENDED + OVERBOUGHT / EXTENDED / BELOW EMA CLUSTER / COILING NEAR HIGH)
- EMA exit signal function for portfolio positions

**`bot/select.py`** — contract filtering + ranking. Two-pass filter (strict then relaxed OTM window). Every pick includes full Black-Scholes Greeks via `compute_greeks()`. Ranking penalizes wide spreads, low OI/volume, moneyness deviation, budget usage, high IV.

**`bot/bs.py`** — pure-Python Black-Scholes (Abramowitz & Stegun approximation for norm CDF, no scipy):
- `compute_greeks()` — delta / gamma / theta_per_day / vega / prob_itm / prob_profit
- `kelly_size()` — half-Kelly with per-contract sizing recommendation
- `monte_carlo_probs()` — GBM simulation for P(2x)/P(3x)/P(5x)/P(10x) with dollar targets. **Function exists; not yet wired into picks display.**

**`bot/market_regime.py`** — full market regime module.
- Four action-based states: **DEPLOY, SELECTIVE, CAUTION, HOLD_CASH** (renamed from RISK_ON/RISK_OFF for clarity)
- Monitors SPY / QQQ / SMH / DRAM (QQQ weighted 2x)
- Seven sector ETFs: XLK / XLV / XLF / XLE / XLI / XLC / XLY
- Sentiment: VIX + NAAIM (CSV scrape, flaky)
- 200W SMA state per ticker (informational + RECLAIM boost 1.5x sizing)
- Hard blocks fire on: (sector rotating out AND HOLD_CASH), (NAAIM > 95 AND VIX < 15)
- `--force` flag bypasses hard blocks
- 1-hour cache during market hours, 4-hour cache after hours

**`bot/macro_calendar.py`** — static calendar (FOMC / CPI / NFP / OpEx / PCE / Jackson Hole) through Aug 2026. Injects 21-day-ahead events into every Gemini prompt with sizing guidance based on event proximity.

**`bot/ticker_discovery.py`** — auto-classify unknown tickers via yfinance sector/industry + LLM fallback. Default fallback: `retail` (previously `big_tech`, caused misclassifications). LLM prompt explicitly warns against big_tech overuse.

**`bot/correlations.py`** — sector info per ticker. Provides `get_sector(ticker) → SectorInfo(slug, leader, tickers, beta_to_leader)`. Used for regime sector detection and sector rotation warnings.

**`bot/logger.py`** — SQLite persistence. Tables: `paper_trades` (both paper and real), `runs` (research history), `account_transactions` (deposits/withdrawals ledger). `realized_pnl_partial` column tracks P&L from trim-trade for buying power accuracy.

**`bot/chain_yf.py`** — yfinance options chain wrapper. `get_chain(ticker, expiry)`, `get_expirations(ticker)`, `get_spot(ticker)`, `get_price_history(ticker, period)`.

**`bot/models.py`** — dataclasses:
- `Intake` — parsed user input (raw text, tickers, direction, thesis, timeframe, budget)
- `ResearchResult` — everything research pipeline found (price, technicals, options, analyst, thesis verdict, regime, 200W state, sector ETF, hard block, sizing multiplier)
- `Pick` — recommended contract (strike, side, expiry, DTE, bid/ask/mid, cost, breakeven, OTM%, IV, OI, spread, full Greeks, rank score)

**`bot/config.py`** — env loader. Reads BANKROLL_USD, PAPER_BANKROLL, DEFAULT_BUDGET_USD, LLM_PROVIDER, GEMINI_API_KEY, GEMINI_MODEL, GEMINI_MODEL_FALLBACK, OLLAMA_URL, OLLAMA_MODEL from `.env`. Loads `universe.json` for known tickers and proxy mapping.

### Data files

- **`config/sectors.json`** — sector definitions (slug, leader, tickers, beta_to_leader). 25 sectors. big_tech contains only MSFT/GOOGL/AMZN/AAPL/ORCL after cleanup.
- **`config/universe.json`** — all known tickers + proxy map (e.g., SPY→IWM if signal ticker has no liquid options)
- **`config/watchlist.json`** — user's watchlist for `tradebrain watchlist show/add/remove`
- **`trades/log.db`** — SQLite database

---

## 4. Trading Philosophy This Bot Serves

- Options swing trades, 30-45 DTE typically
- Momentum breakout system from Sean's TradingView thread (8 / 21 / 50 daily EMA)
- Long-term investor 200W SMA framework (buy at zone or reclaim, per Sean's second thread)
- AI supercycle names as primary universe (NBIS, CRWV, IREN, AMD, MU, NVDA, PLTR, etc.)
- Contrarian buying at fear extremes (Graham / Buffett thinking) — **CAPITULATION mode pending**
- **Pre-earnings momentum plays supported via Structure 1** (contracts expiring before earnings, exit 1-2 days before report, never take earnings risk) — already built
- **Post-earnings event plays supported via Structure 2** (contracts expiring after earnings, take event exposure) — already built

Both structures are surfaced simultaneously. User chooses their strategy.

---

## 5. Current State (Session Close)

### Live modules — all confirmed working

- `market_regime.py` — full state machine with DEPLOY / SELECTIVE / CAUTION / HOLD_CASH
- `technicals.py` — full TA library with weekly EMA, U&R, setup signals
- `macro_calendar.py` — events through Aug 2026
- `research.py` — full LLM prompt builder with regime/macro/sector/200W injection
- `bs.py` — Greeks, Kelly, Monte Carlo probability simulator
- `select.py` — contract filtering + ranking with full Greeks
- `ticker_discovery.py` — corrected auto-classification with retail default
- `logger.py` — trades + transactions + partial P&L tracking
- `cli.py` — all commands functional. Picks table shows Delta/Theta/Vega/PoP. Two earnings structures both displayed.

### Known bugs (see NEXT_STEPS.md audit section)

- **Bug 3** — sentiment_extreme disabled when NAAIM missing
- **Bug 5** — hard-block vs display divergence in cli.py (needs verification)
- **Bug 6** — 200W RECLAIM detection re-review
- **Bug 7** — cache day-boundary staleness (low priority)
- **Bug 8** — sector `pct_5d or 0` masks broken fetches (low priority)

### Recent bug fixes committed this session

- Auto-discovery sector classification (consumer/retail no longer misclassified as big_tech)
- sectors.json cleanup (12 tickers moved to correct sectors, 3 duplicates removed)
- market_regime.py CORE_INDEXES guard (UNKNOWN state when SPY/QQQ fails)
- Regime state names renamed to DEPLOY/HOLD_CASH
- 200W SMA integration softened (informational + RECLAIM boost only, no hard blocks)
- yfinance nearest-expiry fallback (Monday weeklies like 2026-07-27)
- Trim-trade command with buying power update
- Reset portfolio command (with account_transactions bug fix: `account_type` → `trade_type`)
- Batch and rerun `--deep` flag support
- Gemini model upgrade to 2.5-pro with 2.5-flash fallback on 503s
- `--force` flag on research
- Real account panel always shows when bankroll configured
- SSH GitHub auth on Mac

---

## 6. Current Trading Context

### Portfolio (session close)

- **Paper account:** $10,000 starting, $19,260 total, +92.6%, 22/39 win rate (56%), +$9,259 realized P&L
- **Real account:** $4,000 bankroll, $4,150 total, +3.8%, 3/5 win rate (60%), +$330 realized P&L
- **Open positions:** None
- **Available buying power:** $3,110 real, $19,260 paper
- **Last two closes:** MSFT #79 (+$710, +68.3%), DRAM #78 (-$180, -15%)

### Current market regime

- **State:** HOLD_CASH (score 20/100)
- **Indexes:** SPY holding above all EMAs. QQQ, SMH, DRAM all below all EMAs.
- **Sectors leading:** XLE (+4.0%), XLF (+2.2%), XLC (+1.9%)
- **Sectors rotating out:** XLI, XLK
- **VIX:** 16.6 (normal)
- **NAAIM:** unavailable this session (CSV fetch flaky)

Waiting for: QQQ reclaims 21 EMA, VIX spike >22 for contrarian setup, or index-level RECLAIM signal.

### Rules currently in effect

- Mega-cap AI names (GOOGL, MSFT, META, AMZN) blocked during earnings windows
- Weekly batch runs use `--deep --llm gemini`
- Kelly threshold: +100% for DTE<21 trim, no lower
- EMA is senior exit signal — overrides contract P&L rules

---

## 7. Key Learnings & Principles

- **Contract structure is upstream of thesis quality.** Strike and expiry selection are load-bearing, not details. The two-structure system exists for this reason.
- **Dead-cat bounces vs. genuine reclaims require structural evidence.** A single-day bounce off a drawdown is not a trend reclaim without a retest.
- **The bot's skip-heavy, slow outputs are features, not failures.** Regime filters and hard-block conditions that return no trades are working correctly.
- **200W SMA is display-only for most states + RECLAIM boost.** Hard blocks removed — extended mega-caps are legitimately extended in the AI supercycle. Only RECLAIM gets sizing boost (1.5x, INTC/MU-style setup).
- **Strike selection must match the stated thesis.** A 200W SMA support-bounce thesis requires high-delta (~0.55–0.65) ATM strike, not a 9% OTM strike with breakeven in resistance.
- **Recovery trading has negative expected value.** Chasing losses via oversized or 0DTE positions breaks the framework and compounds drawdowns.
- **NAAIM as a live signal is flaky.** VIX-only froth detection is the fallback. Any planned feature depending on NAAIM (like CAPITULATION mode) needs explicit None-path handling.
- **Regime states should be action-oriented labels (DEPLOY not RISK_ON).** Clarity beats jargon.
- **Contrarian buying at fear extremes is a valid quant edge, not just retail cliché.** CAPITULATION mode is a real signal a quant would use. Best entries in history (Oct 2022, Mar 2020, Dec 2018) all looked like HOLD_CASH regimes at the time.
- **Sector classification must be defensive.** Wrong sector assignment breaks beta calculations and rotation detection.
- **yfinance has gaps for Monday weeklies.** Fallback to nearest expiry gracefully.
- **Follow bot signals or don't trade with them.** Every held-past-signal position (CRWV +300%→+50%, PLTR held past EMA sell, MSFT held past exit) validated the discipline framework by failing.
- **Pre-earnings expiry is not a defect.** Structure 1 is deliberate — capture the run-up, exit 1-2 days before the report. Do not conflate this with "bot picked wrong expiry."

---

## 8. Approach & Working Patterns

### Regime discipline
When regime is HOLD_CASH, sit on cash — this is a position too. Don't force trades in bad environments. Wait for setup to come (index reclaim, capitulation signal, sector leader emerging).

### Decision style expected from Claude
- Direct calls, no option menus
- No thinking-out-loud procedure
- Explanatory and instructive tone only
- Six-point trade analysis framework when reviewing bot output:
  1. Thesis verdict accuracy
  2. Contract quality
  3. Missing risks
  4. Trade viability
  5. Specific contract and exit plan if entering
  6. Conditions required before trade becomes viable

### Code delivery
Always provide **downloadable `.py` script files** for tests — never shell one-liners or `python3 -c "..."` (breaks in PowerShell). Read the live/uploaded file before making claims about its contents; uploaded files take precedence over project knowledge.

### Verification standard
Confirm fixes landed in actual working files before marking done. Verify on live data where possible. **Before marking any feature as "pending" in roadmap docs, verify it doesn't already exist in the codebase.** Roadmap items marked pending when they're actually built waste user time and destroy trust.

### Git hygiene
Commit and push to main. `log.db` should be in `.gitignore`. GitHub auth via SSH on Mac (no token expiry).

### Behavioral guardrails Claude must observe
- Read what user pastes carefully. Don't misread numbers.
- One step at a time. Don't stack changes.
- Make decisions like a senior quant — don't give menus of options.
- Ask for the exact file/context you need before writing code.
- When user pastes terminal output, quote the exact number they said, not what you assumed.
- Before adding anything to the pending roadmap, verify it doesn't already exist in code.

---

## 9. Tools & Environment

- **Runtime:** Python 3.14, yfinance, Rich terminal UI, SQLite, pure-Python Black-Scholes (no scipy)
- **LLM:** Gemini 2.5 Pro (primary), Gemini 2.5 Flash (fallback on 503). Ollama qwen2.5:7b as offline fallback.
- **Execution platform:** Robinhood (options trading)
- **Investment platforms:** Fidelity (ETF SIP), Wealthfront (cash), Acorns (winding down)
- **Market intelligence sources:** Talon weekly sector reports, aibottlenecks.app thesis framework, Twitter/X collections, YouTube summaries
- **Budgeting:** Spendee (manual cash-flow tracker)
- **Dev environment:** Mac M4 Pro (primary, SSH auth), Windows work PC (secondary, HTTPS auth); Git for sync via github.com/aksh-8/tradebrain (single main branch)

### Modules & features by category

**Regime & context injection into every research run:**
- Market regime (state machine + sector rotation + sentiment)
- Macro calendar (21-day event horizon)
- 200W SMA state per ticker
- Sector ETF context

**Per-ticker technical intelligence:**
- Full TA library (SMAs, EMAs, RSI, MACD, BB, ATR, ADX, volume)
- Weekly 8 EMA extension signal
- U&R (Undercut & Rally) detection
- Setup signal (PULLBACK TO EMA / AT EMA SUPPORT / EXTENDED / COILING NEAR HIGH etc.)
- EMA exit signal for portfolio positions

**Options analytics:**
- Full Black-Scholes Greeks (delta, gamma, theta, vega, prob_itm, prob_profit) on every pick
- Kelly sizing recommendation with half-Kelly
- Monte Carlo P(2x/3x/5x/10x) simulation — function exists, display wiring pending
- Expected move (from ATM straddle), IV skew, term structure
- HV rank as IV rank proxy
- Two-structure earnings play system (Structure 1 pre-earnings + Structure 2 post-earnings)

**Position management:**
- Log, close, add-to, trim, delete trades
- Combined EMA + P&L exit signals
- Kelly sizing panel
- Buying power tracking with partial-close accounting
- Deposit/withdrawal ledger

**Portfolio operations:**
- Paper and real account overview panels
- Exit signals with underlying stock EMA position
- Contract signals combining EMA + P&L rules
- Reset command with confirmation

**Utility:**
- Watchlist management
- Batch thesis runs from file
- Rerun past thesis with fresh prices
- Review open position with fresh Gemini analysis

---

## 10. What's Next

See `NEXT_STEPS.md` for the complete prioritized roadmap. High-level:

**Immediate:**
- Wire Monte Carlo probabilities into picks table (function exists, needs display)
- Add structured base detection fields alongside qualitative setup signals

**TIER 0 — Regime quant-level upgrades:**
- CAPITULATION contrarian mode
- Index-level RECLAIM signal
- Quality-vs-market divergence detection
- Dry powder guidance
- Full audit of regime edge cases

**Longer term:**
- Leader scanner (Sean's TradingView criteria)
- Trade plan generator with entry/stop/targets
- SEC EDGAR Form 4 fetch
- `tradebrain morning` command
- Finviz chart + Claude Vision analysis
- Chart pattern intelligence (Ichimoku, RSI divergence, GEX approximation)
- Automation infrastructure (cron, alerts, cloud hosting)
- Paid data integrations (Unusual Whales, MenthorQ) at $10-25k account milestones

---

## 11. Investment Side (Non-tradebrain)

- Fidelity brokerage consolidation in progress; four-ETF SIP allocation live (50% VTI, 15% IXUS, 15% QQQ, 20% SMH) with $1k/month automated contributions
- Separate $3k aggressive bucket with single-stock component (~$300 cap; NBIS, APLD, or NNE — not yet finalized)
- Porsche tuition reimbursement clawback terms need follow-up research

---

*Last updated: session close, July 2026*
