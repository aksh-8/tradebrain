# tradebrain — Project Context
> **For a senior quantitative developer/software engineer joining today.**
> This document covers everything needed to understand, run, and extend tradebrain from scratch.

---

## 1. What Is tradebrain?

tradebrain is a **retail options research and contract selection CLI tool**. It ingests a free-form thesis or ticker from the user, runs quantitative research, evaluates the thesis using a local LLM, and surfaces ranked option contracts with full Greeks, Kelly sizing, and earnings-aware trade structure.

**The core loop:**
```
User types: "NVDA bullish AI infrastructure" --budget 1000
    ↓
intake.py        → extract ticker, direction, timeframe, thesis
research.py      → price, technicals, IV, earnings, analyst, news, LLM verdict
engine.py        → DTE window (earnings-aware), contract selection
select.py        → two-pass filter + rank contracts
bs.py            → Black-Scholes Greeks + Kelly sizing
cli.py           → display everything in rich terminal UI
```

---

## 2. Repository Structure

```
tradebrain/
├── src/bot/
│   ├── models.py          # Dataclasses: Intake, ResearchResult, Pick
│   ├── config.py          # Settings from .env (bankroll, LLM model, budget defaults)
│   ├── intake.py          # Parse free-form text → Intake dataclass (LLM + regex fallback)
│   ├── research.py        # Full research pipeline: price, technicals, LLM thesis check
│   ├── technicals.py      # Pure-Python technical indicators: SMA/EMA/RSI/MACD/BB/ATR/ADX
│   ├── engine.py          # Orchestrates research → contract selection → returns picks
│   ├── select.py          # Two-pass contract selection with relaxation
│   ├── chain_yf.py        # yfinance wrapper: spot price, option chain, price history (OHLCV)
│   ├── bs.py              # Black-Scholes: compute_greeks(), kelly_size()
│   ├── correlations.py    # Sector data, beta, cross-sector signals, company name map
│   ├── ticker_discovery.py # Auto-discover unknown tickers: validate → classify → add to universe
│   ├── logger.py          # SQLite trade log: every run saved to trades/log.db
│   └── cli.py             # Rich terminal UI, all commands: main, history, watch, contract
├── config/
│   ├── universe.json      # All known tickers
│   ├── sectors.json       # 16 sectors with leader, beta_to_leader, company_name_map
│   └── watchlist.json     # Morning scan tickers
├── tests/
│   ├── test_engine.py     # Basic engine smoke test
│   └── check_log.py       # SQLite log checker
├── trades/
│   └── log.db             # SQLite — auto-created, gitignored
├── .env                   # Machine-specific secrets (gitignored)
├── .env.example           # Template
└── pyproject.toml         # Package config
```

---

## 3. Machine Setup

**Two machines — different LLM models:**

| Machine | OS | Ollama Model | .env |
|---|---|---|---|
| Mac M4 Pro (primary) | macOS | qwen2.5:14b (Metal GPU) | `OLLAMA_MODEL=qwen2.5:14b` |
| Windows work PC | Windows 11 | qwen2.5:7b (CPU) | `OLLAMA_MODEL=qwen2.5:7b` |

**Installation:**
```bash
cd tradebrain
python -m venv .venv
source .venv/bin/activate        # Mac
.venv\Scripts\activate           # Windows
pip install -e .
pip install yfinance rich python-dotenv requests ddgs
```

**`.env` file (create per machine, never commit):**
```
BANKROLL_USD=2000
OLLAMA_MODEL=qwen2.5:14b
OLLAMA_URL=http://localhost:11434/api/generate
OLLAMA_TIMEOUT=120
DEFAULT_BUDGET_USD=300
```

**Ollama:**
- Mac: `ollama serve` in terminal, or let the app handle it
- Windows: Ollama UI runs automatically on startup
- Models: `ollama pull qwen2.5:14b` (Mac) / `ollama pull qwen2.5:7b` (Windows)

---

## 4. CLI Commands

```bash
# Main research
tradebrain "NVDA bullish AI infrastructure" --budget 1000
tradebrain "NVDA bullish" --budget 1000 --deep          # 5 articles, deeper LLM
tradebrain "AMD calls" --budget 500 --direction bullish  # force direction
tradebrain "AMD bullish" --budget 500 --bankroll 20000  # override Kelly bankroll

# Multi-ticker
tradebrain "AMD NVDA MSFT bullish" --budget 500

# History
tradebrain history --last 10
tradebrain history --ticker AMD
tradebrain history --id 5

# Morning scan
tradebrain watch
tradebrain watch --budget 500 --direction bullish

# Contract analysis (recently built, may need further testing)
tradebrain contract "INTC $35c 2026-06-18" --budget 200
```

---

## 5. Core Architecture Decisions

### Intake — two paths
`parse_intake()` tries LLM first (Ollama), falls back to regex if Ollama is down:
- LLM path: structured JSON extraction, handles company names, complex theses
- Regex path: `detect_direction()`, `detect_timeframe()`, ticker extraction via 3-pass system
- Both paths call `ticker_discovery.discover_and_add()` for unknown tickers

