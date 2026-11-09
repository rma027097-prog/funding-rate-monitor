"""把資料庫裡的「實測」數字整理成 docs/live_results.md（README 的結果段落引用這份）。

python scripts/summarize_results.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from frm.config import load_config  # noqa: E402
from frm.storage import read_df  # noqa: E402

cfg = load_config()
DB = cfg.database


def tpe(ms) -> str:
    return pd.to_datetime(ms, unit="ms", utc=True).tz_convert("Asia/Taipei").strftime("%Y-%m-%d %H:%M")


runs = read_df(DB, "SELECT * FROM runs ORDER BY run_id")
opps = read_df(DB, "SELECT * FROM funding_opps")
arbs = read_df(DB, "SELECT * FROM price_arbs")
prem = read_df(DB, "SELECT * FROM tw_premium")
L = ["# 實測結果 Live Results（自動產生）", "",
     f"- 收集輪數：**{len(runs)}**，期間 {tpe(runs.ts_ms.min())} ~ {tpe(runs.ts_ms.max())}（台北時間）",
     f"- 每輪平均耗時：{runs.duration_s.mean():.1f} 秒；每輪平均比較 {runs.n_snapshots.mean():.0f} 個市場快照",
     f"- 有錯誤的輪數：{int((runs.errors.fillna('{}') != '{}').sum())}", ""]

clean = opps[opps.suspect == 0]
L += ["## A. 每輪「扣成本後年化」最高的配對（排除可疑配對）", "",
      "| 時間（台北） | 幣 | 做空 | 做多 | 毛年化 | 來回成本 | 回本 | 淨年化 |", "|---|---|---|---|---|---|---|---|"]
for rid, g in clean.groupby("run_id"):
    r = g.sort_values("net_apr").iloc[-1]
    L.append(f"| {tpe(r.ts_ms)} | {r.base} | {r.short_ex} | {r.long_ex} | {r.gross_apr*100:.1f}% | "
             f"{r.total_cost*100:.3f}% | {r.break_even_h:.1f} h | {r.net_apr*100:.1f}% |")

n_runs = clean.run_id.nunique()
top = clean.assign(rank=clean.groupby("run_id").net_apr.rank(ascending=False, method="first"))
top = top[top["rank"] <= 10]
g = top.groupby(["base", "short_ex", "long_ex"]).agg(
    runs=("run_id", "nunique"), mean_net=("net_apr", "mean"), min_net=("net_apr", "min"),
    first=("ts_ms", "min"), last=("ts_ms", "max")).reset_index().sort_values(["runs", "mean_net"], ascending=False)
L += ["", f"## B. 持續性：出現在前 10 名的次數（共 {n_runs} 輪）", "",
      "| 幣 | 做空 | 做多 | 出現輪數 | 平均淨年化 | 最低淨年化 | 首次 ~ 最後（台北） |", "|---|---|---|---|---|---|---|"]
for _, r in g.head(10).iterrows():
    L.append(f"| {r.base} | {r.short_ex} | {r.long_ex} | {r.runs}/{n_runs} | {r.mean_net*100:.1f}% | {r.min_net*100:.1f}% | "
             f"{tpe(r['first'])} ~ {tpe(r['last'])[11:]} |")

majors = clean[clean.base.isin(["BTC", "ETH", "SOL", "XRP", "DOGE"])]
if not majors.empty:
    m = majors.groupby("base").agg(best_gross=("gross_apr", "max"), best_net=("net_apr", "max"),
                                   med_be=("break_even_h", "median")).reset_index()
    L += ["", "## C. 主流幣對照（全部輪次中最好的一組）", "", "| 幣 | 最高毛年化 | 最高淨年化 | 回本小時中位數 |", "|---|---|---|---|"]
    for _, r in m.iterrows():
        L.append(f"| {r.base} | {r.best_gross*100:.2f}% | {r.best_net*100:.2f}% | {r.med_be:.0f} h |")

pa = arbs[(arbs.market_type == "perp") & (arbs.suspect == 0)]
tw = arbs[arbs.market_type == "tw_spot"]
L += ["", "## D. 即時搬磚與台灣市場", ""]
if not pa.empty:
    b = pa.sort_values("net_edge").iloc[-1]
    L.append(f"- 永續合約跨所即時搬磚，扣吃單費後的最佳一筆：{b.base} 賣 {b.sell_ex} / 買 {b.buy_ex}，"
             f"淨 {b.net_edge*100:.3f}%（{tpe(b.ts_ms)}）；所有輪次中淨利率 > 0 的比例 {(pa.net_edge > 0).mean()*100:.1f}%。")
if not tw.empty:
    b = tw.sort_values("net_edge").iloc[-1]
    L.append(f"- MAX ↔ BitoPro 台幣現貨：最佳一筆 {b.base} 賣 {b.sell_ex} / 買 {b.buy_ex} 毛 {b.gross_edge*100:.3f}%、"
             f"扣手續費淨 {b.net_edge*100:.3f}%（{tpe(b.ts_ms)}）；淨利率 > 0 的次數：{int((tw.net_edge > 0).sum())}/{len(tw)}。")
if not prem.empty:
    for base, gg in prem.groupby("base"):
        L.append(f"- 台灣溢價 {base}：平均 {gg.premium.mean()*100:+.3f}%（範圍 {gg.premium.min()*100:+.3f}% ~ {gg.premium.max()*100:+.3f}%）")

out = Path(__file__).resolve().parent.parent / "docs" / "live_results.md"
out.write_text("\n".join(L) + "\n", encoding="utf-8")
print("\n".join(L))
