from frm import storage


def test_insert_and_read_roundtrip(tmp_path):
    db = tmp_path / "x.db"
    with storage.connect(db) as conn:
        rid = storage.new_run(conn, 123)
        storage.insert_rows(conn, "funding_history", [
            dict(exchange="okx", base="BTC", symbol="BTC/USDT:USDT", ts_ms=1, rate=0.0001),
            dict(exchange="okx", base="BTC", symbol="BTC/USDT:USDT", ts_ms=1, rate=0.0001),  # 重複 → 被忽略
        ])
        storage.finish_run(conn, rid, 1.5, 2, "{}")
    df = storage.read_df(db, "SELECT * FROM funding_history")
    assert len(df) == 1
    assert storage.read_df(db, "SELECT duration_s FROM runs").duration_s[0] == 1.5


def test_read_missing_db_returns_empty(tmp_path):
    assert storage.read_df(tmp_path / "nope.db", "SELECT 1").empty
