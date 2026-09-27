from datetime import date

from radar.parse import Item, parse_month
from radar.score import classify_action, tag_topics

SAMPLE = """
<html><body><div class="view-content">
<p><strong><span style="color:#3366ff">Highlights of this Issue</span></strong></p>
<p><strong><span style="color:#3366ff">Recap item that must be skipped</span></strong></p>
<div style="border-bottom:solid #3366ff 1.0pt"><p><strong>Finance</strong></p></div>
<p><strong><span style="color:#3366ff">Parliament passes the Income Tax Bill, 2026</span></strong></p>
<p><em>Someone (someone@prsindia.org)</em></p>
<p>The Income Tax Bill, 2026 was passed.<a href="#_edn1">[1]</a> It replaces the 1961 Act.
<a href="/billtrack/income-tax-bill-2026">here</a></p>
<ul><li><p>Slabs unchanged.</p></li></ul>
<div style="border-bottom:solid #3366ff 1.0pt"><p><strong>Education</strong></p></div>
<p><strong><span style="color:#3366ff">Draft rules on public examinations released for comments</span></strong></p>
<p>The Ministry released draft rules to curb paper leaks in NEET and other exams.</p>
<p><a name="_edn1"></a>[1] Endnote</p>
</div></body></html>
"""


def test_parse_skips_highlights_and_splits_sectors():
    items = parse_month(SAMPLE, date(2026, 8, 1), "u")
    assert [i.title for i in items] == [
        "Parliament passes the Income Tax Bill, 2026",
        "Draft rules on public examinations released for comments",
    ]
    assert items[0].sector == "Finance" and items[1].sector == "Education"
    assert "[1]" not in items[0].body
    assert "Slabs unchanged." in items[0].body
    assert items[0].links == ["https://prsindia.org/billtrack/income-tax-bill-2026"]
    assert items[0].month == "2026-08"


def test_classify_action_priority():
    assert classify_action("Parliament passes the Income Tax Bill") == "enacted"
    assert classify_action("Draft rules on public examinations released for comments") == "consultation"
    assert classify_action("Standing Committee submits report on gig workers") == "committee"
    assert classify_action("Cabinet approves new scheme") == "cabinet"
    assert classify_action("GDP grows 7.8%") == "other"


def test_tag_topics():
    it = Item("2026-08", "Education", "Draft rules on public examinations released",
              "Rules to curb paper leaks in NEET.")
    tags = tag_topics(it)
    assert "education" in tags
    assert "work" not in tags


def test_tag_impacts_lens():
    from radar.score import tag_impacts
    it = Item("2026-03", "Information Technology", "Standing Committee submits report on Impact of AI",
              "The committee examined artificial intelligence and jobs, skilling of workers and school curricula.")
    imps = tag_impacts(it, {"digital-and-ai": 20}, {"student": "x", "founder": "y"})
    assert "digital" in imps            # domain default
    assert "education" in imps          # persona-derived
    assert "business" in imps           # persona-derived
    it2 = Item("2026-08", "Finance", "RBI maintains repo rate at 5.25%", "The MPC kept the policy repo rate unchanged.")
    assert tag_impacts(it2, {"money": 9}) == ["money"]


def test_extractive_summary_respects_abbreviations():
    from radar.summarize import extractive
    body = ("The Committee (Chair: Dr. A. Sharma) presented its report on Cyber Crimes. "
            "It recommended Rs. 500 crore. Third sentence.")
    out = extractive("t", body)
    assert out.startswith("The Committee (Chair: Dr. A. Sharma) presented its report on Cyber Crimes.")
    assert not out.endswith("Dr.")


def test_parse_deadline_variants():
    from datetime import date
    from radar.build_site import parse_deadline, _deadline
    assert parse_deadline("September 4, 2026") == date(2026, 9, 4)
    assert parse_deadline("4 September 2026") == date(2026, 9, 4)
    assert parse_deadline("4th September, 2026") == date(2026, 9, 4)
    assert parse_deadline(None) is None
    assert _deadline("Comments are invited till August 7, 2026.") == "August 7, 2026"
    assert _deadline("The draft was released.") is None


