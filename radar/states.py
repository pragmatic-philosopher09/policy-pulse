"""State legislatures: Bills introduced in Vidhan Sabhas, from PRS (CC BY 4.0).

    https://prsindia.org/bills/states?year=2026&per-page=50&page=N   -> title, state, PDF
    https://prsindia.org/bills/state-legislative-briefs?year=2026     -> PRS plain-English analyses

PRS lists state Bills by *year* only, so this layer is scored per year, not blended into the
monthly momentum arithmetic. Going forward, ``first_seen`` records when a Bill appeared in our
weekly fetch, which lets the brief say "new in the states this week".
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from datetime import date

from bs4 import BeautifulSoup

from .config import PRS_BASE, TOPICS
from .fetch import fetch
from .parse import Item
from .score import tag_topics

log = logging.getLogger(__name__)

LIST_URL = f"{PRS_BASE}/bills/states?year={{year}}&per-page=50&page={{page}}"
BRIEFS_URL = f"{PRS_BASE}/bills/state-legislative-briefs?year={{year}}&per-page=50&page={{page}}"
MAX_PAGES = 20

SCHEMA = """
CREATE TABLE IF NOT EXISTS state_bills (
    key        TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    state      TEXT NOT NULL,
    year       INTEGER NOT NULL,
    pdf_url    TEXT,
    brief_url  TEXT,
    first_seen TEXT NOT NULL DEFAULT (date('now'))
);
CREATE TABLE IF NOT EXISTS state_bill_topics (
    key   TEXT NOT NULL REFERENCES state_bills(key) ON DELETE CASCADE,
    topic TEXT NOT NULL,
    hits  INTEGER NOT NULL,
    PRIMARY KEY (key, topic)
);
"""


def _abs(href: str) -> str:
    return href if href.startswith("http") else PRS_BASE + href


def parse_list(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for row in soup.select("div.view-content .views-row"):
        a = row.select_one(".views-field-title-field a[href]")
        st = row.select_one(".views-field-field-bill-status span")
        if not a:
            continue
        href = a["href"].strip()
        m = re.search(r"/(\d{4})/", href)
        title = " ".join(a.get_text(" ", strip=True).split())
        out.append(dict(title=title, state=(st.get_text(strip=True) if st else "").strip() or "Unknown",
                        year=int(m.group(1)) if m else None, url=_abs(href), key=hashlib.sha1(href.encode()).hexdigest()[:16]))
    return out


def _fetch_all(url_tpl: str, year: int, force_first: bool) -> list[dict]:
    rows: list[dict] = []
    for page in range(MAX_PAGES):
        html = fetch(url_tpl.format(year=year, page=page), force=force_first)
        if not html:
            from .health import IncompleteCollection
            raise IncompleteCollection(f"State listing unavailable for {year}, page {page}")
        batch = parse_list(html)
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < 50:
            break
    else:
        from .health import IncompleteCollection
        raise IncompleteCollection(f"State listing reached page cap for {year}")
    return rows


def refresh(conn: sqlite3.Connection, years: tuple[int, ...] = (date.today().year - 1, date.today().year),
            force_first: bool = True) -> int:
    conn.executescript(SCHEMA)
    n = 0
    for year in years:
        bills = _fetch_all(LIST_URL, year, force_first)
        briefs = _fetch_all(BRIEFS_URL, year, force_first)
        brief_by_title = {b["title"].lower(): b["url"] for b in briefs}
        with conn:
            for b in bills:
                if b["year"] is None:
                    b["year"] = year
                conn.execute(
                    """INSERT INTO state_bills (key, title, state, year, pdf_url, brief_url)
                       VALUES (?,?,?,?,?,?)
                       ON CONFLICT(key) DO UPDATE SET brief_url = COALESCE(excluded.brief_url, brief_url)""",
                    (b["key"], b["title"], b["state"], b["year"], b["url"], brief_by_title.get(b["title"].lower())))
                n += 1
        log.info("state bills %d: %d bills, %d briefs", year, len(bills), len(briefs))
    retag(conn)
    return n


def retag(conn: sqlite3.Connection, threshold: int = 3) -> int:
    conn.executescript(SCHEMA)
    n = 0
    with conn:
        conn.execute("DELETE FROM state_bill_topics")
        for r in conn.execute("SELECT key, title, state FROM state_bills").fetchall():
            it = Item(month="", sector="State legislature", title=r["title"], body="")
            for topic, hits in tag_topics(it, threshold=threshold).items():
                conn.execute("INSERT INTO state_bill_topics VALUES (?,?,?)", (r["key"], topic, hits))
                n += 1
    return n


def by_topic(conn: sqlite3.Connection, recent_days: int = 35) -> dict[str, dict]:
    """Per topic: bills grouped by year, state counts, and what appeared recently."""
    conn.executescript(SCHEMA)
    out: dict[str, dict] = {t.slug: dict(bills=[], by_year={}, states={}, by_year_states={}, recent=[]) for t in TOPICS}
    today = date.today()
    rows = conn.execute(
        """SELECT b.*, t.topic, t.hits FROM state_bills b JOIN state_bill_topics t ON t.key = b.key
           ORDER BY b.year DESC, b.first_seen DESC, b.state""").fetchall()
    for r in rows:
        d = out.get(r["topic"])
        if d is None:
            continue
        rec = dict(r)
        d["bills"].append(rec)
        d["by_year"][r["year"]] = d["by_year"].get(r["year"], 0) + 1
        d["states"][r["state"]] = d["states"].get(r["state"], 0) + 1
        ys = d["by_year_states"].setdefault(r["year"], {})
        ys[r["state"]] = ys.get(r["state"], 0) + 1
        if (today - date.fromisoformat(r["first_seen"])).days <= recent_days:
            d["recent"].append(rec)
    for d in out.values():
        d["states"] = dict(sorted(d["states"].items(), key=lambda kv: -kv[1]))
        d["by_year_states"] = {y: dict(sorted(v.items(), key=lambda kv: -kv[1])) for y, v in d["by_year_states"].items()}
    return out


def totals(conn: sqlite3.Connection) -> dict:
    conn.executescript(SCHEMA)
    r = conn.execute("SELECT COUNT(*) n, COUNT(DISTINCT state) s, MIN(year) y0, MAX(year) y1 FROM state_bills").fetchone()
    return dict(bills=r["n"], states=r["s"], years=(r["y0"], r["y1"]))
