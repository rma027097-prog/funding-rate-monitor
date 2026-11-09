from frm.config import load_config


def test_load_default_config():
    cfg = load_config()
    assert "okx" in cfg.perp_exchanges
    assert cfg.fee("okx").taker == 0.0005
    assert cfg.fee("unknown-exchange").taker > 0     # 未知交易所用保守預設
    assert cfg.default_interval("hyperliquid") == 1
    assert cfg.database.name.endswith(".db")
