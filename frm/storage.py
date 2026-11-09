"""SQLite 儲存層。

SQLite = 一個「單一檔案」的資料庫，不用架伺服器，Python 內建就支援，
很適合個人專案；資料量變大時可以換成 PostgreSQL，只要改這個檔案。

時間一律存成 UTC 毫秒（ts_ms），顯示時才轉成台北時間，避免時區混亂。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms       INTEGER NOT NULL,
    duration_s  REAL,
    n_snapshots INTEGER,
    errors      TEXT
);

CREATE TABLE IF NOT EXISTS perp_snapshots (
    run_id INTEGER, ts_ms INTEGER, exchange TEXT, symbol TEXT, base TEXT,
    funding_rate REAL, interval_h REAL, hourly_rate REAL, next_funding_ms INTEGER,
    bid REAL, ask REAL, last REAL, quote_volume REAL
);
CREATE INDEX IF NOT EXISTS ix_perp_base_ts ON perp_snapshots(base, ts_ms);

CREATE TABLE IF NOT EXISTS funding_opps (
    run_id INTEGER, ts_ms INTEGER, base TEXT, short_ex TEXT, long_ex TEXT,
    short_rate REAL, long_rate REAL, short_interval_h REAL, long_interval_h REAL,
    gross_hourly REAL, gross_apr REAL, fee_cost REAL, spread_cost REAL, total_cost REAL,
    break_even_h REAL, net_apr REAL, basis REAL, suspect INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_opps_ts ON funding_opps(ts_ms);

CREATE TABLE IF NOT EXISTS price_arbs (
    run_id INTEGER, ts_ms INTEGER, market_type TEXT, base TEXT,
    sell_ex TEXT, buy_ex TEXT, sell_bid REAL, buy_ask REAL, gross_edge REAL, net_edge REAL, suspect INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tw_spot (
    run_id INTEGER, ts_ms INTEGER, exchange TEXT, market TEXT, bid REAL, ask REAL, last REAL
);

CREATE TABLE IF NOT EXISTS tw_premium (
    run_id INTEGER, ts_ms INTEGER, base TEXT, premium REAL
);

CREATE TABLE IF NOT EXISTS funding_history (
    exchange TEXT, base TEXT, symbol TEXT, ts_ms INTEGER, rate REAL,
    PRIMARY KEY (exchange, symbol, ts_ms)
);
"""


@contextmanager
def connect(db_path: str | Path):
    """開啟資料庫連線；離開 with 區塊時自動 commit 並關閉。"""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def insert_rows(conn: sqlite3.Connection, table: str, rows: list[dict]) -> int:
    """把 list[dict] 一次寫進資料表（欄位名稱 = dict 的 key）。用 ? 參數化，避免 SQL injection。"""
    if not rows:
        return 0
    cols = list(rows[0].keys())
    sql = f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
    conn.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
    return len(rows)


def new_run(conn: sqlite3.Connection, ts_ms: int) -> int:
    cur = conn.execute("INSERT INTO runs (ts_ms) VALUES (?)", (ts_ms,))
    return int(cur.lastrowid)


def finish_run(conn, run_id: int, duration_s: float, n: int, errors: str) -> None:
    conn.execute(
        "UPDATE runs SET duration_s=?, n_snapshots=?, errors=? WHERE run_id=?",
        (duration_s, n, errors, run_id),
    )


def read_df(db_path: str | Path, sql: str, params: tuple = ()) -> pd.DataFrame:
    """執行 SQL 查詢並回傳 pandas DataFrame（分析與網頁都用這個）。"""
    if not Path(db_path).exists():
        return pd.DataFrame()
    with connect(db_path) as conn:
        return pd.read_sql_query(sql, conn, params=params)
