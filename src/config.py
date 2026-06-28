"""共用設定：載入 .env、常數與路徑。"""
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv

# 專案根目錄（src/ 的上一層）
ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# ---- 標的（通用：由環境變數 STOCK_SYMBOL 指定，預設 2344）----
# 策略建置/排程皆以此為當前標的；切換股票只需設 STOCK_SYMBOL=XXXX。
_KNOWN_NAMES = {"2344": "華邦電"}
SYMBOL = (os.getenv("STOCK_SYMBOL") or "2344").strip()
NAME = (os.getenv("STOCK_NAME") or _KNOWN_NAMES.get(SYMBOL) or SYMBOL).strip()

# ---- 時區 ----
TZ = timezone(timedelta(hours=8))  # Asia/Taipei


def now_tpe() -> datetime:
    return datetime.now(TZ)


def today_str() -> str:
    return now_tpe().strftime("%Y%m%d")


# ---- 路徑 ----
# 共用（多 symbol，DB 已含 symbol 欄）：market.db / xs.db 放 DATA_DIR 根。
# 每股獨立狀態（weights/score_params/strategy/news/branch/每日快照）放 DATA_DIR/<symbol>/。
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"
LOGS_DIR = ROOT / "logs"
SYMBOL_DIR = DATA_DIR / SYMBOL                 # 當前標的的專屬資料夾
SYMBOL_REPORTS_DIR = REPORTS_DIR / SYMBOL      # 當前標的的專屬報告夾
for _d in (DATA_DIR, REPORTS_DIR, LOGS_DIR, SYMBOL_DIR, SYMBOL_REPORTS_DIR):
    _d.mkdir(exist_ok=True, parents=True)


def data_path(date_str: str | None = None) -> Path:
    return SYMBOL_DIR / f"{SYMBOL}_{date_str or today_str()}.json"


def report_path(date_str: str | None = None) -> Path:
    return SYMBOL_REPORTS_DIR / f"{SYMBOL}_{date_str or today_str()}.md"


# 每股專屬狀態檔（集中管理，模組一律用這些而非自拼路徑）
def weights_path() -> Path:
    return SYMBOL_DIR / "weights.json"


def score_params_path() -> Path:
    return SYMBOL_DIR / "score_params.json"


def strategy_path() -> Path:
    return SYMBOL_DIR / "strategy.json"


def news_patterns_path() -> Path:
    return SYMBOL_DIR / "news_patterns.json"


def branch_profiles_path() -> Path:
    return SYMBOL_DIR / "branch_profiles.json"


def branch_polarity_path() -> Path:
    return SYMBOL_DIR / "branch_polarity.json"


# ---- 金鑰 / 來源設定 ----
FUGLE_API_KEY = os.getenv("FUGLE_MARKETDATA_API_KEY", "").strip()
FUGLE_BASE = "https://api.fugle.tw/marketdata/v1.0/stock"

TWSE_OPENAPI = "https://openapi.twse.com.tw/v1"
TWSE_RWD = "https://www.twse.com.tw/rwd/zh"

CMONEY_FORUM_URL = f"https://www.cmoney.tw/forum/stock/{SYMBOL}"

# 富邦 DJ「個股主力進出」（券商分點買賣超）；憑證有 SKI 問題，抓取須 verify=False
DJ_CHIPS_URL = f"https://fubon-ebrokerdj.fbs.com.tw/z/zc/zco/zco_{SYMBOL}_1.djhtm"

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
