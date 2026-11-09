"""純數學模組：資金費率、手續費、價差、回本時間。

這個檔案「不連網、不讀檔」，只有輸入 → 輸出的純函式（pure functions），
所以非常好測試（見 tests/test_metrics.py）。

單位約定（很重要！）：
- 所有比率都用「小數」：0.0001 = 0.01%，0.12 = 12%。
- 只有在畫面顯示時才乘以 100 變成百分比。

部分想法（年化、四筆成交的來回成本、回本時間）參考 hamood1337/CryptoFundingArb
(src/fundingarb/funding.py, fees.py, MIT License)。本檔改寫為以「每小時費率」為核心單位，
並新增：盤口買賣價差成本、跨所即時價差、回本「小時」數。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

HOURS_PER_YEAR = 24 * 365  # 8760

_INTERVAL_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([hHdD])?\s*$")


def parse_interval_hours(interval, default: float = 8.0) -> float:
    """把交易所回報的結算週期轉成「小時」。

    可接受 '8h'、'1h'、'1d'、數字 8、或 None（None/無法解析時回傳 default）。
    """
    if isinstance(interval, bool):
        return default
    if isinstance(interval, (int, float)):
        return float(interval) if interval > 0 else default
    if isinstance(interval, str):
        m = _INTERVAL_RE.match(interval)
        if m:
            value = float(m.group(1))
            hours = value * 24 if (m.group(2) or "h").lower() == "d" else value
            if hours > 0:
                return hours
    return default


def hourly_rate(rate: float, interval_hours: float) -> float:
    """把「每 N 小時收一次」的費率換成「每小時」費率，才能跨所比較。

    例：OKX 8 小時收 0.01% → 每小時 0.00125%；Hyperliquid 本來就是每小時。
    """
    if interval_hours <= 0:
        raise ValueError("interval_hours must be positive")
    return rate / interval_hours


def annualize(rate: float, interval_hours: float) -> float:
    """單利年化：每期費率 × 一年有幾期。回傳小數（0.1095 = 10.95%）。"""
    return hourly_rate(rate, interval_hours) * HOURS_PER_YEAR


def round_trip_fee(taker_long: float, taker_short: float) -> float:
    """開倉 + 平倉、兩邊各一次 = 四筆成交的手續費總和（佔名目本金的比例）。"""
    return 2.0 * (taker_long + taker_short)


def spread_cost(bid: float | None, ask: float | None) -> float:
    """穿越買賣價差的成本：(ask - bid) / mid。

    市價買要付 ask、市價賣只拿 bid，開+平各吃半個價差，合計約一個完整價差。
    沒有報價時回傳 0（保守起見呼叫端應另行過濾）。
    """
    if not bid or not ask or bid <= 0 or ask <= 0:
        return 0.0
    mid = (bid + ask) / 2
    return max(ask - bid, 0.0) / mid


def break_even_hours(gross_hourly: float, cost: float) -> float:
    """要收幾小時的資金費，才能把一次性成本賺回來？沒有正收益時回傳無限大。"""
    if gross_hourly <= 0:
        return math.inf
    return cost / gross_hourly


def net_apr(gross_apr: float, cost: float, holding_days: float) -> float:
    """把一次性成本攤到持有天數後，重新年化，得到「扣成本後」的年化。"""
    if holding_days <= 0:
        raise ValueError("holding_days must be positive")
    return gross_apr - cost * (365.0 / holding_days)


@dataclass(frozen=True)
class Leg:
    """一個交易所上某個幣的永續合約快照（計算用的最小資訊）。"""

    exchange: str
    rate: float  # 本期資金費率（小數）
    interval_hours: float
    bid: float | None
    ask: float | None
    taker: float  # 吃單手續費（小數）

    @property
    def hourly(self) -> float:
        return hourly_rate(self.rate, self.interval_hours)

    @property
    def mid(self) -> float | None:
        if self.bid and self.ask:
            return (self.bid + self.ask) / 2
        return None


@dataclass(frozen=True)
class FundingSpread:
    """跨所資金費率套利（delta-neutral）：在費率高的交易所做空、費率低的交易所做多。

    做空方「收」正的資金費，做多方「付」正的資金費，所以淨收入/小時 = 空方費率 - 多方費率。
    價格漲跌兩邊互相抵銷（理想情況），賺的是費率差。
    """

    short: Leg
    long: Leg
    gross_hourly: float
    gross_apr: float
    fee_cost: float
    spread_cost: float
    total_cost: float
    break_even_h: float
    net_apr: float
    basis: float | None  # (空方 mid - 多方 mid) / 多方 mid：開倉時兩邊的價格差（另計，不算進成本）


def funding_spread(a: Leg, b: Leg, holding_days: float) -> FundingSpread:
    """給兩個交易所的同一個幣，自動判斷哪邊做空、哪邊做多，算出完整的成本與收益。"""
    short, long_ = (a, b) if a.hourly >= b.hourly else (b, a)
    gross_h = short.hourly - long_.hourly
    gross_apr = gross_h * HOURS_PER_YEAR
    fee_c = round_trip_fee(long_.taker, short.taker)
    spr_c = spread_cost(long_.bid, long_.ask) + spread_cost(short.bid, short.ask)
    total = fee_c + spr_c
    basis = None
    if short.mid and long_.mid:
        basis = (short.mid - long_.mid) / long_.mid
    return FundingSpread(
        short=short,
        long=long_,
        gross_hourly=gross_h,
        gross_apr=gross_apr,
        fee_cost=fee_c,
        spread_cost=spr_c,
        total_cost=total,
        break_even_h=break_even_hours(gross_h, total),
        net_apr=net_apr(gross_apr, total, holding_days),
        basis=basis,
    )


def instant_arb_edge(sell_bid: float, buy_ask: float, taker_sell: float, taker_buy: float) -> float:
    """即時搬磚：在 A 用 bid 賣、在 B 用 ask 買，扣兩邊吃單手續費後的淨利率。

    > 0 才代表「理論上」有賺（還沒算轉帳、滑價、延遲）。
    """
    if sell_bid <= 0 or buy_ask <= 0:
        raise ValueError("prices must be positive")
    return sell_bid / buy_ask - 1.0 - taker_sell - taker_buy
