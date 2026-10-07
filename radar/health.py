"""Persist collection outcomes independently of page-generation timestamps."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)
SCHEMA = """
CREATE TABLE IF NOT EXISTS pipeline_health (
    source TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    successful_at TEXT,
    detail TEXT NOT NULL
);
"""
CADENCES = {"prs": 8, "states": 8, "crosscheck": 8, "announcements": 2, "chatter": 2, "citizens": 2}


class IncompleteCollection(RuntimeError):
    """Expected source failure or incomplete sample; already-collected data may remain useful."""


def record(conn, source, status, detail, now=None):
    conn.executescript(SCHEMA)
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    with conn:
        conn.execute(
            """INSERT INTO pipeline_health VALUES (?,?,?,?,?)
               ON CONFLICT(source) DO UPDATE SET status=excluded.status, checked_at=excluded.checked_at,
               successful_at=COALESCE(excluded.successful_at,successful_at), detail=excluded.detail""",
            (source, status, stamp, stamp if status == "ok" else None, detail))


def run_stage(conn, source, callback):
    """Isolate stages, record failures, and let the caller publish health before returning failure."""
    record(conn, source, "running", "Collection started")
    try:
        result = callback()
    except IncompleteCollection as exc:
        log.warning("%s: %s", source, exc)
        record(conn, source, "partial", str(exc))
        return False
    except Exception as exc:
        log.exception("%s collection failed", source)
        # Exception messages can include URLs or credentials. Keep those out of public artifacts.
        record(conn, source, "failed", f"{type(exc).__name__}; inspect workflow logs")
        return False
    if result == "not_configured":
        record(conn, source, "not_configured", "X/Reddit not configured; no requests made")
    elif isinstance(result, tuple) and len(result) == 2 and result[0] == "ok":
        record(conn, source, "ok", str(result[1]))
    else:
        record(conn, source, "ok", "Collection finished; see source coverage for sample limits")
    return True


def snapshot(conn, now=None):
    conn.executescript(SCHEMA)
    now = now or datetime.now(timezone.utc)
    stored = {r["source"]: dict(r) for r in conn.execute("SELECT * FROM pipeline_health")}
    rows = []
    for source, days in CADENCES.items():
        row = stored.get(source, dict(source=source, status="never", checked_at=None,
                                     successful_at=None, detail="No monitored collection recorded"))
        last = datetime.fromisoformat(row["successful_at"]) if row["successful_at"] else None
        rows.append({**row, "stale": last is None or now - last > timedelta(days=days),
                     "stale_after_days": days})
    return {"generated_at": now.isoformat(), "sources": rows}
