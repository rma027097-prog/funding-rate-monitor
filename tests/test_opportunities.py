"""用假的快照資料測試機會計算，不需要連網。"""

from pathlib import Path

import pytest

from frm.config import Config, Fee
from frm.exchanges import PerpSnapshot, SpotQuote
from frm.opportunities import (
    funding_opportunities, perp_price_arbs, select_assets, taiwan_premium, tw_spot_arbs,
)


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return Config(database=tmp_path / "t.db", min_quote_volume_usd=1_000_000, max_assets=10,
                  fees={"okx": Fee(0.0002, 0.0005), "hyperliquid": Fee(0.00015, 0.00045),
                        "max": Fee(0.0008, 0.0016), "bitopro": Fee(0.001, 0.002)})


def snap(ex, base, rate, interval, bid, ask, vol=5e6, symbol=None):
    return PerpSnapshot(ex, symbol or f"{base}/USDT:USDT", base, rate, interval, None, bid, ask, bid, vol)


def test_select_assets_filters_volume_and_single_venue(cfg):
    snaps = [snap("okx", "BTC", 1e-4, 8, 100, 100.1), snap("hyperliquid", "BTC", 1e-5, 1, 100, 100.1),
             snap("okx", "ONLYOKX", 1e-4, 8, 1, 1.01),
             snap("okx", "ILLIQ", 1e-4, 8, 1, 1.01, vol=10), snap("hyperliquid", "ILLIQ", 1e-4, 1, 1, 1.01, vol=10)]
    assets = select_assets(snaps, cfg)
    assert list(assets) == ["BTC"]


def test_funding_opportunities_and_suspect_flag(cfg):
    snaps = [snap("okx", "AAA", 3e-4, 8, 100, 100.05), snap("hyperliquid", "AAA", 0, 1, 100, 100.05),
             snap("okx", "BBB", 9e-4, 8, 100, 100.05), snap("hyperliquid", "BBB", 0, 1, 150, 150.1)]  # BBB 價格差 50%
    res = funding_opportunities(select_assets(snaps, cfg), cfg)
    assert res[0][0] == "AAA" and res[0][2] is False       # 正常的排前面
    assert res[-1][0] == "BBB" and res[-1][2] is True      # 可疑的排後面
    assert res[0][1].short.exchange == "okx"


def test_perp_price_arbs_both_directions(cfg):
    snaps = [snap("okx", "AAA", 0, 8, 101, 101.1), snap("hyperliquid", "AAA", 0, 1, 100, 100.1)]
    arbs = perp_price_arbs(select_assets(snaps, cfg), cfg)
    assert len(arbs) == 2
    best = arbs[0]
    assert best["sell_ex"] == "okx" and best["buy_ex"] == "hyperliquid"
    assert best["net_edge"] == pytest.approx(101 / 100.1 - 1 - 0.0005 - 0.00045)


def test_tw_spot_and_premium(cfg):
    quotes = [SpotQuote("max", "usdt", 32.0, 32.1, None), SpotQuote("bitopro", "usdt", 32.05, 32.2, None),
              SpotQuote("max", "btc", 3_200_000, 3_201_000, None)]
    arbs = tw_spot_arbs(quotes, cfg)
    assert {(a["sell_ex"], a["buy_ex"]) for a in arbs} == {("max", "bitopro"), ("bitopro", "max")}
    assert all(a["net_edge"] < 0 for a in arbs)
    prem = taiwan_premium(quotes, [snap("okx", "BTC", 0, 8, 99_990, 100_010)])
    # MAX BTC 中價 3,200,500 ÷ USDT 中價 32.05 = 99,859.6 USD；國際 100,000 → 約 -0.14%
    assert prem["BTC"] == pytest.approx(3_200_500 / 32.05 / 100_000 - 1)
