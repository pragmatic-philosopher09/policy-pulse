"""Cross-check PRS items against independent coverage (government releases + newspapers).

For every visible item we query Google News RSS for the policy instrument named in the
title, within a ±45-day window of the PRS review month, and keep results whose headline
overlaps the query. Each hit is classified by domain:

    government  pib.gov.in, newsonair.gov.in, *.gov.in, *.nic.in, rbi.org.in, sansad.in ...
    news        mainstream/legal press (The Hindu, Mint, LiveLaw, SCC Online, ...)
    other       coaching sites, blogs, aggregators (kept, shown, but not counted toward confidence)

PRS itself is never counted: it is the primary source we are checking.
An item is *corroborated* when it has >= 1 government hit or >= 2 distinct news outlets.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote_plus, urlparse

import requests

from .config import USER_AGENT

log = logging.getLogger(__name__)

CACHE_DIR = Path(".cache/news")
GN = "https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
DELAY = 2.0
MAX_HITS = 6

GOV_DOMAINS = ("pib.gov.in", "newsonair.gov.in", "sansad.in", "rbi.org.in", "sebi.gov.in", "irdai.gov.in",
               "trai.gov.in", "mygov.in", "egazette.gov.in", "indiacode.nic.in", "ddnews.gov.in")
GOV_NAMES = {"pib.gov.in": "PIB", "newsonair.gov.in": "News On AIR", "ddnews.gov.in": "DD News", "sansad.in": "Sansad",
             "rbi.org.in": "RBI", "sebi.gov.in": "SEBI", "irdai.gov.in": "IRDAI", "trai.gov.in": "TRAI", "mygov.in": "MyGov"}
GOV_SUFFIXES = (".gov.in", ".nic.in")
NEWS_DOMAINS = {
    "thehindu.com": "The Hindu", "indianexpress.com": "Indian Express", "timesofindia.indiatimes.com": "Times of India",
    "hindustantimes.com": "Hindustan Times", "livemint.com": "Mint", "economictimes.indiatimes.com": "Economic Times",
    "business-standard.com": "Business Standard", "ndtv.com": "NDTV", "theprint.in": "ThePrint", "scroll.in": "Scroll",
    "thewire.in": "The Wire", "moneycontrol.com": "Moneycontrol", "livelaw.in": "LiveLaw", "barandbench.com": "Bar & Bench",
    "scconline.com": "SCC Online", "medianama.com": "Medianama", "inc42.com": "Inc42", "financialexpress.com": "Financial Express",
    "deccanherald.com": "Deccan Herald", "telegraphindia.com": "The Telegraph", "tribuneindia.com": "The Tribune",
    "news18.com": "News18", "indiatoday.in": "India Today", "firstpost.com": "Firstpost", "thequint.com": "The Quint",
    "reuters.com": "Reuters", "bloomberg.com": "Bloomberg", "bbc.com": "BBC", "newindianexpress.com": "New Indian Express",
    "outlookindia.com": "Outlook", "frontline.thehindu.com": "Frontline", "businesstoday.in": "Business Today",
    "cnbctv18.com": "CNBC-TV18", "zeenews.india.com": "Zee News", "abplive.com": "ABP", "amarujala.com": "Amar Ujala",
    "jagran.com": "Dainik Jagran", "bhaskar.com": "Dainik Bhaskar", "navbharattimes.indiatimes.com": "Navbharat Times",
    "thehindubusinessline.com": "BusinessLine", "downtoearth.org.in": "Down To Earth", "theken.com": "The Ken",
    "themorningcontext.com": "The Morning Context", "boomlive.in": "BOOM", "altnews.in": "Alt News",
    "economictimes.com": "Economic Times", "aninews.in": "ANI", "timesnownews.com": "Times Now",
    "expresspharma.in": "Express Pharma", "indiatvnews.com": "India TV", "wionews.com": "WION",
    "republicworld.com": "Republic", "dnaindia.com": "DNA", "mid-day.com": "Mid-day", "freepressjournal.in": "Free Press Journal",
    "newslaundry.com": "Newslaundry", "thehindubusinessline.com": "BusinessLine", "ptinews.com": "PTI",
    "hindi.moneycontrol.com": "Moneycontrol Hindi", "zeebiz.com": "Zee Business", "goodreturns.in": "Goodreturns",
    "taxguru.in": "TaxGuru", "taxscan.in": "Taxscan", "lawbeat.in": "Law Beat", "theleaflet.in": "The Leaflet",
}
EXCLUDE_DOMAINS = ("prsindia.org",)

_STOP = set("""the a an of and or to in on for by with under at from as is are was were be been has have had its their
this that these those into over about after before between during via per new draft bill act rules regulations amendment
amendments passed passes pass introduced released releases release notified notifies notify issued issues issue invited
invites invite comments comment approves approved approve submits submitted submit report presents presented parliament
lok sabha rajya cabinet standing committee ministry department india indian government govt various several held""".split())

_INSTRUMENT = re.compile(
    r"((?:(?:\(?[A-Z][\w’'&().,-]*|of|and|for|on|the|to|in|against)\s+){1,12}(?:Bill|Act|Rules|Regulations|Code|Scheme|Policy|Ordinance|Guidelines|Directions?|"
    r"Framework|Mission|Yojana|Sanhita|Commission|Authority)(?:[\s,()]+\d{4})?)"
)


def build_query(title: str) -> tuple[str, set[str]]:
    """Return (google-news query, significant tokens for overlap matching)."""
    m = _INSTRUMENT.search(title)
    core = m.group(1) if m else title
    core = re.sub(r"[“”\"]", "", core).strip(" ,.;:")
    words = re.findall(r"[A-Za-z][\w’'-]*|\d{4}", core)
    tokens = set()
    for w in words:
        lw = re.sub(r"[’']s$", "", w.lower().strip("’'(),."))
        if w.isupper() and len(w) >= 2:          # acronyms: IT, AI, GST, UPI, RBI
            tokens.add(lw)
        elif lw.isdigit() or (len(lw) > 2 and lw not in _STOP):
            tokens.add(lw)
    if m:  # inside an instrument name, generic words still discriminate ("Rules, 2021")
        tokens |= {w.lower().strip(",.") for w in words if w.lower() in ("rules", "act", "bill", "code", "regulations")}
    if m:
        q = f'"{core}"'
    else:
        q = " ".join(w for w in re.findall(r"[A-Za-z][\w’'-]*|\d[\d.%]*", title) if w.lower() not in _STOP)[:120]
    return q, tokens


def classify(url: str) -> tuple[str, str]:
    """-> (kind, outlet_name)"""
    host = urlparse(url).netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    if any(host == d or host.endswith("." + d) for d in EXCLUDE_DOMAINS):
        return "exclude", host
    if host in GOV_DOMAINS or host.endswith(GOV_SUFFIXES):
        return "government", GOV_NAMES.get(host, host)
    for d, name in NEWS_DOMAINS.items():
        if host == d or host.endswith("." + d):
            return "news", name
    return "other", host


_last = 0.0


def _fetch(query: str, after: date, before: date) -> str:
    global _last
    q = f"{query} after:{after.isoformat()} before:{before.isoformat()}"
    url = GN.format(q=quote_plus(q))
    path = CACHE_DIR / (hashlib.sha1(url.encode()).hexdigest() + ".xml")
    if path.exists() and time.time() - path.stat().st_mtime < 7 * 86400:
        return path.read_text(encoding="utf-8")
    wait = DELAY - (time.monotonic() - _last)
    if wait > 0:
        time.sleep(wait)
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    _last = time.monotonic()
    resp.raise_for_status()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(resp.text, encoding="utf-8")
    return resp.text


def _parse(xml: str) -> list[dict]:
    out = []
    for it in re.findall(r"<item>(.*?)</item>", xml, re.S):
        t = re.search(r"<title>(.*?)</title>", it, re.S)
        link = re.search(r"<link>(.*?)</link>", it, re.S)
        pub = re.search(r"<pubDate>(.*?)</pubDate>", it, re.S)
        src = re.search(r"<source url=\"([^\"]+)\"[^>]*>(.*?)</source>", it, re.S)
        if not (t and link):
            continue
        title = html.unescape(re.sub(r"<!\[CDATA\[|\]\]>", "", t.group(1))).strip()
        src_url = html.unescape(src.group(1)) if src else link.group(1).strip()
        src_name = html.unescape(src.group(2)).strip() if src else ""
        # Google appends " - Outlet" to titles
        title = re.sub(r"\s+-\s+[^-]{2,40}$", "", title)
        try:
            published = parsedate_to_datetime(pub.group(1)).date().isoformat() if pub else None
        except Exception:
            published = None
        out.append(dict(title=title, link=html.unescape(link.group(1).strip()), source_url=src_url,
                        source_name=src_name, published=published))
    return out


_GENERIC = {"rules", "regulations", "act", "bill", "code", "draft", "amendment", "amendments", "scheme", "policy",
            "guidelines", "directions", "direction", "framework", "report", "committee", "standing", "national",
            "2019", "2020", "2021", "2022", "2023", "2024", "2025", "2026", "central", "union", "india"}


def _relevant(tokens: set[str], headline: str) -> bool:
    if not tokens:
        return False
    words = {re.sub(r"[’']s$", "", w.lower().strip("’'(),.")) for w in re.findall(r"[A-Za-z][\w’'-]*|\d{4}", headline)}
    hits = tokens & words
    distinctive = hits - _GENERIC
    if not distinctive:                      # "Rules and Regulations" alone matches motorsport
        return False
    return len(hits) >= 2


SCHEMA = """
CREATE TABLE IF NOT EXISTS corroborations (
    uid       TEXT NOT NULL REFERENCES items(uid) ON DELETE CASCADE,
    url       TEXT NOT NULL,
    outlet    TEXT NOT NULL,
    kind      TEXT NOT NULL,          -- government | news | other
    title     TEXT NOT NULL,
    published TEXT,
    PRIMARY KEY (uid, url)
);
CREATE TABLE IF NOT EXISTS crosscheck_runs (
    uid        TEXT PRIMARY KEY REFERENCES items(uid) ON DELETE CASCADE,
    query      TEXT NOT NULL,
    checked_at TEXT NOT NULL DEFAULT (datetime('now')),
    n_results  INTEGER NOT NULL
);
"""


def crosscheck(conn: sqlite3.Connection, uids: list[str], recheck_days: int = 30) -> int:
    from .health import IncompleteCollection
    conn.executescript(SCHEMA)
    done = {r["uid"]: r["checked_at"] for r in conn.execute("SELECT uid, checked_at FROM crosscheck_runs")}
    cutoff = (datetime.utcnow() - timedelta(days=recheck_days)).strftime("%Y-%m-%d %H:%M:%S")
    todo = [u for u in uids if u not in done or done[u] < cutoff]
    n = 0
    failures = 0
    for uid in todo:
        row = conn.execute("SELECT title, month FROM items WHERE uid = ?", (uid,)).fetchone()
        if not row:
            continue
        y, m = map(int, row["month"].split("-"))
        first = date(y, m, 1)
        query, tokens = build_query(row["title"])
        loose_q, loose_tokens = build_query(re.sub(r"[()\"“”]", " ", row["title"]).replace(",", " "))
        loose_q = " ".join(w for w in re.findall(r"[A-Za-z][\w’'-]*|\d[\d.%]*", row["title"])
                           if w.lower() not in _STOP)[:120]
        kept: list[tuple] = []
        seen_outlets: set[str] = set()
        failed = False
        for q, toks in ((query, tokens), (loose_q, tokens | loose_tokens)):
            try:
                xml = _fetch(q, first - timedelta(days=45), first + timedelta(days=75))
                if ET.fromstring(xml).tag != "rss":
                    raise ValueError("Expected Google News RSS")
                hits = _parse(xml)
            except Exception as exc:
                log.warning("crosscheck failed for %s: %s", uid, exc)
                failed = True
                break
            for h in hits:
                kind, outlet = classify(h["source_url"])
                if kind == "exclude" or not _relevant(toks, h["title"]):
                    continue
                name = outlet if kind in ("news", "government") else (h["source_name"] or outlet)
                if name in seen_outlets:
                    continue
                seen_outlets.add(name)
                kept.append((uid, h["link"], name, kind, h["title"], h["published"]))
                if len(kept) >= MAX_HITS:
                    break
            if len(kept) >= 2 or q == loose_q:
                break
        if failed:
            failures += 1
            continue
        with conn:
            conn.execute("DELETE FROM corroborations WHERE uid = ?", (uid,))
            conn.executemany("INSERT OR IGNORE INTO corroborations VALUES (?,?,?,?,?,?)", kept)
            conn.execute("INSERT OR REPLACE INTO crosscheck_runs (uid, query, n_results) VALUES (?,?,?)",
                         (uid, query, len(kept)))
        n += 1
    if failures:
        raise IncompleteCollection(f"{failures} cross-checks failed; previous corroborations retained")
    return n


def lookup(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    try:
        rows = conn.execute("SELECT uid, url, outlet, kind, title, published FROM corroborations "
                            "ORDER BY CASE kind WHEN 'government' THEN 0 WHEN 'news' THEN 1 ELSE 2 END, published").fetchall()
    except sqlite3.OperationalError:
        return out
    for r in rows:
        out.setdefault(r["uid"], []).append(dict(r))
    return out


def is_corroborated(hits: list[dict]) -> bool:
    gov = sum(1 for h in hits if h["kind"] == "government")
    news = len({h["outlet"] for h in hits if h["kind"] == "news"})
    return gov >= 1 or news >= 2