### Research pipeline
`research_ticker()` in `research.py`:
1. `get_spot()` + `get_price_history(ticker, period="1y")` — live price + 1 year OHLCV
2. `compute_technicals(history)` → SMA 9/20/50/100/200, EMA, RSI, MACD, Bollinger, ATR, ADX, volume
3. `_get_hv_rank(history)` — HV rank (0-100) from 30-day realized vol vs 6-month range
4. `_get_unusual_options()` — detects volume > OI as unusual flow signal
5. `_get_earnings_days_away()` — yfinance calendar
6. `_get_analyst_data()` — consensus rating, mean target, upside
7. `_get_news(ticker, deep=False)` — DDG headlines + full article fetch from trusted sources
8. `_check_thesis()` — Ollama LLM evaluates thesis with all data including full technicals block

### Direction override logic
```
contradicted + high confidence   → FLIP direction (THESIS OVERRIDE)
contradicted + medium confidence → warn, proceed with user direction (THESIS WARNING)
contradicted + low confidence    → warn, proceed (THESIS CONTRADICTED)
neutral + low confidence         → no strong signal
```

### Earnings-aware DTE
`_dte_window()` in `engine.py`:
```
Earnings today      → post-earnings only, warning
Earnings 1-3 days   → post-earnings only
Earnings 3-30 days  → TWO tables: Structure 3 (post) + Structure 1 (pre, exit before)
Earnings > 30 days  → normal window
```

### Two-pass contract selection
`select_contracts()` in `select.py`:
- Pass 1: strict OTM window (5-15%), normal liquidity filters
- Pass 2: relaxed OTM (0-20%), looser filters — if pass 1 returns nothing
- Contracts ranked by: spread, OI, volume, OTM proximity to target, budget utilization

### Black-Scholes Greeks
`compute_greeks()` in `bs.py` — pure Python, no scipy:
- delta, gamma, theta ($/day/contract), vega ($/1pt IV), prob_itm, prob_profit
- `kelly_size()` uses bankroll from `.env`, 3x target multiple, half-Kelly formula

---

## 6. Key Data Models

### `Intake` (frozen dataclass)
```python
raw_text: str
tickers: tuple[str, ...]          # primary ticker first
context_tickers: tuple[str, ...]  # mentioned but not the trade
direction: "bullish" | "bearish" | "unknown"
thesis: Optional[str]
timeframe: "this week" | "this month" | "1-3 months" | "unknown"
budget: float
```

### `ResearchResult` (frozen dataclass)
```python
ticker, price, price_change_5d, price_change_1m
week_52_high, week_52_low
sma50, sma200, above_sma50, above_sma200
iv_rank                    # HV rank 0-100
unusual_options_activity   # string description of unusual flow
analyst_target, analyst_upside, analyst_rating
avg_volume, earnings_days_away
news_summary               # pipe-separated headlines
thesis_verdict             # "supported" | "contradicted" | "neutral"
thesis_reasoning           # LLM reasoning string
recommended_direction      # "bullish" | "bearish" | "unknown"
confidence                 # "high" | "medium" | "low"
skip_reason
```

### `Pick` (frozen dataclass)
```python
ticker, expiration, strike, side, dte
bid, ask, mid, cost, breakeven, otm_pct
iv, iv_rank, oi, volume, spread_pct
delta, gamma, theta, vega, prob_itm, prob_profit
rank_score, why, relaxed, relax_note
```

---

## 7. Sectors Configuration

`config/sectors.json` has 16 sectors:
- semis_compute (leader: NVDA)
- semis_memory (leader: MU)
- semis_equipment (leader: AMAT)
- ai_infra (leader: NBIS)
- big_tech (leader: MSFT)
- ai_software (leader: PLTR)
- cloud_cyber (leader: CRWD)
- cyber_nextgen (leader: CRWD)
- robotics (leader: PATH)
- healthtech (leader: HIMS)
- crypto_miners (leader: MARA)
- quantum (leader: IONQ)
- etfs (leader: SPY)
- energy_infra (leader: SMR)
- ai_data (leader: PLTR)
- fintech (leader: COIN)

Each sector has: `leader`, `leader_note`, `tickers`, `beta_to_leader`, `correlated_sectors`, `macro_sensitivity`, `risk_note`, `cross_sector_signals`.

**IMPORTANT:** `sectors.json` must be saved as **UTF-8** encoding. Windows cp1252 causes UnicodeDecodeError on the em-dash characters. All JSON reads in the codebase use `encoding="utf-8"`.

---

## 8. Auto-Ticker Discovery

`ticker_discovery.py` — when an unknown ticker appears in intake:
1. Validate via yfinance (`get_spot()`)
2. Pull `yf.Ticker().info` — sector, industry, company name
3. Classify sector using `_INDUSTRY_MAP` → `_SECTOR_MAP` priority lookup
4. Compute real beta vs sector leader from 3mo price history correlation
5. Add to `universe.json` tickers list
6. Add to correct sector in `sectors.json` (tickers + beta_to_leader)
7. Add company name to `company_name_map`

