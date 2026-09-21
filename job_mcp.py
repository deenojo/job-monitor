"""Minimal read-only MCP server for the local job-monitor SQLite database."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys
from typing import Any
from review_queue import get_review, list_reviews

ROOT = Path(__file__).resolve().parent
DATABASE_PATH = ROOT / "job_monitor.db"
PROFILE_PATH = ROOT / "profile.json"


def text_result(value: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(value, indent=2)}]}


def database_rows(query: str, values: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    if not DATABASE_PATH.exists():
        return []
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(query, values).fetchall()]


def recent_matches(arguments: dict[str, Any]) -> dict[str, Any]:
    limit = min(max(int(arguments.get("limit", 20)), 1), 100)
    return text_result(database_rows(
        """
        SELECT fingerprint, title, company, location, source, url, posted_at, first_seen, matched_role, match_tier, resume_path
        FROM jobs ORDER BY first_seen DESC LIMIT ?
        """,
        (limit,),
    ))


def job_details(arguments: dict[str, Any]) -> dict[str, Any]:
    fingerprint = arguments.get("fingerprint", "")
    rows = database_rows("SELECT * FROM jobs WHERE fingerprint = ?", (fingerprint,))
    return text_result(rows[0] if rows else {"error": "No job found for that fingerprint."})


def resume_for_role(arguments: dict[str, Any]) -> dict[str, Any]:
    requested = arguments.get("role", "").casefold()
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    for route in profile["role_routes"]:
        if requested == route["role"].casefold():
            return text_result({"role": route["role"], "resume_path": route["resume"]})
    return text_result({"error": "Use one of the configured job role names."})


def pending_reviews(arguments: dict[str, Any]) -> dict[str, Any]:
    limit = min(max(int(arguments.get("limit", 20)), 1), 100)
    return text_result(list_reviews(DATABASE_PATH, "pending_review", limit))


def review_package(arguments: dict[str, Any]) -> dict[str, Any]:
    review = get_review(DATABASE_PATH, arguments.get("fingerprint", ""))
    return text_result(review or {"error": "Review package not found."})


TOOLS = [
    {
        "name": "recent_matches",
        "description": "List recent internship and full-time jobs matched by the local monitor.",
        "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}}},
    },
    {
        "name": "job_details",
        "description": "Read full stored metadata for one job by its fingerprint.",
        "inputSchema": {"type": "object", "properties": {"fingerprint": {"type": "string"}}, "required": ["fingerprint"]},
    },
    {
        "name": "resume_for_role",
        "description": "Return the resume path selected for one configured job role.",
        "inputSchema": {"type": "object", "properties": {"role": {"type": "string"}}, "required": ["role"]},
    },
    {
        "name": "pending_reviews",
        "description": "List local application packages awaiting user review. These packages do not submit applications.",
        "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}}},
    },
    {
        "name": "review_package",
        "description": "Read a local review-only application package by matched-job fingerprint.",
        "inputSchema": {"type": "object", "properties": {"fingerprint": {"type": "string"}}, "required": ["fingerprint"]},
    },
]


def response(request_id: Any, result: dict[str, Any]) -> None:
    print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}), flush=True)


def error(request_id: Any, message: str) -> None:
    print(json.dumps({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": message}}), flush=True)


def main() -> int:
    handlers = {
        "recent_matches": recent_matches,
        "job_details": job_details,
        "resume_for_role": resume_for_role,
        "pending_reviews": pending_reviews,
        "review_package": review_package,
    }
    for line in sys.stdin:
        try:
            message = json.loads(line)
            method = message.get("method")
            request_id = message.get("id")
            if method == "initialize":
                response(request_id, {
                    "protocolVersion": message.get("params", {}).get("protocolVersion", "2024-11-05"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "local-job-monitor", "version": "1.0.0"},
                })
            elif method == "tools/list":
                response(request_id, {"tools": TOOLS})
            elif method == "tools/call":
                params = message.get("params", {})
                handler = handlers.get(params.get("name"))
                if handler is None:
                    error(request_id, "Unknown tool.")
                else:
                    response(request_id, handler(params.get("arguments", {})))
            elif request_id is not None:
                error(request_id, "Unsupported method.")
        except (json.JSONDecodeError, OSError, ValueError) as exception:
            if "request_id" in locals() and request_id is not None:
                error(request_id, str(exception))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
