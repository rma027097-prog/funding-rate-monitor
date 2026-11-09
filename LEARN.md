# LEARN.md — 從零看懂這個專案

> 閱讀順序：先懂概念 → 再一個檔案一個檔案看。
> 建議搭配 VS Code 打開專案，邊讀邊對照程式碼。

---

## 0. 先跑起來（5 分鐘）

```bash
python -m venv .venv && source .venv/bin/activate     # 建立獨立的 Python 環境
pip install -r requirements-dev.txt                    # 安裝套件
python -m frm.collector once                           # 抓一次資料
python -m frm.collector backfill --days 30             # 抓 30 天歷史資金費率
python -m frm.analysis report                          # 產生統計報告 docs/analysis_report.md
streamlit run app/streamlit_app.py                     # 打開網頁儀表板
python -m pytest                                       # 跑測試
```

---

## 1. 關鍵概念

### 1.1 API 是什麼？
API（Application Programming Interface）= 程式跟程式溝通的「窗口」。
交易所提供「公開行情 API」，你送一個網址請求（HTTP GET），它回傳 JSON 格式的資料，例如：

```json
{"market":"btctwd","buy":"2449477.3","sell":"2449687.7"}
```

- `buy` = 最高買價（bid）：你現在「賣」只能賣到這個價。
- `sell` = 最低賣價（ask）：你現在「買」要付這個價。
- 本專案只用公開 API，所以**不需要帳號、不需要 API Key、也不可能下單**。

**ccxt** 是一個開源函式庫，把 100 多家交易所不同格式的 API 包成同一套寫法：
`exchange.fetch_funding_rates()` 在 OKX 和 Gate 都能用，省下大量重工。

### 1.2 永續合約與資金費率（Funding Rate）
- 永續合約（perpetual futures）：沒有到期日的期貨，可以做多也可以做空。
- 問題：沒有到期日，合約價格怎麼跟現貨價格綁在一起？→ 靠**資金費率**。
- 合約價 > 現貨價（大家都在做多）→ 資金費率為正 → **多方付錢給空方**，鼓勵人做空把價格壓回來。
- 合約價 < 現貨價 → 費率為負 → 空方付錢給多方。
- 結算頻率：OKX/Gate/Bitget 多為每 8 小時（部分幣 4h、1h），Hyperliquid 每 1 小時。

### 1.3 跨所資金費率套利（為什麼是 delta-neutral）
同一個幣，A 交易所費率 +0.03%/8h，B 交易所 −0.01%/8h：
- 在 A **做空**（收 0.03%）、在 B **做多**（費率為負，多方也收 0.01%）
- 兩個部位一多一空，幣價漲跌互相抵銷 → 叫做 **delta-neutral（方向中性）**
- 賺的是「費率差」，不是賭漲跌。

### 1.4 手續費數學（本專案最重要的一段）
```
每小時費率   = 本期費率 ÷ 結算週期小時數            # 先換成同一單位才能比較！
毛年化       = (空方每小時 − 多方每小時) × 8760
來回成本     = 2 × (多方吃單費 + 空方吃單費)          # 開倉 2 筆 + 平倉 2 筆 = 4 筆成交
             + 兩邊的買賣價差 (ask − bid) / mid       # 市價單要「吃」價差
回本小時     = 來回成本 ÷ 每小時費率差
淨年化       = 毛年化 − 來回成本 × 365 ÷ 持有天數
```
**手算例子**：OKX 吃單 0.05%、Hyperliquid 吃單 0.045% → 來回手續費 = 2 × 0.095% = 0.19%。
若費率差是 0.01%/8h（= 0.00125%/h），回本要 0.19% ÷ 0.00125% = **152 小時 ≈ 6.3 天**。
費率差只要在這之前消失，就是虧錢。這就是為什麼我們需要「統計分析」來看費率差會不會持續。

