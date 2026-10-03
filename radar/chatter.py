"""Public chatter: what newsrooms, forums and social feeds are saying about each topic.

This layer sits *beside* the momentum score, never inside it. PRS records formal government
action; this records public attention, so the site can say "Centre quiet — but people are
talking" or "12 conversations this month, 4 confirmed by two newspapers".

Sources (all public, all rate-limited, all cached on disk so re-runs cost nothing):

    rss        whitelisted newsroom + government feeds (The Hindu, Indian Express, Mint, PIB ...)
    gnews      Google News search RSS per topic phrase, kept only when the outlet is on the allow-list
    gdelt      GDELT DOC 2.0 article search, sourcecountry:IN, 1 request / 5.5 s
    bluesky    app.bsky.feed.searchPosts (public AppView, no key)
    mastodon   public hashtag timelines on large instances (no key)
    X/Reddit   collected separately by citizens.py using approved credentials only

Credibility, in three steps:

1. **Per-post quality gate** — drop replies, deleted/NSFW posts, very short text, hashtag walls,
   ALL-CAPS shouting, promo/spam vocabulary, link dumps, and low-engagement forum posts.
2. **Clustering** — posts and articles about the same thing are grouped (shared links, then
   token-overlap on distinctive words). One conversation = one cluster, however many posts.
3. **Cluster credibility** — a single post or article is never shown; the label a cluster earns
   depends on *who* is in it (a shared link alone never verifies an attached opinion):
       confirmed   >= 1 government source, or >= 2 distinct newsrooms
       reported    exactly 1 newsroom, plus at least one more document
       community   no newsroom, but >= 3 distinct authors, on >= 2 platforms or with real engagement
       thin        anything else — stored, never shown
   Social posts must place themselves in India (named institutions, states, ₹/crore, .in links);
   the same search phrase on a global network returns Ohio wage bills otherwise.
   Near-identical text from several accounts is collapsed to one post and flagged *coordinated*;
   a community-only cluster that is mostly coordinated is demoted to thin.

Finally every shown cluster is matched against the topic's recent PRS actions, so the page can
say whether the conversation tracks a formal action or is running ahead of one.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qsl, quote_plus, urlencode, urlparse, urlunparse

import requests

from .config import CHATTER_QUERIES, TOPICS, USER_AGENT
from .crosscheck import _STOP, _relevant, build_query, classify as classify_domain
from .score import _TOPIC_PATTERNS

log = logging.getLogger(__name__)

CACHE_DIR = Path(".cache/chatter")
CACHE_TTL_HOURS = 6
WINDOW_DAYS = 30           # how far back a conversation counts
MAX_REQUESTS = int(os.environ.get("CHATTER_MAX_REQUESTS", "160"))   # hard budget per run, all sources

# ---------------------------------------------------------------------------
# Source lists. Everything here is an allow-list: unknown newsrooms are "other" and never lift
# credibility; unknown subreddits are never queried.

RSS_FEEDS: tuple[tuple[str, str], ...] = (
    ("The Hindu", "https://www.thehindu.com/news/national/feeder/default.rss"),
    ("The Hindu", "https://www.thehindu.com/business/Economy/feeder/default.rss"),
    ("The Hindu", "https://www.thehindu.com/sci-tech/technology/feeder/default.rss"),
    ("Indian Express", "https://indianexpress.com/section/india/feed/"),
    ("Indian Express", "https://indianexpress.com/section/business/feed/"),
    ("Mint", "https://www.livemint.com/rss/news"),
    ("Mint", "https://www.livemint.com/rss/politics"),
    ("Hindustan Times", "https://www.hindustantimes.com/feeds/rss/india-news/rssfeed.xml"),
    ("Economic Times", "https://economictimes.indiatimes.com/news/economy/policy/rssfeeds/1286551815.cms"),
    ("Business Standard", "https://www.business-standard.com/rss/economy-policy-10201.rss"),
    ("NDTV", "https://feeds.feedburner.com/ndtvnews-india-news"),
    ("Moneycontrol", "https://www.moneycontrol.com/rss/economy.xml"),
    ("Scroll", "https://scroll.in/feed"),
    ("ThePrint", "https://theprint.in/feed/"),
    ("The Wire", "https://thewire.in/feed"),
    ("The Quint", "https://www.thequint.com/feed"),
    ("Medianama", "https://www.medianama.com/feed/"),
    ("Inc42", "https://inc42.com/feed/"),
    ("Bar & Bench", "https://www.barandbench.com/feed"),
    ("Taxscan", "https://www.taxscan.in/feed/"),
    ("PIB", "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3"),
    ("News On AIR", "https://www.newsonair.gov.in/feed/"),
)

SUBREDDITS: tuple[str, ...] = ("india", "IndiaSpeaks", "indianews", "LegalAdviceIndia", "IndiaTax",
                               "developersIndia", "IndianStreetBets", "Indian_Academia", "IndiaCareers", "unitedstatesofindia")
MASTODON_INSTANCES: tuple[str, ...] = ("mastodon.social", "mastodon.online")
MASTODON_TAGS: tuple[str, ...] = ("india", "indianpolitics", "indiannews", "policy", "indianlaw")

GDELT = "https://api.gdeltproject.org/api/v2/doc/doc?query={q}&mode=artlist&format=json&maxrecords=25&timespan=1month&sort=datedesc"
GNEWS = "https://news.google.com/rss/search?q={q}+when:30d&hl=en-IN&gl=IN&ceid=IN:en"
BLUESKY = ("https://api.bsky.app/xrpc/app.bsky.feed.searchPosts?q={q}&limit=25&sort=latest",
           "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts?q={q}&limit=25&sort=latest")

# Minimum seconds between requests, per host. Published/observed limits, then some slack.
HOST_DELAY = {
    "api.gdeltproject.org": 6.0,       # "one request every 5 seconds" (and it throttles shared IPs hard)
    "news.google.com": 2.0,            # same spacing crosscheck.py has used without trouble
    "www.reddit.com": 6.5,             # ~10/min anonymous
    "oauth.reddit.com": 1.2,           # 100/min with OAuth
    "api.bsky.app": 1.0, "public.api.bsky.app": 1.0,   # 3000/5min; be gentle anyway
    "api.twitter.com": 3.0,
    "default": 2.0,                    # RSS + mastodon
}

SPAM_WORDS = re.compile(
    r"\b(giveaway|airdrop|casino|betting tips?|earn (?:money|\$|₹)|make money|work from home job|dm (?:me|us)|whatsapp (?:me|us|group)|"
    r"join (?:now|my|our) (?:group|channel|telegram)|click (?:here|the link)|promo ?code|discount code|free (?:crypto|bitcoin|money)|"
    r"limited offer|buy now|subscribe (?:to )?my|follow (?:back|for follow)|f4f|onlyfans|escort|loan approval in|guaranteed returns?|"
    r"100% (?:profit|guaranteed)|pump|shitcoin|nudes)\b", re.I)
SHORTENERS = ("bit.ly", "t.co", "tinyurl.com", "goo.gl", "cutt.ly", "rb.gy", "is.gd", "shorturl.at")
MIN_TEXT_CHARS = 40

# A social post has to place itself in India to count. Newsroom feeds are Indian by construction; a global search
# box (Bluesky, Mastodon, X) returns Ohio wage bills and EU deepfake rules for the same phrases.
INDIA_MARKERS = re.compile(
    r"\b(India|Indian|Bharat|Hindustan|Centre|Union (?:Cabinet|Budget|Government|Minister)|Lok Sabha|Rajya Sabha|Sansad|Parliament of India|"
    r"Supreme Court of India|Delhi|Mumbai|Bengaluru|Bangalore|Chennai|Kolkata|Hyderabad|Pune|Ahmedabad|Lucknow|Patna|Bhopal|Jaipur|"
    r"Modi|MeitY|RBI|SEBI|IRDAI|TRAI|PFRDA|NPCI|UPI|GST|Aadhaar|NEET|JEE|CUET|UGC|NTA|NMC|AICTE|CBSE|EPFO?|ESIC|NREGA|MGNREGA|"
    r"DPDP|IndiaAI|Nyaya Sanhita|BNSS?|UAPA|PMLA|CBI|NIA|ED|CDSCO|AYUSH|MSME|DPIIT|NITI Aayog|PIB|Vidhan Sabha|"
    r"Andhra|Arunachal|Assam|Bihar|Chhattisgarh|Goa|Gujarat|Haryana|Himachal|Jharkhand|Karnataka|Kerala|Madhya Pradesh|Maharashtra|"
    r"Manipur|Meghalaya|Mizoram|Nagaland|Odisha|Punjab|Rajasthan|Sikkim|Tamil Nadu|Telangana|Tripura|Uttar Pradesh|Uttarakhand|"
    r"West Bengal|Kashmir|Ladakh|Puducherry|crore|lakh|₹|Rs\.?\s?\d)\b")


def india_relevant(doc: Doc) -> bool:
    if doc.kind in ("news", "government"):
        return True
    if INDIA_MARKERS.search(f"{doc.title} {doc.text}"):
        return True
    for l in doc.links:
        host = urlparse(l).netloc.lower()
        if host.endswith(".in") or classify_domain(l)[0] in ("news", "government"):
            return True
    return False

_TOKEN = re.compile(r"[A-Za-z][\w’'-]*|\d{4}")
_GENERIC = {"india", "indian", "government", "govt", "centre", "modi", "bjp", "congress", "news", "update", "breaking", "today",
            "people", "country", "policy", "law", "bill", "rules", "act", "draft", "amendment", "minister", "ministry", "new",
            "says", "said", "will", "amid", "over", "after", "big", "latest", "live", "explained", "know", "need", "why", "how", "what"}


# ---------------------------------------------------------------------------
# Polite HTTP: per-host spacing, disk cache with TTL, 429/Retry-After handling, global budget.

class _Http:
    def __init__(self, conn=None) -> None:
        self.last: dict[str, float] = {}
        self.n = 0
        self.disabled: set[str] = set()
        self.failures: set[str] = set()
        self.cached = 0
        self.conn = conn
        if conn is not None:
            conn.execute("CREATE TABLE IF NOT EXISTS chatter_cooldowns (host TEXT PRIMARY KEY, until_at REAL NOT NULL)")

    def get(self, url: str, *, headers: dict | None = None, ttl_hours: float = CACHE_TTL_HOURS,
            cache_key: str | None = None) -> str | None:
        key = hashlib.sha1((cache_key or url).encode()).hexdigest()
        path = CACHE_DIR / (key + ".txt")
        if path.exists() and (time.time() - path.stat().st_mtime) < ttl_hours * 3600:
            self.cached += 1
            return path.read_text(encoding="utf-8")
        host = urlparse(url).netloc.lower()
        cooldown = self.conn.execute("SELECT until_at FROM chatter_cooldowns WHERE host=?", (host,)).fetchone() if self.conn is not None else None
        if cooldown and cooldown[0] > time.time():
            self.disabled.add(host)
        if host in self.disabled or self.n >= MAX_REQUESTS:
            self.failures.add(host)
            return None
        wait = HOST_DELAY.get(host, HOST_DELAY["default"]) - (time.monotonic() - self.last.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        h = {"User-Agent": USER_AGENT, "Accept": "application/json, application/rss+xml, application/atom+xml, text/xml, */*"}
        h.update(headers or {})
        try:
            resp = requests.get(url, headers=h, timeout=15)
        except requests.RequestException as exc:
            log.warning("GET %s failed: %s", host, exc)
            self.failures.add(host)
            return None
        finally:
            self.last[host] = time.monotonic()
            self.n += 1
        if resp.status_code == 429:
            until = time.time() + 900
            retry = resp.headers.get("Retry-After")
            if retry:
                try:
                    until = max(until, time.time() + float(retry) if retry.isdigit()
                                else parsedate_to_datetime(retry).timestamp())
                except (TypeError, ValueError, OverflowError):
                    log.warning("Invalid Retry-After from %s; pausing for 15 minutes", host)
            if self.conn is not None:
                with self.conn:
                    self.conn.execute("INSERT OR REPLACE INTO chatter_cooldowns VALUES (?,?)", (host, until))
            log.warning("%s rate-limited; skipping until reset", host)
            self.failures.add(host)
            self.disabled.add(host)
            return None
        if resp.status_code in (401, 403):
            log.info("%s answered %s — source skipped for this run", host, resp.status_code)
            self.disabled.add(host)
            self.failures.add(host)
            return None
        if not 200 <= resp.status_code < 300:
            self.failures.add(host)
            log.warning("GET %s -> %s", url[:90], resp.status_code)
            return None
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(resp.text, encoding="utf-8")
        return resp.text


# ---------------------------------------------------------------------------

@dataclass
class Doc:
    source: str                  # rss | gnews | gdelt | bluesky | mastodon | reddit | x
    kind: str                    # government | news | community
    outlet: str                  # newsroom name, or platform (+ subreddit) for community posts
    author: str
    url: str
    title: str
    text: str
    published: str | None        # ISO date
    engagement: int = 0
    links: list[str] = field(default_factory=list)   # outbound links in the post (already normalised)
    quality: float = 1.0
    flags: list[str] = field(default_factory=list)
    topics: dict[str, int] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return hashlib.sha1(self.url.encode()).hexdigest()[:16]


def normalise_url(u: str) -> str:
    """Strip tracking params and fragments so the same article from two feeds dedups."""
    try:
        p = urlparse(u.strip())
    except ValueError:
        return u
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=False)
         if not (k.startswith("utm_") or k in ("fbclid", "gclid", "ref", "source", "cmpid", "ocid", "igshid", "mc_cid"))]
    host = p.netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    return urlunparse((p.scheme or "https", host, p.path.rstrip("/") or "/", "", urlencode(q), ""))


def _clean(s: str) -> str:
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    return re.sub(r"\s+", " ", s).strip()


def _iso(dt) -> str | None:
    try:
        if isinstance(dt, (int, float)):
            return datetime.fromtimestamp(dt, tz=timezone.utc).date().isoformat()
        if isinstance(dt, str):
            s = dt.strip()
            if re.match(r"\d{14}$", s):                       # GDELT seendate 20260927T... or 20260927123000
                return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
            if re.match(r"\d{8}T\d{6}Z?$", s):
                return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
            try:
                return datetime.fromisoformat(s.replace("Z", "+00:00")).date().isoformat()
            except ValueError:
                return parsedate_to_datetime(s).date().isoformat()
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# Parsers (pure; unit-tested without network)

def parse_feed(xml: str, outlet: str) -> list[Doc]:
    """RSS 2.0 or Atom -> Docs. Kind comes from the link's domain (PIB -> government)."""
    out: list[Doc] = []
    entries = re.findall(r"<item\b.*?</item>", xml, re.S) or re.findall(r"<entry\b.*?</entry>", xml, re.S)
    for e in entries:
        t = re.search(r"<title[^>]*>(.*?)</title>", e, re.S)
        link = re.search(r"<link[^>]*href=\"([^\"]+)\"", e) or re.search(r"<link[^>]*>(.*?)</link>", e, re.S)
        desc = re.search(r"<(?:description|summary|content)[^>]*>(.*?)</(?:description|summary|content)>", e, re.S)
        pub = re.search(r"<(?:pubDate|published|updated|dc:date)[^>]*>(.*?)</(?:pubDate|published|updated|dc:date)>", e, re.S)
        if not (t and link):
            continue
        title = _clean(re.sub(r"<!\[CDATA\[|\]\]>", "", t.group(1)))
        url = normalise_url(_clean(re.sub(r"<!\[CDATA\[|\]\]>", "", link.group(1))))
        if not title or not url.startswith("http"):
            continue
        kind, name = classify_domain(url)
        if kind == "exclude":
            continue
        if kind == "other":
            kind = "news"   # the feed itself is allow-listed; keep the outlet name we were given
            name = outlet
        out.append(Doc(source="rss", kind=kind, outlet=name if kind == "government" else outlet, author=outlet, url=url,
                       title=title, text=_clean(re.sub(r"<!\[CDATA\[|\]\]>", "", desc.group(1)))[:600] if desc else "",
                       published=_iso(_clean(pub.group(1))) if pub else None))
    return out


