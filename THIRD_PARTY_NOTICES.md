# Third-Party Notices 第三方授權聲明

## 1. hamood1337/CryptoFundingArb — MIT License

- Repository: https://github.com/hamood1337/CryptoFundingArb
- 使用方式：改寫（adapted）下列模組的邏輯，並在檔案開頭標註來源：
  - `src/fundingarb/symbols.py` → 本專案 `frm/symbols.py`（合約乘數前綴正規化 `normalize_base`）
  - `src/fundingarb/funding.py`、`fees.py` → 本專案 `frm/metrics.py`（結算週期解析、年化、四筆成交來回手續費、回本時間的概念）
- 主要修改：以「每小時費率」為統一單位；加入盤口買賣價差成本、跨所即時價差、可疑配對檢查；回本時間改以小時計。

```
MIT License

Copyright (c) 2026 Ahmad Maharmeh

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## 2. ccxt — MIT License

- Repository: https://github.com/ccxt/ccxt （作為函式庫依賴使用，未修改其原始碼）
- 用途：統一 OKX、Hyperliquid、Gate、Bitget 的公開 API 格式。

## 3. 參考但「未使用程式碼」的專案

- supervik/funding-rate-arbitrage-scanner（https://github.com/supervik/funding-rate-arbitrage-scanner）
  — 此 repo **沒有授權條款（No license）**，依著作權法預設保留所有權利，因此本專案**沒有複製任何程式碼**，
  只參考了其 README 描述的分析概念（perp-perp 配對、歷史平均年化）。

## 4. 其他依賴（皆透過 pip 安裝、未修改）

pandas (BSD-3), numpy (BSD-3), statsmodels (BSD-3), scipy (BSD-3), matplotlib (PSF-based), plotly (MIT),
streamlit (Apache-2.0), PyYAML (MIT), requests (Apache-2.0), pytest (MIT), playwright (Apache-2.0)。