### 1.5 SQLite 資料庫
- 一個檔案（`data/monitor.db`）就是一個資料庫，不用安裝伺服器。
- 用 SQL 語言查詢：`SELECT base, net_apr FROM funding_opps WHERE suspect=0 ORDER BY net_apr DESC`
- 時間一律存 **UTC 毫秒**（`ts_ms`），顯示時才轉成台北時間 → 避免時區錯亂（這是業界常見做法）。
- 開啟 **WAL 模式**：收集器在寫入時，網頁還是可以同時讀取。

### 1.6 排程（Scheduling）
- 本機：`python -m frm.collector loop` 用 `while True` + `time.sleep()`，每 N 分鐘跑一次。
- 雲端：GitHub Actions 的 `cron: "17 */6 * * *"` = 每 6 小時的第 17 分自動執行（UTC 時間）。
- cron 語法 5 個欄位：分 時 日 月 星期。

### 1.7 統計概念（數據科學的部分）
| 名詞 | 白話解釋 | 在本專案的用途 |
|---|---|---|
| 分佈 distribution | 數值大部分落在哪裡、有多分散 | 價差平均多大、多常是正的 |
| 自我相關 autocorrelation | 這一期的值跟上一期有多像（−1~1） | 越接近 1 → 價差越「持續」 |
| 正負號持續機率 | 這期正、下期也正的機率 | 0.5 ≈ 擲銅板，越高越好預測 |
| 平穩 stationary | 平均和波動不會隨時間亂跑 | 價差會「回到平均」嗎？ |
| ADF 檢定 | 檢定序列是否平穩；p < 0.05 → 平穩 | 判斷均值回歸 |
| AR(1) 模型 | `x_t = c + φ·x_{t−1} + 誤差` | φ 越接近 1 越持續 |
| 半衰期 half-life | `−ln2 / ln φ`：偏離後幾期回到一半 | 價差能撐多久 |
| 樣本外 MAE | 用前 70% 資料訓練、後 30% 測試的平均絕對誤差 | 比較 AR(1) 是否贏過「天真預測」 |
| 回測 backtest | 用歷史資料模擬策略 | 扣完手續費到底賺不賺 |
| look-ahead bias | 回測偷看到未來資料 → 結果虛高 | 本專案訊號只用 t 以前的資料 |

---

## 2. 逐檔導讀

```
frm/
├── config.py        讀設定檔
├── symbols.py       幣種名稱對齊
├── metrics.py       純數學（手續費、年化、回本）  ← 最該先讀
├── exchanges.py     連交易所抓資料（唯一有網路 I/O 的地方）
├── opportunities.py 把快照變成機會清單
├── storage.py       SQLite 存取
├── collector.py     主程式：抓 → 算 → 存（排程進入點）
└── analysis.py      統計分析與報告
app/streamlit_app.py 網頁儀表板
tests/               自動化測試
```

### `frm/config.py`
- 用 `yaml.safe_load` 讀 `config.yaml`，轉成 `Config` **dataclass**（一種「只裝資料」的類別）。
- `cfg.fee("okx")` 查手續費；沒設定的交易所回傳保守的 default → **防呆設計**。
- 設計理念：會變的數字放設定檔，邏輯放程式碼。

### `frm/symbols.py`
- `normalize_base("1000PEPE") → "PEPE"`：用正規表達式（regex）去掉合約乘數前綴。
- `is_usd_linear_perp("BTC/USDT:USDT")`：ccxt 的 symbol 格式是 `基礎幣/報價幣:結算幣`，
  我們只要 USDT/USDC 結算的線性合約，排除幣本位（`BTC/USD:BTC`）。
- 改寫自 MIT 授權的 CryptoFundingArb（檔頭有標註，見 THIRD_PARTY_NOTICES.md）。

### `frm/metrics.py`（核心，請逐行看）
- 全部是 **純函式**：同樣輸入永遠得到同樣輸出、不碰網路 → 非常好測試。
- `Leg`：一邊的部位（交易所、費率、週期、bid/ask、手續費）。
- `funding_spread(a, b, holding_days)`：自動判斷誰的每小時費率高就做空誰，算出毛年化、成本、回本、淨年化。
- `instant_arb_edge`：A 賣 B 買的即時淨利率。