def parse_gnews(xml: str) -> list[Doc]:
    """Google News search RSS. The outlet is in <source url=...>; unknown outlets are dropped (allow-list only)."""
    out: list[Doc] = []
    for e in re.findall(r"<item\b.*?</item>", xml, re.S):
        t = re.search(r"<title[^>]*>(.*?)</title>", e, re.S)
        link = re.search(r"<link[^>]*>(.*?)</link>", e, re.S)
        pub = re.search(r"<pubDate>(.*?)</pubDate>", e, re.S)
        src = re.search(r"<source url=\"([^\"]+)\"[^>]*>(.*?)</source>", e, re.S)
        if not (t and link and src):
            continue
        kind, name = classify_domain(html.unescape(src.group(1)))
        if kind not in ("news", "government"):
            continue
        title = _clean(re.sub(r"<!\[CDATA\[|\]\]>", "", t.group(1)))
        src_text = _clean(src.group(2))
        if src_text and title.endswith(" - " + src_text):
            title = title[: -len(src_text) - 3].rstrip()
        title = re.sub(r"\s+-\s+(?:[\w.-]+\.\w{2,}|[^-]{2,40})$", "", title)
        url = _clean(re.sub(r"<!\[CDATA\[|\]\]>", "", link.group(1)))
        if not title or not url.startswith("http"):
            continue
        out.append(Doc(source="gnews", kind=kind, outlet=name, author=name, url=url, title=title, text="",
                       published=_iso(_clean(pub.group(1))) if pub else None))
    return out


