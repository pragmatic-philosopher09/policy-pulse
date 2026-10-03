"""PRS 'Announcements' — the live list of drafts open for public comment, with exact deadlines.

https://prsindia.org/announcements is a table: Comments invited on | Deadline | Press Release | PRS Analysis.
It is refreshed far more often than the monthly review, so it is the primary source for the
"You can still respond" block and for deadline reminders. Rows are stored so we keep history
(first_seen / last_seen) and can link them to monthly-review items when the same instrument shows up.
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from datetime import date, datetime

from bs4 import BeautifulSoup

from .config import PRS_BASE
from .fetch import fetch

log = logging.getLogger(__name__)

URL = f"{PRS_BASE}/announcements"

SCHEMA = """
CREATE TABLE IF NOT EXISTS announcements (
    key          TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    deadline     TEXT,                 -- ISO date
    draft_url    TEXT,
    press_url    TEXT,
    analysis_url TEXT,
    first_seen   TEXT NOT NULL DEFAULT (date('now')),
    last_seen    TEXT NOT NULL DEFAULT (date('now')),
    item_uid     TEXT                  -- matching monthly-review item, if any
);
"""

_DATE_FORMATS = ("%b %d,%Y", "%b %d, %Y", "%B %d,%Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y")


def _abs(href: str | None) -> str | None:
    if not href:
        return None
    href = href.strip()
    return href if href.startswith("http") else PRS_BASE + (href if href.startswith("/") else "/" + href)


def parse(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    table = soup.select_one("div.view-content table") or soup.find("table")
    if table is None:
        return []
    rows = []
    for tr in table.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 2:
            continue
        title = " ".join(cells[0].get_text(" ", strip=True).split())
        if not title:
            continue
        raw = cells[1].get_text(" ", strip=True)
        deadline = None
        for fmt in _DATE_FORMATS:
            try:
                deadline = datetime.strptime(re.sub(r"\s+", " ", raw).strip(), fmt).date()
                break
            except ValueError:
                continue
        a0 = cells[0].find("a", href=True)
        a2 = cells[2].find("a", href=True) if len(cells) > 2 else None
        a3 = cells[3].find("a", href=True) if len(cells) > 3 else None
        rows.append(dict(
            title=title, deadline=deadline,
            draft_url=_abs(a0["href"]) if a0 else None,
            press_url=_abs(a2["href"]) if a2 else None,
            analysis_url=_abs(a3["href"]) if a3 and a3["href"].strip() else None,
        ))
    return rows


def _key(r: dict) -> str:
    return hashlib.sha1(f"{r['title'].lower()}|{r['deadline']}".encode()).hexdigest()[:16]


_STOP = {"the", "draft", "bill", "bills", "rules", "regulations", "amendment", "amendments", "code", "act", "central",
         "and", "of", "on", "for", "to", "in", "under", "2024", "2025", "2026", "comments", "invited", "released"}
_TYPES = {"bill": "bill", "bills": "bill", "rules": "rules", "regulations": "rules", "code": "code", "policy": "policy",
          "act": "act", "scheme": "scheme", "guidelines": "rules", "directions": "rules"}


def _tokens(s: str) -> set[str]:
    words = {re.sub(r"s$", "", w.lower()) for w in re.findall(r"[A-Za-z][\w'-]*", s)}
    return words - _STOP - {re.sub(r"s$", "", w) for w in _STOP}


def _types(s: str) -> set[str]:
    return {_TYPES[w.lower()] for w in re.findall(r"[A-Za-z]+", s) if w.lower() in _TYPES}


def link_to_items(conn: sqlite3.Connection) -> int:
    """Attach the monthly-review item whose title shares the announcement's distinctive words."""
    items = conn.execute("SELECT uid, title FROM items WHERE action = 'consultation' OR title LIKE '%Bill%' "
                         "OR title LIKE '%Rules%' OR title LIKE '%Regulations%'").fetchall()
    n = 0
    for a in conn.execute("SELECT key, title FROM announcements WHERE item_uid IS NULL").fetchall():
        at = _tokens(a["title"])
        best, score = None, 0.0
        atypes = _types(a["title"])
        for it in items:
            itok = _tokens(it["title"])
            if not at or not itok:
                continue
            if atypes and _types(it["title"]) and not (atypes & _types(it["title"])):
                continue  # a Bill is not the Rules under it
            shared = at & itok
            if len(shared) < 2 and not (len(at) == 1 and len(itok) <= 3):
                continue  # one generic word in common is not a match
            overlap = len(shared) / len(at)
            if overlap > score:
                best, score = it["uid"], overlap
        if best and score >= 0.7:
            conn.execute("UPDATE announcements SET item_uid = ? WHERE key = ?", (best, a["key"]))
            n += 1
    conn.commit()
    return n


def refresh(conn: sqlite3.Connection, force: bool = True) -> int:
    from .health import IncompleteCollection
    conn.executescript(SCHEMA)
    html = fetch(URL, force=force)
    if not html:
        raise IncompleteCollection("Announcements unavailable; previous records retained")
    rows = parse(html)
    if not rows:
        log.warning("announcements page parsed to zero rows — layout change?")
        raise IncompleteCollection("Announcements parsed to zero rows; previous records retained")
    today = date.today().isoformat()
    with conn:
        for r in rows:
            conn.execute(
                """INSERT INTO announcements (key, title, deadline, draft_url, press_url, analysis_url, first_seen, last_seen)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(key) DO UPDATE SET last_seen = excluded.last_seen,
                       draft_url = COALESCE(excluded.draft_url, draft_url),
                       press_url = COALESCE(excluded.press_url, press_url),
                       analysis_url = COALESCE(excluded.analysis_url, analysis_url)""",
                (_key(r), r["title"], r["deadline"].isoformat() if r["deadline"] else None,
                 r["draft_url"], r["press_url"], r["analysis_url"], today, today))
    link_to_items(conn)
    # read the notice itself for rows that are open or closed within the last month
    from . import notice
    for r in rows:
        if r["deadline"] and (date.today() - r["deadline"]).days > 30:
            continue
        for u in {r["press_url"], r["draft_url"]}:
            if u:
                notice.read_notice(conn, u)
    return len(rows)


def load(conn: sqlite3.Connection) -> list[dict]:
    conn.executescript(SCHEMA)
    out = []
    for r in conn.execute("SELECT * FROM announcements ORDER BY deadline DESC"):
        d = dict(r)
        d["deadline"] = date.fromisoformat(d["deadline"]) if d["deadline"] else None
        out.append(d)
    return out
