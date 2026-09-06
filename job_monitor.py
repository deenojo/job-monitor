"""Collect, normalize, match, and notify on public internship job listings."""

from __future__ import annotations

import argparse
import hashlib
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen
from gmail_mail import (
    GmailConfigurationError,
    GmailMailError,
    gmail_status,
    send_job_email,
    send_test_email,
)

ROOT = Path(__file__).resolve().parent
PROFILE_PATH = ROOT / "profile.json"
WATCHLIST_PATH = ROOT / "watchlists.json"
DATABASE_PATH = ROOT / "job_monitor.db"
USER_AGENT = "WarpJobMonitor/1.0 (public-job-monitoring)"
INTERNSHIP_TERMS = ("intern", "internship", "co-op", "coop")


class AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._current: dict[str, Any] | None = None
        self.anchors: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            values = dict(attrs)
            self._current = {"href": values.get("href", ""), "text": ""}

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current["text"] += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._current is not None:
            href = self._current["href"].strip()
            text = " ".join(self._current["text"].split())
            if href and text:
                self.anchors.append({"href": href, "text": text})
            self._current = None


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def request_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/html"})
    with urlopen(request, timeout=30) as response:
        return response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")


def canonical_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def title_is_internship(title: str) -> bool:
    lowered = title.casefold()
    return any(term in lowered for term in INTERNSHIP_TERMS)


def match_role(title: str, profile: dict[str, Any]) -> dict[str, str] | None:
    if not title_is_internship(title):
        return None
    lowered = title.casefold()
    best: dict[str, str] | None = None
    best_length = 0
    for route in profile["role_routes"]:
        for alias in route["aliases"]:
            if alias in lowered and len(alias) > best_length:
                best = {"role": route["role"], "resume": route["resume"], "keyword": alias}
                best_length = len(alias)
    return best


def normalized_job(
    source: str,
    identifier: str,
    title: str,
    company: str,
    location: str,
    url: str,
    posted_at: str | None = None,
) -> dict[str, str | None]:
    return {
        "source": source,
        "external_id": identifier,
        "title": html.unescape(" ".join(title.split())),
        "company": html.unescape(" ".join(company.split())) or company,
        "location": html.unescape(" ".join(location.split())),
        "url": canonical_url(url),
        "posted_at": posted_at,
    }


