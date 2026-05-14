from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional

import yfinance as yf

from bot.chain_yf import get_price_history, ChainError

_UNIVERSE_PATH = Path(__file__).parent.parent.parent / "config" / "universe.json"
_SECTORS_PATH  = Path(__file__).parent.parent.parent / "config" / "sectors.json"

# ---------------------------------------------------------------------------
# Sector classification vocab — maps yfinance sector/industry to our slugs
# ---------------------------------------------------------------------------

_SECTOR_MAP: dict[str, str] = {
    # yfinance sector string → our slug
    "technology":               "big_tech",
    "communication services":   "social_media",   # SNAP, PINS, RDDT — was wrongly big_tech
    "semiconductors":           "semis_compute",
    "semiconductor equipment":  "semis_equipment",
    "software":                 "ai_software",
    "cybersecurity":            "cloud_cyber",
    "cloud":                    "cloud_cyber",
    "financial services":       "fintech",
    "financial":                "fintech",
    "energy":                   "energy_trad",     # OXY, XOM, CVX — split from nuclear/SMR
    "utilities":                "energy_infra",
    "healthcare":               "healthtech",
    "biotechnology":            "healthtech",
    "consumer cyclical":        "ev_auto",         # RIVN, NIO, GM, F — was wrongly big_tech
    "consumer defensive":       "big_tech",
    "industrials":              "robotics",
    "basic materials":          "energy_infra",
}

_INDUSTRY_MAP: dict[str, str] = {
    # yfinance industry string → our slug (more specific, checked first)

    # Semis
    "semiconductors":                      "semis_compute",
    "semiconductor equipment & materials": "semis_equipment",

    # Software / Cloud
    "software — application":              "ai_software",
    "software — infrastructure":           "cloud_cyber",

    # EV / Auto — NEW
    "auto manufacturers":                  "ev_auto",
    "auto parts":                          "ev_auto",
    "automobiles":                         "ev_auto",
    "electric vehicles":                   "ev_auto",

    # Social / Consumer internet — NEW
    "internet content & information":      "social_media",
    "entertainment":                       "social_media",
    "media":                               "social_media",

    # China ADRs tend to be e-commerce/consumer — NEW
    "internet retail":                     "china_adr",
    "specialty retail":                    "big_tech",

    # Hardware / compute
    "computer hardware":                   "big_tech",
    "electronic components":               "semis_compute",

    # Health
    "medical devices":                     "healthtech",
    "drug manufacturers":                  "healthtech",
    "biotechnology":                       "healthtech",

    # Energy
    "uranium":                             "energy_infra",
    "nuclear":                             "energy_infra",
    "solar":                               "energy_infra",                # NEW
    "renewable electricity":               "energy_infra",                # NEW
    "electrical equipment & parts":        "energy_infra",                # NEW
    "fuel cells":                          "energy_infra",                # NEW
    "specialty industrial machinery":      "energy_infra",                # NEW — was robotics
    "farm & heavy construction machinery": "robotics",                    # keep robotics for true industrial
    "oil & gas e&p":                       "energy_trad",
    "oil & gas integrated":                "energy_trad",
    "oil & gas midstream":                 "energy_trad",

    # Funds / ETFs
    "asset management":                    "etfs",
    "exchange traded fund":                "etfs",
    "gold":                                "etfs",          # GLD, GDX
    "precious metals":                     "etfs",

    # Fintech
    "capital markets":                     "fintech",
    "financial data & stock exchanges":    "fintech",
    "financial technology":                "fintech",
    "insurance":                           "fintech",
    "banks":                               "fintech",
    "banks — diversified":                 "fintech",       # WFC, JPM
    "credit services":                     "fintech",       # PYPL

    # Crypto
    "crypto":                              "crypto_miners",
    "bitcoin":                             "crypto_miners",

    # Quantum computing
    "quantum computing":    "quantum",
    "quantum":              "quantum",
}

# default fallback — big_tech is safer than ai_software for true unknowns
_DEFAULT_SLUG = "big_tech"


