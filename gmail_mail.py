"""Gmail SMTP delivery for the local job monitor."""

from __future__ import annotations

from email.message import EmailMessage
import html
import os
from pathlib import Path
import smtplib
import ssl
from typing import Any

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465


class GmailMailError(RuntimeError):
    """Base error for Gmail mail setup or delivery."""


class GmailConfigurationError(GmailMailError):
    """The local Gmail SMTP configuration is incomplete."""

def environment_value(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if value.startswith("{{") and value.endswith("}}"):
        raise GmailConfigurationError(f"Replace the {name} documentation placeholder with the real local value.")
    return value


def sender() -> str:
    address = environment_value("JOB_MONITOR_GMAIL_ADDRESS")
    if not address:
        raise GmailConfigurationError("Set JOB_MONITOR_GMAIL_ADDRESS before enabling Gmail alerts.")
    return address


def app_password() -> str:
    value = environment_value("JOB_MONITOR_GMAIL_APP_PASSWORD").replace(" ", "")
    if not value:
        raise GmailConfigurationError("Set JOB_MONITOR_GMAIL_APP_PASSWORD to a Gmail App Password.")
    return value


def recipient() -> str:
    return environment_value("JOB_MONITOR_ALERT_EMAIL") or sender()


def gmail_status() -> dict[str, bool]:
    values = (
        os.environ.get("JOB_MONITOR_GMAIL_ADDRESS", "").strip(),
        os.environ.get("JOB_MONITOR_GMAIL_APP_PASSWORD", "").strip(),
    )
    recipient_value = os.environ.get("JOB_MONITOR_ALERT_EMAIL", "").strip()
    has_placeholder = lambda value: value.startswith("{{") and value.endswith("}}")
    return {
        "configured": bool(all(values) and not any(has_placeholder(value) for value in values)),
        "recipient_configured": bool(recipient_value and not has_placeholder(recipient_value)),
    }


def render_job_email(jobs: list[dict[str, Any]]) -> str:
    rows = []
    for job in jobs:
        rows.append(
            "<li>"
            f"<strong>{html.escape(job['title'])}</strong> — {html.escape(job['company'])}"
            f"<br>Location: {html.escape(job['location'] or 'not listed')}"
            f"<br>Matched role: {html.escape(job['matched_role'])}"
            f"<br>Alert tier: {html.escape(job.get('match_tier') or 'individual_contributor')}"
            f"<br>Resume: {html.escape(Path(job['resume_path']).name)}"
            f"<br><a href=\"{html.escape(job['url'], quote=True)}\">Open application</a>"
            "</li>"
        )
    return (
        "<html><body><h2>New job matches</h2>"
        "<p>Review the role and application requirements before submitting.</p>"
        f"<ol>{''.join(rows)}</ol></body></html>"
    )


def send_message(subject: str, html_body: str) -> None:
    message = EmailMessage()
    message["From"] = sender()
    message["To"] = recipient()
    message["Subject"] = subject
    message.set_content("Open this message in an HTML-capable email client to view job links.")
    message.add_alternative(html_body, subtype="html")
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context(), timeout=30) as smtp:
            smtp.login(sender(), app_password())
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as error:
        raise GmailMailError(f"Gmail SMTP delivery failed: {error}") from error


def send_job_email(jobs: list[dict[str, Any]]) -> None:
    if jobs:
        send_message(
            f"{len(jobs)} new job match{'es' if len(jobs) != 1 else ''}",
            render_job_email(jobs),
        )


def send_test_email() -> None:
    send_message(
        "Test: job monitor Gmail alerts",
        (
            "<html><body><h2>Job monitor Gmail alerts are ready</h2>"
            "<p>This is a test message. No job application was submitted.</p></body></html>"
        ),
    )
