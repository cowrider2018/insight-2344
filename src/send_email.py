"""將當日分析報告（reports/2344_YYYYMMDD.md）轉 HTML 寄給 MAIL_TO。

白話新手版（reports/2344_YYYYMMDD_simple.md）另以固定版型寄給 MAIL_TO_SIMPLE，
兩封信收件名單獨立、互不影響。

用法：
  python send_email.py [報告路徑]
  python send_email.py --simple [精簡稿路徑]   # 白話新手版
  python send_email.py --error "錯誤摘要"      # 流程失敗時寄出錯誤通知
"""
from __future__ import annotations

import base64
import re
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import markdown

import config


def _gmail_service():
    """以 OAuth 取得 Gmail API 服務；首次會開瀏覽器授權，之後沿用 token.json。全程不需密碼。"""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if config.GMAIL_TOKEN.exists():
        creds = Credentials.from_authorized_user_file(
            str(config.GMAIL_TOKEN), config.GMAIL_SCOPES
        )
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())  # 用 refresh token 自動續期，不需重新授權
        else:
            if not config.GMAIL_CREDENTIALS.exists():
                raise RuntimeError(
                    f"找不到 OAuth 憑證 {config.GMAIL_CREDENTIALS}，無法寄信"
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(config.GMAIL_CREDENTIALS), config.GMAIL_SCOPES
            )
            creds = flow.run_local_server(port=0)
        config.GMAIL_TOKEN.write_text(creds.to_json(), encoding="utf-8")
    return build("gmail", "v1", credentials=creds)


def _send(subject: str, html: str, text: str, to: str):
    service = _gmail_service()
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["To"] = to
    # From 交由已授權帳號決定（Gmail API 自動帶入），不需指定寄件者
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()
    print(f"[send_email] 已寄至 {to}：{subject}")


def send_report(report_path=None):
    from pathlib import Path

    path = Path(report_path) if report_path else config.report_path()
    md_text = path.read_text(encoding="utf-8")
    html_body = markdown.markdown(md_text, extensions=["tables", "fenced_code"])
    html = f"""<html><body style="font-family:'Microsoft JhengHei',Arial,sans-serif;
    line-height:1.6;color:#222;max-width:760px;margin:auto;">{html_body}</body></html>"""
    date = config.today_str()
    subject = f"[{config.SYMBOL} {config.NAME}] 盤前走勢分析 {date[:4]}-{date[4:6]}-{date[6:]}"
    _send(subject, html, md_text, config.MAIL_TO)


# 白話新手版版型：Gmail 等客戶端對 <head><style> 的支援不可靠，故一律以 inline style 為基準。
# 基準字級直接採手機可讀的尺寸，media query 只做小螢幕的進一步縮放——
# 即使 <style> 被客戶端濾掉，版面仍完整可讀。
# 表列涵蓋精簡稿目前未用到的標籤（h3/ol/code/pre/blockquote），供版型日後擴充時不致退化。
_SIMPLE_STYLES = {
    "h1": "font-size:19px;line-height:1.4;margin:0 0 14px;padding-bottom:10px;"
          "border-bottom:2px solid #2b6cb0;color:#1a365d;font-weight:700;",
    "h2": "font-size:15px;line-height:1.5;margin:20px 0 8px;padding:7px 11px;"
          "background:#edf2f7;border-left:4px solid #2b6cb0;border-radius:4px;"
          "color:#1a365d;font-weight:700;",
    "h3": "font-size:14px;line-height:1.5;margin:14px 0 6px;color:#2c5282;"
          "font-weight:700;",
    "p": "font-size:14px;line-height:1.75;margin:8px 0;",
    "ul": "margin:8px 0;padding-left:20px;",
    "ol": "margin:8px 0;padding-left:20px;",
    "li": "font-size:14px;line-height:1.75;margin-bottom:6px;",
    "table": "width:100%;border-collapse:collapse;margin:10px 0;font-size:12.5px;"
             "table-layout:fixed;word-break:break-word;",
    "th": "background:#2b6cb0;color:#fff;padding:7px 8px;text-align:left;"
          "font-weight:600;line-height:1.5;",
    "td": "padding:7px 8px;border-bottom:1px solid #e2e8f0;vertical-align:top;"
          "line-height:1.6;",
    "strong": "color:#1a365d;",
    "code": "font-family:Consolas,Menlo,monospace;font-size:12px;background:#edf2f7;"
            "color:#2c5282;padding:1px 5px;border-radius:3px;word-break:break-all;",
    "pre": "background:#f7fafc;border:1px solid #e2e8f0;border-radius:6px;padding:10px;"
           "overflow-x:auto;font-size:12px;line-height:1.6;",
    "blockquote": "margin:10px 0;padding:8px 12px;border-left:3px solid #cbd5e0;"
                  "background:#f7fafc;color:#4a5568;font-size:13.5px;",
    "hr": "border:0;border-top:1px solid #e2e8f0;margin:18px 0;",
    "em": "color:#888;font-size:12px;",
}