def collect_greenhouse(board: str) -> Iterable[dict[str, str | None]]:
    payload = json.loads(request_text(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs"))
    for job in payload.get("jobs", []):
        yield normalized_job(
            "greenhouse",
            str(job["id"]),
            job.get("title", ""),
            board,
            job.get("location", {}).get("name", ""),
            job.get("absolute_url", ""),
            job.get("updated_at"),
        )


def collect_lever(board: str) -> Iterable[dict[str, str | None]]:
    page_url = f"https://jobs.lever.co/{board}"
    parser = AnchorParser()
    parser.feed(request_text(page_url))
    seen: set[str] = set()
    pattern = re.compile(rf"^https?://jobs\.lever\.co/{re.escape(board)}/([0-9a-f-]+)", re.I)
    for anchor in parser.anchors:
        url = urljoin(page_url, anchor["href"])
        match = pattern.match(url)
        if not match or match.group(1) in seen:
            continue
        seen.add(match.group(1))
        yield normalized_job("lever", match.group(1), anchor["text"], board, "", url)


def collect_public_board(source: str, board_url: str) -> Iterable[dict[str, str | None]]:
    parser = AnchorParser()
    parser.feed(request_text(board_url))
    seen: set[str] = set()
    if source == "a16z":
        accepted = re.compile(r"^https?://jobs\.a16z\.com/(?:jobs|companies)/.+", re.I)
    else:
        accepted = re.compile(r"^https?://(?:www\.)?ycombinator\.com/companies/.+/jobs/.+", re.I)
    for anchor in parser.anchors:
        url = urljoin(board_url, anchor["href"])
        if not accepted.match(url):
            continue
        key = canonical_url(url)
        if key in seen:
            continue
        seen.add(key)
        yield normalized_job(source, hashlib.sha256(key.encode()).hexdigest()[:20], anchor["text"], source, "", url)


def initialize_database(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            fingerprint TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            external_id TEXT NOT NULL,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            location TEXT NOT NULL,
            url TEXT NOT NULL,
            posted_at TEXT,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            matched_role TEXT,
            resume_path TEXT,
            UNIQUE(source, external_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS notifications (
            fingerprint TEXT PRIMARY KEY,
            notified_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS deliveries (
            channel TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            delivered_at TEXT NOT NULL,
            PRIMARY KEY (channel, fingerprint)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS monitor_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    migrated = connection.execute(
        "SELECT 1 FROM monitor_meta WHERE key = 'channel_delivery_migration'"
    ).fetchone()
    if migrated is None:
        existing = connection.execute("SELECT fingerprint FROM jobs").fetchall()
        timestamp = now()
        for channel in ("ntfy", "email"):
            connection.executemany(
                "INSERT OR IGNORE INTO deliveries (channel, fingerprint, delivered_at) VALUES (?, ?, ?)",
                [(channel, row[0], timestamp) for row in existing],
            )
        connection.execute(
            "INSERT INTO monitor_meta (key, value) VALUES ('channel_delivery_migration', ?)",
            (timestamp,),
        )
    connection.commit()


def fingerprint(job: dict[str, str | None]) -> str:
    identity = f"{job['source']}:{job['external_id']}:{job['url']}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def save_jobs(jobs: Iterable[dict[str, str | None]], profile: dict[str, Any]) -> list[dict[str, str | None]]:
    inserted: list[dict[str, str | None]] = []
    with sqlite3.connect(DATABASE_PATH) as connection:
        initialize_database(connection)
        for job in jobs:
            if not job["url"] or not job["title"]:
                continue
            matched = match_role(job["title"] or "", profile)
            if matched is None:
                continue
            job["fingerprint"] = fingerprint(job)
            timestamp = now()
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO jobs (
                    fingerprint, source, external_id, title, company, location, url, posted_at,
                    first_seen, last_seen, matched_role, resume_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job["fingerprint"], job["source"], job["external_id"], job["title"], job["company"],
                    job["location"], job["url"], job["posted_at"], timestamp, timestamp,
                    matched["role"], matched["resume"],
                ),
            )
            if cursor.rowcount:
                job["matched_role"] = matched["role"]
                job["resume_path"] = matched["resume"]
                inserted.append(job)
            else:
                connection.execute(
                    "UPDATE jobs SET last_seen = ? WHERE fingerprint = ?",
                    (timestamp, job["fingerprint"]),
                )
        connection.commit()
    return inserted


def pending_delivery(channel: str) -> list[dict[str, Any]]:
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in connection.execute(
                """
                SELECT j.* FROM jobs j
                LEFT JOIN deliveries d ON d.fingerprint = j.fingerprint AND d.channel = ?
                WHERE d.fingerprint IS NULL
                ORDER BY j.first_seen ASC
                """,
                (channel,),
            ).fetchall()
        ]


def mark_delivered(channel: str, jobs: Iterable[dict[str, Any]]) -> None:
    values = [(channel, job["fingerprint"], now()) for job in jobs]
    if not values:
        return
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.executemany(
            "INSERT OR IGNORE INTO deliveries (channel, fingerprint, delivered_at) VALUES (?, ?, ?)",
            values,
        )
        connection.commit()


def send_ntfy(jobs: list[dict[str, Any]]) -> bool:
    topic = os.environ.get("JOB_MONITOR_NTFY_TOPIC") or os.environ.get("JOB_MONITOR_NTFY_TOPIC_USER")
    if not topic or not jobs:
        return False
    shown = jobs[:5]
    lines = [
        f"{job['title']} — {job['company']} ({job['location'] or 'location not listed'})\n{job['url']}"
        for job in shown
    ]
    extra = f"\n\n+{len(jobs) - len(shown)} more matches in the local job monitor." if len(jobs) > len(shown) else ""
    body = "\n\n".join(lines) + extra
    request = Request(
        f"https://ntfy.sh/{topic}",
        data=body.encode("utf-8"),
        method="POST",
        headers={
            "Title": f"{len(jobs)} new internship match{'es' if len(jobs) != 1 else ''}",
            "Priority": "high",
            "Tags": "briefcase",
        },
    )
    try:
        with urlopen(request, timeout=30):
            pass
    except (HTTPError, URLError) as error:
        print(f"ntfy notification failed: {error}", file=sys.stderr)
        return False
    return True


def collect_all(watchlists: dict[str, Any]) -> tuple[list[dict[str, str | None]], list[str]]:
    jobs: list[dict[str, str | None]] = []
    failures: list[str] = []
    collectors: list[tuple[str, str, Any]] = []
    collectors.extend(("greenhouse", board, collect_greenhouse) for board in watchlists["greenhouse_boards"])
    collectors.extend(("lever", board, collect_lever) for board in watchlists["lever_boards"])
    collectors.extend(("public", source, collect_public_board) for source in watchlists["public_boards"])
    for source_type, name, collector in collectors:
        try:
            if source_type == "public":
                jobs.extend(collector(name, watchlists["public_boards"][name]))
            else:
                jobs.extend(collector(name))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError) as error:
            failures.append(f"{source_type}:{name} ({error})")
    return jobs, failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-notify", action="store_true", help="Collect and store matches without pushing alerts.")
    parser.add_argument("--email-test", action="store_true", help="Send a Gmail SMTP test to the configured recipient.")
    parser.add_argument("--email-status", action="store_true", help="Show whether Gmail SMTP is configured.")
    arguments = parser.parse_args()
    if arguments.email_test:
        send_test_email()
        print("Gmail SMTP test email accepted for delivery.")
        return 0
    if arguments.email_status:
        print(json.dumps(gmail_status(), indent=2))
        return 0
    profile = load_json(PROFILE_PATH)
    watchlists = load_json(WATCHLIST_PATH)
    with sqlite3.connect(DATABASE_PATH) as connection:
        initialize_database(connection)
    collected, failures = collect_all(watchlists)
    new_matches = save_jobs(collected, profile)
    ntfy_pending = pending_delivery("ntfy")
    email_pending = pending_delivery("email")
    ntfy_status = "skipped"
    email_delivery_status = "skipped"
    if not arguments.no_notify and ntfy_pending:
        if send_ntfy(ntfy_pending):
            mark_delivered("ntfy", ntfy_pending)
            ntfy_status = "delivered"
        else:
            ntfy_status = "failed"
    if not arguments.no_notify and email_pending:
        try:
            send_job_email(email_pending)
            mark_delivered("email", email_pending)
            email_delivery_status = "delivered"
        except GmailConfigurationError:
            email_delivery_status = "not_configured"
        except GmailMailError as error:
            email_delivery_status = f"failed: {error}"
    print(json.dumps({
        "collected": len(collected),
        "new_matches": len(new_matches),
        "ntfy": ntfy_status,
        "email": email_delivery_status,
        "source_failures": failures,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
