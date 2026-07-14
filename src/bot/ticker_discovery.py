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
    # NOTE: sector map is only used as LAST RESORT after industry map + LLM fail.
    # Both consumer categories are intentionally excluded — they must go through LLM.
    "technology":               "big_tech",
    "communication services":   "social_media",
    "semiconductors":           "semis_compute",
    "semiconductor equipment":  "semis_equipment",
    "software":                 "ai_software",
    "cybersecurity":            "cloud_cyber",
    "cloud":                    "cloud_cyber",
    "financial services":       "fintech",
    "financial":                "fintech",
    "energy":                   "energy_trad",
    "utilities":                "energy_infra",
    "healthcare":               "healthtech",
    "biotechnology":            "healthtech",
    "industrials":              "defense_aero",   # industrial default — LLM should override
    "basic materials":          "energy_infra",
    "defense":                  "defense_aero",
    "aerospace":                "defense_aero",
    # DELETED: consumer cyclical → ev_auto (too broad, breaks retail/travel/etc)
    # DELETED: consumer defensive → big_tech (INSANE mapping, that's how WMT broke)
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
    "entertainment":                       "entertainment",  # was social_media — wrong
    "media":                               "entertainment",  # was social_media — wrong
    "advertising agencies":                "ai_software",    # TTD, ZETA — ad tech platforms


    # E-commerce / retail
    "internet retail":                     "retail",     # AMZN, EBAY — plain retail default; LLM overrides for BABA/PDD → china_adr
    "specialty retail":                    "retail",     # HD, LOW, BBY, ULTA
    "discount stores":                     "retail",     # WMT, TGT, COST, DG, DLTR
    "grocery stores":                      "retail",     # KR, ACI
    "department stores":                   "retail",     # M, KSS, JWN
    "apparel retail":                      "retail",     # LULU, ANF, GPS
    "home improvement retail":             "retail",     # HD, LOW
    "beverages — non-alcoholic":           "retail",     # KO, PEP
    "packaged foods":                      "retail",     # GIS, K, KHC
    "household & personal products":       "retail",     # PG, CL, CHD
    "confectioners":                       "retail",     # HSY, MDLZ
    "tobacco":                             "retail",     # MO, PM
    "beverages — alcoholic":               "retail",     # STZ, DEO
    "restaurants":                         "retail",     # MCD, CMG, SBUX
    "leisure":                             "retail",     # NKE, LULU also
    "footwear & accessories":              "retail",     # NKE, DECK, CROX

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
    
    # Defense / aerospace — NEW
    "aerospace & defense":           "defense_aero",
    "defense":                       "defense_aero",
    "drone":                         "defense_aero",

    # Space
    "space":                  "space",
    "satellite":              "space",
    "launch services":        "space",

    "travel services":          "travel",
    "lodging":                  "travel",
    "resorts & casinos":        "travel",
    "airlines":                 "travel",
    "rental & leasing services": "travel",

    "real estate":              "real_estate",
    "real estate services":     "real_estate",
    "mortgage finance":         "real_estate",
    "residential construction": "real_estate",
}

# default fallback — only used when EVERYTHING else fails.
# retail is safer than big_tech because it doesn't pollute the AI supercycle sector
# with unrelated names that would break beta calculations and sector rotation signals.
_DEFAULT_SLUG = "retail"