def parse_gdelt(payload: str) -> list[Doc]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return []
    out: list[Doc] = []
    for a in data.get("articles", []):
        url = normalise_url(a.get("url", ""))
        title = _clean(a.get("title", ""))
        if not url.startswith("http") or not title:
            continue
        kind, name = classify_domain(url)
        if kind == "exclude":
            continue
        if kind == "other":
            continue          # GDELT indexes everything; only allow-listed newsrooms count here
        if (a.get("language") or "English") not in ("English", "Hindi"):
            continue
        out.append(Doc(source="gdelt", kind=kind, outlet=name, author=a.get("domain", name), url=url, title=title, text="",
                       published=_iso(a.get("seendate", ""))))
    return out


def parse_bluesky(payload: str) -> list[Doc]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return []
    out: list[Doc] = []
    for p in data.get("posts", []):
        rec = p.get("record") or {}
        if "reply" in rec:
            continue
        langs = rec.get("langs") or ["en"]
        if not any(l.startswith(("en", "hi")) for l in langs):
            continue
        text = _clean(rec.get("text", ""))
        handle = (p.get("author") or {}).get("handle", "")
        rkey = p.get("uri", "").rsplit("/", 1)[-1]
        url = f"https://bsky.app/profile/{handle}/post/{rkey}"
        links = []
        for f in rec.get("facets", []):
            for ft in f.get("features", []):
                if ft.get("uri"):
                    links.append(normalise_url(ft["uri"]))
        emb = (p.get("embed") or {}).get("external") or {}
        if emb.get("uri"):
            links.append(normalise_url(emb["uri"]))
            if emb.get("title"):
                text = f"{text} {emb['title']}"
        eng = int(p.get("likeCount", 0)) + 2 * int(p.get("repostCount", 0)) + int(p.get("replyCount", 0))
        out.append(Doc(source="bluesky", kind="community", outlet="Bluesky", author=handle, url=url,
                       title=text[:160], text=text, published=_iso(rec.get("createdAt") or p.get("indexedAt")),
                       engagement=eng, links=sorted(set(links))))
    return out


