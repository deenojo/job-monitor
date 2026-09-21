"""Collect, normalize, match, and notify on public internship job listings."""

from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import html
from html.parser import HTMLParser
import json
import ipaddress
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
REQUEST_TIMEOUT_SECONDS = 10
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
    with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")
def request_json(url: str, method: str = "GET", payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        url,
        data=data,
        method=method,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json", "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode(response.headers.get_content_charset() or "utf-8"))

def safe_application_url(url: str) -> str | None:
    parsed = urlparse(url)
    hostname = parsed.hostname
    if parsed.scheme != "https" or not hostname or parsed.username or parsed.password:
        return None
    if hostname.casefold() == "localhost":
        return None
    try:
        if ipaddress.ip_address(hostname).is_private:
            return None
    except ValueError:
        pass
    if parsed.port not in (None, 443):
        return None
    return canonical_url(url)

def initialize_source_cache(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS source_cache (
            source_key TEXT PRIMARY KEY,
            etag TEXT,
            last_modified TEXT,
            checked_at TEXT NOT NULL
        )
        """
    )
    connection.commit()

def conditional_feed_content(feed: dict[str, Any], cache_database: Path | None) -> str | None:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json,text/markdown,text/plain"}
    cache_row = None
    if cache_database is not None:
        with closing(sqlite3.connect(cache_database)) as connection:
            initialize_source_cache(connection)
            cache_row = connection.execute(
                "SELECT etag, last_modified FROM source_cache WHERE source_key = ?",
                (feed["name"],),
            ).fetchone()
    if cache_row and cache_row[0]:
        headers["If-None-Match"] = cache_row[0]
    if cache_row and cache_row[1]:
        headers["If-Modified-Since"] = cache_row[1]
    request = Request(feed["url"], headers=headers)
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            content = response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")
            etag = response.headers.get("ETag")
            last_modified = response.headers.get("Last-Modified")
    except HTTPError as error:
        if error.code == 304:
            return None
        raise
    if cache_database is not None:
        with closing(sqlite3.connect(cache_database)) as connection:
            initialize_source_cache(connection)
            connection.execute(
                """
                INSERT INTO source_cache (source_key, etag, last_modified, checked_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(source_key) DO UPDATE SET
                    etag = excluded.etag,
                    last_modified = excluded.last_modified,
                    checked_at = excluded.checked_at
                """,
                (feed["name"], etag, last_modified, now()),
            )
            connection.commit()
    return content


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
    lowered = title.casefold()
    best: dict[str, str] | None = None
    best_length = 0
    for route in profile["role_routes"]:
        for alias in route["aliases"]:
            if alias in lowered and len(alias) > best_length:
                best = {
                    "role": route["role"],
                    "resume": route["resume"],
                    "tier": route.get("tier", "individual_contributor"),
                    "keyword": alias,
                }
                best_length = len(alias)
    return best

def evaluate_matches(jobs: Iterable[dict[str, str | None]], profile: dict[str, Any]) -> list[dict[str, str | None]]:
    matches: list[dict[str, str | None]] = []
    for job in jobs:
        match = match_role(job["title"] or "", profile)
        if match is not None:
            matches.append({
                **job,
                "matched_role": match["role"],
                "match_tier": match["tier"],
                "resume_path": match["resume"],
            })
    return matches


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


def collect_ashby(board: str) -> Iterable[dict[str, str | None]]:
    payload = request_json(f"https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=false")
    for job in payload.get("jobs", []):
        if not job.get("isListed", True):
            continue
        url = job.get("applyUrl") or job.get("jobUrl") or ""
        yield normalized_job(
            "ashby", str(job.get("id") or url), job.get("title", ""), board,
            job.get("location", ""), url, job.get("publishedAt"),
        )


def collect_smartrecruiters(company: str) -> Iterable[dict[str, str | None]]:
    payload = request_json(f"https://api.smartrecruiters.com/v1/companies/{company}/postings?limit=100")
    for job in payload.get("content", []):
        location = job.get("location", {})
        location_text = location if isinstance(location, str) else ", ".join(
            filter(None, [location.get("city"), location.get("region"), location.get("country")])
        )
        identifier = str(job.get("id", ""))
        url = job.get("ref") or f"https://jobs.smartrecruiters.com/{company}/{identifier}"
        yield normalized_job(
            "smartrecruiters", identifier or url, job.get("name", ""), company,
            location_text, url, job.get("releasedDate"),
        )


def collect_workable(account: str) -> Iterable[dict[str, str | None]]:
    payload = request_json(f"https://apply.workable.com/api/v1/widget/accounts/{account}")
    for job in payload.get("results", payload.get("jobs", [])):
        identifier = str(job.get("shortcode") or job.get("id") or "")
        location = job.get("location", {})
        location_text = location if isinstance(location, str) else ", ".join(
            filter(None, [location.get("city"), location.get("region"), location.get("country")])
        )
        url = job.get("url") or job.get("application_url") or f"https://apply.workable.com/{account}/j/{identifier}/"
        yield normalized_job(
            "workable", identifier or url, job.get("title", ""), account,
            location_text, url, job.get("published_at") or job.get("created_at"),
        )


def collect_workday(board: dict[str, str]) -> Iterable[dict[str, str | None]]:
    endpoint = board["url"]
    payload = request_json(endpoint, "POST", {"limit": 50, "offset": 0, "searchText": ""})
    for job in payload.get("jobPostings", []):
        identifier = str(job.get("bulletFields", [""])[0]) + str(job.get("externalPath", ""))
        location = " / ".join(job.get("locationsText", [])) if isinstance(job.get("locationsText"), list) else job.get("locationsText", "")
        url = urljoin(endpoint, job.get("externalPath", ""))
        yield normalized_job(
            "workday", identifier or url, job.get("title", ""), board.get("name", "workday"),
            location, url, job.get("postedOn"),
        )

def collect_keryx(feed: dict[str, Any], cache_database: Path | None) -> Iterable[dict[str, str | None]]:
    content = conditional_feed_content(feed, cache_database)
    if content is None:
        return
    payload = json.loads(content)
    for job in payload.get("jobs", []):
        title = job.get("title") or job.get("role") or job.get("position") or ""
        cycle = str(job.get("cycle") or "")
        if not re.search(r"\b(?:intern|internship|co-op|coop)\b", f"{title} {cycle}", re.I):
            continue
        if job.get("closed_at"):
            continue
        link_status = str(job.get("link_status") or "")
        if link_status and link_status not in {"ats-verified", "structured", "verified"}:
            continue
        url = safe_application_url(job.get("apply_url") or job.get("application_url") or job.get("url") or "")
        if not url:
            continue
        yield normalized_job(
            "keryx", str(job.get("id") or url), title, job.get("company") or "Keryx",
            job.get("location") or "", url, job.get("first_seen") or job.get("last_changed"),
        )

def collect_simplify(feed: dict[str, Any], cache_database: Path | None) -> Iterable[dict[str, str | None]]:
    content = conditional_feed_content(feed, cache_database)
    if content is None:
        return
    for line in content.splitlines():
        if not re.search(r"\b(?:intern|internship|co-op|coop)\b", line, re.I):
            continue
        links = re.findall(r"\[([^\]]+)\]\((https://[^)\s]+)\)", line)
        if not links:
            continue
        url = safe_application_url(links[-1][1])
        if not url:
            continue
        cells = [re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cell).strip() for cell in line.strip().strip("|").split("|")]
        values = [cell for cell in cells if cell]
        title = values[1] if len(values) > 1 else links[0][0]
        company = values[0] if values else "Simplify"
        location = values[2] if len(values) > 2 else ""
        yield normalized_job(
            "simplify", hashlib.sha256(url.encode()).hexdigest()[:20], title, company, location, url, None,
        )


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
            match_tier TEXT,
            resume_path TEXT,
            UNIQUE(source, external_id)
        )
        """
    )
    columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)").fetchall()}
    if "match_tier" not in columns:
        connection.execute("ALTER TABLE jobs ADD COLUMN match_tier TEXT")
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