def _classify_sector(info: dict) -> str:
    """
    Maps yfinance info dict to our sector slug.
    Uses industry map first, then sector map, then LLM for ambiguous cases.
    """
    sector   = (info.get("sector")   or "").lower().strip()
    industry = (info.get("industry") or "").lower().strip()

    # normalize dashes for consistent matching
    industry_n = industry.replace("—", "-").replace("–", "-").replace("  ", " ")

    # industry is more specific — check first
    for key, slug in _INDUSTRY_MAP.items():
        key_n = key.replace("—", "-").replace("–", "-")
        if key_n in industry_n:
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

    # consumer fallback if LLM unavailable (both cyclical and defensive)
    if "consumer" in sector:
        return "retail"

    return _DEFAULT_SLUG

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
            "semis_compute", "semis_memory", "semis_equipment", "ai_infra", "ai_data",
            "big_tech", "ai_software", "cloud_cyber",
            "social_media", "entertainment",
            "robotics", "ev_auto", "space", "defense_aero",
            "healthtech",
            "crypto_miners", "quantum",
            "etfs",
            "energy_infra", "energy_trad",
            "fintech", "banks",
            "china_adr",
            "retail", "travel", "real_estate",
        ]

        prompt = (
            f"Classify this company into exactly one sector slug. "
            f"Return ONLY the slug, nothing else.\n\n"
            f"Company: {company_name}\n"
            f"yfinance sector: {sector}\n"
            f"yfinance industry: {industry}\n"
            f"Description: {description}\n\n"
            f"Sector slugs:\n"
            f"- semis_compute: semiconductor chips (NVDA, AMD, INTC, QCOM, AVGO, ARM)\n"
            f"- semis_memory: memory chips (MU, SNDK, WDC, STX)\n"
            f"- semis_equipment: chip fab equipment (ASML, AMAT, LRCX, KLAC)\n"
            f"- ai_infra: AI infrastructure and data centers (NBIS, CRWV, IREN, VRT, ANET)\n"
            f"- ai_data: AI training data services (INOD, DDOG)\n"
            f"- big_tech: mega-cap platform tech only (MSFT, GOOGL, AAPL, AMZN, NFLX, ORCL)\n"
            f"- ai_software: AI/enterprise SaaS (PLTR, NOW, CRM, SNOW)\n"
            f"- cloud_cyber: cloud and cybersecurity platforms (CRWD, NET, PANW, ZS)\n"
            f"- social_media: social platforms and consumer internet (META, SNAP, PINS, RDDT)\n"
            f"- entertainment: media and streaming (DIS, WBD, PARA)\n"
            f"- robotics: industrial automation and robotics ONLY (ABB, ROK, ISRG)\n"
            f"- ev_auto: EVs, autos, auto parts (TSLA, RIVN, LCID, GM, F, NIO)\n"
            f"- space: space/satellite/launch (RKLB, LUNR, ASTS, PL)\n"
            f"- defense_aero: defense and aerospace (LMT, RTX, GD, BA, KTOS)\n"
            f"- healthtech: healthcare, biotech, pharma (LLY, JNJ, PFE, HIMS)\n"
            f"- crypto_miners: bitcoin/crypto mining (MARA, RIOT, CLSK, HUT)\n"
            f"- quantum: quantum computing (IONQ, RGTI, QBTS, QUBT)\n"
            f"- energy_infra: nuclear/solar/utilities/grid (SMR, OKLO, FSLR, NEE, VST)\n"
            f"- energy_trad: oil and gas (XOM, CVX, OXY, COP)\n"
            f"- fintech: payments and fintech (COIN, HOOD, SOFI, SQ, PYPL, AFRM)\n"
            f"- banks: traditional banks (JPM, WFC, C, MS, GS, BAC)\n"
            f"- china_adr: China e-commerce and tech ADRs (BABA, PDD, JD, BIDU)\n"
            f"- retail: retail, staples, restaurants, grocery, consumer goods "
            f"(WMT, TGT, COST, HD, LOW, MCD, SBUX, KO, PEP, PG, NKE)\n"
            f"- travel: airlines, cruises, hotels, casinos (UAL, DAL, CCL, MAR, MGM)\n"
            f"- real_estate: real estate, REITs, home builders (Z, OPEN, RDFN, DHI)\n"
            f"- etfs: ETFs, index funds, gold (SPY, QQQ, GLD, XLK)\n\n"
            f"CRITICAL: 'big_tech' is ONLY for mega-cap platform companies like MSFT/GOOGL/AAPL/AMZN. "
            f"Do NOT classify retailers, consumer goods, industrials, healthcare, or unrelated companies as big_tech. "
            f"When in doubt about a consumer/retail company, use 'retail'."
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