def parse_mastodon(payload: str, instance: str) -> list[Doc]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return []
    out: list[Doc] = []
    for s in data if isinstance(data, list) else []:
        if s.get("in_reply_to_id") or s.get("reblog") or s.get("sensitive"):
            continue
        if (s.get("language") or "en") not in ("en", "hi"):
            continue
        text = _clean(s.get("content", ""))
        links = [normalise_url(m.group(1)) for m in re.finditer(r'href="(https?://[^"]+)"', s.get("content", ""))
                 if "/tags/" not in m.group(1) and "@" not in urlparse(m.group(1)).path]
        card = s.get("card") or {}
        if card.get("url"):
            links.append(normalise_url(card["url"]))
            if card.get("title"):
                text = f"{text} {card['title']}"
        acct = (s.get("account") or {}).get("acct", "")
        eng = int(s.get("favourites_count", 0)) + 2 * int(s.get("reblogs_count", 0)) + int(s.get("replies_count", 0))
        out.append(Doc(source="mastodon", kind="community", outlet=f"Mastodon", author=f"{acct}@{instance}" if "@" not in acct else acct,
                       url=s.get("url") or s.get("uri", ""), title=text[:160], text=text, published=_iso(s.get("created_at")),
                       engagement=eng, links=sorted(set(links)),
                       flags=[] if (s.get("account") or {}).get("bot") is not True else ["bot"]))
    return out


