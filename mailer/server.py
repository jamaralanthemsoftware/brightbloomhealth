#!/usr/bin/env python3
"""
BrightBloom Health Landing Pages — "Ask a Question" Form Mailer Service
Listens on 127.0.0.1:8090 behind Nginx (/api/ask-question).
Supports Postmark HTTPS API (POSTMARK_SERVER_TOKEN) or SMTP (SMTP_HOST/SMTP_USER/SMTP_PASS)
configured in /etc/brightbloom-mail.env, and logs all submissions to /var/log/brightbloom-leads.jsonl.
"""

from datetime import datetime, timezone
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, HTTPServer
import html
import json
import os
import smtplib
import ssl
import urllib.error
import urllib.parse
import urllib.request

ENV_PATH = os.environ.get("BRIGHTBLOOM_ENV_FILE", "/etc/brightbloom-mail.env")
LEADS_LOG_PATH = os.environ.get("BRIGHTBLOOM_LEADS_LOG", "/var/log/brightbloom-leads.jsonl")


def load_env(path: str) -> dict:
    cfg = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    for k in (
        "POSTMARK_SERVER_TOKEN",
        "FROM_EMAIL",
        "TO_EMAIL",
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USER",
        "SMTP_PASS",
    ):
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    return cfg


def build_email_bodies(lead: dict) -> tuple[str, str, str]:
    page_name = lead["page_name"]
    page_slug = lead["page_slug"]
    page_url = lead["page_url"]
    first_name = lead["first_name"]
    last_name = lead["last_name"]
    email = lead["email"]
    phone = lead["phone"]
    question = lead["question"]
    submitted_at = lead["submitted_at"]

    subject = f"[BrightBloom Health] New Question from {first_name} {last_name} — {page_name}"

    text_body = (
        f"New 'Not Ready to Book? Ask a Question' submission:\n\n"
        f"Page Name:    {page_name} ({page_slug})\n"
        f"Page URL:     {page_url}\n"
        f"Submitted At: {submitted_at}\n"
        f"----------------------------------------\n"
        f"First Name:   {first_name}\n"
        f"Last Name:    {last_name}\n"
        f"Email:        {email}\n"
        f"Phone:        {phone}\n\n"
        f"Question:\n{question}\n"
    )

    esc = html.escape
    html_body = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; color: #111; line-height: 1.55;">
  <div style="max-width: 620px; margin: 0 auto; border: 1px solid #e2e2e2; border-radius: 12px; overflow: hidden;">
    <div style="background: #0d0d0d; color: #ffffff; padding: 20px 24px; border-bottom: 3px solid #F5A800;">
      <div style="font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em; color: #FFC533; font-weight: 700;">
        Not Ready to Book? Ask a Question
      </div>
      <h2 style="margin: 6px 0 0; font-size: 20px;">New Question — {esc(page_name)}</h2>
    </div>
    <div style="padding: 24px;">
      <table style="width: 100%; border-collapse: collapse; font-size: 15px;">
        <tr>
          <td style="padding: 8px 0; color: #555; width: 140px; font-weight: 600;">Landing Page:</td>
          <td style="padding: 8px 0;"><strong>{esc(page_name)}</strong> <span style="color:#666;">({esc(page_slug)})</span></td>
        </tr>
        <tr>
          <td style="padding: 8px 0; color: #555; font-weight: 600;">Page URL:</td>
          <td style="padding: 8px 0;"><a href="{esc(page_url)}">{esc(page_url)}</a></td>
        </tr>
        <tr>
          <td style="padding: 8px 0; color: #555; font-weight: 600;">First Name:</td>
          <td style="padding: 8px 0;">{esc(first_name)}</td>
        </tr>
        <tr>
          <td style="padding: 8px 0; color: #555; font-weight: 600;">Last Name:</td>
          <td style="padding: 8px 0;">{esc(last_name)}</td>
        </tr>
        <tr>
          <td style="padding: 8px 0; color: #555; font-weight: 600;">Email:</td>
          <td style="padding: 8px 0;"><a href="mailto:{esc(email)}">{esc(email)}</a></td>
        </tr>
        <tr>
          <td style="padding: 8px 0; color: #555; font-weight: 600;">Phone Number:</td>
          <td style="padding: 8px 0;"><a href="tel:{esc(phone)}">{esc(phone)}</a></td>
        </tr>
      </table>
      <div style="margin-top: 18px; padding: 16px; background: #f8f8f8; border-left: 4px solid #F5A800; border-radius: 6px;">
        <div style="font-size: 13px; font-weight: 700; text-transform: uppercase; color: #444; margin-bottom: 6px;">Question</div>
        <div style="white-space: pre-wrap; font-size: 15px; color: #111;">{esc(question)}</div>
      </div>
      <div style="margin-top: 18px; font-size: 12px; color: #777;">
        Submitted at {esc(submitted_at)} UTC
      </div>
    </div>
  </div>
