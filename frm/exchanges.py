"""跟交易所拿資料（網路 I/O 都集中在這個檔案）。

- 永續合約：透過開源函式庫 ccxt（MIT License）統一不同交易所的 API 格式。
- 台灣現貨（MAX、BitoPro）：直接呼叫官方公開 REST API（ccxt 沒有 MAX）。

只用「公開行情」端點，不需要 API Key、不會下單。
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import ccxt
import requests

from .config import Config
from .metrics import hourly_rate, parse_interval_hours
from .symbols import is_usd_linear_perp, normalize_base, parse_symbol

log = logging.getLogger(__name__)

_EXCHANGE_CACHE: dict[str, ccxt.Exchange] = {}


@dataclass
class PerpSnapshot:
    """某一刻、某交易所、某個幣的永續合約狀態。"""

    exchange: str
    symbol: str
    base: str
    funding_rate: float
    interval_h: float
    next_funding_ms: int | None
    bid: float | None
    ask: float | None
    last: float | None
    quote_volume: float | None

    @property
    def hourly(self) -> float:
        return hourly_rate(self.funding_rate, self.interval_h)


@dataclass
class SpotQuote:
    """台灣交易所的台幣現貨報價。"""

    exchange: str
    market: str  # 例如 'usdt'、'btc'（都是對 TWD）
    bid: float
    ask: float
    last: float | None


def get_exchange(name: str) -> ccxt.Exchange:
    """建立（並快取）ccxt 交易所物件。load_markets 很慢，所以一個 process 只做一次。"""
    if name not in _EXCHANGE_CACHE:
        ex = getattr(ccxt, name)({"enableRateLimit": True, "timeout": 20000})
        ex.load_markets()
        _EXCHANGE_CACHE[name] = ex
    return _EXCHANGE_CACHE[name]


def _quote_volume(ex_name: str, ticker: dict) -> float | None:
    """24h 成交額（美元）。各交易所欄位不一致，這裡做統一。"""
    qv = ticker.get("quoteVolume")
    if qv:
        return float(qv)
    info = ticker.get("info") or {}
    last = ticker.get("last")
    if ex_name == "okx" and info.get("volCcy24h") and last:
        # OKX 永續的 baseVolume 是「張數」，volCcy24h 才是幣的數量
        return float(info["volCcy24h"]) * float(last)
    if ticker.get("baseVolume") and last:
        return float(ticker["baseVolume"]) * float(last)
    return None


def _interval_for(ex: ccxt.Exchange, ex_name: str, symbol: str, fr: dict, cfg: Config) -> float:
    """資金費率結算週期（小時）：先看 ccxt 回傳，再看市場資訊，最後用設定檔預設值。"""
    default = cfg.default_interval(ex_name)
    if fr.get("interval"):
        return parse_interval_hours(fr["interval"], default)
    info = (ex.markets.get(symbol) or {}).get("info") or {}
    for key in ("fundInterval", "fundingInterval", "funding_interval"):
        if info.get(key):
            v = info[key]
            # gate 以「秒」表示（28800），bitget 以「小時」表示（'8'）
            v = float(v)
            return v / 3600 if v > 100 else v
    return default


def fetch_perp_snapshots(ex_name: str, cfg: Config) -> list[PerpSnapshot]:
    """抓一家交易所「所有」USDT/USDC 線性永續的資金費率 + 最佳買賣價。"""
    ex = get_exchange(ex_name)
    rates = ex.fetch_funding_rates()
    params = {} if ex_name == "hyperliquid" else {"type": "swap"}
    tickers = ex.fetch_tickers(params=params)

    out: list[PerpSnapshot] = []
    for symbol, fr in rates.items():
        if not is_usd_linear_perp(symbol) or fr.get("fundingRate") is None:
            continue
        t = tickers.get(symbol) or {}
        base, _, _ = parse_symbol(symbol)
        out.append(
            PerpSnapshot(
                exchange=ex_name,
                symbol=symbol,
                base=normalize_base(base),
                funding_rate=float(fr["fundingRate"]),
                interval_h=_interval_for(ex, ex_name, symbol, fr, cfg),
                next_funding_ms=fr.get("nextFundingTimestamp") or fr.get("fundingTimestamp"),
                bid=t.get("bid"),
                ask=t.get("ask"),
                last=t.get("last"),
                quote_volume=_quote_volume(ex_name, t),
            )
        )
    return out


def fetch_all_perps(cfg: Config) -> tuple[list[PerpSnapshot], dict[str, str]]:
    """平行（多執行緒）抓所有交易所。某一家失敗不影響其他家，錯誤會記錄下來。"""
    snaps: list[PerpSnapshot] = []
    errors: dict[str, str] = {}

    def work(name):
        try:
            return name, fetch_perp_snapshots(name, cfg), None
        except Exception as e:  # noqa: BLE001 — 單一交易所出錯時繼續跑其他家
            return name, [], f"{type(e).__name__}: {str(e)[:200]}"

    with ThreadPoolExecutor(max_workers=len(cfg.perp_exchanges) or 1) as pool:
        for name, rows, err in pool.map(work, cfg.perp_exchanges):
            snaps.extend(rows)
            if err:
                errors[name] = err
                log.warning("%s failed: %s", name, err)
    return snaps, errors


# ---------------- 台灣現貨（MAX / BitoPro） ----------------

def fetch_max(markets: list[str]) -> list[SpotQuote]:
    """MAX（現代財富）公開 API v3：一次拿多個市場的最佳買賣價。"""
    params = [("markets[]", f"{m}twd") for m in markets]
    r = requests.get("https://max-api.maicoin.com/api/v3/tickers", params=params, timeout=15)
    r.raise_for_status()
    out = []
    for row in r.json():
        out.append(
            SpotQuote("max", row["market"].removesuffix("twd"), float(row["buy"]), float(row["sell"]), float(row["last"]))
        )
    return out


def fetch_bitopro(markets: list[str]) -> list[SpotQuote]:
    """BitoPro（幣託）公開 API v3：各市場訂單簿第一檔。"""
    out = []
    for m in markets:
        r = requests.get(f"https://api.bitopro.com/v3/order-book/{m}_twd", params={"limit": 1}, timeout=15)
        r.raise_for_status()
        ob = r.json()
        if ob.get("bids") and ob.get("asks"):
            out.append(SpotQuote("bitopro", m, float(ob["bids"][0]["price"]), float(ob["asks"][0]["price"]), None))
        time.sleep(0.1)  # 禮貌性限速
    return out


def fetch_tw_spot(cfg: Config) -> tuple[list[SpotQuote], dict[str, str]]:
    quotes: list[SpotQuote] = []
    errors: dict[str, str] = {}
    for name, fn in (("max", fetch_max), ("bitopro", fetch_bitopro)):
        try:
            quotes.extend(fn(cfg.tw_spot_markets))
        except Exception as e:  # noqa: BLE001
            errors[name] = f"{type(e).__name__}: {str(e)[:200]}"
    return quotes, errors


# ---------------- 歷史資金費率（給統計分析用） ----------------

def find_symbol(ex_name: str, base: str) -> str | None:
    """在某交易所找到某個幣的 USDT/USDC 線性永續 symbol（考慮 1000PEPE / kPEPE 這類乘數）。"""
    ex = get_exchange(ex_name)
    for sym, m in ex.markets.items():
        if m.get("swap") and is_usd_linear_perp(sym) and normalize_base(parse_symbol(sym)[0]) == base:
            return sym
    return None


def fetch_funding_history(ex_name: str, symbol: str, since_ms: int, cfg: Config) -> list[dict]:
    """分頁抓取歷史資金費率（每頁最多 100 筆），直到現在。"""
    ex = get_exchange(ex_name)
    rows: list[dict] = []
    cursor = since_ms
    now = ex.milliseconds()
    for _ in range(50):  # 安全上限，避免無限迴圈
        page = ex.fetch_funding_rate_history(symbol, since=cursor, limit=100)
        page = [p for p in page if p.get("timestamp") and p["timestamp"] >= cursor]
        if not page:
            break
        rows.extend(page)
        last_ts = max(p["timestamp"] for p in page)
        if last_ts <= cursor or last_ts >= now - 60_000:
            break
        cursor = last_ts + 1
    # 去重
    uniq = {r["timestamp"]: r for r in rows}
    return [uniq[k] for k in sorted(uniq)]