def test_crosscheck_query_and_relevance():
    from radar.crosscheck import build_query, classify, _relevant
    q, tok = build_query("Parliament passed the Public Examinations (Prevention of Unfair Means) Amendment Bill, 2026")
    assert q.startswith('"') and "Unfair Means" in q
    assert {"public", "examinations", "unfair", "means"} <= tok
    assert _relevant(tok, "Lok Sabha passes anti-paper-leak Public Examinations Bill")
    assert not _relevant(tok, "Formula 2 rules and regulations updated for 2026")
    assert classify("https://pib.gov.in/PressReleasePage.aspx?PRID=1")[0] == "government"
    assert classify("https://www.thehindu.com/news/x.ece") == ("news", "The Hindu")
    assert classify("https://prsindia.org/billtrack/x")[0] == "exclude"
    assert classify("https://someblog.example.com/post")[0] == "other"


def test_digest_composes_without_network():
    from datetime import date
    from radar.config import TOPICS
    from radar.notify import compose_digest, compose_pings
    from radar.score import TopicScore, Evidence
    ev = Evidence("u1", "2026-08", "Finance", "RBI maintains repo rate", "other", 1.0, None, [], "https://prsindia.org/x",
                  hook="Repo on hold — your EMI isn't moving.")
    ev.corroborations = [{"kind": "government", "outlet": "PIB", "url": "https://pib.gov.in", "title": "", "published": None}]
    ts = TopicScore(TOPICS[0], ["2026-07", "2026-08"], [0, 1.0], [0, 1], 0.5, 0.2, 150, 1, 50, "heating", [ev],
                    confidence="low", why=[ev])
    cons = {"open": [dict(uid="c1", title="Draft X", hook="Should X change?", deadline=date(2026, 10, 9), days_left=5,
                          links=["https://example.gov.in/draft.pdf"], source_url="https://prsindia.org/y",
                          route=dict(body="RBI", url="", how="rbi"))], "unknown": [], "closed": []}
    text = compose_digest([ts], cons, date(2026, 9, 28))
    assert "Policy Pulse" in text and "Should X change?" in text and "Repo on hold" in text and "✓" in text
    pings = compose_pings(cons)
    assert len(pings) == 1 and pings[0][0] == "ping:c1:7d" and "5 days left" in pings[0][1]


def test_announcements_parse():
    from datetime import date
    from radar.announcements import parse
    html = """<div class="view-content"><table><thead><tr><th>Comments invited on</th><th>Deadline for submission</th>
    <th>Press Release</th><th>PRS Analysis</th></tr></thead><tbody>
    <tr><td><a href="/files/x.pdf">The Indian Statistical Institute Bill, 2026</a></td><td>Sep 28,2026</td>
    <td><a href="https://sansad.in/pr">Press Release</a></td><td></td></tr>
    <tr><td>Draft SHANTI Rules</td><td>Sep 04,2026</td><td><a href="https://dae.gov.in/x">Press Release</a></td><td><a href="">   </a></td></tr>
    </tbody></table></div>"""
    rows = parse(html)
    assert rows[0]["title"] == "The Indian Statistical Institute Bill, 2026"
    assert rows[0]["deadline"] == date(2026, 9, 28)
    assert rows[0]["draft_url"] == "https://prsindia.org/files/x.pdf" and rows[0]["press_url"] == "https://sansad.in/pr"
    assert rows[1]["analysis_url"] is None


def test_notice_headline_deadline_and_likely_closed():
    from datetime import date
    from radar.notice import deadline_from_headlines, apply
    hits = [{"kind": "news", "outlet": "SCC Online", "title": "BCI Releases Draft Advocates (Amendment) Bill, 2026; Invites Suggestions Till 31 July"}]
    d, src = deadline_from_headlines(hits, "2026-07")
    assert d == date(2026, 7, 31) and src == "SCC Online"
    e = dict(uid="x", month="2026-07", deadline=None, corroborations=[], route=dict(body="", url="", how="generic"))
    apply(e, date(2026, 9, 27))
    assert e["likely_closed"] is True and e["deadline"] is None


def test_state_bills_parse():
    from radar.states import parse_list
    html = """<div class="view-content">
    <div class="views-row"><div class="views-field views-field-title-field"><span><h3 class="file">
    <a href="/files/bills_acts/bills_states/karnataka/2025/Bill1of2025KA.pdf">The Karnataka Platform Based Gig Workers (Social Security and Welfare) Bill, 2025</a>
    </h3></span></div><div class="views-field views-field-field-bill-status"><span class="status-pending">Karnataka</span></div></div>
    </div>"""
    rows = parse_list(html)
    assert rows[0]["state"] == "Karnataka" and rows[0]["year"] == 2025
    assert rows[0]["url"].startswith("https://prsindia.org/files/") and rows[0]["title"].startswith("The Karnataka Platform")


