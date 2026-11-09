"""把不同交易所的幣種名稱「對齊」成同一個 key。

例如 Gate 叫 `1000PEPE`、Hyperliquid 叫 `kPEPE`，其實是同一個幣（合約乘數不同而已）。
資金費率是「百分比」，不受乘數影響，所以去掉乘數後就能跨所比較。

Adapted from hamood1337/CryptoFundingArb (src/fundingarb/symbols.py), MIT License,
Copyright (c) 2026 Ahmad Maharmeh. See THIRD_PARTY_NOTICES.md.
修改：加入 ccxt symbol 解析（parse_symbol）與 USDT/USDC 視為同一報價幣的判斷。
"""

from __future__ import annotations

import re

# 常見的合約乘數前綴：1000PEPE、1MBONK、10KSATS...
_MULTIPLIER_PREFIX = re.compile(r"^(1000000|1000K|1000|100|10K|1M|1K)(?=[A-Z0-9])")

# 我們視為「美元」的報價幣（USDT 與 USDC 價格非常接近 1:1，這是一個簡化假設，README 有寫）
USD_QUOTES = {"USDT", "USDC"}


def normalize_base(base: str) -> str:
    """回傳去掉合約乘數後的幣種代號。

    >>> normalize_base("1000PEPE")
    'PEPE'
    >>> normalize_base("kPEPE")
    'PEPE'
    """
    if not base:
        return base
    # Hyperliquid 用小寫 k 當「千」的前綴，例如 kPEPE、kSHIB
    if base[0] == "k" and len(base) > 1 and base[1].isupper():
        return base[1:].upper()
    upper = base.upper()
    stripped = _MULTIPLIER_PREFIX.sub("", upper)
    return stripped or upper


def parse_symbol(symbol: str) -> tuple[str, str, str | None]:
    """把 ccxt 統一格式 'BTC/USDT:USDT' 拆成 (base, quote, settle)。

    >>> parse_symbol("BTC/USDT:USDT")
    ('BTC', 'USDT', 'USDT')
    """
    pair, _, settle = symbol.partition(":")
    base, _, quote = pair.partition("/")
    return base, quote, (settle or None)


def is_usd_linear_perp(symbol: str) -> bool:
    """是否為以 USDT/USDC 結算的線性永續合約（排除幣本位反向合約）。"""
    base, quote, settle = parse_symbol(symbol)
    return bool(base) and quote in USD_QUOTES and settle in USD_QUOTES
