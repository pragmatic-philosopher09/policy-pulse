"""CLI entry point.

  python -m radar ingest --months 18      # backfill / refresh PRS monthly reviews
  python -m radar summarise               # fill missing plain-English summaries
  python -m radar translate               # Hindi (etc.) titles/summaries via Claude, cached
  python -m radar crosscheck              # independent coverage per item (Google News: PIB, newspapers)
  python -m radar chatter                 # newsroom feeds, Google News, GDELT, Bluesky, Mastodon
  python -m radar citizens                # credential-only X/Reddit discussion sampling
  python -m radar build                   # render static site into docs/
  python -m radar notify --digest|--pings # Telegram channel
  python -m radar run --months 18         # all three
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import date

from . import db
from .fetch import fetch
from .parse import iso_month, month_url, parse_month
from .score import classify_action, tag_impacts, tag_topics
from .summarize import summarise_missing
from .translate import translate_missing, visible_uids
from .crosscheck import crosscheck
from .enrich import enrich_missing
from .announcements import refresh as refresh_announcements
from .states import refresh as refresh_states
from .chatter import refresh as refresh_chatter
from .citizens import refresh as refresh_citizens

log = logging.getLogger("radar")


def _month_iter(n: int, start: date | None = None):
    d = (start or date.today()).replace(day=1)
    for _ in range(n):
        yield d
        d = (d.replace(month=d.month - 1) if d.month > 1 else d.replace(year=d.year - 1, month=12))


def ingest(months: int, refresh_latest: int = 2) -> None:
    """Fetch the last ``months`` reviews. Only the newest ``refresh_latest`` are re-fetched."""
    conn = db.connect()
    have = set(db.months_present(conn))
    for i, d in enumerate(_month_iter(months)):
        ym = iso_month(d)
        force = i < refresh_latest
        if ym in have and not force:
            continue
        url = month_url(d)
        html = fetch(url, force=force)
        if html is None:
            log.info("%s not published yet", ym)
            continue
        items = parse_month(html, d, source_url=url)
        if not items:
            log.warning("%s parsed to zero items — layout change?", ym)
            continue
        actions = {it.uid: classify_action(it.title) for it in items}
        topics = {it.uid: tag_topics(it) for it in items}
        db.upsert_month(conn, ym, url, items, actions, topics)
        tagged = sum(1 for t in topics.values() if t)
        log.info("%s: %d items, %d tagged to a topic", ym, len(items), tagged)

    # Re-apply current keyword/action rules to everything so config edits propagate
    n = db.retag_all(conn, classify_action, tag_topics, tag_impacts)
    log.info("retagged %d stored items", n)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="radar")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("ingest", "run"):
        s = sub.add_parser(name)
        s.add_argument("--months", type=int, default=18)
    sub.add_parser("retag", help="re-apply topic/action rules to stored items")
    sub.add_parser("summarise")
    sub.add_parser("translate", help="translate visible items into other languages (needs ANTHROPIC_API_KEY)")
    sub.add_parser("announcements", help="refresh the live list of drafts open for comment (prsindia.org/announcements)")
    sub.add_parser("states", help="state legislature Bills (prsindia.org/bills/states) for this and last year")
    sub.add_parser("enrich", help="hook headlines + persona 'so what' lines (seed file, Claude for new items)")
    sub.add_parser("crosscheck", help="look for independent coverage (govt releases, newspapers) of visible items")
    sub.add_parser("chatter", help="public chatter: whitelisted news feeds, GDELT, Bluesky, Mastodon; clustered + credibility-gated")
    sub.add_parser("build")
    sub.add_parser("citizens", help="sample X and Reddit policy discussions using approved API credentials")
    n = sub.add_parser("notify", help="post to Telegram (dry run without TELEGRAM_BOT_TOKEN)")
    n.add_argument("--digest", action="store_true")
    n.add_argument("--pings", action="store_true")
    args = p.parse_args(argv)

    if args.cmd in ("ingest", "run"):
        ingest(args.months)
    if args.cmd == "retag":
        log.info("retagged %d items", db.retag_all(db.connect(), classify_action, tag_topics, tag_impacts))
    if args.cmd in ("summarise", "run"):
        n = summarise_missing(db.connect())
        log.info("summarised %d items", n)
    if args.cmd in ("translate", "run"):
        from .i18n import DEFAULT_LANG, LANGS
        conn = db.connect()
        for lang in LANGS:
            if lang != DEFAULT_LANG:
                log.info("translated %d items into %s", translate_missing(conn, lang), lang)
    if args.cmd in ("announcements", "run"):
        log.info("announcements: %d rows", refresh_announcements(db.connect()))
    if args.cmd in ("states", "run"):
        log.info("state bills: %d rows", refresh_states(db.connect()))
    if args.cmd in ("enrich", "run"):
        conn = db.connect()
        log.info("enriched %d items", enrich_missing(conn, visible_uids(conn)))
    if args.cmd in ("crosscheck", "run"):
        conn = db.connect()
        log.info("cross-checked %d items", crosscheck(conn, visible_uids(conn)))
    if args.cmd == "chatter" or (args.cmd == "run" and not os.environ.get("POLICY_PULSE_SKIP_CHATTER")):
        try:
            log.info("chatter: %d conversations shown", refresh_chatter(db.connect()))
        except Exception:   # a third-party API outage must never block the weekly issue
            log.exception("chatter refresh failed; building with the last stored conversations")
    if args.cmd in ("citizens", "run"):
        refresh_citizens(db.connect())
    if args.cmd in ("build", "run"):
        from .build_site import build
        build()
    if args.cmd == "notify":
        from .build_site import assemble
        from .notify import notify
        conn = db.connect()
        if args.pings:
            refresh_announcements(conn)  # new drafts appear between weekly runs
        scores, consultations = assemble(conn, lang="en")
        log.info("sent %d message(s)", notify(conn, scores, consultations, digest=args.digest, pings=args.pings))


if __name__ == "__main__":
    main()
