import sys
from pathlib import Path

# 讓測試可以 import 專案根目錄的 frm 套件
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
