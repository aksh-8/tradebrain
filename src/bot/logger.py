from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from bot.models import Intake, ResearchResult, Pick


_DB_PATH = Path(__file__).parent.parent.parent / "trades" / "log.db"

def _conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(_DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _init() -> None:
    with _conn() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT    NOT NULL,
            ticker      TEXT    NOT NULL,
            direction   TEXT,
            budget      REAL,
            timeframe   TEXT,
            thesis      TEXT,
            verdict     TEXT,
            reasoning   TEXT,
            confidence  TEXT,
            iv_rank     REAL,
            price       REAL,
            earnings_days INTEGER,
            news        TEXT,
            raw_text    TEXT
        );

        CREATE TABLE IF NOT EXISTS picks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id      INTEGER NOT NULL REFERENCES runs(id),
            rank        INTEGER,
            ticker      TEXT,
            expiration  TEXT,
            strike      REAL,
            side        TEXT,
            dte         INTEGER,
            cost        REAL,
            breakeven   REAL,
            otm_pct     REAL,
            iv          REAL,
            oi          INTEGER,
            volume      INTEGER,
            spread_pct  REAL,
            rank_score  REAL,
            relaxed     INTEGER,
            relax_note  TEXT,
            why         TEXT
        );
        """)


def log_run(
    intake: Intake,
    research: ResearchResult,
    picks: list[Pick],
) -> int:
    """
    Saves a full run to the database.
    Returns the run_id so it can be referenced later.
    """
    _init()
    ts = datetime.utcnow().isoformat()

    with _conn() as con:
        cur = con.execute(
            """
            INSERT INTO runs
              (ts, ticker, direction, budget, timeframe, thesis,
               verdict, reasoning, confidence, iv_rank, price,
               earnings_days, news, raw_text)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                ts,
                research.ticker,
                intake.direction,
                intake.budget,
                intake.timeframe,
                intake.thesis,
                research.thesis_verdict,
                research.thesis_reasoning,
                research.confidence,
                research.iv_rank,
                research.price,
                research.earnings_days_away,
                research.news_summary,
                intake.raw_text,
            ),
        )
        run_id = cur.lastrowid

        for rank, p in enumerate(picks, 1):
            con.execute(
                """
                INSERT INTO picks
                  (run_id, rank, ticker, expiration, strike, side,
                   dte, cost, breakeven, otm_pct, iv, oi, volume,
                   spread_pct, rank_score, relaxed, relax_note, why)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id, rank, p.ticker, p.expiration, p.strike,
                    p.side, p.dte, p.cost, p.breakeven, p.otm_pct,
                    p.iv, p.oi, p.volume, p.spread_pct, p.rank_score,
                    int(p.relaxed), p.relax_note,
                    json.dumps(list(p.why)),
                ),
            )

    return run_id


def get_recent_runs(n: int = 10) -> list[dict]:
    """
    Returns the last n runs as dicts, most recent first.
    """
    _init()
    with _conn() as con:
        rows = con.execute(
            """
            SELECT r.*, COUNT(p.id) as pick_count
            FROM runs r
            LEFT JOIN picks p ON p.run_id = r.id
            GROUP BY r.id
            ORDER BY r.id DESC
            LIMIT ?
            """,
            (n,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_run_picks(run_id: int) -> list[dict]:
    """
    Returns all picks for a given run_id.
    """
    _init()
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM picks WHERE run_id = ? ORDER BY rank",
            (run_id,),
        ).fetchall()
    return [dict(r) for r in rows]

def get_runs_by_ticker(ticker: str, n: int = 20) -> list[dict]:
    """
    Returns last n runs for a specific ticker, most recent first.
    """
    _init()
    with _conn() as con:
        rows = con.execute(
            """
            SELECT r.*, COUNT(p.id) as pick_count
            FROM runs r
            LEFT JOIN picks p ON p.run_id = r.id
            WHERE r.ticker = ?
            GROUP BY r.id
            ORDER BY r.id DESC
            LIMIT ?
            """,
            (ticker.upper().strip(), n),
        ).fetchall()
    return [dict(r) for r in rows]


def get_run_detail(run_id: int) -> dict:
    """
    Returns a single run with all its picks.
    """
    _init()
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        if not row:
            return {}
        result = dict(row)
        result["picks"] = get_run_picks(run_id)
    return result

# ---------------------------------------------------------------------------
# Paper trading
# ---------------------------------------------------------------------------

def _init_paper_trades() -> None:
    """Creates the paper_trades table if it doesn't exist."""
    with _conn() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS paper_trades (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            logged_at       TEXT    NOT NULL,
            ticker          TEXT    NOT NULL,
            strike          REAL    NOT NULL,
            side            TEXT    NOT NULL,
            expiry          TEXT    NOT NULL,
            entry_cost      REAL    NOT NULL,
            quantity        INTEGER NOT NULL DEFAULT 1,
            total_invested  REAL    NOT NULL,
            trade_type      TEXT    NOT NULL,
            source          TEXT,
            thesis          TEXT,
            llm_provider    TEXT,
            status          TEXT    NOT NULL DEFAULT 'open',
            exit_cost       REAL,
            exit_at         TEXT,
            pnl_dollars     REAL,
            pnl_pct         REAL
        );
        """)
    
    # migrate existing table — add llm_provider if missing
    with _conn() as con:
        cols = [r[1] for r in con.execute("PRAGMA table_info(paper_trades)").fetchall()]
        if "llm_provider" not in cols:
            con.execute("ALTER TABLE paper_trades ADD COLUMN llm_provider TEXT")


def log_paper_trade(
    ticker:       str,
    strike:       float,
    side:         str,
    expiry:       str,
    entry_cost:   float,
    quantity:     int,
    trade_type:   str,
    source:       Optional[str] = None,
    thesis:       Optional[str] = None,
    llm_provider: Optional[str] = None,
) -> int:
    """
    Logs a paper or real trade.
    entry_cost = dollars per contract.
    Returns the trade id.
    """
    _init_paper_trades()
    logged_at      = datetime.utcnow().isoformat()
    total_invested = entry_cost * quantity

    with _conn() as con:
        cur = con.execute(
            """
            INSERT INTO paper_trades
              (logged_at, ticker, strike, side, expiry, entry_cost,
               quantity, total_invested, trade_type, source, thesis, llm_provider, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                logged_at, ticker.upper(), strike, side, expiry,
                entry_cost, quantity, total_invested,
                trade_type, source, thesis, llm_provider, "open",
            ),
        )
    return cur.lastrowid


