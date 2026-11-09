"""連網整合測試（預設跳過）。要跑的話：FRM_LIVE=1 python -m pytest tests/test_live.py"""

import os

import pytest

pytestmark = pytest.mark.skipif(not os.getenv("FRM_LIVE"), reason="set FRM_LIVE=1 to hit real exchange APIs")


def test_okx_snapshot_live():
    from frm.config import load_config
    from frm.exchanges import fetch_perp_snapshots

    snaps = fetch_perp_snapshots("okx", load_config())
    btc = [s for s in snaps if s.symbol == "BTC/USDT:USDT"]
    assert btc and btc[0].bid and btc[0].ask and btc[0].bid <= btc[0].ask