# ---------------------------------------------------------------------------
# Public chatter layer (radar/chatter.py) — no network

from radar import chatter as ch


def _doc(kind, outlet, url, title, text="", author=None, source=None, links=(), engagement=0):
    return ch.Doc(source=source or ("rss" if kind != "community" else "bluesky"), kind=kind, outlet=outlet,
                  author=author or outlet, url=url, title=title, text=text or title, published="2026-09-20",
                  engagement=engagement, links=list(links))


def test_chatter_quality_gate_drops_spam_and_non_india():
    spam = _doc("community", "Bluesky", "https://bsky.app/p/1", "", "GIVEAWAY!!! earn money click here #crypto #india #free #win #now", author="a")
    assert ch.quality(spam) == 0.0 and "promo" in spam.flags
    ohio = _doc("community", "Bluesky", "https://bsky.app/p/2", "", "Ohio taxpayers face a $1,670 deportation bill and a smaller workforce this year", author="b")
    assert ch.quality(ohio) == 0.0 and "not-india" in ohio.flags
    ok = _doc("community", "Bluesky", "https://bsky.app/p/3", "", "The DPDP rules draft is finally out and MeitY wants comments; consent managers look weak.",
              author="c", links=["https://www.thehindu.com/news/x"], engagement=12)
    assert ch.quality(ok) >= 0.8 and "cites-source" in ok.flags
    news = _doc("news", "Mint", "https://livemint.com/a", "Centre notifies draft DPDP Rules, invites comments")
    assert ch.quality(news) == 1.0


def test_chatter_cluster_and_credibility():
    a = _doc("news", "The Hindu", "https://thehindu.com/a", "Centre notifies draft DPDP Rules, seeks comments on data protection")
    b = _doc("news", "Mint", "https://livemint.com/b", "Draft DPDP Rules notified: what the data protection rules mean for users")
    c = _doc("community", "Bluesky", "https://bsky.app/p/9", "DPDP rules draft is out. Data protection for consent managers looks weak.",
             author="x.bsky", links=["https://thehindu.com/a"])
    d = _doc("news", "Indian Express", "https://indianexpress.com/d", "Cabinet raises EPFO wage ceiling to Rs 25,000 from Rs 15,000")
    groups = ch.cluster([a, b, c, d])
    assert sorted(len(g) for g in groups) == [1, 3]
    big = max(groups, key=len)
    assert big[0].kind == "news"                       # most credible doc leads the cluster
    assert ch.credibility(big) == "confirmed"          # two newsrooms
    assert ch.credibility([d]) == "thin"               # one article is not a conversation
    assert ch.credibility([a, c]) == "reported"        # one newsroom + a post
    # a lone account sharing links is thin; three accounts with engagement are 'community'
    posts = [_doc("community", "Bluesky", f"https://bsky.app/p/{i}", "NEET PG counselling delayed again, MCC silent, students in Delhi protest",
                  author=f"u{i}", engagement=5) for i in range(3)]
    assert ch.credibility(posts[:2]) == "thin"
    assert ch.credibility(posts) == "community"
    assert ch.credibility([posts[0], _doc("community", "Bluesky", "https://bsky.app/p/x", posts[0].title, author="u0", links=["https://livemint.com/z"])]) == "thin"
    # a forum post linking a newsroom counts that newsroom
    assert ch.credibility([posts[0], _doc("community", "Bluesky", "https://bsky.app/p/y", posts[0].title, author="u9", links=["https://livemint.com/z"])]) == "reported"


def test_chatter_collapses_coordinated_posts():
    text = "Join the movement: the labour codes will destroy gig workers in India, share widely"
    posts = [_doc("community", "Bluesky", f"https://bsky.app/p/{i}", text, author=f"bot{i}") for i in range(4)]
    kept, dropped = ch._collapse_coordinated(posts)
    assert len(kept) == 1 and dropped == 3
    assert ch.credibility(kept, coordinated=dropped) == "thin"


