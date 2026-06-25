"""將當日分析報告（reports/2344_YYYYMMDD.md）轉 HTML 寄給 MAIL_TO。

用法：
  python send_email.py [報告路徑]
  python send_email.py --error "錯誤摘要"   # 流程失敗時寄出錯誤通知
"""
from __future__ import annotations

import base64
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


def _send(subject: str, html: str, text: str):
    service = _gmail_service()
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["To"] = config.MAIL_TO
    # From 交由已授權帳號決定（Gmail API 自動帶入），不需指定寄件者
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()
    print(f"[send_email] 已寄至 {config.MAIL_TO}：{subject}")


def send_report(report_path=None):
    from pathlib import Path

    path = Path(report_path) if report_path else config.report_path()
    md_text = path.read_text(encoding="utf-8")
    html_body = markdown.markdown(md_text, extensions=["tables", "fenced_code"])
    html = f"""<html><body style="font-family:'Microsoft JhengHei',Arial,sans-serif;
    line-height:1.6;color:#222;max-width:760px;margin:auto;">{html_body}</body></html>"""
    date = config.today_str()
    subject = f"[{config.SYMBOL} {config.NAME}] 盤前走勢分析 {date[:4]}-{date[4:6]}-{date[6:]}"
    _send(subject, html, md_text)


def send_error(message: str):
    date = config.today_str()
    subject = f"[{config.SYMBOL} {config.NAME}] 每日分析流程失敗 {date}"
    html = f"<html><body><h3>每日分析流程發生問題</h3><pre>{message}</pre></body></html>"
    _send(subject, html, message)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--error":
        send_error(args[1] if len(args) > 1 else "未知錯誤")
    else:
        send_report(args[0] if args else None)
