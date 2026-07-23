# tradebrain — Next Steps

Prioritized backlog of features, fixes, and infrastructure work.

Priority scale: **P0** = critical / immediate, **P1** = high impact, **P2** = medium, **P3** = deferred.

---

## 🐛 Active Audit — market_regime.py

Open bug list from the ongoing regime module audit. Fix these before adding TIER 0 features on top.

| # | Priority | Status | Description |
|---|----------|--------|-------------|
| Bug 1 | P0 | ✅ Done | Partial-fetch score inversion — core-index guard added, UNKNOWN state returned when SPY/QQQ fail |
| Bug 2 | P0 | ✅ Done | `format_regime_for_llm` dead branch fixed; UNKNOWN and DEPLOY rules added |
| Bug 3 | P0 | ✅ Done | `sentiment_extreme` disabled when NAAIM is None — VIX-only fallback implemented |
| Bug 5 | — | ❌ Not a bug | Investigated: no divergence. `compute_market_regime()` is cached (1h market hours / 4h after), so the pre-check and display calls return the same result. `--force` bypasses cache when a fresh compute is wanted. Closed — no fix needed. |
| Bug 5a | P1 | ✅ Fixed | `cli.py` line 4021 — hard-block 200W check was fed `period="1y"` (~52 weeks), computing an approximated SMA while the display used real 5y data. Changed to `period="5y"`. |
| Bug 6 | P1 | ✅ Done | 200W RECLAIM detection re-reviewed — trigger now requires ≥4 of last 20 weekly closes below 200W SMA |
| Bug 7 | P3 | ⬜ Open | Cache day-boundary staleness edge case |
| Bug 8 | P3 | ⬜ Open | Sector `pct_5d or 0` masks broken fetches — should flag missing data instead of treating as neutral |

---

## 🔴 P0 — Immediate Priority

### 1. Wire Monte Carlo probabilities into picks table
**Deliverable:** Add `P(2x)`, `P(3x)`, `P(5x)` columns to `_print_picks()` in `cli.py`. Optionally `P(10x)` in a detailed view.
**Why:** `monte_carlo_probs()` fully implemented in `bs.py` (lines 240-328). Returns p2x/p3x/p5x/p10x + dollar targets. Highest-value low-effort win — the math is done, just needs the display wiring in `select.py` (add fields to pick dict) and `cli.py` (add columns to table).
**Files touched:** `select.py`, `cli.py`, `models.py` (add fields to `Pick` dataclass)
**Estimate:** 1-2 hours

### 2. Structured base detection fields
**Deliverable:** Add explicit fields to technicals result dict: `tight_base: bool`, `trigger_level: float` (highest high tested 2+ times), `base_days: int`, `volume_declining: bool`.
**Why:** `setup_signal` in `technicals.py` currently emits qualitative strings ("PULLBACK TO EMA", "COILING NEAR HIGH — tight base building"). Downstream code can't make quantitative decisions from strings. Structured fields let the LLM prompt inject exact trigger levels and let contract selection filter for setups.
**Files touched:** `technicals.py`, `research.py` (inject into Gemini prompt), `models.py`
**Estimate:** 3-4 hours

---

## 🟠 TIER 0 — Regime Quant-Level Upgrades

The current regime is trend-following only ("market broken → hold cash"). Missing the contrarian mode that makes it quant-level instead of retail-level. Buffett/Graham/institutional thinking: fear = opportunity for quality.

### 3. CAPITULATION / contrarian mode
**Priority:** P1
**Deliverable:** New regime state that overrides HOLD_CASH when contrarian conditions align. Fires when NAAIM < 30 AND VIX > 25 AND pct_score < 0.35 AND quality names (>50% of universe) hold their 21 EMA.
**Sizing:** 1.5x multiplier on names still above key EMAs. Explicitly names which quality tickers still hold up.
**Why:** Best entries in history (Oct 2022, Mar 2020, Dec 2018) all looked like HOLD_CASH regimes at the time. Missing this signal costs generational opportunities.
**Depends on:** Nothing blocking — Bug 3 (NAAIM None handling) already resolved with VIX-only fallback
**Files touched:** `market_regime.py`, `cli.py` display

