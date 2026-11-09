"""讀取 config.yaml，轉成好用的 Python 物件。

為什麼要有設定檔？把「會變的數字」（手續費、門檻、交易所清單）和「不會變的邏輯」分開，
之後調整參數不用改程式碼，也比較容易測試。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

# 專案根目錄 = 這個檔案的上一層的上一層
ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Fee:
    """單一交易所的手續費（小數表示，0.0005 = 0.05%）。"""

    maker: float
    taker: float


@dataclass
class Config:
    database: Path
    interval_minutes: int = 5
    perp_exchanges: list[str] = field(default_factory=lambda: ["okx", "hyperliquid"])
    watchlist: list[str] = field(default_factory=list)
    min_quote_volume_usd: float = 5_000_000
    max_assets: int = 60
    holding_days: float = 7
    max_price_deviation: float = 0.02
    fees: dict[str, Fee] = field(default_factory=dict)
    default_funding_interval_hours: dict[str, float] = field(default_factory=lambda: {"default": 8})
    tw_spot_enabled: bool = True
    tw_spot_markets: list[str] = field(default_factory=lambda: ["usdt", "btc", "eth"])

    def fee(self, exchange: str) -> Fee:
        """取得某交易所手續費；沒設定就用 default（保守值）。"""
        return self.fees.get(exchange) or self.fees.get("default") or Fee(0.0002, 0.0006)

    def default_interval(self, exchange: str) -> float:
        d = self.default_funding_interval_hours
        return float(d.get(exchange, d.get("default", 8)))


def load_config(path: str | Path | None = None) -> Config:
    """讀 YAML 設定檔並回傳 Config。path 預設為專案根目錄的 config.yaml。"""
    path = Path(path) if path else ROOT / "config.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    fees = {name: Fee(float(v["maker"]), float(v["taker"])) for name, v in (raw.get("fees") or {}).items()}
    db = Path(raw.get("database", "data/monitor.db"))
    if not db.is_absolute():
        db = ROOT / db
    tw = raw.get("tw_spot") or {}

    return Config(
        database=db,
        interval_minutes=int(raw.get("interval_minutes", 5)),
        perp_exchanges=list(raw.get("perp_exchanges") or ["okx", "hyperliquid"]),
        watchlist=[s.upper() for s in (raw.get("watchlist") or [])],
        min_quote_volume_usd=float(raw.get("min_quote_volume_usd", 5_000_000)),
        max_assets=int(raw.get("max_assets", 60)),
        holding_days=float(raw.get("holding_days", 7)),
        max_price_deviation=float(raw.get("max_price_deviation", 0.02)),
        fees=fees,
        default_funding_interval_hours=dict(raw.get("default_funding_interval_hours") or {"default": 8}),
        tw_spot_enabled=bool(tw.get("enabled", True)),
        tw_spot_markets=[m.lower() for m in (tw.get("markets") or ["usdt", "btc", "eth"])],
    )