def parse_reddit(payload: str) -> list[Doc]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return []
    out: list[Doc] = []
    for c in (data.get("data") or {}).get("children", []):
        d = c.get("data") or {}
        if d.get("over_18") or d.get("stickied") or d.get("removed_by_category") or d.get("author") in (None, "[deleted]"):
            continue
        if d.get("selftext") in ("[removed]", "[deleted]"):
            continue
        title = _clean(d.get("title", ""))
        text = _clean(d.get("selftext", ""))[:800]
        links = []
        if not d.get("is_self") and d.get("url"):
            links.append(normalise_url(d["url"]))
        score, ratio, ncom = int(d.get("score", 0)), float(d.get("upvote_ratio", 1.0)), int(d.get("num_comments", 0))
        flags = []
        if score < 5 or ratio < 0.55:
            flags.append("low-signal")
        if ncom < 2:
            flags.append("no-discussion")
        out.append(Doc(source="reddit", kind="community", outlet=f"r/{d.get('subreddit', '')}", author=f"u/{d['author']}",
                       url="https://www.reddit.com" + d.get("permalink", ""), title=title, text=f"{title}. {text}".strip(),
                       published=_iso(d.get("created_utc")), engagement=score + 2 * ncom, links=links, flags=flags))
    return out


def parse_x(payload: str) -> list[Doc]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return []
    users = {u["id"]: u for u in (data.get("includes") or {}).get("users", [])}
    out: list[Doc] = []
    for tw in data.get("data", []):
        if tw.get("lang", "en") not in ("en", "hi"):
            continue
        u = users.get(tw.get("author_id"), {})
        handle = u.get("username", "")
        text = _clean(tw.get("text", ""))
        links = [normalise_url(e.get("expanded_url") or e.get("url")) for e in (tw.get("entities") or {}).get("urls", [])
                 if (e.get("expanded_url") or "").find("twitter.com") < 0]
        m = tw.get("public_metrics") or {}
        eng = int(m.get("like_count", 0)) + 2 * int(m.get("retweet_count", 0)) + int(m.get("reply_count", 0)) + int(m.get("quote_count", 0))
        followers = int((u.get("public_metrics") or {}).get("followers_count", 0))
        out.append(Doc(source="x", kind="community", outlet="X", author=f"@{handle}", url=f"https://x.com/{handle}/status/{tw['id']}",
                       title=text[:160], text=text, published=_iso(tw.get("created_at")), engagement=eng, links=sorted(set(links)),
                       flags=["new-account"] if followers < 20 else []))
    return out


# ---------------------------------------------------------------------------
# Step 1 — per-post quality gate