### 4. Index-level RECLAIM signal
**Priority:** P1
**Deliverable:** Detect when SPY/QQQ/SMH reclaim 21 EMA after 5+ days below. Score jumping 20→60 in 2 days = deploy aggressively. New "RECLAIMING" state or CAPITULATION-adjacent boost.
**Why:** Sean's tweet framework — reclaims are the highest-conviction entries at index level, same as 200W SMA reclaims per ticker.
**Files touched:** `market_regime.py`

### 5. Quality-vs-market divergence detection
**Priority:** P1
**Deliverable:** When market broken but individual leaders hold their 21W or 200W EMAs, surface those specific names as "still accumulating." Boost sizing on those tickers even in CAUTION/HOLD_CASH regimes.
**Why:** Money doesn't leave the market — it rotates. When SPY breaks but MSFT holds its 21 EMA with volume, that's where institutional money went.
**Files touched:** `market_regime.py` (add quality_holders list), `research.py` (inject into prompt)

### 6. Dry powder guidance
**Priority:** P2
**Deliverable:** When state = HOLD_CASH, replace "hold cash" message with specific watch-for levels. Example: "Watch SPY to hold $748. Watch QQQ to reclaim 8 EMA at $706. Watch VIX spike >22 without SPY breaking $748."
**Why:** "Hold cash" alone isn't actionable. User needs the trigger to deploy.
**Files touched:** `market_regime.py` (add dry_powder_watchlist), `cli.py` display

### 7. Full regime edge case audit
**Priority:** P1
**Deliverable:** Test regime module with 1+ indexes missing, VIX/NAAIM offline, sector rotation edge cases, cache boundary crossings. Automate as pytest suite.
**Why:** Bugs 3, 5, 6, 7, 8 above are all edge case failures. Systematic audit prevents future regressions.
**Files touched:** New `tests/test_market_regime.py`

---

## 🟡 TIER 1 — Highest Impact Features

### 8. Leader scanner — `tradebrain scan`
**Priority:** P1
**Deliverable:** New CLI command implementing Sean's TradingView screener criteria: price > $3, market cap > $300M, avg volume > 500K, ADR > 3%, price > 21 EMA AND > 50 EMA. Returns top 20 names sorted by volume descending.
**Output:** Morning watchlist table with tickers, price, volume, ADR, EMA position, sector.
**Why:** Currently no systematic way to build a fresh morning watchlist. User relies on manual Twitter scans.
**Files touched:** New `bot/scan.py`, `cli.py`

### 9. Trade plan generator
**Priority:** P1
**Deliverable:** For every recommended pick, auto-output structured trade plan: entry price, stop loss (from ATR or key level), target 1 (next resistance), target 2 (next major level or 1.618 Fib), risk/reward ratio, position size in contracts.
**Why:** Currently picks include Greeks and Kelly sizing but no explicit exit plan tied to entry. Discipline breaks down without a written plan.
**Files touched:** `select.py` or new `bot/trade_plan.py`, `cli.py` display

### 10. Urgent alert panel at top of portfolio
**Priority:** P1
**Deliverable:** Banner panel above account overview showing positions requiring IMMEDIATE action (below 50 EMA, at +300%, at -75%, at DTE<5). Impossible to miss.
**Why:** Exit signals exist but buried in standard portfolio flow. Cost CRWV +300%→+50% type losses.
**Files touched:** `cli.py` (`_cmd_portfolio`, new `_print_urgent_alerts()`)

---

## 🟢 TIER 2 — Intelligence Upgrades

### 11. SEC EDGAR Form 4 fetch
**Priority:** P2
**Deliverable:** Free insider trade data. Surface CEO/CFO buys in last 30 days for any researched ticker. Alpha signal at zero cost.
**API:** `data.sec.gov/submissions/CIK{cik}.json` — no authentication, no rate limits, no cost.
**Files touched:** New `bot/insider.py`, injected into `research.py` prompt