def get_open_trades() -> list[dict]:
    _init_paper_trades()
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM paper_trades WHERE status = 'open' ORDER BY logged_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_trades() -> list[dict]:
    _init_paper_trades()
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM paper_trades ORDER BY logged_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_trade_by_id(trade_id: int) -> Optional[dict]:
    _init_paper_trades()
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM paper_trades WHERE id = ?", (trade_id,)
        ).fetchone()
    return dict(row) if row else None


def close_paper_trade(trade_id: int, exit_cost: float) -> dict:
    """
    Closes a trade at exit_cost (dollars per contract).
    Returns the updated trade dict with P&L.
    """
    _init_paper_trades()
    trade = get_trade_by_id(trade_id)
    if not trade:
        raise ValueError(f"Trade ID {trade_id} not found.")
    if trade["status"] != "open":
        raise ValueError(f"Trade ID {trade_id} is already {trade['status']}.")

    exit_at     = datetime.utcnow().isoformat()
    pnl_dollars = (exit_cost - trade["entry_cost"]) * trade["quantity"]
    pnl_pct     = (pnl_dollars / trade["total_invested"] * 100) if trade["total_invested"] else 0
    status      = "expired" if exit_cost == 0 else "closed"

    with _conn() as con:
        con.execute(
            """
            UPDATE paper_trades
            SET status=?, exit_cost=?, exit_at=?, pnl_dollars=?, pnl_pct=?
            WHERE id=?
            """,
            (status, exit_cost, exit_at, pnl_dollars, pnl_pct, trade_id),
        )

    return get_trade_by_id(trade_id)

def delete_paper_trade(trade_id: int) -> bool:
    """Permanently deletes a trade by ID. Returns True if deleted."""
    _init_paper_trades()
    with _conn() as con:
        cur = con.execute(
            "DELETE FROM paper_trades WHERE id = ?", (trade_id,)
        )
    return cur.rowcount > 0

def get_paper_account_summary() -> dict:
    """
    Computes the full paper trading account state.
    Returns dict with starting, deployed, available, realized_pnl, total_value.
    """
    from bot.config import get_settings
    _init_paper_trades()
    starting = get_settings().paper_bankroll

    with _conn() as con:
        open_trades   = con.execute(
            "SELECT entry_cost, quantity FROM paper_trades "
            "WHERE status='open' AND trade_type='paper'"
        ).fetchall()
        closed_trades = con.execute(
            "SELECT pnl_dollars FROM paper_trades "
            "WHERE status IN ('closed','expired') AND trade_type='paper'"
        ).fetchall()

    deployed     = sum(r["entry_cost"] * r["quantity"] for r in open_trades)
    realized_pnl = sum(r["pnl_dollars"] or 0 for r in closed_trades)
    available    = starting - deployed + realized_pnl

    return {
        "starting":     starting,
        "deployed":     deployed,
        "available":    available,
        "realized_pnl": realized_pnl,
        "open_count":   len(open_trades),
    }


def get_real_account_summary() -> dict:
    """
    Computes the real trading account state from logged real trades.
    """
    from bot.config import get_settings
    _init_paper_trades()
    starting = get_settings().bankroll_usd

    with _conn() as con:
        open_trades   = con.execute(
            "SELECT entry_cost, quantity FROM paper_trades "
            "WHERE status='open' AND trade_type='real'"
        ).fetchall()
        closed_trades = con.execute(
            "SELECT pnl_dollars FROM paper_trades "
            "WHERE status IN ('closed','expired') AND trade_type='real'"
        ).fetchall()

    deployed     = sum(r["entry_cost"] * r["quantity"] for r in open_trades)
    realized_pnl = sum(r["pnl_dollars"] or 0 for r in closed_trades)
    available    = starting - deployed + realized_pnl

    return {
        "starting":     starting,
        "deployed":     deployed,
        "available":    available,
        "realized_pnl": realized_pnl,
        "open_count":   len(open_trades),
    }