RSS = """<rss><channel><item><title>Cabinet raises EPFO wage ceiling</title><link>https://indianexpress.com/x?utm_source=rss</link>
<description><![CDATA[<p>The ceiling goes to Rs 25,000.</p>]]></description><pubDate>Sat, 20 Sep 2026 10:00:00 +0530</pubDate></item></channel></rss>"""
ATOM = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Draft DPDP Rules out</title><link href="https://scroll.in/y"/>
<updated>2026-09-21T08:00:00Z</updated><summary>Comments open.</summary></entry></feed>"""
GNEWS = """<rss><channel><item><title>GST Council to meet on Sept 12 - business-standard.com</title>
<link>https://news.google.com/rss/articles/abc</link><pubDate>Mon, 22 Sep 2026 05:00:00 GMT</pubDate>
<source url="https://www.business-standard.com">business-standard.com</source></item>
<item><title>Something from a blog - Blog</title><link>https://news.google.com/rss/articles/def</link>
<source url="https://randomblog.example">Blog</source></item></channel></rss>"""


def test_chatter_feed_parsers():
    rss = ch.parse_feed(RSS, "Indian Express")
    assert rss[0].url == "https://indianexpress.com/x" and rss[0].published == "2026-09-20" and rss[0].kind == "news"
    assert "Rs 25,000" in rss[0].text
    atom = ch.parse_feed(ATOM, "Scroll")
    assert atom[0].url == "https://scroll.in/y" and atom[0].published == "2026-09-21"
    gn = ch.parse_gnews(GNEWS)
    assert len(gn) == 1 and gn[0].outlet == "Business Standard" and gn[0].title == "GST Council to meet on Sept 12"
    bsky = ch.parse_bluesky('{"posts":[{"uri":"at://did/app.bsky.feed.post/k1","author":{"handle":"h.bsky"},"likeCount":3,"repostCount":1,'
                            '"record":{"text":"UGC draft regulations for Indian universities","createdAt":"2026-09-19T00:00:00Z","langs":["en"]}},'
                            '{"uri":"at://did/app.bsky.feed.post/k2","author":{"handle":"r.bsky"},"record":{"text":"reply","reply":{}}}]}')
    assert len(bsky) == 1 and bsky[0].url == "https://bsky.app/profile/h.bsky/post/k1" and bsky[0].engagement == 5
    reddit = ch.parse_reddit('{"data":{"children":[{"data":{"title":"Labour codes finally notified","selftext":"thoughts?","author":"u1","subreddit":"india",'
                             '"permalink":"/r/india/1","score":42,"upvote_ratio":0.9,"num_comments":10,"created_utc":1789000000,"is_self":true}},'
                             '{"data":{"title":"x","author":"[deleted]","subreddit":"india","permalink":"/r/india/2"}}]}}')
    assert len(reddit) == 1 and reddit[0].outlet == "r/india" and reddit[0].engagement == 62 and not reddit[0].flags


def test_chatter_refresh_offline_and_by_topic(tmp_path):
    from radar import db
    conn = db.connect(tmp_path / "t.sqlite")
    with conn:
        conn.execute("INSERT INTO months (month, source_url, n_items) VALUES ('2026-08', 'u', 1)")
        conn.execute("INSERT INTO items (uid, month, sector, title, body, links, source_url, action) VALUES "
                     "('i1', '2026-08', 'Labour', 'Cabinet approves raising the EPFO wage ceiling to Rs 25,000', '', '[]', 'u', 'cabinet')")
        conn.execute("INSERT INTO item_topics VALUES ('i1', 'work', 9)")
    docs = [
        _doc("news", "Indian Express", "https://indianexpress.com/d", "Cabinet raises EPFO wage ceiling to Rs 25,000 from Rs 15,000"),
        _doc("news", "Economic Times", "https://economictimes.indiatimes.com/e", "EPFO wage ceiling raised to Rs 25,000: what changes for salaried workers"),
        _doc("news", "Mint", "https://livemint.com/solo", "Employment data: PLFS shows urban jobs steady"),   # single article -> hidden
        _doc("community", "Bluesky", "https://bsky.app/p/1", "", "Ohio minimum wage bill passes the state senate", author="us"),
    ]
    shown = ch.refresh(conn, today=date(2026, 9, 27), docs=docs)
    assert shown == 1
    bt = ch.by_topic(conn)
    work = bt["work"]
    assert work["total"] == 1 and work["counts"]["confirmed"] == 1 and work["hidden"] == 1
    c = work["clusters"][0]
    assert c["n_docs"] == 2 and c["item_uid"] == "i1" and c["item"]["title"].startswith("Cabinet approves")
    assert work["ahead"] == 0
    assert ch.last_run(conn)["n_fetched"] == 4
