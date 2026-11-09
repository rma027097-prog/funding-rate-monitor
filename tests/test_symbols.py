from frm.symbols import is_usd_linear_perp, normalize_base, parse_symbol


def test_normalize_base():
    assert normalize_base("1000PEPE") == "PEPE"
    assert normalize_base("kPEPE") == "PEPE"
    assert normalize_base("1MBONK") == "BONK"
    assert normalize_base("BTC") == "BTC"
    assert normalize_base("1INCH") == "1INCH"  # 不是乘數前綴，不能被誤砍


def test_parse_and_filter():
    assert parse_symbol("ETH/USDC:USDC") == ("ETH", "USDC", "USDC")
    assert is_usd_linear_perp("BTC/USDT:USDT")
    assert not is_usd_linear_perp("BTC/USD:BTC")   # 幣本位反向合約
    assert not is_usd_linear_perp("BTC/USDT")      # 現貨
