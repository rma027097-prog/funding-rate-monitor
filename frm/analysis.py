"""統計分析模組（數據科學的部分）。

核心問題：「看到的資金費率價差，會不會持續？」
如果價差一下就消失，扣完手續費根本賺不到；如果會持續（有持續性 persistence），才值得做。

我們用 30 天歷史資金費率回答這個問題：
1. 分佈（distribution）：價差平均多大？多常是正的？
2. 持續性：lag-1 自我相關（autocorrelation）、正負號維持機率
3. 均值回歸檢定：ADF 檢定（Augmented Dickey-Fuller）+ AR(1) 模型估計「半衰期」
4. 預測：AR(1) vs 天真預測（naive：下一期 = 這一期），用樣本外 MAE 比較
5. 回測（backtest，屬於「歷史模擬」不是實盤）：依上一期價差方向持倉，扣換倉手續費

用法：python -m frm.analysis report   → 產生 docs/analysis_report.md 與圖表 PNG
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

from .config import ROOT, Config, load_config
from .metrics import round_trip_fee
from .storage import read_df

WINDOW = "8h"  # 以 8 小時為一期（多數交易所的結算週期），每小時結算的 Hyperliquid 會加總成 8 小時
PERIODS_PER_YEAR = 365 * 3


# ---------------- 資料準備 ----------------

def load_history(db_path) -> pd.DataFrame:
    df = read_df(db_path, "SELECT exchange, base, ts_ms, rate FROM funding_history")
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
    return df


def window_funding(hist: pd.DataFrame, exchange: str, base: str) -> pd.Series:
    """某交易所某幣，每 8 小時「實際累積收付的資金費率」（每小時結算的會加總起來）。"""
    s = hist[(hist.exchange == exchange) & (hist.base == base)].set_index("ts")["rate"].sort_index()
    if s.empty:
        return s
    # closed/label='right'：00:00 結算的那筆歸在 (16:00, 00:00] 這一期
    return s.resample(WINDOW, closed="right", label="right").sum(min_count=1)


def pair_spread(hist: pd.DataFrame, base: str, ex_a: str, ex_b: str) -> pd.Series:
    """價差序列 = A 的 8h 費率 - B 的 8h 費率（> 0 代表空 A、多 B 收錢）。只保留兩邊都有資料的期。"""
    a = window_funding(hist, ex_a, base)
    b = window_funding(hist, ex_b, base)
    df = pd.concat([a.rename("a"), b.rename("b")], axis=1).dropna()
    return (df["a"] - df["b"]).rename(f"{base}:{ex_a}-{ex_b}")


# ---------------- 統計量 ----------------

def ar1_fit(x: np.ndarray) -> tuple[float, float]:
    """用最小平方法估計 AR(1)：x_t = c + phi * x_{t-1} + 誤差。回傳 (c, phi)。"""
    if len(x) < 3:
        return 0.0, 0.0
    X = np.column_stack([np.ones(len(x) - 1), x[:-1]])
    c, phi = np.linalg.lstsq(X, x[1:], rcond=None)[0]
    return float(c), float(phi)


def half_life(phi: float) -> float:
    """均值回歸半衰期（期數）：偏離平均後，要幾期才會回到一半。phi 不在 (0,1) 時沒有意義。"""
    if 0 < phi < 1:
        return -math.log(2) / math.log(phi)
    return math.inf


def sign_persistence(x: np.ndarray) -> float:
    """這一期跟下一期「正負號相同」的機率。0.5 ≈ 擲銅板，越接近 1 越持續。"""
    s = np.sign(x)
    if len(s) < 2:
        return float("nan")
    return float(np.mean(s[1:] == s[:-1]))


def adf_pvalue(x: np.ndarray) -> float:
    """ADF 檢定 p 值：< 0.05 通常解讀為「序列是平穩的、會均值回歸」。"""
    try:
        from statsmodels.tsa.stattools import adfuller

        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return float(adfuller(x, autolag="AIC")[1])
    except Exception:  # noqa: BLE001 — 樣本太少或常數序列
        return float("nan")


def forecast_eval(x: np.ndarray, train_frac: float = 0.7) -> dict:
    """樣本外預測比較：AR(1) vs naive（下一期=這一期）vs 平均值。MAE 越小越好。"""
    n = len(x)
    k = int(n * train_frac)
    if k < 10 or n - k < 5:
        return {}
    c, phi = ar1_fit(x[:k])
    mean = x[:k].mean()
    prev, actual = x[k - 1 : n - 1], x[k:]
    return dict(
        mae_ar1=float(np.mean(np.abs(c + phi * prev - actual))),
        mae_naive=float(np.mean(np.abs(prev - actual))),
        mae_mean=float(np.mean(np.abs(mean - actual))),
    )


def backtest_carry(x: np.ndarray, round_trip_cost: float, lookback: int = 3, horizon: int = 21) -> dict:
    """簡單的資金費率套利策略回測（歷史模擬，非實盤）。

    規則（只用「當下以前」的資料，避免偷看未來 look-ahead bias）：
    - 訊號 = 過去 lookback 期價差的平均
    - 空手時：若 |訊號| × horizon（預期持有期數，21 期 = 7 天）> 來回成本 → 依訊號方向開倉
    - 持倉時：訊號正負號跟持倉方向相反 → 平倉
    - 每次開倉、平倉各扣「半個來回成本」
    回傳總報酬、年化、交易次數、權益曲線。
    """
    half = round_trip_cost / 2
    pos = 0
    pnl, trades = [], 0
    for t in range(lookback, len(x)):
        sig = float(np.mean(x[t - lookback : t]))
        cost = 0.0
        if pos == 0 and abs(sig) * horizon > round_trip_cost:
            pos = 1 if sig > 0 else -1
            cost += half
            trades += 1
        elif pos != 0 and np.sign(sig) == -pos:
            pos = 0
            cost += half
        pnl.append(pos * x[t] - cost)
    if pos != 0:  # 期末強制平倉
        pnl[-1] -= half
    pnl = np.array(pnl)
    total = float(pnl.sum()) if len(pnl) else 0.0
    days = max(len(pnl) / 3, 1e-9)
    return dict(total_return=total, apr=total / days * 365, trades=trades, periods=len(pnl), equity=np.cumsum(pnl))


@dataclass
class PairStats:
    pair: str
    base: str
    ex_a: str
    ex_b: str
    n: int
    mean_8h: float
    std_8h: float
    mean_apr: float
    pct_positive: float
    autocorr1: float
    sign_persist: float
    adf_p: float
    phi: float
    half_life_periods: float
    mae_ar1: float
    mae_naive: float
    mae_mean: float
    bt_apr: float
    bt_trades: int


def pair_stats(hist: pd.DataFrame, cfg: Config) -> tuple[list[PairStats], dict[str, pd.Series]]:
    """對每個幣、每一對交易所計算全部統計量。"""
    out, series = [], {}
    for base in sorted(hist.base.unique()):
        exs = sorted(hist[hist.base == base].exchange.unique())
        for a, b in combinations(exs, 2):
            s = pair_spread(hist, base, a, b)
            if len(s) < 30:
                continue
            x = s.to_numpy()
            c, phi = ar1_fit(x)
            fe = forecast_eval(x)
            cost = round_trip_fee(cfg.fee(a).taker, cfg.fee(b).taker)
            bt = backtest_carry(x, cost)
            series[s.name] = s
            out.append(PairStats(
                pair=s.name, base=base, ex_a=a, ex_b=b, n=len(x),
                mean_8h=float(x.mean()), std_8h=float(x.std(ddof=1)),
                mean_apr=float(x.mean() * PERIODS_PER_YEAR),
                pct_positive=float((x > 0).mean()),
                autocorr1=float(pd.Series(x).autocorr(1)),
                sign_persist=sign_persistence(x), adf_p=adf_pvalue(x),
                phi=phi, half_life_periods=half_life(phi),
                mae_ar1=fe.get("mae_ar1", np.nan), mae_naive=fe.get("mae_naive", np.nan),
                mae_mean=fe.get("mae_mean", np.nan), bt_apr=bt["apr"], bt_trades=bt["trades"],
            ))
    return out, series


# ---------------- 即時快照（collector 記錄）分析 ----------------

def live_persistence(db_path, top_n: int = 10) -> pd.DataFrame:
    """在 collector 跑過的每一輪中，哪些組合重複出現在「前 N 名」？出現越多次 = 越持續。"""
    df = read_df(db_path, "SELECT run_id, ts_ms, base, short_ex, long_ex, net_apr, gross_apr, break_even_h "
                          "FROM funding_opps WHERE suspect=0")
    if df.empty:
        return df
    df["rank"] = df.groupby("run_id")["net_apr"].rank(ascending=False, method="first")
    top = df[df["rank"] <= top_n]
    n_runs = df.run_id.nunique()
    g = top.groupby(["base", "short_ex", "long_ex"]).agg(
        appearances=("run_id", "nunique"), mean_net_apr=("net_apr", "mean"),
        min_net_apr=("net_apr", "min"), max_net_apr=("net_apr", "max"),
        median_break_even_h=("break_even_h", "median"),
        first_ms=("ts_ms", "min"), last_ms=("ts_ms", "max"),
    ).reset_index()
    g["share_of_runs"] = g["appearances"] / n_runs
    return g.sort_values(["appearances", "mean_net_apr"], ascending=False)


# ---------------- 報告 ----------------

def _pct(x, d=2):
    return "n/a" if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))) else f"{x*100:.{d}f}%"


def make_report(cfg: Config, out_dir: Path | None = None) -> Path:
    """產生 docs/analysis_report.md 與圖表。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir = out_dir or ROOT / "docs"
    out_dir.mkdir(parents=True, exist_ok=True)
    hist = load_history(cfg.database)
    lines = ["# 統計分析報告 Analysis Report", "",
             "> 本報告由 `python -m frm.analysis report` 自動產生。歷史資料 = 交易所公開 API 的實際歷史資金費率；",
             "> 回測（backtest）= 用歷史資料模擬，**不是實盤績效**，未計滑價、資金占用、轉帳與爆倉風險。", ""]

    if not hist.empty:
        stats, series = pair_stats(hist, cfg)
        sdf = pd.DataFrame([s.__dict__ for s in stats]).sort_values("mean_apr", key=abs, ascending=False)
        t0 = hist.ts.min().tz_convert("Asia/Taipei").strftime("%Y-%m-%d %H:%M")
        t1 = hist.ts.max().tz_convert("Asia/Taipei").strftime("%Y-%m-%d %H:%M")
        lines += [f"## 1. 歷史資金費率價差（{t0} ~ {t1}，台北時間）", "",
                  f"幣種 {hist.base.nunique()} 個、交易所 {hist.exchange.nunique()} 家、交易所配對 {len(sdf)} 組，每期 = 8 小時。", "",
                  "| 配對 (A−B) | 期數 | 平均年化 | 正值比例 | lag-1 自相關 | 正負號持續 | ADF p | 半衰期(期) | MAE AR1 / naive | 回測年化 (交易次數) |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for _, r in sdf.head(15).iterrows():
            hl = "∞" if math.isinf(r.half_life_periods) else f"{r.half_life_periods:.1f}"
            lines.append(f"| {r.pair} | {r.n} | {_pct(r.mean_apr)} | {r.pct_positive*100:.0f}% | {r.autocorr1:.2f} | "
                         f"{r.sign_persist*100:.0f}% | {r.adf_p:.3f} | {hl} | {r.mae_ar1*1e4:.2f} / {r.mae_naive*1e4:.2f} bp | {_pct(r.bt_apr)} ({r.bt_trades}) |")
        n_stat = int((sdf.adf_p < 0.05).sum())
        ar_wins = int((sdf.mae_ar1 < sdf.mae_naive).sum())
        lines += ["", "**解讀（自動計算）**", "",
                  f"- {n_stat}/{len(sdf)} 組配對的 ADF p < 0.05 → 價差大多是「平穩、會回到平均」的序列。",
                  f"- 正負號持續機率中位數 {sdf.sign_persist.median()*100:.0f}%，lag-1 自相關中位數 {sdf.autocorr1.median():.2f}。",
                  f"- AR(1) 樣本外誤差比 naive 小的配對：{ar_wins}/{len(sdf)}。",
                  f"- 簡單 carry 策略回測：有交易的配對 {int((sdf.bt_trades > 0).sum())}/{len(sdf)}，其中年化 > 0 的 {int(((sdf.bt_apr > 0) & (sdf.bt_trades > 0)).sum())} 組（扣 taker 手續費後）。",
                  f"- 全部配對的平均價差絕對值中位數只有 {sdf.mean_apr.abs().median()*100:.2f}%/年，而一次來回手續費約 {round_trip_fee(0.0005, 0.00045)*100:.2f}%：主流幣的價差通常太小。", ""]
        sdf.to_csv(out_dir / "pair_stats.csv", index=False)

        # 圖 1：前 4 組價差時間序列
        fig, ax = plt.subplots(figsize=(10, 4))
        for name in sdf.pair.head(4):
            s = series[name]
            ax.plot(s.index.tz_convert("Asia/Taipei"), s.values * 1e4, label=name, lw=1)
        ax.axhline(0, color="k", lw=0.5)
        ax.set_ylabel("8h funding spread (bp)")
        ax.set_title("Cross-exchange funding spread, top 4 pairs by |mean| (Asia/Taipei)")
        ax.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(out_dir / "chart_spread_timeseries.png", dpi=120)
        plt.close(fig)

        # 圖 2：價差分佈直方圖（全部配對）
        allx = np.concatenate([s.values for s in series.values()]) * 1e4
        fig, ax = plt.subplots(figsize=(7, 4))
        ax.hist(np.clip(allx, -10, 10), bins=80, color="#3b82f6")
        ax.set_xlabel("8h funding spread (bp, clipped ±10)")
        ax.set_ylabel("count")
        ax.set_title(f"Distribution of funding spreads, all pairs (n={len(allx)})")
        fig.tight_layout()
        fig.savefig(out_dir / "chart_spread_hist.png", dpi=120)
        plt.close(fig)

        # 圖 3：自相關 vs 平均年化（持續性 vs 大小）
        fig, ax = plt.subplots(figsize=(7, 4))
        ax.scatter(sdf.autocorr1, sdf.mean_apr * 100, c=(sdf.bt_apr > 0), cmap="coolwarm_r", s=25)
        for _, r in sdf.head(6).iterrows():
            ax.annotate(r.pair, (r.autocorr1, r.mean_apr * 100), fontsize=6)
        ax.set_xlabel("lag-1 autocorrelation (persistence)")
        ax.set_ylabel("mean spread, annualized %")
        ax.set_title("Persistence vs size (blue = backtest APR > 0)")
        fig.tight_layout()
        fig.savefig(out_dir / "chart_persistence.png", dpi=120)
        plt.close(fig)
        lines += ["![spread timeseries](chart_spread_timeseries.png)", "",
                  "![spread histogram](chart_spread_hist.png)", "", "![persistence](chart_persistence.png)", ""]

    live = live_persistence(cfg.database)
    runs = read_df(cfg.database, "SELECT COUNT(*) n, MIN(ts_ms) a, MAX(ts_ms) b FROM runs")
    if not live.empty:
        a = pd.to_datetime(runs.a[0], unit="ms", utc=True).tz_convert("Asia/Taipei").strftime("%Y-%m-%d %H:%M")
        b = pd.to_datetime(runs.b[0], unit="ms", utc=True).tz_convert("Asia/Taipei").strftime("%H:%M")
        lines += [f"## 2. 即時收集（collector）實測：{int(runs.n[0])} 輪，{a} ~ {b} 台北時間", "",
                  "每一輪「扣成本後年化」前 10 名中，重複出現的組合（出現越多輪越持續）：", "",
                  "| 幣 | 做空 | 做多 | 出現輪數 | 平均淨年化 | 最低 / 最高 | 回本時間中位數 |", "|---|---|---|---|---|---|---|"]
        for _, r in live.head(10).iterrows():
            lines.append(f"| {r.base} | {r.short_ex} | {r.long_ex} | {r.appearances} | {_pct(r.mean_net_apr)} | "
                         f"{_pct(r.min_net_apr)} / {_pct(r.max_net_apr)} | {r.median_break_even_h:.1f} h |")
        lines.append("")

    path = out_dir / "analysis_report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["report"])
    ap.add_argument("--config", default=None)
    args = ap.parse_args(argv)
    print("report written:", make_report(load_config(args.config)))


if __name__ == "__main__":
    main()