### 12. `tradebrain morning` command
**Priority:** P2
**Deliverable:** Daily 6am ritual combining watchlist scan + portfolio exit signals + market regime + macro calendar + top 5 leader scan results into one panel.
**Why:** Reduces morning friction. One command instead of five.
**Files touched:** `cli.py`

### 13. Pullback entry zone awareness
**Priority:** P2
**Deliverable:** Bot compares current price to known accumulation zones from a curated file (e.g., NBIS $150-180 buy zone, CRWV $85-100). Says "CRWV at $112 — above entry zone $85-100, wait."
**Why:** Users have specific bigpicture entry zones from Talon/Twitter analysis. Bot should surface when we're in them.
**Files touched:** New `config/entry_zones.json`, `research.py`

### 14. Finviz chart image + Claude Vision analysis
**Priority:** P2
**Deliverable:** New `tradebrain chart TICKER` command that fetches Finviz chart image URL, passes to Claude Vision API, returns pattern analysis (cup/handle, bull flag, U&R, EMA alignment, RSI divergence).
**Why:** Complements our internal setup_signal detection with visual pattern recognition.
**Files touched:** New `bot/chart_vision.py`, `cli.py`

### 15. Market health context expansion
**Priority:** P2
**Deliverable:** Cleaner integration of QQQ/SMH/DRAM 5-day performance into LLM prompt as first-class signal. Currently partially injected via regime block.
**Files touched:** `research.py`

---

## 🔵 TIER 3 — Chart Pattern Intelligence

### 16. Ichimoku cloud
**Priority:** P3
**Deliverable:** Add Ichimoku cloud (tenkan/kijun/senkou spans) to technicals.py. Report cloud position (above/below/inside) and cloud color (bullish/bearish).
**Files touched:** `technicals.py`

### 17. RSI divergence detection
**Priority:** P3
**Deliverable:** Detect bullish divergence (price lower lows, RSI higher lows) and bearish divergence.
**Files touched:** `technicals.py`

### 18. Descending trendline detection
**Priority:** P3
**Deliverable:** Auto-detect from slope of recent highs. Setup signals detect "EXTENDED" but not resistance trendline from swing highs.
**Files touched:** `technicals.py`

### 19. GEX approximation from yfinance chain
**Priority:** P3
**Deliverable:** Pin levels, gamma flip approximated from OI × 100 × spot² × gamma. Free version of MenthorQ data.
**Files touched:** New `bot/gex.py`

### 20. VWAP from intraday 1m bars
**Priority:** P3
**Deliverable:** Session VWAP for entry confirmation.
**Files touched:** `technicals.py` or new `bot/vwap.py`

### 21. Wave 3 pullback-to-EMA cluster
**Priority:** P3
**Deliverable:** Elliot Wave 3 pullback pattern detection. setup_signal detects "PULLBACK TO EMA" but doesn't specifically identify Wave 3 setups.
**Files touched:** `technicals.py`

---

## ⚙️ TIER 4 — Automation & Infrastructure

### 22. Cron job on Mac
**Priority:** P2
**Deliverable:** Nightly automated batch run at 4pm. Emails/writes summary file with regime + top scans + portfolio exit signals.
**Files touched:** New `scripts/nightly.sh`, launchd plist

### 23. Pre-market alert-check cron
**Priority:** P2
**Deliverable:** Runs every 15 min during market hours. Sends macOS native popup notifications when position hits threshold (+300%, -75%, EMA break).
**Why:** Prevents CRWV-style profit slippage from missing intraday moves.
**Files touched:** New `scripts/alert_check.py`

### 24. Test suite
**Priority:** P1
**Deliverable:** Proper pytest coverage for engine, technicals, picks, regime, logger. Currently zero tests.
**Files touched:** New `tests/` directory

