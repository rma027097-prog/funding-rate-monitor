"""收集器（Collector）：整個系統的「心臟」。

用法（在專案根目錄執行）：
    python -m frm.collector once                 # 抓一次
    python -m frm.collector loop --cycles 6      # 每 interval_minutes 分鐘抓一次，共 6 次（不給 cycles 就一直跑）
    python -m frm.collector backfill --days 30   # 抓過去 30 天的歷史資金費率（給統計分析用）

每一輪（cycle）做的事：
1. 平行抓各交易所永續合約的資金費率 + 最佳買賣價
2. 抓台灣 MAX / BitoPro 的台幣現貨報價
3. 計算三種機會（資金費率價差、即時價差、台灣溢價）
4. 全部寫進 SQLite，並在終端機印出前幾名
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime, timedelta, timezone

from . import exchanges, opportunities, storage
from .config import Config, load_config

log = logging.getLogger("frm")
TAIPEI = timezone(timedelta(hours=8))


def now_ms() -> int:
    return int(time.time() * 1000)


def fmt_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, TAIPEI).strftime("%Y-%m-%d %H:%M:%S (台北)")


def run_once(cfg: Config) -> dict:
    """執行一輪收集，回傳摘要 dict（方便測試與印出）。"""
    t0 = time.time()
    ts = now_ms()

    snaps, errors = exchanges.fetch_all_perps(cfg)
    quotes: list = []
    if cfg.tw_spot_enabled:
        quotes, tw_err = exchanges.fetch_tw_spot(cfg)
        errors.update(tw_err)

    assets = opportunities.select_assets(snaps, cfg)
    kept = [s for by_ex in assets.values() for s in by_ex.values()]
    fopps = opportunities.funding_opportunities(assets, cfg)
    parbs = opportunities.perp_price_arbs(assets, cfg)
    tarbs = opportunities.tw_spot_arbs(quotes, cfg)
    prem = opportunities.taiwan_premium(quotes, snaps)

    with storage.connect(cfg.database) as conn:
        run_id = storage.new_run(conn, ts)
        storage.insert_rows(conn, "perp_snapshots", [
            dict(run_id=run_id, ts_ms=ts, exchange=s.exchange, symbol=s.symbol, base=s.base,
                 funding_rate=s.funding_rate, interval_h=s.interval_h, hourly_rate=s.hourly,
                 next_funding_ms=s.next_funding_ms, bid=s.bid, ask=s.ask, last=s.last,
                 quote_volume=s.quote_volume)
            for s in kept
        ])
        storage.insert_rows(conn, "funding_opps", [
            dict(run_id=run_id, ts_ms=ts, base=b, short_ex=o.short.exchange, long_ex=o.long.exchange,
                 short_rate=o.short.rate, long_rate=o.long.rate,
                 short_interval_h=o.short.interval_hours, long_interval_h=o.long.interval_hours,
                 gross_hourly=o.gross_hourly, gross_apr=o.gross_apr, fee_cost=o.fee_cost,
                 spread_cost=o.spread_cost, total_cost=o.total_cost,
                 break_even_h=(o.break_even_h if o.break_even_h != float("inf") else None),
                 net_apr=o.net_apr, basis=o.basis, suspect=int(sus))
            for b, o, sus in fopps
        ])
        storage.insert_rows(conn, "price_arbs", [dict(run_id=run_id, ts_ms=ts, **r) for r in parbs + tarbs])
        storage.insert_rows(conn, "tw_spot", [
            dict(run_id=run_id, ts_ms=ts, exchange=q.exchange, market=q.market, bid=q.bid, ask=q.ask, last=q.last)
            for q in quotes
        ])
        storage.insert_rows(conn, "tw_premium", [
            dict(run_id=run_id, ts_ms=ts, base=b, premium=p) for b, p in prem.items()
        ])
        dur = time.time() - t0
        storage.finish_run(conn, run_id, dur, len(kept), json.dumps(errors, ensure_ascii=False))

    return dict(run_id=run_id, ts_ms=ts, duration_s=dur, n_raw=len(snaps), n_assets=len(assets),
                n_snapshots=len(kept), funding=fopps, perp_arbs=parbs, tw_arbs=tarbs, premium=prem, errors=errors)


def print_summary(res: dict, top: int = 5) -> None:
    print(f"\n=== run #{res['run_id']} @ {fmt_ts(res['ts_ms'])}  "
          f"({res['duration_s']:.1f}s, {res['n_raw']} raw → {res['n_assets']} assets) ===")
    if res["errors"]:
        print("errors:", res["errors"])
    print(f"Top {top} funding spreads (net APR after fees, holding period from config):")
    clean = [(b, o) for b, o, sus in res["funding"] if not sus]
    n_sus = sum(1 for *_, sus in res["funding"] if sus)
    print(f"  ({len(clean)} clean pairs, {n_sus} flagged suspect: price deviation > threshold)")
    for base, o in clean[:top]:
        be = "∞" if o.break_even_h == float("inf") else f"{o.break_even_h:.1f}h"
        print(f"  {base:<8} short {o.short.exchange:<11} long {o.long.exchange:<11} "
              f"gross {o.gross_apr*100:7.2f}%/yr  cost {o.total_cost*100:.3f}%  break-even {be:>8}  net {o.net_apr*100:7.2f}%/yr")
    clean_arbs = [r for r in res["perp_arbs"] if not r["suspect"]]
    if clean_arbs:
        r = clean_arbs[0]
        print(f"Best perp instant arb: {r['base']} sell {r['sell_ex']} / buy {r['buy_ex']} "
              f"gross {r['gross_edge']*100:.4f}%  net {r['net_edge']*100:.4f}%")
    for r in res["tw_arbs"][:2]:
        print(f"TW spot {r['base']}: sell {r['sell_ex']} / buy {r['buy_ex']} gross {r['gross_edge']*100:.4f}% net {r['net_edge']*100:.4f}%")
    for b, p in res["premium"].items():
        print(f"Taiwan premium {b}: {p*100:+.3f}%")


def backfill(cfg: Config, days: int, assets: list[str] | None = None, exchanges_: list[str] | None = None) -> int:
    """抓歷史資金費率存進 funding_history（已存在的資料會自動略過）。"""
    assets = assets or ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "LINK", "AVAX", "SUI", "BNB"]
    exs = exchanges_ or cfg.perp_exchanges
    since = now_ms() - days * 86_400_000
    total = 0
    with storage.connect(cfg.database) as conn:
        for ex in exs:
            for base in assets:
                try:
                    sym = exchanges.find_symbol(ex, base)
                    if not sym:
                        continue
                    hist = exchanges.fetch_funding_history(ex, sym, since, cfg)
                    n = storage.insert_rows(conn, "funding_history", [
                        dict(exchange=ex, base=base, symbol=sym, ts_ms=int(h["timestamp"]), rate=float(h["fundingRate"]))
                        for h in hist if h.get("fundingRate") is not None
                    ])
                    total += n
                    print(f"  backfill {ex:<11} {base:<5} {sym:<18} {n:4d} rows")
                except Exception as e:  # noqa: BLE001
                    print(f"  backfill {ex} {base} failed: {type(e).__name__}: {str(e)[:120]}")
    return total


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Funding-rate & spread collector (read-only)")
    ap.add_argument("mode", choices=["once", "loop", "backfill"])
    ap.add_argument("--config", default=None)
    ap.add_argument("--cycles", type=int, default=0, help="loop 模式跑幾輪（0 = 無限）")
    ap.add_argument("--interval", type=float, default=None, help="覆寫 interval_minutes")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--assets", default=None, help="backfill 的幣，逗號分隔，例如 BTC,ETH")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cfg = load_config(args.config)
    if args.mode == "once":
        print_summary(run_once(cfg))
    elif args.mode == "backfill":
        n = backfill(cfg, args.days, args.assets.split(",") if args.assets else None)
        print(f"backfill done: {n} rows")
    else:
        interval_s = (args.interval or cfg.interval_minutes) * 60
        i = 0
        while True:
            i += 1
            start = time.time()
            try:
                print_summary(run_once(cfg))
            except Exception as e:  # noqa: BLE001 — 單輪失敗不讓整個程式掛掉
                log.exception("cycle failed: %s", e)
            if args.cycles and i >= args.cycles:
                break
            time.sleep(max(0.0, interval_s - (time.time() - start)))


if __name__ == "__main__":
    main()
