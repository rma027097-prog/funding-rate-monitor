"""統計函式測試：用「已知答案」的人造資料檢查估計是否正確。"""

import math

import numpy as np
import pandas as pd
import pytest

from frm.analysis import (
    adf_pvalue, ar1_fit, backtest_carry, forecast_eval, half_life, pair_spread, sign_persistence,
)


def simulate_ar1(phi, n=3000, c=0.0, seed=0):
    rng = np.random.default_rng(seed)
    x = np.zeros(n)
    for t in range(1, n):
        x[t] = c + phi * x[t - 1] + rng.normal(0, 1)
    return x


def test_ar1_recovers_phi():
    _, phi = ar1_fit(simulate_ar1(0.6))
    assert phi == pytest.approx(0.6, abs=0.05)


def test_half_life():
    assert half_life(0.5) == pytest.approx(1.0)
    assert math.isinf(half_life(1.0))
    assert math.isinf(half_life(-0.2))


def test_sign_persistence():
    assert sign_persistence(np.array([1, 1, 1, 1.0])) == 1.0
    assert sign_persistence(np.array([1, -1, 1, -1.0])) == 0.0


def test_adf_stationary_vs_random_walk():
    assert adf_pvalue(simulate_ar1(0.3, n=500)) < 0.05
    rw = np.cumsum(np.random.default_rng(1).normal(size=500))
    assert adf_pvalue(rw) > 0.05


def test_forecast_ar1_beats_naive_on_mean_reverting_data():
    fe = forecast_eval(simulate_ar1(0.2, n=1000))
    assert fe["mae_ar1"] < fe["mae_naive"]


def test_backtest_profitable_on_persistent_positive_spread():
    x = np.full(100, 0.001)  # 每期 0.1%，遠大於成本
    bt = backtest_carry(x, round_trip_cost=0.002)
    assert bt["trades"] == 1
    assert bt["total_return"] == pytest.approx(0.001 * 97 - 0.002)


def test_backtest_no_trade_when_spread_too_small():
    bt = backtest_carry(np.full(100, 1e-6), round_trip_cost=0.002)
    assert bt["trades"] == 0 and bt["total_return"] == 0


def test_pair_spread_aggregates_hourly_into_8h():
    ts8 = pd.date_range("2026-01-01 08:00", periods=3, freq="8h", tz="UTC")
    ts1 = pd.date_range("2026-01-01 01:00", periods=24, freq="1h", tz="UTC")
    hist = pd.concat([
        pd.DataFrame({"exchange": "okx", "base": "BTC", "ts": ts8, "rate": 0.0001}),
        pd.DataFrame({"exchange": "hyperliquid", "base": "BTC", "ts": ts1, "rate": 0.00001}),
    ])
    s = pair_spread(hist, "BTC", "okx", "hyperliquid")
    assert len(s) == 3
    assert s.iloc[0] == pytest.approx(0.0001 - 8 * 0.00001)
