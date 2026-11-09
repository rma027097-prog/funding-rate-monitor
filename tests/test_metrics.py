"""metrics.py 的單元測試：用手算得出的答案驗證公式。"""

import math

import pytest

from frm.metrics import (
    Leg, annualize, break_even_hours, funding_spread, hourly_rate, instant_arb_edge,
    net_apr, parse_interval_hours, round_trip_fee, spread_cost,
)


@pytest.mark.parametrize("raw,expected", [("8h", 8), ("1h", 1), ("1d", 24), (4, 4), (None, 8), ("garbage", 8), (0, 8)])
def test_parse_interval(raw, expected):
    assert parse_interval_hours(raw, default=8) == expected


def test_hourly_and_annualize():
    # 0.01% 每 8 小時 → 每年 1095 期 → 10.95%
    assert hourly_rate(0.0001, 8) == pytest.approx(0.0000125)
    assert annualize(0.0001, 8) == pytest.approx(0.1095)
    # Hyperliquid 每小時 0.00125% 應該跟上面完全一樣
    assert annualize(0.0000125, 1) == pytest.approx(0.1095)
    with pytest.raises(ValueError):
        hourly_rate(0.0001, 0)


def test_round_trip_fee_four_fills():
    # OKX taker 0.05% + HL taker 0.045%，開+平 → 2 × 0.095% = 0.19%
    assert round_trip_fee(0.0005, 0.00045) == pytest.approx(0.0019)


def test_spread_cost():
    assert spread_cost(99, 101) == pytest.approx(0.02)
    assert spread_cost(None, 101) == 0.0


def test_break_even_and_net_apr():
    assert break_even_hours(0.0001, 0.002) == pytest.approx(20)
    assert math.isinf(break_even_hours(0, 0.002))
    # 毛年化 20%，成本 0.2%，持有 7 天 → 0.2% × 365/7 ≈ 10.43%
    assert net_apr(0.20, 0.002, 7) == pytest.approx(0.20 - 0.002 * 365 / 7)


def test_funding_spread_picks_correct_direction():
    high = Leg("okx", 0.0003, 8, 100, 100.01, 0.0005)       # 費率高 → 應該做空
    low = Leg("hyperliquid", -0.00001, 1, 100, 100.01, 0.00045)
    fs = funding_spread(low, high, holding_days=7)          # 故意把順序顛倒傳入
    assert fs.short.exchange == "okx" and fs.long.exchange == "hyperliquid"
    assert fs.gross_hourly == pytest.approx(0.0003 / 8 + 0.00001)
    assert fs.fee_cost == pytest.approx(0.0019)
    assert fs.total_cost > fs.fee_cost                       # 有加上買賣價差成本
    assert fs.break_even_h == pytest.approx(fs.total_cost / fs.gross_hourly)


def test_instant_arb_edge():
    # 在 A 以 101 賣、B 以 100 買：毛利 1%，扣 0.05%+0.05%
    assert instant_arb_edge(101, 100, 0.0005, 0.0005) == pytest.approx(0.009)
    with pytest.raises(ValueError):
        instant_arb_edge(0, 100, 0, 0)