</body>
</html>"""
    return subject, text_body, html_body


def send_via_postmark(cfg: dict, lead: dict) -> None:
    token = cfg["POSTMARK_SERVER_TOKEN"]
    from_email = cfg.get("FROM_EMAIL", "no-reply@brightbloomhealth.com")
    to_email = cfg.get("TO_EMAIL", "nicole@brightbloomhealth.com")
    subject, text_body, html_body = build_email_bodies(lead)

    payload = json.dumps(
        {
            "From": from_email,
            "To": to_email,
            "ReplyTo": lead["email"],
            "Subject": subject,
            "TextBody": text_body,
            "HtmlBody": html_body,
            "MessageStream": cfg.get("POSTMARK_STREAM", "outbound"),
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        "https://api.postmarkapp.com/email",
        data=payload,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Postmark-Server-Token": token,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        if resp.status >= 300:
            raise RuntimeError(f"Postmark returned HTTP {resp.status}")


def send_via_smtp(cfg: dict, lead: dict) -> None:
    host = cfg["SMTP_HOST"]
    port = int(cfg.get("SMTP_PORT", "587"))
    user = cfg.get("SMTP_USER", "")
    password = cfg.get("SMTP_PASS", "")
    from_email = cfg.get("FROM_EMAIL", user or "no-reply@brightbloomhealth.com")
    to_email = cfg.get("TO_EMAIL", "nicole@brightbloomhealth.com")
    subject, text_body, html_body = build_email_bodies(lead)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg["Reply-To"] = lead["email"]
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=12) as server:
            if user and password:
                server.login(user, password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=12) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            if user and password:
                server.login(user, password)
            server.send_message(msg)


class QuestionHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, data: dict) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/api/ask-question":
            self._send_json(404, {"ok": False, "error": "Not found"})
            return

        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 65536:
            self._send_json(400, {"ok": False, "error": "Invalid request payload."})
            return

        raw_body = self.rfile.read(length).decode("utf-8", errors="replace")
        ctype = self.headers.get("Content-Type", "")
        try:
            if "application/json" in ctype:
                data = json.loads(raw_body)
            else:
                parsed = urllib.parse.parse_qs(raw_body)
                data = {k: v[0] for k, v in parsed.items()}
        except Exception:
            self._send_json(400, {"ok": False, "error": "Malformed request data."})
            return

        # Honeypot check
        if str(data.get("website", "")).strip():
            self._send_json(200, {"ok": True})
            return

        first_name = str(data.get("first_name", "")).strip()
        last_name = str(data.get("last_name", "")).strip()
        email = str(data.get("email", "")).strip()
        phone = str(data.get("phone", "")).strip()
        question = str(data.get("question", "")).strip()
        page_name = str(data.get("page_name", "BrightBloom Health Landing Page")).strip()
        page_slug = str(data.get("page_slug", "")).strip()
        page_url = str(data.get("page_url", "")).strip()

        if not (first_name and last_name and email and phone and question) or "@" not in email:
            self._send_json(
                400,
                {"ok": False, "error": "Please fill in your first name, last name, email, phone number, and question."},
            )
            return

        lead = {
            "submitted_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "client_ip": self.headers.get("X-Real-IP") or self.client_address[0],
            "page_name": page_name[:120],
            "page_slug": page_slug[:80],
            "page_url": page_url[:300],
            "first_name": first_name[:100],
            "last_name": last_name[:100],
            "email": email[:160],
            "phone": phone[:60],
            "question": question[:4000],
        }

        # Always persist lead to local JSONL log
        try:
            os.makedirs(os.path.dirname(LEADS_LOG_PATH), exist_ok=True)
            with open(LEADS_LOG_PATH, "a", encoding="utf-8") as logf:
                logf.write(json.dumps(lead) + "\n")
        except Exception as e:
            print(f"[WARN] Failed to write lead log: {e}", flush=True)

        cfg = load_env(ENV_PATH)
        try:
            if cfg.get("POSTMARK_SERVER_TOKEN"):
                send_via_postmark(cfg, lead)
            elif cfg.get("SMTP_HOST"):
                send_via_smtp(cfg, lead)
            else:
                print("[WARN] Mail credentials not configured yet in /etc/brightbloom-mail.env; lead saved to disk.", flush=True)
        except Exception as e:
            print(f"[ERROR] Email delivery failed: {e}", flush=True)
            self._send_json(
                502,
                {
                    "ok": False,
                    "error": "We received your question, but email delivery encountered an issue. Please also call (656) 244-2777.",
                },
            )
            return

        self._send_json(200, {"ok": True})

    def log_message(self, fmt: str, *args) -> None:
        return


if __name__ == "__main__":
    host = os.environ.get("BIND_HOST", "127.0.0.1")
    port = int(os.environ.get("BIND_PORT", "8090"))
    server = HTTPServer((host, port), QuestionHandler)
    print(f"BrightBloom Mailer listening on {host}:{port}", flush=True)
    server.serve_forever()