def save_jobs(
    jobs: Iterable[dict[str, str | None]],
    profile: dict[str, Any],
    create_reviews: bool = True,
) -> list[dict[str, str | None]]:
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
            existing = connection.execute("SELECT fingerprint FROM jobs WHERE url = ?", (job["url"],)).fetchone()
            if existing is not None:
                connection.execute(
                    "UPDATE jobs SET last_seen = ?, matched_role = ?, match_tier = ?, resume_path = ? WHERE fingerprint = ?",
                    (timestamp, matched["role"], matched["tier"], matched["resume"], existing[0]),
                )
                continue
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO jobs (
                    fingerprint, source, external_id, title, company, location, url, posted_at,
                    first_seen, last_seen, matched_role, match_tier, resume_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job["fingerprint"], job["source"], job["external_id"], job["title"], job["company"],
                    job["location"], job["url"], job["posted_at"], timestamp, timestamp,
                    matched["role"], matched["tier"], matched["resume"],
                ),
            )
            if cursor.rowcount:
                job["matched_role"] = matched["role"]
                job["match_tier"] = matched["tier"]
                job["resume_path"] = matched["resume"]
                inserted.append(job)
            else:
                connection.execute(
                    "UPDATE jobs SET last_seen = ?, matched_role = ?, match_tier = ?, resume_path = ? WHERE fingerprint = ?",
                    (timestamp, matched["role"], matched["tier"], matched["resume"], job["fingerprint"]),
                )
        connection.commit()
    if inserted and create_reviews:
        from review_queue import enqueue_reviews
        enqueue_reviews(DATABASE_PATH, inserted)
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
        f"[{job['match_tier'] or 'individual_contributor'}] {job['title']} — {job['company']} ({job['location'] or 'location not listed'})\n{job['url']}"
        for job in shown
    ]
    extra = f"\n\n+{len(jobs) - len(shown)} more matches in the local job monitor." if len(jobs) > len(shown) else ""
    body = "\n\n".join(lines) + extra
    request = Request(
        f"https://ntfy.sh/{topic}",
        data=body.encode("utf-8"),
        method="POST",
        headers={
            "Title": f"{len(jobs)} new job match{'es' if len(jobs) != 1 else ''}",
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


def collect_all(watchlists: dict[str, Any], cache_database: Path | None = None) -> tuple[list[dict[str, str | None]], list[str]]:
    jobs: list[dict[str, str | None]] = []
    failures: list[str] = []
    collectors: list[tuple[str, str, Any]] = []
    collectors.extend(("greenhouse", board, collect_greenhouse) for board in watchlists.get("greenhouse_boards", []))
    collectors.extend(("lever", board, collect_lever) for board in watchlists.get("lever_boards", []))
    collectors.extend(("ashby", board, collect_ashby) for board in watchlists.get("ashby_boards", []))
    collectors.extend(("smartrecruiters", company, collect_smartrecruiters) for company in watchlists.get("smartrecruiters_companies", []))
    collectors.extend(("workable", account, collect_workable) for account in watchlists.get("workable_accounts", []))
    collectors.extend(("public", source, collect_public_board) for source in watchlists.get("public_boards", {}))
    for source_type, name, collector in collectors:
        try:
            if source_type == "public":
                jobs.extend(collector(name, watchlists["public_boards"][name]))
            else:
                jobs.extend(collector(name))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError) as error:
            failures.append(f"{source_type}:{name} ({error})")
    for board in watchlists.get("workday_boards", []):
        try:
            jobs.extend(collect_workday(board))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError) as error:
            failures.append(f"workday:{board.get('name', 'unknown')} ({error})")
    for feed in watchlists.get("internship_feeds", []):
        if not feed.get("enabled", True):
            continue
        try:
            if feed["type"] == "keryx":
                jobs.extend(collect_keryx(feed, cache_database))
            elif feed["type"] == "simplify":
                jobs.extend(collect_simplify(feed, cache_database))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError) as error:
            failures.append(f"internship_feed:{feed.get('name', 'unknown')} ({error})")
    return jobs, failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-notify", action="store_true", help="Collect and store matches without pushing alerts.")
    parser.add_argument("--baseline", action="store_true", help="Record current matches as already delivered without sending alerts.")
    parser.add_argument("--dry-run", action="store_true", help="Collect and evaluate matches without database writes or alerts.")
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
    if not arguments.dry_run:
        with sqlite3.connect(DATABASE_PATH) as connection:
            initialize_database(connection)
    collected, failures = collect_all(watchlists, None if arguments.dry_run else DATABASE_PATH)
    if arguments.dry_run:
        matches = evaluate_matches(collected, profile)
        role_counts: dict[str, int] = {}
        for job in matches:
            role_counts[job["matched_role"]] = role_counts.get(job["matched_role"], 0) + 1
        print(json.dumps({
            "collected": len(collected),
            "dry_run_matches": len(matches),
            "matches_by_role": role_counts,
            "sample_matches": matches[:10],
            "source_failures": failures,
        }, indent=2))
        return 0
    new_matches = save_jobs(collected, profile, create_reviews=not arguments.baseline)
    ntfy_pending = pending_delivery("ntfy")
    email_pending = pending_delivery("email")
    if arguments.baseline:
        mark_delivered("ntfy", ntfy_pending)
        mark_delivered("email", email_pending)
        print(json.dumps({
            "collected": len(collected),
            "new_matches": len(new_matches),
            "baseline_marked": len(ntfy_pending),
            "source_failures": failures,
        }, indent=2))
        return 0
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
