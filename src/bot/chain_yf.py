from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Literal

import yfinance as yf


class ChainError(RuntimeError):
    pass


CallPut = Literal["call", "put"]


@dataclass(frozen=True)
class YFContract:
    symbol: str
    strike: float
    expiration: str             # YYYY-MM-DD
    call_put: CallPut
    bid: Optional[float]
    ask: Optional[float]
    last: Optional[float]
    iv: Optional[float]
    oi: Optional[int]
    volume: Optional[int]
    in_the_money: Optional[bool]


def get_expirations(ticker: str) -> List[str]:
    try:
        t = yf.Ticker(ticker.upper().strip())
        return list(t.options)
    except Exception as e:
        raise ChainError(f"yfinance expirations failed for {ticker}: {e}") from e


def get_chain(ticker: str, expiration: str) -> List[YFContract]:
    try:
        t = yf.Ticker(ticker.upper().strip())
        chain = t.option_chain(expiration)
    except Exception as e:
        raise ChainError(f"yfinance chain failed for {ticker} {expiration}: {e}") from e

    out: List[YFContract] = []
    out.extend(_parse_df(chain.calls, expiration, "call"))
    out.extend(_parse_df(chain.puts, expiration, "put"))
    return out


def get_spot(ticker: str) -> float:
    """
    Live spot price from yfinance fast_info.
    Replaces Polygon entirely — no API key needed.
    """
    try:
        info = yf.Ticker(ticker.upper().strip()).fast_info
        price = float(info["last_price"])
        if price <= 0:
            raise ValueError("zero or negative price returned")
        return price
    except Exception as e:
        raise ChainError(f"spot price failed for {ticker}: {e}") from e


def get_price_history(ticker: str, period: str = "3mo") -> list[dict]:
    """
    Returns price history for trend analysis and technical indicators.
    period: 5d | 1mo | 3mo | 6mo | 1y
    """
    try:
        hist = yf.Ticker(ticker.upper().strip()).history(period=period)
        if hist.empty:
            return []
        return [
            {
                "date":   str(idx.date()),
                "open":   float(row["Open"]),
                "high":   float(row["High"]),
                "low":    float(row["Low"]),
                "close":  float(row["Close"]),
                "volume": int(row["Volume"]),
            }
            for idx, row in hist.iterrows()
        ]
    except Exception as e:
        raise ChainError(f"price history failed for {ticker}: {e}") from e


def _parse_df(df, expiration: str, call_put: CallPut) -> List[YFContract]:
    def col(name: str):
        return df[name] if name in df.columns else None

    c_symbol = col("contractSymbol")
    c_strike = col("strike")
    c_bid    = col("bid")
    c_ask    = col("ask")
    c_last   = col("lastPrice")
    c_iv     = col("impliedVolatility")
    c_oi     = col("openInterest")
    c_vol    = col("volume")
    c_itm    = col("inTheMoney")

    out: List[YFContract] = []
    for idx in range(len(df.index)):
        out.append(YFContract(
            symbol       = str(c_symbol.iloc[idx]) if c_symbol is not None else "",
            strike       = float(c_strike.iloc[idx]) if c_strike is not None else 0.0,
            expiration   = expiration,
            call_put     = call_put,
            bid          = _f(c_bid.iloc[idx])  if c_bid  is not None else None,
            ask          = _f(c_ask.iloc[idx])  if c_ask  is not None else None,
            last         = _f(c_last.iloc[idx]) if c_last is not None else None,
            iv           = _f(c_iv.iloc[idx])   if c_iv   is not None else None,
            oi           = _i(c_oi.iloc[idx])   if c_oi   is not None else None,
            volume       = _i(c_vol.iloc[idx])  if c_vol  is not None else None,
            in_the_money = bool(c_itm.iloc[idx]) if c_itm is not None else None,
        ))
    return out


def _f(x) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        if v != v:      # NaN check
            return None
        return v
    except Exception:
        return None


def _i(x) -> Optional[int]:
    try:
        if x is None:
            return None
        return int(x)
    except Exception:
        return None