def _classify_sector(info: dict) -> str:
    """
    Maps yfinance info dict to our sector slug.
    Uses industry map first, then sector map, then LLM for ambiguous cases.
    """
    sector   = (info.get("sector")   or "").lower().strip()
    industry = (info.get("industry") or "").lower().strip()

    # industry is more specific — check first
    for key, slug in _INDUSTRY_MAP.items():
        if key in industry:
            return slug

    # sector map — but skip consumer cyclical (too broad, goes to LLM)
    for key, slug in _SECTOR_MAP.items():
        if key == "consumer cyclical":
            continue
        if key in sector:
            return slug


    # LLM fallback for ambiguous cases (consumer cyclical, unknown sector, etc.)
    company_name = info.get("longName") or info.get("shortName") or ""
    description  = (info.get("longBusinessSummary") or "")[:300]
    llm_slug = _classify_sector_with_llm(company_name, sector, industry, description)
    if llm_slug:
        return llm_slug

    # consumer cyclical fallback if LLM unavailable
    if "consumer" in sector:
        return "big_tech"

    return "big_tech"

def _classify_sector_with_llm(
    company_name: str,
    sector: str,
    industry: str,
    description: str,
) -> Optional[str]:
    """
    Uses Gemini to classify sector when industry map is ambiguous.
    Only called once per new ticker discovery.
    """
    try:
        from bot.research import _gemini_available, _call_gemini
        if not _gemini_available():
            return None

        valid_slugs = [
            "semis_compute", "semis_memory", "semis_equipment", "ai_infra",
            "big_tech", "ai_software", "cloud_cyber", "cyber_nextgen",
            "robotics", "healthtech", "crypto_miners", "quantum",
            "etfs", "energy_infra", "ai_data", "fintech",
        ]

        prompt = (
            f"Classify this company into exactly one sector slug. "
            f"Return ONLY the slug, nothing else.\n\n"
            f"Company: {company_name}\n"
            f"yfinance sector: {sector}\n"
            f"yfinance industry: {industry}\n"
            f"Description: {description}\n\n"
            f"Sector slugs:\n"
            f"- semis_compute: semiconductor chips (NVIDIA, AMD, Intel, Qualcomm)\n"
            f"- semis_memory: memory chips (Micron, WD, SNDK)\n"
            f"- semis_equipment: chip equipment (ASML, AMAT, LRCX)\n"
            f"- big_tech: large tech, consumer internet, China ADRs, social media "
            f"(MSFT, GOOGL, META, BABA, PDD, JD, SNAP, PINS)\n"
            f"- ai_software: AI/enterprise software (Palantir, Salesforce, ServiceNow)\n"
            f"- cloud_cyber: cloud and cybersecurity (CrowdStrike, Cloudflare, Palo Alto)\n"
            f"- robotics: EVs, autonomous vehicles, automation, industrial (Tesla, Rivian, Lucid)\n"
            f"- healthtech: healthcare, biotech, pharma (Hims, Moderna, Pfizer)\n"
            f"- crypto_miners: bitcoin/crypto mining and blockchain (MARA, CleanSpark, Riot)\n"
            f"- quantum: quantum computing (IonQ, Rigetti, D-Wave, QBTS)\n"
            f"- energy_infra: energy, nuclear, solar, utilities, materials (SMR, OKLO)\n"
            f"- fintech: payments, banking, financial tech (Coinbase, SQ, Stripe)\n"
            f"- etfs: ETFs and index funds\n"
        )

        result = _call_gemini(prompt).strip().lower()
        # clean up any extra text Gemini might add
        for slug in valid_slugs:
            if slug in result:
                return slug
        return None

    except Exception:
        return None
    

def _compute_beta(
    ticker: str,
    leader: str,
    period: str = "3mo",
) -> float:
    """
    Computes 30-day beta of ticker vs sector leader.
    Beta = cov(ticker_returns, leader_returns) / var(leader_returns)
    Returns 0.5 as default if computation fails.
    """
    try:
        t_hist = get_price_history(ticker, period=period)
        l_hist = get_price_history(leader, period=period)

        if len(t_hist) < 10 or len(l_hist) < 10:
            return 0.5

        # align by date
        t_dates = {h["date"]: h["close"] for h in t_hist}
        l_dates = {h["date"]: h["close"] for h in l_hist}
        common  = sorted(set(t_dates.keys()) & set(l_dates.keys()))

        if len(common) < 10:
            return 0.5

        t_prices = [t_dates[d] for d in common]
        l_prices = [l_dates[d] for d in common]

        # daily log returns
        t_returns = [math.log(t_prices[i] / t_prices[i-1]) for i in range(1, len(t_prices))]
        l_returns = [math.log(l_prices[i] / l_prices[i-1]) for i in range(1, len(l_prices))]

        n    = len(t_returns)
        t_mu = sum(t_returns) / n
        l_mu = sum(l_returns) / n

        cov = sum((t_returns[i] - t_mu) * (l_returns[i] - l_mu) for i in range(n)) / (n - 1)
        var = sum((r - l_mu) ** 2 for r in l_returns) / (n - 1)

        if var == 0:
            return 0.5

        beta = round(cov / var, 2)
        # clamp to reasonable range
        return max(0.1, min(2.0, beta))

    except Exception:
        return 0.5