def quality(doc: Doc) -> float:
    """0..1; anything under 0.35 is dropped. Newsrooms start high, forums start lower and earn it."""
    text = doc.text or doc.title
    flags = list(doc.flags)
    if doc.kind in ("news", "government"):
        q = 1.0
        if len(doc.title) < 15:
            q -= 0.4
    else:
        q = 0.6
        if len(text) < MIN_TEXT_CHARS:
            flags.append("too-short"); q = 0.0
        if SPAM_WORDS.search(text):
            flags.append("promo"); q = 0.0
        tags = len(re.findall(r"#\w+", text))
        words = max(len(text.split()), 1)
        if tags > 4 or tags / words > 0.3:
            flags.append("hashtag-wall"); q -= 0.3
        letters = [c for c in text if c.isalpha()]
        if letters and sum(c.isupper() for c in letters) / len(letters) > 0.5 and len(letters) > 20:
            flags.append("shouting"); q -= 0.2
        if len(re.findall(r"https?://", text)) + len(doc.links) > 3:
            flags.append("link-dump"); q -= 0.3
        if len(re.findall(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", text)) > 8:
            flags.append("emoji-wall"); q -= 0.2
        if doc.links and all(urlparse(l).netloc in SHORTENERS for l in doc.links):
            flags.append("shortener-only"); q -= 0.1
        if "low-signal" in flags:
            q -= 0.3
        if "no-discussion" in flags:
            q -= 0.1
        if "bot" in flags or "new-account" in flags:
            q -= 0.25
        if not india_relevant(doc):
            flags.append("not-india"); q = 0.0
        # a forum post that carries a real newsroom / government link is worth more
        if any(classify_domain(l)[0] in ("news", "government") for l in doc.links):
            flags.append("cites-source"); q += 0.3
        if doc.engagement >= 20:
            q += 0.2
        elif doc.engagement >= 5:
            q += 0.1
    doc.flags = flags
    doc.quality = max(0.0, min(1.0, round(q, 2)))
    return doc.quality


# ---------------------------------------------------------------------------
# Step 2 — topic tagging + clustering

def tag_text(title: str, text: str, threshold: int = 3) -> dict[str, int]:
    """Same keyword rules as PRS items: title hits x3, body hits x1."""
    out: dict[str, int] = {}
    for t in TOPICS:
        if t.exclude and re.search(t.exclude, title, re.I):
            continue
        pat = _TOPIC_PATTERNS[t.slug]
        s = 3 * len(pat.findall(title)) + len(pat.findall(text[:800]))
        if s >= threshold:
            out[t.slug] = s
    return out


def tokens(doc: Doc) -> set[str]:
    """Distinctive words: acronyms, years, and content words not in the stop/generic lists."""
    words = _TOKEN.findall(f"{doc.title} {doc.text[:300]}")
    out: set[str] = set()
    for w in words:
        lw = re.sub(r"[’']s$", "", w.lower().strip("’'(),.#"))
        if w.isupper() and len(w) >= 2:
            out.add(lw)
        elif (lw.isdigit() and len(lw) == 4) or (len(lw) > 3 and lw not in _STOP and lw not in _GENERIC):
            out.add(lw)
    return out


def similar(a: set[str], b: set[str]) -> float:
    """Overlap coefficient: short social posts vs long headlines still match on the words they share."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


KIND_RANK = {"government": 0, "news": 1, "community": 2}


def cluster(docs: list[Doc], sim_threshold: float = 0.5, min_shared: int = 3) -> list[list[Doc]]:
    """Greedy star clustering: most credible doc first; join by shared link, else token overlap with the
    cluster's *lead* document. Comparing to the lead (not a growing centroid) stops "GST Council" from
    swallowing every GST story of the month."""
    ordered = sorted(docs, key=lambda d: (KIND_RANK[d.kind], -d.quality, -d.engagement, d.published or "", d.url))
    clusters: list[dict] = []
    for d in ordered:
        toks = tokens(d)
        urls = set(d.links) | {d.url}
        best, best_sim = None, 0.0
        for c in clusters:
            if urls & c["urls"]:
                best, best_sim = c, 1.0
                break
            s = similar(toks, c["toks"])
            if s >= sim_threshold and len(toks & c["toks"]) >= min_shared and s > best_sim:
                best, best_sim = c, s
        if best is None:
            clusters.append(dict(docs=[d], toks=set(toks), urls=set(urls)))
        else:
            best["docs"].append(d)
            best["urls"] |= urls
    return [c["docs"] for c in clusters]


def _collapse_coordinated(docs: list[Doc]) -> tuple[list[Doc], int]:
    """Same text from several accounts -> keep the first, count the rest as coordinated."""
    kept: list[Doc] = []
    seen: list[set[str]] = []
    dropped = 0
    for d in docs:
        if d.kind != "community":
            kept.append(d)
            continue
        norm = set(re.findall(r"\w+", d.text.lower()))
        if any(len(norm & s) / max(len(norm | s), 1) >= 0.9 for s in seen):
            dropped += 1
            continue
        seen.append(norm)
        kept.append(d)
    return kept, dropped


# ---------------------------------------------------------------------------
# Step 3 — credibility

def credibility(docs: list[Doc], coordinated: int = 0) -> str:
    """A single post or article is never a conversation: every shown label needs >= 2 documents."""
    if len(docs) < 2:
        return "thin"
    gov = sum(1 for d in docs if d.kind == "government")
    outlets = {d.outlet for d in docs if d.kind == "news"}
    community = [d for d in docs if d.kind == "community"]
    authors = {d.author for d in community}
    if not gov and not outlets and len(authors) < 2:
        return "thin"                      # one account talking to itself, however many links it shares
    platforms = {d.source for d in community}
    engagement = sum(d.engagement for d in community)
    if gov >= 1 or len(outlets) >= 2:
        return "confirmed"
    if len(outlets) == 1:
        return "reported"
    if coordinated and coordinated >= len(community):
        return "thin"
    if len(authors) >= 3 and (len(platforms) >= 2 or engagement >= 10):
        return "community"
    return "thin"


CRED_RANK = {"confirmed": 0, "reported": 1, "community": 2, "thin": 3}


# ---------------------------------------------------------------------------
# Fetching

def fetch_all(http: _Http | None = None, topics: dict[str, tuple[str, ...]] | None = None) -> list[Doc]:
    http = http or _Http()
    topics = topics or CHATTER_QUERIES
    docs: list[Doc] = []

    def collect(url, parser, *args):
        body = http.get(url)
        if body is None:
            return False
        try:
            if parser in (parse_feed, parse_gnews):
                root = ET.fromstring(body)
                if root.tag.rsplit("}", 1)[-1] not in ("rss", "feed", "RDF"):
                    raise ValueError("Not a feed")
            else:
                payload = json.loads(body)
                if parser == parse_mastodon:
                    valid = isinstance(payload, list)
                else:
                    key = "articles" if parser == parse_gdelt else "posts"
                    valid = isinstance(payload, dict) and isinstance(payload.get(key), list)
                if not valid:
                    raise ValueError("Unexpected API response")
            docs.extend(parser(body, *args))
        except (ValueError, TypeError, KeyError, AttributeError, ET.ParseError) as exc:
            log.warning("Invalid chatter payload from %s: %s", urlparse(url).hostname, type(exc).__name__)
            http.failures.add(urlparse(url).netloc)
            return False
        return True

    for outlet, url in RSS_FEEDS:
        collect(url, parse_feed, outlet)

    for tag in MASTODON_TAGS:
        for inst in MASTODON_INSTANCES:
            collect(f"https://{inst}/api/v1/timelines/tag/{tag}?limit=40&local=false", parse_mastodon, inst)

    for slug, queries in topics.items():
        # GDELT supports OR inside parentheses: one 5.5s request per topic instead of one per phrase
        gq = "(" + " OR ".join(f'"{q}"' for q in queries) + ") sourcecountry:IN"
        collect(GDELT.format(q=quote_plus(gq)), parse_gdelt)
        for q in queries:
            collect(GNEWS.format(q=quote_plus(f'"{q}"')), parse_gnews)
            for tpl in BLUESKY:
                if collect(tpl.format(q=quote_plus(f"{q}")), parse_bluesky):
                    break
    log.info("chatter: %d raw posts/articles from %d requests", len(docs), http.n)
    return docs


# ---------------------------------------------------------------------------
# Persistence

SCHEMA = """
CREATE TABLE IF NOT EXISTS chatter_docs (
    id         TEXT PRIMARY KEY,
    source     TEXT NOT NULL,
    kind       TEXT NOT NULL,          -- government | news | community
    outlet     TEXT NOT NULL,
    author     TEXT NOT NULL,
    url        TEXT NOT NULL UNIQUE,
    title      TEXT NOT NULL,
    text       TEXT NOT NULL,
    published  TEXT,
    engagement INTEGER NOT NULL DEFAULT 0,
    quality    REAL NOT NULL,
    flags      TEXT NOT NULL,          -- JSON list
    links      TEXT NOT NULL,          -- JSON list
    first_seen TEXT NOT NULL DEFAULT (date('now'))
);
CREATE TABLE IF NOT EXISTS chatter_doc_topics (
    doc_id TEXT NOT NULL REFERENCES chatter_docs(id) ON DELETE CASCADE,
    topic  TEXT NOT NULL,
    hits   INTEGER NOT NULL,
    PRIMARY KEY (doc_id, topic)
);
CREATE TABLE IF NOT EXISTS chatter_clusters (
    id          TEXT PRIMARY KEY,
    topic       TEXT NOT NULL,
    label       TEXT NOT NULL,
    label_url   TEXT NOT NULL,
    credibility TEXT NOT NULL,        -- confirmed | reported | community | thin
    n_docs      INTEGER NOT NULL,
    n_outlets   INTEGER NOT NULL,
    n_authors   INTEGER NOT NULL,
    platforms   TEXT NOT NULL,        -- JSON list of sources
    outlets     TEXT NOT NULL,        -- JSON list of newsroom names
    engagement  INTEGER NOT NULL,
    coordinated INTEGER NOT NULL DEFAULT 0,
    first_seen  TEXT,
    last_seen   TEXT,
    item_uid    TEXT,                 -- matching PRS action, if any
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS chatter_members (
    cluster_id TEXT NOT NULL REFERENCES chatter_clusters(id) ON DELETE CASCADE,
    doc_id     TEXT NOT NULL REFERENCES chatter_docs(id) ON DELETE CASCADE,
    PRIMARY KEY (cluster_id, doc_id)
);
CREATE TABLE IF NOT EXISTS chatter_runs (
    run_at     TEXT NOT NULL DEFAULT (datetime('now')),
    n_fetched  INTEGER NOT NULL,
    n_kept     INTEGER NOT NULL,
    n_clusters INTEGER NOT NULL,
    n_shown    INTEGER NOT NULL,
    n_requests INTEGER NOT NULL
);
"""


def store_docs(conn: sqlite3.Connection, docs: list[Doc]) -> int:
    conn.executescript(SCHEMA)
    n = 0
    with conn:
        for d in docs:
            conn.execute(
                """INSERT INTO chatter_docs (id, source, kind, outlet, author, url, title, text, published, engagement, quality, flags, links)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(url) DO UPDATE SET engagement = MAX(engagement, excluded.engagement), quality = excluded.quality,
                                                  flags = excluded.flags, published = COALESCE(published, excluded.published)""",
                (d.id, d.source, d.kind, d.outlet, d.author, d.url, d.title, d.text, d.published, d.engagement, d.quality,
                 json.dumps(d.flags), json.dumps(d.links)))
            conn.execute("DELETE FROM chatter_doc_topics WHERE doc_id = ?", (d.id,))
            for slug, hits in d.topics.items():
                conn.execute("INSERT INTO chatter_doc_topics VALUES (?,?,?)", (d.id, slug, hits))
            n += 1
    return n


def _load_window(conn: sqlite3.Connection, since: date) -> dict[str, list[Doc]]:
    """Docs from the last WINDOW_DAYS (by published date, else first_seen), grouped by topic."""
    by_topic: dict[str, list[Doc]] = {}
    rows = conn.execute(
        """SELECT d.*, t.topic, t.hits FROM chatter_docs d JOIN chatter_doc_topics t ON t.doc_id = d.id
           WHERE d.quality >= 0.35 AND COALESCE(d.published, d.first_seen) BETWEEN ? AND ?""",
        (since.isoformat(), (since + timedelta(days=WINDOW_DAYS)).isoformat())).fetchall()
    for r in rows:
        d = Doc(source=r["source"], kind=r["kind"], outlet=r["outlet"], author=r["author"], url=r["url"], title=r["title"],
                text=r["text"], published=r["published"] or r["first_seen"], engagement=r["engagement"], links=json.loads(r["links"]),
                quality=r["quality"], flags=json.loads(r["flags"]), topics={r["topic"]: r["hits"]})
        by_topic.setdefault(r["topic"], []).append(d)
    return by_topic


def match_prs(conn: sqlite3.Connection, slug: str, label: str, doc_titles: list[str], months: list[str]) -> str | None:
    """Does this conversation track a formal action recorded by PRS for the topic?"""
    if not months:
        return None
    rows = conn.execute(
        f"""SELECT i.uid, i.title FROM items i JOIN item_topics t ON t.uid = i.uid
            WHERE t.topic = ? AND i.month IN ({','.join('?' * len(months))}) ORDER BY i.month DESC""", (slug, *months)).fetchall()
    for r in rows:
        _, toks = build_query(r["title"])
        if _relevant(toks, label) or any(_relevant(toks, t) for t in doc_titles[:6]):
            return r["uid"]
    return None


def refresh(conn: sqlite3.Connection, today: date | None = None, docs: list[Doc] | None = None) -> int:
    """Fetch (unless docs given), gate, tag, store, cluster per topic, store clusters. Returns #clusters shown."""
    conn.executescript(SCHEMA)
    today = today or date.today()
    http = _Http(conn)
    fetched = fetch_all(http) if docs is None else docs
    kept: list[Doc] = []
    seen_urls: set[str] = set()
    for d in fetched:
        d.url = normalise_url(d.url)
        if d.url in seen_urls:
            continue
        seen_urls.add(d.url)
        if quality(d) < 0.35:
            continue
        d.topics = tag_text(d.title, d.text)
        if not d.topics:
            continue
        kept.append(d)
    store_docs(conn, kept)

    since = today - timedelta(days=WINDOW_DAYS)
    months = [r["month"] for r in conn.execute("SELECT month FROM months ORDER BY month DESC LIMIT 4")]
    by_topic = _load_window(conn, since)
    n_clusters = n_shown = 0
    with conn:
        conn.execute("DELETE FROM chatter_clusters")
        conn.execute("DELETE FROM chatter_members")
        for slug, tdocs in by_topic.items():
            for group in cluster(tdocs):
                members, coordinated = _collapse_coordinated(group)
                cred = credibility(members, coordinated)
                lead = members[0]            # already ordered: government > news > community
                cid = hashlib.sha1(f"{slug}:{lead.url}".encode()).hexdigest()[:16]
                dates = sorted(d.published for d in members if d.published)
                item_uid = match_prs(conn, slug, lead.title, [d.title for d in members], months) if cred != "thin" else None
                conn.execute(
                    """INSERT OR REPLACE INTO chatter_clusters (id, topic, label, label_url, credibility, n_docs, n_outlets, n_authors,
                       platforms, outlets, engagement, coordinated, first_seen, last_seen, item_uid) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (cid, slug, lead.title, lead.url, cred, len(members),
                     len({d.outlet for d in members if d.kind == "news"}) + (1 if any(d.kind == "government" for d in members) else 0),
                     len({d.author for d in members if d.kind == "community"}),
                     json.dumps(sorted({d.source for d in members})),
                     json.dumps(sorted({d.outlet for d in members if d.kind in ("news", "government")})),
                     sum(d.engagement for d in members), coordinated, dates[0] if dates else None, dates[-1] if dates else None, item_uid))
                conn.executemany("INSERT OR IGNORE INTO chatter_members VALUES (?,?)", [(cid, d.id) for d in members])
                n_clusters += 1
                n_shown += cred != "thin"
        conn.execute("INSERT INTO chatter_runs (n_fetched, n_kept, n_clusters, n_shown, n_requests) VALUES (?,?,?,?,?)",
                     (len(fetched), len(kept), n_clusters, n_shown, http.n))
        # keep the table from growing forever; the clusters are recomputed from the window anyway
        conn.execute("DELETE FROM chatter_docs WHERE COALESCE(published, first_seen) < ?", ((today - timedelta(days=180)).isoformat(),))
    log.info("chatter: kept %d/%d, %d conversations (%d shown)", len(kept), len(fetched), n_clusters, n_shown)
    if http.failures:
        from .health import IncompleteCollection
        raise IncompleteCollection(
            f"Partial chatter sample: {len(fetched)} documents; {http.n} requests; "
            f"{http.cached} cached responses; unavailable hosts: {', '.join(sorted(http.failures))}")
    return n_shown


# ---------------------------------------------------------------------------
# Read side (build_site / notify)

def by_topic(conn: sqlite3.Connection, limit: int = 8) -> dict[str, dict]:
    """Per topic: shown conversations (most credible first), counts by credibility, and how many run ahead of PRS."""
    conn.executescript(SCHEMA)
    out: dict[str, dict] = {t.slug: dict(clusters=[], counts={"confirmed": 0, "reported": 0, "community": 0}, ahead=0, hidden=0,
                                          platforms=set(), n_posts=0) for t in TOPICS}
    items = {r["uid"]: dict(r) for r in conn.execute("SELECT uid, title, month, action FROM items")}
    rows = conn.execute(
        """SELECT * FROM chatter_clusters WHERE last_seen BETWEEN ? AND ? ORDER BY topic,
           CASE credibility WHEN 'confirmed' THEN 0 WHEN 'reported' THEN 1 WHEN 'community' THEN 2 ELSE 3 END,
           n_docs DESC, engagement DESC, last_seen DESC""",
        ((date.today() - timedelta(days=WINDOW_DAYS)).isoformat(), date.today().isoformat())).fetchall()
    for r in rows:
        d = out.get(r["topic"])
        if d is None:
            continue
        if r["credibility"] == "thin":
            d["hidden"] += 1
            continue
        c = dict(r)
        c["platforms"] = json.loads(r["platforms"])
        c["outlets"] = json.loads(r["outlets"])
        c["item"] = items.get(r["item_uid"]) if r["item_uid"] else None
        c["members"] = [dict(m) for m in conn.execute(
            """SELECT d.source, d.kind, d.outlet, d.author, d.url, d.title, d.published, d.engagement FROM chatter_members m
               JOIN chatter_docs d ON d.id = m.doc_id WHERE m.cluster_id = ?
               ORDER BY CASE d.kind WHEN 'government' THEN 0 WHEN 'news' THEN 1 ELSE 2 END, d.engagement DESC LIMIT 6""", (r["id"],))]
        d["counts"][r["credibility"]] += 1
        d["ahead"] += r["item_uid"] is None
        d["platforms"].update(c["platforms"])
        d["n_posts"] += r["n_docs"]
        if len(d["clusters"]) < limit:
            d["clusters"].append(c)
    for d in out.values():
        d["platforms"] = sorted(d["platforms"])
        d["total"] = sum(d["counts"].values())
    return out


def last_run(conn: sqlite3.Connection) -> dict | None:
    conn.executescript(SCHEMA)
    r = conn.execute("SELECT * FROM chatter_runs ORDER BY run_at DESC LIMIT 1").fetchone()
    return dict(r) if r else None
