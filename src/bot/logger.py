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