def _load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def is_known_ticker(ticker: str) -> bool:
    """Returns True if ticker is already in universe.json."""
    d = _load_json(_UNIVERSE_PATH)
    return ticker.upper() in [t.upper() for t in d.get("tickers", [])]


def discover_and_add(ticker: str) -> dict:
    """
    Main entry point. Validates ticker, classifies sector, 
    computes beta, adds to universe.json and sectors.json.

    Returns dict with discovery result:
      success: bool
      ticker: str
      company_name: str
      sector_slug: str
      beta: float
      message: str
    """
    ticker = ticker.upper().strip()

    # Step 1 — already known?
    if is_known_ticker(ticker):
        return {
            "success": True,
            "ticker":  ticker,
            "message": f"{ticker} already in universe",
            "new":     False,
        }

    # Step 2 — validate with yfinance
    try:
        t    = yf.Ticker(ticker)
        info = t.info
        price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")
        if not price or price <= 0:
            # try fast_info
            price = float(t.fast_info.get("last_price", 0))
        if not price or price <= 0:
            return {
                "success": False,
                "ticker":  ticker,
                "message": f"{ticker} not found or no price data — may be invalid",
                "new":     False,
            }
    except Exception as e:
        return {
            "success": False,
            "ticker":  ticker,
            "message": f"{ticker} yfinance error: {e}",
            "new":     False,
        }

    # Step 3 — get company info
    company_name  = info.get("longName") or info.get("shortName") or ticker
    sector_slug   = _classify_sector(info)
    market_cap    = info.get("marketCap", 0)
    description   = (info.get("longBusinessSummary") or "")[:200]

    # Step 4 — load sectors.json to get sector leader
    sectors_data  = _load_json(_SECTORS_PATH)
    sector_entry  = next(
        (s for s in sectors_data.get("sectors", []) if s["slug"] == sector_slug),
        None,
    )
    leader = sector_entry["leader"] if sector_entry else "SPY"

    # Step 5 — compute real beta vs sector leader
    beta = _compute_beta(ticker, leader)

    # Step 6 — update universe.json
    universe_data = _load_json(_UNIVERSE_PATH)
    tickers = universe_data.get("tickers", [])
    if ticker not in [t.upper() for t in tickers]:
        tickers.append(ticker)
        universe_data["tickers"] = tickers
        _save_json(_UNIVERSE_PATH, universe_data)

    # Step 7 — update sectors.json
    # 7a: add ticker to correct sector tickers list
    # 7b: add beta to beta_to_leader
    # 7c: add company name to company_name_map
    updated = False
    for s in sectors_data.get("sectors", []):
        if s["slug"] == sector_slug:
            if ticker not in [t.upper() for t in s.get("tickers", [])]:
                s.setdefault("tickers", []).append(ticker)
                updated = True
            s.setdefault("beta_to_leader", {})[ticker] = beta
            updated = True
            break

    # 7c: company name map
    name_key = company_name.lower().strip()
    sectors_data.setdefault("company_name_map", {})[name_key] = ticker

    # also add short name variant if different
    short_name = (info.get("shortName") or "").lower().strip()
    if short_name and short_name != name_key:
        sectors_data["company_name_map"][short_name] = ticker

    if updated or True:  # always save to get name map update
        _save_json(_SECTORS_PATH, sectors_data)

    return {
        "success":      True,
        "ticker":       ticker,
        "company_name": company_name,
        "sector_slug":  sector_slug,
        "sector_name":  sector_entry["name"] if sector_entry else sector_slug,
        "beta":         beta,
        "price":        price,
        "market_cap":   market_cap,
        "message":      f"Added {ticker} ({company_name}) → {sector_entry['name'] if sector_entry else sector_slug} | beta={beta}",
        "new":          True,
    }