"""用 Playwright（無頭瀏覽器）自動幫儀表板截圖，存到 docs/。

先另外開一個終端機執行：streamlit run app/streamlit_app.py
再執行：python scripts/screenshot.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8501"
OUT = Path(__file__).resolve().parent.parent / "docs"
TABS = {"即時機會": "dashboard_live.png", "歷史走勢": "dashboard_history.png",
        "統計分析": "dashboard_stats.png", "台灣市場": "dashboard_taiwan.png"}

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
    page.goto(URL, wait_until="networkidle")
    page.wait_for_selector("text=資金費率", timeout=60000)
    for tab, fname in TABS.items():
        page.get_by_role("tab", name=tab).click()
        page.wait_for_timeout(4000)  # 等圖表畫完
        page.screenshot(path=str(OUT / fname), full_page=True)
        print("saved", OUT / fname)
    browser.close()
