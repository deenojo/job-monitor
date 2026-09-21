"""Local, review-only application packages for matched jobs."""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

ROOT = Path(__file__).resolve().parent
DATABASE_PATH = ROOT / "job_monitor.db"
FACTS_PATH = ROOT / "candidate_facts.json"
VALID_STATUSES = {"pending_review", "ready_for_user", "declined"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_facts() -> dict[str, Any]:
    return json.loads(FACTS_PATH.read_text(encoding="utf-8"))


def initialize_queue(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS application_reviews (
            fingerprint TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            reviewed_at TEXT,
            notes TEXT NOT NULL DEFAULT '',
            package_json TEXT NOT NULL
        )
        """
    )
    connection.commit()


def create_package(job: dict[str, Any], facts: dict[str, Any] | None = None) -> dict[str, Any]:
    facts = facts or load_facts()
    tier = job.get("match_tier") or "individual_contributor"
    evidence = facts["evidence_by_tier"].get(tier, facts["evidence_by_tier"]["individual_contributor"])
    return {
        "job": {
            "fingerprint": job["fingerprint"],
            "title": job["title"],
            "company": job["company"],
            "location": job["location"],
            "source": job["source"],
            "application_url": job["url"],
            "matched_role": job["matched_role"],
            "match_tier": tier,
            "resume_path": job["resume_path"],
        },
        "checklist": [
            "Open the direct application URL and verify the role is still active.",
            "Review the selected resume and tailor it only if the job description supports factual changes.",
            "Compare required qualifications with the factual evidence below.",
            "Review each employer question before answering.",
            "Confirm all user-required inputs before submitting.",
            "Submit the application yourself after final review."
        ],
        "drafts": {
            "professional_summary": facts["professional_summary"],
            "relevant_evidence": evidence,
            "common_response": (
                "My background combines cloud infrastructure, reliability engineering, data-driven operations, "
                "and AI systems management. [Confirm and tailor the specific reason this company and role are of interest.]"
            ),
            "skills": facts["skills"],
            "education": facts["education"],
            "certifications": facts["certifications"]
        },
        "user_required_inputs": facts["user_required_inputs"],
        "submission_boundary": "This package is for review only. It does not open forms, upload documents, or submit an application."
    }


def enqueue_review(database_path: Path, job: dict[str, Any]) -> bool:
    package = create_package(job)
    timestamp = now()
    with closing(sqlite3.connect(database_path)) as connection:
        initialize_queue(connection)
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO application_reviews (
                fingerprint, status, created_at, updated_at, package_json
            ) VALUES (?, 'pending_review', ?, ?, ?)
            """,
            (job["fingerprint"], timestamp, timestamp, json.dumps(package)),
        )
        connection.commit()
        return bool(cursor.rowcount)


def enqueue_reviews(database_path: Path, jobs: list[dict[str, Any]]) -> int:
    return sum(enqueue_review(database_path, job) for job in jobs)


def list_reviews(database_path: Path, status: str = "pending_review", limit: int = 50) -> list[dict[str, Any]]:
    if not database_path.exists():
        return []
    with closing(sqlite3.connect(database_path)) as connection:
        initialize_queue(connection)
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(
            """
            SELECT r.fingerprint, r.status, r.created_at, r.updated_at, r.reviewed_at, r.notes,
                   j.title, j.company, j.location, j.url, j.matched_role, j.match_tier, j.resume_path
            FROM application_reviews r
            JOIN jobs j ON j.fingerprint = r.fingerprint
            WHERE (? = 'all' OR r.status = ?)
            ORDER BY r.created_at ASC
            LIMIT ?
            """,
            (status, status, limit),
        ).fetchall()]


def get_review(database_path: Path, fingerprint: str) -> dict[str, Any] | None:
    if not database_path.exists():
        return None
    with closing(sqlite3.connect(database_path)) as connection:
        initialize_queue(connection)
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT fingerprint, status, created_at, updated_at, reviewed_at, notes, package_json FROM application_reviews WHERE fingerprint = ?",
            (fingerprint,),
        ).fetchone()
        if row is None:
            return None
        review = dict(row)
        review["package"] = json.loads(review.pop("package_json"))
        return review


def update_review(database_path: Path, fingerprint: str, *, status: str | None = None, notes: str | None = None) -> bool:
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"Unknown review status: {status}")
    assignments: list[str] = ["updated_at = ?"]
    values: list[Any] = [now()]
    if status is not None:
        assignments.extend(["status = ?", "reviewed_at = ?"])
        values.extend([status, now()])
    if notes is not None:
        assignments.append("notes = ?")
        values.append(notes)
    values.append(fingerprint)
    with closing(sqlite3.connect(database_path)) as connection:
        initialize_queue(connection)
        cursor = connection.execute(
            f"UPDATE application_reviews SET {', '.join(assignments)} WHERE fingerprint = ?",
            values,
        )
        connection.commit()
        return bool(cursor.rowcount)


def backfill(database_path: Path) -> int:
    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        jobs = [dict(row) for row in connection.execute("SELECT * FROM jobs ORDER BY first_seen ASC").fetchall()]
    return enqueue_reviews(database_path, jobs)


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="List review packages.")
    parser.add_argument("--status", default="pending_review", help="Review status to list, or 'all'.")
    parser.add_argument("--show", metavar="FINGERPRINT", help="Show one review package.")
    parser.add_argument("--note", nargs=2, metavar=("FINGERPRINT", "TEXT"), help="Set review notes.")
    parser.add_argument("--set-status", nargs=2, metavar=("FINGERPRINT", "STATUS"), help="Set pending_review, ready_for_user, or declined.")
    parser.add_argument("--export", nargs=2, metavar=("FINGERPRINT", "PATH"), help="Export one review package as JSON.")
    parser.add_argument("--backfill", action="store_true", help="Explicitly create review packages for historical matches.")
    arguments = parser.parse_args()
    if arguments.list:
        print_json(list_reviews(DATABASE_PATH, arguments.status))
    elif arguments.show:
        print_json(get_review(DATABASE_PATH, arguments.show) or {"error": "Review not found."})
    elif arguments.note:
        print_json({"updated": update_review(DATABASE_PATH, arguments.note[0], notes=arguments.note[1])})
    elif arguments.set_status:
        print_json({"updated": update_review(DATABASE_PATH, arguments.set_status[0], status=arguments.set_status[1])})
    elif arguments.export:
        review = get_review(DATABASE_PATH, arguments.export[0])
        if review is None:
            raise SystemExit("Review not found.")
        Path(arguments.export[1]).write_text(json.dumps(review, indent=2), encoding="utf-8")
        print_json({"exported": arguments.export[1]})
    elif arguments.backfill:
        print_json({"created": backfill(DATABASE_PATH)})
    else:
        parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