# 小螢幕再縮一級；被濾掉也無妨，inline 基準已是手機可讀尺寸。
_MOBILE_CSS = """
@media only screen and (max-width:480px){
  .wrap{padding:8px !important;}
  .card{padding:14px !important;border-radius:6px !important;}
  h1{font-size:17px !important;}
  h2{font-size:14px !important;padding:6px 9px !important;}
  h3{font-size:13px !important;}
  p,li{font-size:13px !important;line-height:1.7 !important;}
  table{font-size:11.5px !important;}
  th,td{padding:5px 6px !important;}
  code{font-size:11px !important;}
}
"""


def _render_simple(md_text: str) -> str:
    """精簡稿 markdown -> 手機可讀的 inline-style HTML（版型固定，不隨每日內容變動）。"""
    body = markdown.markdown(md_text, extensions=["tables"])
    for tag, style in _SIMPLE_STYLES.items():
        body = re.sub(rf"<{tag}(?=[ >])", f'<{tag} style="{style}"', body)
    # 寬表格在窄螢幕可橫向捲動，不撐破版面
    body = body.replace("<table", '<div style="overflow-x:auto;"><table').replace(
        "</table>", "</table></div>"
    )
    return (
        "<html><head>"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<style>{_MOBILE_CSS}</style></head>"
        '<body class="wrap" style="margin:0;padding:14px;background:#f4f6f8;">'
        '<div class="card" style="font-family:\'Microsoft JhengHei\',Arial,sans-serif;'
        "color:#2d3748;max-width:680px;margin:auto;background:#fff;padding:20px;"
        'border-radius:10px;border:1px solid #e2e8f0;">'
        f"{body}</div></body></html>"
    )


def send_simple(report_path=None):
    """寄出白話新手版給 MAIL_TO_SIMPLE；精簡稿不存在時只警告、不中斷排程。"""
    from pathlib import Path

    path = Path(report_path) if report_path else config.simple_report_path()
    if not path.exists():
        print(f"[send_email] 找不到精簡稿 {path}，跳過白話版寄送")
        return
    md_text = path.read_text(encoding="utf-8")
    date = config.today_str()
    subject = f"[{config.SYMBOL} {config.NAME}] 白話版 今天怎麼看 {date[:4]}-{date[4:6]}-{date[6:]}"
    _send(subject, _render_simple(md_text), md_text, config.MAIL_TO_SIMPLE)


def send_error(message: str):
    date = config.today_str()
    subject = f"[{config.SYMBOL} {config.NAME}] 每日分析流程失敗 {date}"
    html = f"<html><body><h3>每日分析流程發生問題</h3><pre>{message}</pre></body></html>"
    _send(subject, html, message, config.MAIL_TO)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--error":
        send_error(args[1] if len(args) > 1 else "未知錯誤")
    elif args and args[0] == "--simple":
        send_simple(args[1] if len(args) > 1 else None)
    else:
        send_report(args[0] if args else None)