### 25. Linode server + cloud cron
**Priority:** P3
**Deliverable:** Fully hosted bot on cheap Linode. Runs even when laptop is off. Web hook for command execution.
**Estimated cost:** $5-10/month

### 26. XPI tweet webhook automation
**Priority:** P3
**Deliverable:** Auto-ingest tweets from watched accounts into thesis batch files. Uses XPI (X API) webhook.

### 27. YouTube transcript pipeline
**Priority:** P3
**Deliverable:** Auto-fetch market analysis channel transcripts, extract theses, feed to bot as batch input.

### 28. Web dashboard
**Priority:** P3
**Deliverable:** Flask + React UI for non-CLI users. Shows portfolio, regime, watchlist, batch results.

### 29. Mobile notifications
**Priority:** P3
**Deliverable:** Push alerts to phone via Pushover or similar.

---

## 💰 TIER 5 — Paid Data Integrations

### 30. Unusual Whales API Basic
**Priority:** P2 (when portfolio at $10k+)
**Cost:** $135/mo
**Deliverable:** Real options flow, dark pool prints, congressional trades, insider Form 4, real IV rank.
**Replaces:** DDG news scrape, HV rank as IV proxy, basic unusual activity detection.

### 31. MenthorQ Premium
**Priority:** P2 (when portfolio at $15k+)
**Cost:** $129/mo
**Deliverable:** GEX heatmaps, call/put walls, gamma flip, dealer positioning grid, Trinity multi-asset view.
**Replaces:** GEX approximation from yfinance.

### 32. Both combined
**Priority:** P3 (when portfolio at $25k+)
**Cost:** $264/mo combined
**Deliverable:** Full quant data stack. UW for flow/dark pool/insider. MenthorQ for gamma/heatmaps.

---

## ✅ Verified Already Built (Code Audit Confirmed)

Removed from active roadmap after verifying in `research.py`, `cli.py`, `technicals.py`, `bs.py`, `models.py`, `select.py`:

- ✅ **Earnings-aware structure system** — Two structures shown simultaneously in `cli.py`. Structure 1 (Pre-Earnings Run-Up Play): contracts expiring BEFORE earnings, yellow panel with exit reminder. Structure 2 (Post-earnings, standard): contracts expiring AFTER earnings. User chooses their strategy. Previously falsely flagged as a "defect" — it is a deliberate feature.
- ✅ **Black-Scholes delta + PoP in picks table** — `_print_picks()` displays Delta, Theta/day, Vega, PoP columns with color coding. Full Greeks computed by `compute_greeks()` in `bs.py`, wired via `select.py`.
- ✅ **Monte Carlo probability function** — Fully implemented in `bs.py`. Only display wiring is missing (see P0 item #1 above).
- ✅ **Setup signal detection (qualitative)** — `technicals.py` emits PULLBACK TO EMA / AT EMA SUPPORT / EXTENDED + OVERBOUGHT / EXTENDED / BELOW EMA CLUSTER / COILING NEAR HIGH. Structured fields still needed (P0 item #2).
- ✅ Auto-discovery sector classification fix (retail no longer misclassified as big_tech)
- ✅ sectors.json cleanup (12 tickers moved to correct sectors, 3 duplicates removed)
- ✅ Regime state names renamed (DEPLOY/HOLD_CASH replaced RISK_ON/RISK_OFF)
- ✅ 200W SMA sizing softened (informational + RECLAIM boost only)
- ✅ yfinance nearest-expiry fallback (Monday weeklies)
- ✅ Trim-trade command with buying power update
- ✅ Reset portfolio command
- ✅ Batch and rerun `--deep` flag support
- ✅ Gemini 2.5-pro primary with 2.5-flash fallback
- ✅ `--force` flag on research
- ✅ Real account panel always shows when bankroll configured
- ✅ SSH GitHub auth on Mac
- ✅ Bug 1 — partial-fetch score inversion
- ✅ Bug 2 — format_regime_for_llm dead branch

---

*Last updated: session close, July 2026*