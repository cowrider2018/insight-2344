"""共用設定：載入 .env、常數與路徑。"""
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv

# 專案根目錄（src/ 的上一層）
ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# ---- 標的 ----
SYMBOL = "2344"
NAME = "華邦電"

# ---- 時區 ----
TZ = timezone(timedelta(hours=8))  # Asia/Taipei


def now_tpe() -> datetime:
    return datetime.now(TZ)


def today_str() -> str:
    return now_tpe().strftime("%Y%m%d")


# ---- 路徑 ----
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"
LOGS_DIR = ROOT / "logs"
for _d in (DATA_DIR, REPORTS_DIR, LOGS_DIR):
    _d.mkdir(exist_ok=True)


def data_path(date_str: str | None = None) -> Path:
    return DATA_DIR / f"{SYMBOL}_{date_str or today_str()}.json"


def report_path(date_str: str | None = None) -> Path:
    return REPORTS_DIR / f"{SYMBOL}_{date_str or today_str()}.md"


# ---- 金鑰 / 來源設定 ----
FUGLE_API_KEY = os.getenv("FUGLE_MARKETDATA_API_KEY", "").strip()
FUGLE_BASE = "https://api.fugle.tw/marketdata/v1.0/stock"

TWSE_OPENAPI = "https://openapi.twse.com.tw/v1"
TWSE_RWD = "https://www.twse.com.tw/rwd/zh"

CMONEY_FORUM_URL = f"https://www.cmoney.tw/forum/stock/{SYMBOL}"

# ---- Email（Gmail API + OAuth，全程不需密碼）----
# OAuth 用戶端憑證（Google Cloud Console 下載的 credentials.json）
GMAIL_CREDENTIALS = ROOT / os.getenv("GMAIL_CREDENTIALS_FILE", "credentials.json")
# 首次授權後自動產生的權杖（含 refresh token），勿外流
GMAIL_TOKEN = ROOT / os.getenv("GMAIL_TOKEN_FILE", "token.json")
# 只需寄信權限
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
MAIL_TO = os.getenv("MAIL_TO", "").strip()

# 通用 HTTP UA
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
