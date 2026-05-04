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
    "communication services":   "big_tech",
    "semiconductors":           "semis_compute",
    "semiconductor equipment":  "semis_equipment",
    "software":                 "ai_software",
    "cybersecurity":            "cloud_cyber",
    "cloud":                    "cloud_cyber",
    "financial services":       "fintech",
    "financial":                "fintech",
    "energy":                   "energy_infra",
    "utilities":                "energy_infra",
    "healthcare":               "healthtech",
    "biotechnology":            "healthtech",
    "consumer cyclical":        "big_tech",
    "consumer defensive":       "big_tech",
    "industrials":              "robotics",
    "basic materials":          "energy_infra",
}

_INDUSTRY_MAP: dict[str, str] = {
    # yfinance industry string → our slug (more specific, checked first)
    "semiconductors":                      "semis_compute",
    "semiconductor equipment & materials": "semis_equipment",
    "software — application":              "ai_software",
    "software — infrastructure":           "cloud_cyber",
    "internet content & information":      "big_tech",
    "computer hardware":                   "big_tech",
    "electronic components":               "semis_compute",
    "medical devices":                     "healthtech",
    "drug manufacturers":                  "healthtech",
    "uranium":                             "energy_infra",
    "nuclear":                             "energy_infra",
    "asset management":                    "etfs",
    "exchange traded fund":                "etfs",
    "capital markets":                     "fintech",
    "financial data & stock exchanges":    "fintech",
    "financial technology":                "fintech",
    "insurance":                           "fintech",
    "banks":                               "fintech",
}


def _classify_sector(info: dict) -> str:
    """
    Maps yfinance info dict to our sector slug.
    Falls back to 'ai_software' as default.
    """
    sector   = (info.get("sector")   or "").lower().strip()
    industry = (info.get("industry") or "").lower().strip()

    # industry is more specific — check first
    for key, slug in _INDUSTRY_MAP.items():
        if key in industry:
            return slug

    for key, slug in _SECTOR_MAP.items():
        if key in sector:
            return slug

    return "ai_software"  # default for unknown


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