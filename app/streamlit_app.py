"""Streamlit 網頁儀表板。

本機執行：streamlit run app/streamlit_app.py
Streamlit 會把這個 Python 檔「由上往下」執行一次就畫出整個網頁；使用者點按鈕時再重跑一次。
資料全部從 SQLite（data/monitor.db）讀取，所以收集器和網頁是兩個獨立的程式。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 讓 `import frm` 找得到

from frm import analysis  # noqa: E402
from frm.config import load_config  # noqa: E402
from frm.storage import read_df  # noqa: E402

st.set_page_config(page_title="Funding-Rate Monitor 資金費率監測", page_icon="📈", layout="wide")
cfg = load_config()
DB = cfg.database
TZ = "Asia/Taipei"


def to_local(ms: pd.Series) -> pd.Series:
    return pd.to_datetime(ms, unit="ms", utc=True).dt.tz_convert(TZ)


@st.cache_data(ttl=60)
def q(sql: str, params: tuple = ()) -> pd.DataFrame:
    return read_df(DB, sql, params)


@st.cache_data(ttl=600)
def hist_stats():
    hist = analysis.load_history(DB)
    if hist.empty:
        return pd.DataFrame(), {}, hist
    stats, series = analysis.pair_stats(hist, cfg)
    return pd.DataFrame([s.__dict__ for s in stats]), series, hist


# ---------------- 側邊欄 ----------------
runs = q("SELECT run_id, ts_ms, duration_s, n_snapshots, errors FROM runs ORDER BY run_id DESC")
st.sidebar.title("📈 Funding-Rate Monitor")
st.sidebar.caption("唯讀監測系統：不下單、不需 API Key。所有數字僅供研究，非投資建議。")
if runs.empty:
    st.warning("資料庫還沒有資料。請先在終端機執行：`python -m frm.collector once`")
    st.stop()

latest = runs.iloc[0]
st.sidebar.metric("最新一輪資料（台北時間）", to_local(pd.Series([latest.ts_ms]))[0].strftime("%m-%d %H:%M:%S"))
st.sidebar.metric("累計收集輪數", len(runs))
st.sidebar.write("交易所：", ", ".join(cfg.perp_exchanges), "＋ MAX、BitoPro（台幣現貨）")
st.sidebar.write(f"持有天數假設：{cfg.holding_days:g} 天｜成交額門檻：${cfg.min_quote_volume_usd/1e6:.0f}M")
hide_suspect = st.sidebar.checkbox("隱藏可疑配對（兩所價格差 > 門檻）", value=True)

st.title("加密貨幣資金費率 & 跨交易所價差監測")
st.caption("Crypto funding-rate & cross-exchange spread monitor — data collected live from public exchange APIs.")

tab_live, tab_hist, tab_stats, tab_tw, tab_method = st.tabs(
    ["🔴 即時機會", "📉 歷史走勢", "🧪 統計分析", "🇹🇼 台灣市場", "📘 方法說明"]
)

# ---------------- 1. 即時機會 ----------------
with tab_live:
    opps = q("SELECT * FROM funding_opps WHERE run_id=?", (int(latest.run_id),))
    if hide_suspect:
        opps = opps[opps.suspect == 0]
    opps = opps.sort_values("net_apr", ascending=False)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("比較的幣種數", opps.base.nunique())
    c2.metric("交易所配對數", len(opps))
    if not opps.empty:
        top = opps.iloc[0]
        c3.metric("最高扣成本後年化", f"{top.net_apr*100:.1f}%", f"{top.base} 空 {top.short_ex} / 多 {top.long_ex}")
        c4.metric("扣成本後仍 > 0 的配對", int((opps.net_apr > 0).sum()))

    st.subheader("跨所資金費率價差（依扣成本後年化排序）")
    st.caption("推估值：用「這一刻」的費率假設會維持整個持有期，實際費率每期都會變。回本時間 = 來回成本 ÷ 每小時費率差。")
    show = opps.assign(
        做空=opps.short_ex, 做多=opps.long_ex,
        空方費率=(opps.short_rate * 100).round(4).astype(str) + "% / " + opps.short_interval_h.astype(int).astype(str) + "h",
        多方費率=(opps.long_rate * 100).round(4).astype(str) + "% / " + opps.long_interval_h.astype(int).astype(str) + "h",
        毛年化=(opps.gross_apr * 100).round(2), 來回成本=(opps.total_cost * 100).round(3),
        回本小時=opps.break_even_h.round(1), 淨年化=(opps.net_apr * 100).round(2),
        價格基差=(opps.basis * 100).round(3),
    )[["base", "做空", "做多", "空方費率", "多方費率", "毛年化", "來回成本", "回本小時", "淨年化", "價格基差"]]
    st.dataframe(show.head(50), width="stretch", hide_index=True,
                 column_config={k: st.column_config.NumberColumn(format="%.2f%%") for k in ["毛年化", "淨年化"]})

    st.subheader("永續合約即時搬磚（A 賣 bid、B 買 ask，扣兩邊吃單費）")
    arbs = q("SELECT * FROM price_arbs WHERE run_id=? AND market_type='perp'", (int(latest.run_id),))
    if hide_suspect:
        arbs = arbs[arbs.suspect == 0]
    arbs = arbs.sort_values("net_edge", ascending=False).head(10)
    st.dataframe(arbs.assign(毛利率=(arbs.gross_edge * 100).round(4), 淨利率=(arbs.net_edge * 100).round(4))
                 [["base", "sell_ex", "buy_ex", "sell_bid", "buy_ask", "毛利率", "淨利率"]],
                 width="stretch", hide_index=True)

# ---------------- 2. 歷史走勢 ----------------
with tab_hist:
    st.subheader("即時收集紀錄：同一組配對的淨年化如何隨時間變化")
    allopps = q("SELECT ts_ms, base, short_ex, long_ex, net_apr, gross_apr FROM funding_opps WHERE suspect=0")
    if not allopps.empty:
        allopps["pair"] = allopps.base + " 空" + allopps.short_ex + "/多" + allopps.long_ex
        latest_top = opps.head(8).apply(lambda r: f"{r.base} 空{r.short_ex}/多{r.long_ex}", axis=1).tolist()
        pick = st.multiselect("選擇配對", sorted(allopps.pair.unique()), default=latest_top[:5])
        d = allopps[allopps.pair.isin(pick)].assign(time=lambda x: to_local(x.ts_ms), net_apr_pct=lambda x: x.net_apr * 100)
        st.plotly_chart(px.line(d, x="time", y="net_apr_pct", color="pair", markers=True,
                                labels={"net_apr_pct": "淨年化 %", "time": "台北時間"}), width="stretch")

    st.subheader("30 天歷史資金費率（交易所公開歷史資料）")
    sdf, series, hist = hist_stats()
    if not hist.empty:
        base = st.selectbox("幣種", sorted(hist.base.unique()))
        d = hist[hist.base == base].copy()
        d["8h_rate"] = d.apply(lambda r: r.rate, axis=1)
        w = pd.concat({ex: analysis.window_funding(hist, ex, base) for ex in sorted(d.exchange.unique())}, axis=1)
        w = (w * 1e4).reset_index().melt(id_vars="ts", var_name="exchange", value_name="bp_per_8h")
        w["ts"] = w.ts.dt.tz_convert(TZ)
        st.plotly_chart(px.line(w, x="ts", y="bp_per_8h", color="exchange",
                                labels={"bp_per_8h": "每 8 小時資金費率 (bp, 1bp=0.01%)", "ts": "台北時間"}),
                        width="stretch")

# ---------------- 3. 統計分析 ----------------
with tab_stats:
    sdf, series, hist = hist_stats()
    if sdf.empty:
        st.info("先執行 `python -m frm.collector backfill --days 30` 取得歷史資料。")
    else:
        st.markdown("**問題：資金費率價差會不會持續？值不值得付手續費去做？**")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("配對數", len(sdf))
        c2.metric("ADF p<0.05（平穩/均值回歸）", f"{(sdf.adf_p < 0.05).mean()*100:.0f}%")
        c3.metric("正負號持續機率中位數", f"{sdf.sign_persist.median()*100:.0f}%")
        c4.metric("回測年化 > 0 的配對", f"{int(((sdf.bt_apr > 0) & (sdf.bt_trades > 0)).sum())} / {len(sdf)}")
        tbl = sdf.assign(平均年化=(sdf.mean_apr * 100).round(2), 正值比例=(sdf.pct_positive * 100).round(0),
                         自相關=sdf.autocorr1.round(2), 正負號持續=(sdf.sign_persist * 100).round(0),
                         ADF_p=sdf.adf_p.round(3), 半衰期_期=sdf.half_life_periods.round(1),
                         MAE_AR1_bp=(sdf.mae_ar1 * 1e4).round(2), MAE_naive_bp=(sdf.mae_naive * 1e4).round(2),
                         回測年化=(sdf.bt_apr * 100).round(2), 交易次數=sdf.bt_trades)
        st.dataframe(tbl[["pair", "n", "平均年化", "正值比例", "自相關", "正負號持續", "ADF_p", "半衰期_期",
                          "MAE_AR1_bp", "MAE_naive_bp", "回測年化", "交易次數"]]
                     .sort_values("平均年化", key=abs, ascending=False), width="stretch", hide_index=True)
        allx = pd.concat(series.values()) * 1e4
        st.plotly_chart(px.histogram(allx.clip(-10, 10), nbins=80, title="所有配對 8h 價差分佈 (bp, 截斷 ±10)"),
                        width="stretch")
        st.plotly_chart(px.scatter(sdf.assign(mean_apr_pct=sdf.mean_apr * 100), x="autocorr1", y="mean_apr_pct",
                                   color="base", hover_name="pair",
                                   title="持續性（lag-1 自相關） vs 價差大小（年化 %）"), width="stretch")

# ---------------- 4. 台灣市場 ----------------
with tab_tw:
    st.subheader("MAX vs BitoPro 台幣現貨價差")
    tw = q("SELECT ts_ms, base, sell_ex, buy_ex, gross_edge, net_edge FROM price_arbs WHERE market_type='tw_spot'")
    if not tw.empty:
        tw = tw.assign(time=to_local(tw.ts_ms), route=tw.base + " 賣" + tw.sell_ex + "/買" + tw.buy_ex,
                       net_pct=tw.net_edge * 100, gross_pct=tw.gross_edge * 100)
        st.plotly_chart(px.line(tw, x="time", y="net_pct", color="route", markers=True,
                                labels={"net_pct": "扣手續費淨利率 %", "time": "台北時間"}), width="stretch")
        st.caption("淨利率 < 0 代表搬磚會虧錢：價差通常小於兩邊吃單手續費（約 0.36%）。")
    prem = q("SELECT ts_ms, base, premium FROM tw_premium")
    if not prem.empty:
        st.subheader("台灣溢價（MAX 台幣價換算美元 vs OKX 國際價）")
        prem = prem.assign(time=to_local(prem.ts_ms), premium_pct=prem.premium * 100)
        st.plotly_chart(px.line(prem, x="time", y="premium_pct", color="base", markers=True,
                                labels={"premium_pct": "溢價 %", "time": "台北時間"}), width="stretch")

# ---------------- 5. 方法說明 ----------------
with tab_method:
    st.markdown(r"""
### 計算公式
- **每小時費率** = 本期資金費率 ÷ 結算週期（小時）。OKX/Gate/Bitget 多為 8h，Hyperliquid 為 1h。
- **毛年化** = (空方每小時費率 − 多方每小時費率) × 8760
- **來回成本** = 2 × (多方吃單費 + 空方吃單費) ＋ 兩邊買賣價差 (ask−bid)/mid
- **回本小時** = 來回成本 ÷ 每小時費率差
- **淨年化** = 毛年化 − 來回成本 × 365 ÷ 持有天數
- **即時搬磚淨利率** = A 的 bid ÷ B 的 ask − 1 − A 吃單費 − B 吃單費

### 重要限制
- 即時數字都是**推估**：假設當下費率會維持，但費率每期都會變。
- 未計入：滑價、資金在兩所之間轉帳的時間與費用、保證金占用、爆倉風險、交易所倒閉風險。
- 「可疑」配對：兩所中價相差 > 門檻，多半是不同標的（例如盤前合約、股票代幣定義不同）。
- 回測使用歷史資料模擬，不代表未來績效。本系統不下單。
""")