Called automatically from both `_llm_parse()` and `_regex_parse()` in `intake.py`.

---

## 9. Technical Indicators

`technicals.py` — pure Python, no external libraries:
- **SMA**: 9D, 20D, 50D, 100D, 200D
- **EMA**: 9D, 20D, 50D
- **RSI(14)**: with overbought/oversold note
- **MACD(12,26,9)**: line, signal, histogram, bullish/bearish note
- **Bollinger Bands(20,2)**: upper, mid, lower, %B position note
- **ATR(14)**: daily range expectation in dollars
- **ADX(14)**: trend strength
- **Volume**: vs 20D average, ratio note

`format_technicals_for_llm()` renders all indicators into a clean string injected into the LLM thesis evaluation prompt.

Requires `period="1y"` in `get_price_history()` to compute 200D SMA.

---

## 10. LLM Prompt Engineering

`_check_thesis()` in `research.py` — two prompts:
- **With thesis**: evaluates user thesis against all data
- **Without thesis**: pure directional analysis

Key prompt rules added:
- TECHNICAL ANALYSIS RULES: explicitly instructs LLM to address SMA confluences, price targets, RSI/MACD, support/resistance mentioned in thesis
- CRITICAL ticker anchor: instructs LLM to ignore other company names in article context
- Always address earnings risk if < 21 days
- Always comment on IV environment

**Known LLM limitation**: With qwen2.5:7b on Windows, article context can cause ticker confusion (saw ARM analysis for TSLA). qwen2.5:14b on Mac is significantly better. Claude API is the recommended upgrade path for production reliability.

---

## 11. News Pipeline

`_get_news(ticker, deep=False)` in `research.py`:
- DDG search: `"{ticker} stock"` or `"{ticker} {company_name} stock"` for ≤2 char tickers
- Standard mode: 10 DDG results, fetch 3 articles, 400 words each
- Deep mode (`--deep`): 15 DDG results, fetch 5 articles, 600 words each
- Source filtering: blocks bloomberg.com, wsj.com, ft.com, barrons.com (paywalled)
- Prefers: reuters.com, cnbc.com, marketwatch.com, fool.com, benzinga.com
- Full article text passed to LLM as `article_context` in thesis check prompt

---

## 12. SQLite Trade Log

`logger.py` — auto-creates `trades/log.db`:
- `runs` table: id, ts, ticker, direction, budget, verdict, confidence, iv_rank, earnings_days, reasoning, news, thesis
- `picks` table: run_id, rank, ticker, strike, side, expiration, cost, breakeven, oi, spread_pct

Functions: `log_run()`, `get_recent_runs()`, `get_runs_by_ticker()`, `get_run_detail()`, `get_run_picks()`

---

## 13. Known Issues / Technical Debt

| Issue | Severity | Status |
|---|---|---|
| Off-hours lastPrice fallback returns stale deep ITM prices | Medium | Known, affects budget suggestions off-hours |
| qwen2.5:7b ticker confusion in article context | High | Known, use --deep sparingly on Windows |
| Sectors.json encoding must be UTF-8 | Medium | Fixed, must maintain |
| Auto-discovery sector classification not perfect | Low | `_INDUSTRY_MAP` covers most cases |
| Kelly always says "no edge" for PoP < 33% at 3x target | By design | Not a bug, math is correct |
| Contract mode recently built, needs testing | Medium | Test during market hours |

---

## 14. Git Workflow

```bash
# Development branch
git checkout dev
# ... make changes ...
git add .
git commit -m "feat/fix/refactor: description"
git push origin dev

# Merge to main
git checkout main
git merge dev
git push origin main
git checkout dev

# Sync between machines
git pull origin dev
git pull origin main
```

Mac is primary dev machine. Windows pulls and tests. Both machines have separate `.env` files with machine-specific LLM model settings.

---

## 15. Testing

```bash
# Smoke tests
python -c "from bot.engine import run, run_multi; print('engine OK')"
python -c "from bot.cli import main; print('cli OK')"
python -c "from bot.research import research_ticker; print('research OK')"
python -c "from bot.technicals import compute_technicals; print('technicals OK')"
python -c "from bot.ticker_discovery import discover_and_add; print('discovery OK')"

# Full engine test
python tests/test_engine.py

# Market hours test (requires live data)
tradebrain "AMD bullish breakout" --budget 3000
tradebrain watch
```

---

## 16. Dependencies

```
yfinance        # price data, option chains, earnings calendar, analyst data
rich            # terminal UI
python-dotenv   # .env loading
requests        # Ollama HTTP calls
ddgs            # DuckDuckGo news search (must be installed in .venv, not conda)
```

**Windows gotcha**: `ddgs` must be installed in the `.venv`, not the conda base. Use:
```powershell
.venv\Scripts\python.exe -m pip install ddgs
```