### `frm/exchanges.py`
- `get_exchange()`：建立 ccxt 物件並**快取**（`load_markets` 要好幾秒，只做一次）。
- `fetch_perp_snapshots()`：一次抓「全部幣」的資金費率 + 報價（bulk API，比一個一個抓快 100 倍）。
- `fetch_all_perps()`：用 `ThreadPoolExecutor` **平行**抓 4 家交易所；某家壞掉只記錄錯誤，不影響其他家 → **容錯**。
- `_quote_volume()`：各交易所成交量欄位不一致（OKX 回傳張數），這裡做資料清洗。
- `fetch_max()` / `fetch_bitopro()`：ccxt 沒有 MAX，所以用 `requests` 直接打官方 REST API。

### `frm/opportunities.py`
- `select_assets()`：過濾成交量太小的市場、只留「至少 2 家都有」的幣、依成交量取前 60 名。
- `is_suspect()`：兩所價格差 > 2% → 標記可疑。**真實案例**：開發時發現某些「盤前 / 股票代幣」合約
  在兩家交易所價格差 3% 以上，其實是不同定義的標的，若不過濾會產生假的「超高報酬」。這是**資料品質檢查**。
- `taiwan_premium()`：MAX 的 BTC/TWD ÷ USDT/TWD = 台灣 BTC 的美元價，跟國際價比較。

### `frm/storage.py`
- `connect()` 是 **context manager**（`with` 語法）：離開區塊自動 commit、關閉連線。
- `insert_rows()` 用 `?` 參數化查詢 → 防止 **SQL injection**（資安基本功）。
- `INSERT OR IGNORE` + PRIMARY KEY → 歷史資料重複抓也不會重複存（**冪等性 idempotency**）。

### `frm/collector.py`
- `run_once()`：抓 → 算 → 存，一輪約 16 秒。
- `loop` 模式：`sleep(interval − 本輪花費時間)`，讓每輪間隔固定；單輪出錯用 `try/except` 接住，不讓程式掛掉。
- `backfill`：分頁抓歷史資金費率（每頁 100 筆）。

### `frm/analysis.py`
- `window_funding()`：把 Hyperliquid 每小時的費率**加總**成 8 小時，才能跟 8 小時結算的交易所對齊（resample）。
- `pair_spread()`：兩所 8h 費率相減，`dropna()` 只保留兩邊都有資料的期。
- `ar1_fit()`：用 numpy 最小平方法（`lstsq`）估 AR(1)。
- `backtest_carry()`：過去 3 期平均價差 × 預期持有 21 期 > 來回成本才開倉；方向反轉就平倉。
- `make_report()`：輸出 Markdown 報告 + 3 張 matplotlib 圖。

### `app/streamlit_app.py`
- Streamlit 每次互動都「從頭重跑整個檔案」，所以用 `@st.cache_data(ttl=60)` 快取查詢結果。
- 5 個分頁：即時機會、歷史走勢、統計分析、台灣市場、方法說明。

### `tests/`
- `test_metrics.py`：用手算答案驗證公式（例：0.01%/8h 年化 = 10.95%）。
- `test_analysis.py`：**用已知答案的模擬資料測試統計函式**（模擬 φ=0.6 的 AR(1)，檢查估計值 ≈ 0.6；
  隨機漫步的 ADF p 值應 > 0.05）。這是驗證統計程式的標準技巧。
- `test_live.py`：真的連網的整合測試，預設跳過（`FRM_LIVE=1` 才跑），CI 不會因交易所當機而失敗。

### `.github/workflows/`
- `ci.yml`：每次 push 在 Python 3.11 / 3.12 跑測試。
- `collect.yml`：每 6 小時在 GitHub 雲端抓一次資料，資料庫存到 `data` 分支（強制覆蓋成單一 commit，避免 repo 越來越大）。

---

