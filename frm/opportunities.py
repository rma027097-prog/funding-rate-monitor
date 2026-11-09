"""把「一堆快照」變成「排好序的機會清單」。

純函式：輸入快照 + 設定，輸出機會，不碰網路也不碰資料庫 → 好測試。
三種機會：
1. 跨所資金費率價差（funding spread）：在 A 做空、B 做多，賺費率差。
2. 永續合約跨所即時價差（perp price arb）：A 的買價 > B 的賣價 + 手續費。
3. 台灣現貨跨所價差（MAX vs BitoPro）以及「台灣溢價」。
"""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations, permutations

from .config import Config
from .exchanges import PerpSnapshot, SpotQuote
from .metrics import FundingSpread, Leg, funding_spread, instant_arb_edge


def select_assets(snaps: list[PerpSnapshot], cfg: Config) -> dict[str, dict[str, PerpSnapshot]]:
    """依成交量過濾，並只留下「至少 2 家交易所都有」的幣 → {base: {exchange: snapshot}}。

    同一交易所同一幣若有多個市場（例如 USDT 與 USDC 永續），保留成交量大的那個。
    """
    grouped: dict[str, dict[str, PerpSnapshot]] = defaultdict(dict)
    for s in snaps:
        if (s.quote_volume or 0) < cfg.min_quote_volume_usd:
            continue
        if cfg.watchlist and s.base not in cfg.watchlist:
            continue
        cur = grouped[s.base].get(s.exchange)
        if cur is None or (s.quote_volume or 0) > (cur.quote_volume or 0):
            grouped[s.base][s.exchange] = s
    multi = {b: v for b, v in grouped.items() if len(v) >= 2}
    # 依「各所成交量總和」排序，只取前 max_assets 個
    ranked = sorted(multi, key=lambda b: -sum((x.quote_volume or 0) for x in multi[b].values()))
    return {b: multi[b] for b in ranked[: cfg.max_assets]}


def to_leg(s: PerpSnapshot, cfg: Config) -> Leg:
    return Leg(s.exchange, s.funding_rate, s.interval_h, s.bid, s.ask, cfg.fee(s.exchange).taker)


def is_suspect(a_mid: float | None, b_mid: float | None, max_dev: float) -> bool:
    """兩邊價格差太多 → 很可能根本不是同一個標的（資料品質檢查）。"""
    if not a_mid or not b_mid:
        return True
    return abs(a_mid / b_mid - 1) > max_dev


def _mid(s) -> float | None:
    return (s.bid + s.ask) / 2 if s.bid and s.ask else None


def funding_opportunities(assets: dict[str, dict[str, PerpSnapshot]], cfg: Config) -> list[tuple[str, FundingSpread, bool]]:
    """每個幣、每一對交易所，都算一次資金費率價差。

    回傳 (幣, 計算結果, 是否可疑)；排序：正常的在前，再依「扣成本後年化」由高到低。
    """
    out = []
    for base, by_ex in assets.items():
        for a, b in combinations(by_ex.values(), 2):
            fs = funding_spread(to_leg(a, cfg), to_leg(b, cfg), cfg.holding_days)
            out.append((base, fs, is_suspect(_mid(a), _mid(b), cfg.max_price_deviation)))
    out.sort(key=lambda x: (not x[2], x[1].net_apr), reverse=True)
    return out


def perp_price_arbs(assets: dict[str, dict[str, PerpSnapshot]], cfg: Config) -> list[dict]:
    """同一個幣，在 A 賣（bid）、在 B 買（ask）的即時淨利率。兩個方向都算。"""
    out = []
    for base, by_ex in assets.items():
        for sell, buy in permutations(by_ex.values(), 2):
            if not sell.bid or not buy.ask:
                continue
            gross = sell.bid / buy.ask - 1
            net = instant_arb_edge(sell.bid, buy.ask, cfg.fee(sell.exchange).taker, cfg.fee(buy.exchange).taker)
            sus = is_suspect(_mid(sell), _mid(buy), cfg.max_price_deviation)
            out.append(dict(market_type="perp", base=base, sell_ex=sell.exchange, buy_ex=buy.exchange,
                            sell_bid=sell.bid, buy_ask=buy.ask, gross_edge=gross, net_edge=net, suspect=int(sus)))
    out.sort(key=lambda r: (not r["suspect"], r["net_edge"]), reverse=True)
    return out


def tw_spot_arbs(quotes: list[SpotQuote], cfg: Config) -> list[dict]:
    """MAX vs BitoPro 同一個台幣市場的雙向搬磚淨利率。"""
    by_mkt: dict[str, dict[str, SpotQuote]] = defaultdict(dict)
    for q in quotes:
        by_mkt[q.market][q.exchange] = q
    out = []
    for mkt, by_ex in by_mkt.items():
        for sell, buy in permutations(by_ex.values(), 2):
            gross = sell.bid / buy.ask - 1
            net = instant_arb_edge(sell.bid, buy.ask, cfg.fee(sell.exchange).taker, cfg.fee(buy.exchange).taker)
            out.append(dict(market_type="tw_spot", base=mkt.upper(), sell_ex=sell.exchange, buy_ex=buy.exchange,
                            sell_bid=sell.bid, buy_ask=buy.ask, gross_edge=gross, net_edge=net, suspect=0))
    out.sort(key=lambda r: r["net_edge"], reverse=True)
    return out


def taiwan_premium(quotes: list[SpotQuote], snaps: list[PerpSnapshot], ref_exchange: str = "okx") -> dict[str, float]:
    """台灣溢價：MAX 的 BTC/TWD ÷ USDT/TWD，跟國際 BTC/USDT 價格比較。

    > 0 代表台灣買 BTC 比國際貴（類似韓國的「泡菜溢價」概念）。
    國際價用 OKX 永續中價近似（永續與現貨有小基差，這是簡化）。
    """
    q = {(x.exchange, x.market): x for x in quotes}
    usdt = q.get(("max", "usdt"))
    out: dict[str, float] = {}
    if not usdt:
        return out
    usdt_mid = (usdt.bid + usdt.ask) / 2
    for s in snaps:
        if s.exchange != ref_exchange or not s.bid or not s.ask:
            continue
        local = q.get(("max", s.base.lower()))
        if local and s.symbol.endswith("/USDT:USDT"):
            local_usd = (local.bid + local.ask) / 2 / usdt_mid
            out[s.base] = local_usd / ((s.bid + s.ask) / 2) - 1
    return out
