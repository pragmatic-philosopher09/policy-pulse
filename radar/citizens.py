"""Bounded, credential-only public discussion sampling; never part of policy scoring.

Raw text is processed in memory, not archived. History starts at collection time.
Each search is a sample, not an exhaustive platform count or representative poll.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests

from .chatter import Doc, SPAM_WORDS, SUBREDDITS, parse_reddit, parse_x
from .config import CHATTER_QUERIES, TOPIC_BY_SLUG, USER_AGENT

log = logging.getLogger(__name__)
WINDOW = 30
PLATFORMS = ("reddit", "x")
SCHEMA = """
CREATE TABLE IF NOT EXISTS citizen_runs (
    day TEXT NOT NULL, platform TEXT NOT NULL, domain TEXT NOT NULL,
    status TEXT NOT NULL, fetched INTEGER NOT NULL, accepted INTEGER NOT NULL,
    checked_at TEXT NOT NULL,
    PRIMARY KEY(day, platform, domain)
);
CREATE TABLE IF NOT EXISTS citizen_posts (
    policy TEXT NOT NULL, platform TEXT NOT NULL, post_key TEXT NOT NULL,
    author_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
    observed TEXT NOT NULL, url TEXT NOT NULL, labels TEXT NOT NULL,
    PRIMARY KEY(policy, platform, post_key)
);
CREATE TABLE IF NOT EXISTS citizen_cooldowns (
    platform TEXT PRIMARY KEY, until_at REAL NOT NULL
);
"""

# These are text cues, not a sentiment model. Unclear language stays unclassified.
CUES = {
    "support": r"\bi (?:support|welcome|agree with|approve of)\b",
    "opposition": r"\bi (?:oppose|reject|disagree with|do not support|don't support)\b",
    "questions": r"\b(?:how (?:will|does|can)|what (?:will|does)|can someone explain)\b",
    "privacy": r"\b(?:privacy|surveillance|personal data|consent)\b",
    "cost": r"\b(?:afford|costs?|fees?|expensive|financial burden)\b",
    "implementation": r"\b(?:implementation|enforcement|compliance|deadline|rollout)\b",
    "access": r"\b(?:accessibility|excluded|exclusion|eligibility|discrimination)\b",
}
PATTERNS = {key: re.compile(pattern, re.I) for key, pattern in CUES.items()}


def policies() -> list[dict]:
    return [
        {"id": f"{domain}-{digest(phrase)[:10]}", "domain": domain, "name": phrase}
        for domain, phrases in CHATTER_QUERIES.items()
        for phrase in phrases
    ]


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def labels_for(text: str) -> list[str]:
    # Quoted/reported opinions are not reliably attributable to the poster.
    if re.search(r'["“”]|\b(?:said|says|sarcasm|sarcastic|not that|would|if)\b|/s\b', text, re.I):
        return [key for key, pattern in PATTERNS.items()
                if key not in ("support", "opposition") and pattern.search(text)]
    return [key for key, pattern in PATTERNS.items() if pattern.search(text)]


def acceptable(doc: Doc, today: date) -> bool:
    text = doc.text or doc.title
    try:
        published = date.fromisoformat(doc.published or "")
    except ValueError:
        return False
    if not post_url(doc):
        return False
    if not today - timedelta(days=WINDOW - 1) <= published <= today:
        return False
    if (len(text) < 40 or not doc.author or doc.author == "@"
            or SPAM_WORDS.search(text) or "low-signal" in doc.flags
            or len(re.findall(r"#\w+", text)) > 4
            or len(re.findall(r"https?://", text)) > 3):
        return False
    letters = [c for c in text if c.isalpha()]
    return not (len(letters) > 20 and sum(c.isupper() for c in letters) / len(letters) > .5)


def post_url(doc: Doc) -> str | None:
    parsed = urlparse(doc.url)
    if parsed.scheme != "https":
        return None
    if doc.source == "reddit" and parsed.hostname in ("www.reddit.com", "reddit.com"):
        match = re.search(r"/comments/([a-zA-Z0-9]+)(?:/|$)", parsed.path)
        if match:
            return f"https://www.reddit.com/comments/{match[1]}"
    if doc.source == "x" and parsed.hostname == "x.com":
        match = re.search(r"/status/([0-9]+)(?:/|$)", parsed.path)
        if match:
            return f"https://x.com/i/status/{match[1]}"
    return None


def record_batch(conn, platform: str, domain: str, docs: list[Doc], status: str, today: date) -> None:
    """Idempotent observations, capped at one post per author/policy/collection day."""
    conn.executescript(SCHEMA)
    day = today.isoformat()
    accepted = 0
    with conn:
        if status == "ok":
            for doc in docs:
                if doc.source != platform or not acceptable(doc, today):
                    continue
                text = doc.text or doc.title
                fingerprint = digest(re.sub(r"\W+", " ", re.sub(r"https?://\S+", "", text).lower()).strip())
                for policy in policies():
                    if policy["domain"] != domain:
                        continue
                    phrase = r"\b" + r"\s+".join(map(re.escape, policy["name"].split())) + r"\b"
                    if not re.search(phrase, text, re.I):
                        continue
                    relevant = " ".join(sentence for sentence in re.split(r"(?<=[.!?])\s+", text)
                                        if re.search(phrase, sentence, re.I))
                    author = digest(f"{policy['id']}:{platform}:{doc.author}")
                    duplicate = conn.execute(
                        "SELECT 1 FROM citizen_posts WHERE policy=? AND "
                        "(fingerprint=? OR (platform=? AND author_key=? AND observed=?))",
                        (policy["id"], fingerprint, platform, author, day)).fetchone()
                    if duplicate:
                        continue
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO citizen_posts VALUES (?,?,?,?,?,?,?,?)",
                        (policy["id"], platform, digest(post_url(doc)), author, fingerprint, day,
                         post_url(doc), json.dumps(labels_for(relevant))))
                    accepted += cur.rowcount
        conn.execute(
            """INSERT INTO citizen_runs VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(day,platform,domain) DO UPDATE SET status=excluded.status,
               fetched=excluded.fetched, accepted=accepted+excluded.accepted,
               checked_at=excluded.checked_at""",
            (day, platform, domain, status, len(docs), accepted,
             datetime.now(timezone.utc).isoformat()))


class Collector:
    """No raw-response cache, no pagination, no retry loops; at most 17 HTTP calls/run."""

    def __init__(self, conn):
        self.conn = conn
        self.last = {}
        self.blocked = {}
        self.session = requests.Session()
        self.session.headers["User-Agent"] = os.environ.get("REDDIT_USER_AGENT") or USER_AGENT
        self.n = 0

    def request(self, platform, method, url, **kwargs):
        cooldown = self.conn.execute(
            "SELECT until_at FROM citizen_cooldowns WHERE platform=?", (platform,)).fetchone()
        if platform in self.blocked:
            raise CollectionError(self.blocked[platform])
        if cooldown and cooldown[0] > time.time():
            raise CollectionError("rate_limited")
        if self.n >= 17:
            raise CollectionError("budget")
        time.sleep(max(0, 2 - (time.monotonic() - self.last.get(platform, 0))))
        self.n += 1
        try:
            response = self.session.request(method, url, timeout=20, allow_redirects=False, **kwargs)
        except requests.RequestException:
            self.blocked[platform] = "unavailable"
            raise CollectionError("unavailable") from None
        finally:
            self.last[platform] = time.monotonic()
        if response.status_code == 429 or response.headers.get("x-rate-limit-remaining") == "0" or response.headers.get("x-ratelimit-remaining") in ("0", "0.0"):
            until = time.time() + 900
            try:
                retry = response.headers.get("Retry-After", "")
                if retry:
                    until = max(until, time.time() + float(retry) if retry.isdigit()
                                else parsedate_to_datetime(retry).timestamp())
                if response.headers.get("x-rate-limit-reset"):
                    until = max(until, float(response.headers["x-rate-limit-reset"]))
                if response.headers.get("x-ratelimit-reset"):
                    until = max(until, time.time() + float(response.headers["x-ratelimit-reset"]))
            except (ValueError, TypeError, OverflowError):
                log.warning("Invalid rate-limit headers from %s; using conservative cooldown", platform)
            with self.conn:
                self.conn.execute("INSERT OR REPLACE INTO citizen_cooldowns VALUES (?,?)", (platform, until))
        if not 200 <= response.status_code < 300:
            status = "rate_limited" if response.status_code == 429 else (
                "access_denied" if response.status_code in (401, 402, 403) else "unavailable")
            self.blocked[platform] = status
            raise CollectionError(status)
        try:
            payload = response.json()
        except ValueError:
            raise CollectionError("invalid_response") from None
        if not isinstance(payload, dict) or payload.get("errors") or payload.get("error"):
            raise CollectionError("invalid_response")
        return payload


class CollectionError(Exception):
    pass


def refresh(conn, today: date | None = None) -> None:
    today = today or datetime.now(timezone.utc).date()
    conn.executescript(SCHEMA)
    client = Collector(conn)
    tokens = {"x": os.environ.get("X_BEARER_TOKEN"), "reddit": None}
    reddit_error = "not_configured"
    cid, secret = os.environ.get("REDDIT_CLIENT_ID"), os.environ.get("REDDIT_CLIENT_SECRET")
    if cid and secret:
        try:
            payload = client.request("reddit", "POST", "https://www.reddit.com/api/v1/access_token",
                                     auth=(cid, secret), data={"grant_type": "client_credentials"})
            if not payload.get("access_token"):
                raise CollectionError("invalid_response")
            tokens["reddit"] = payload["access_token"]
        except CollectionError as exc:
            reddit_error = str(exc)
            log.warning("Citizen's Corner Reddit authentication: %s", reddit_error)
    try:
        for platform in PLATFORMS:
            for domain, phrases in CHATTER_QUERIES.items():
                prior = conn.execute("SELECT status FROM citizen_runs WHERE day=? AND platform=? AND domain=?",
                                     (today.isoformat(), platform, domain)).fetchone()
                if prior and prior[0] == "ok":
                    continue
                docs = []
                status = reddit_error if platform == "reddit" else "not_configured"
                if tokens[platform]:
                    try:
                        query = " OR ".join(f'"{phrase}"' for phrase in phrases)
                        headers = {"Authorization": f"Bearer {tokens[platform]}"}
                        if platform == "reddit":
                            payload = client.request(platform, "GET",
                                f"https://oauth.reddit.com/r/{'+'.join(SUBREDDITS)}/search",
                                headers=headers, params={"q": query, "restrict_sr": 1, "sort": "new",
                                                       "t": "month", "limit": 25, "raw_json": 1})
                            if not isinstance(payload.get("data"), dict) or not isinstance(payload["data"].get("children"), list):
                                raise CollectionError("invalid_response")
                            if len(payload["data"]["children"]) > 25:
                                raise CollectionError("invalid_response")
                            docs = parse_reddit(json.dumps(payload))
                        else:
                            payload = client.request(platform, "GET", "https://api.x.com/2/tweets/search/recent",
                                headers=headers, params={"query": f"({query}) (India OR Indian) -is:retweet -is:reply lang:en",
                                "max_results": 25, "tweet.fields": "created_at,public_metrics,lang,entities",
                                "expansions": "author_id", "user.fields": "username,public_metrics"})
                            if not isinstance(payload.get("meta"), dict) or "result_count" not in payload["meta"]:
                                raise CollectionError("invalid_response")
                            count = payload["meta"]["result_count"]
                            posts = payload.get("data", [])
                            if type(count) is not int or not isinstance(posts, list) or not 0 <= count <= 25 or len(posts) != count:
                                raise CollectionError("invalid_response")
                            docs = parse_x(json.dumps(payload))
                        status = "ok"
                    except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
                        status = "invalid_response"
                        log.warning("Citizen parser %s/%s: %s", platform, domain, type(exc).__name__)
                    except CollectionError as exc:
                        status = str(exc)
                if status != "ok":
                    log.warning("Citizen collection %s/%s: %s", platform, domain, status)
                record_batch(conn, platform, domain, docs, status, today)
    finally:
        client.session.close()
    with conn:
        conn.execute("DELETE FROM citizen_posts WHERE observed < ?", ((today - timedelta(days=89)).isoformat(),))
        conn.execute("DELETE FROM citizen_runs WHERE day < ?", ((today - timedelta(days=89)).isoformat(),))


def snapshot(conn, today: date | None = None) -> dict:
    conn.executescript(SCHEMA)
    today = today or datetime.now(timezone.utc).date()
    days = [(today - timedelta(days=i)).isoformat() for i in reversed(range(WINDOW))]
    runs = {(r["day"], r["platform"], r["domain"]): dict(r) for r in conn.execute(
        "SELECT * FROM citizen_runs WHERE day BETWEEN ? AND ?", (days[0], days[-1]))}
    result = []
    for policy in policies():
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM citizen_posts WHERE policy=? AND observed BETWEEN ? AND ? ORDER BY observed DESC, post_key",
            (policy["id"], days[0], days[-1]))]
        trends = []
        for day in days:
            series = {}
            for platform in PLATFORMS:
                run = runs.get((day, platform, policy["domain"]))
                series[platform] = {
                    "status": run["status"] if run else "not_collected",
                    "count": sum(r["observed"] == day and r["platform"] == platform for r in rows)
                             if run and run["status"] == "ok" else None,
                }
            trends.append({"day": day, **series})
        themes = []
        for label in CUES:
            matching = [r for r in rows if label in json.loads(r["labels"])]
            authors = {(r["platform"], r["author_key"]) for r in matching}
            if len(authors) >= 3:
                examples = {}
                for row in matching:
                    examples.setdefault((row["platform"], row["author_key"]),
                                        {"platform": row["platform"], "url": row["url"]})
                themes.append({"label": label, "posts": len(matching), "accounts": len(authors),
                               "links": list(examples.values())[:3]})
        result.append({**policy, "total": len(rows), "themes": themes, "days": trends,
                       "peak": max([d[p]["count"] or 0 for d in trends for p in PLATFORMS] + [1]),
                       "platforms": {p: sum(r["platform"] == p for r in rows) for p in PLATFORMS}})
    return {"window_days": WINDOW, "generated": today.isoformat(),
            "measure": "newly_observed_deduplicated_posts", "policies": result,
            "domains": [{"slug": slug, "name": TOPIC_BY_SLUG[slug].name,
                         "name_hi": TOPIC_BY_SLUG[slug].name_hi,
                         "sources": {p: runs.get((days[-1], p, slug), {"status": "not_collected"})
                                     for p in PLATFORMS}} for slug in CHATTER_QUERIES]}
