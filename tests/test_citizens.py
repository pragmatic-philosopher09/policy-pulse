import json
from datetime import date, timedelta

import pytest
import requests

from radar import citizens as c, db
from radar.chatter import Doc
from radar.build_site import _env

TODAY = date(2026, 9, 30)


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "citizens.sqlite")
    connection.executescript(c.SCHEMA)
    yield connection
    connection.close()


def doc(n=1, author=None, text=None, source="reddit", published=None):
    return Doc(source, "community", "r/india", author or f"account{n}",
               f"https://www.reddit.com/r/india/comments/p{n}/title" if source == "reddit"
               else f"https://x.com/account/status/{n}",
               "", text or f"I support labour codes because implementation needs clarity for workers, example {n}.",
               published or TODAY.isoformat(), engagement=20)


def work(snapshot):
    return next(p for p in snapshot["policies"] if p["name"] == "labour codes")


def test_idempotent_days_and_null_gaps(conn):
    c.record_batch(conn, "reddit", "work", [doc()], "ok", TODAY)
    c.record_batch(conn, "reddit", "work", [doc()], "ok", TODAY)
    tomorrow = TODAY + timedelta(days=1)
    c.record_batch(conn, "reddit", "work", [doc()], "ok", tomorrow)
    c.record_batch(conn, "x", "work", [], "rate_limited", tomorrow)
    policy = work(c.snapshot(conn, tomorrow))
    assert policy["total"] == 1
    assert policy["days"][-2]["reddit"]["count"] == 1
    assert policy["days"][-1]["reddit"]["count"] == 0
    assert policy["days"][-1]["x"]["count"] is None
    assert policy["days"][0]["reddit"]["count"] is None
    assert len(policy["days"]) == 30
    assert conn.execute("SELECT accepted FROM citizen_runs WHERE day=? AND platform='reddit'", (TODAY.isoformat(),)).fetchone()[0] == 1


def test_distinct_accounts_repeated_text_and_safe_export(conn):
    c.record_batch(conn, "reddit", "work",
                   [doc(), doc(2, author="account1"), doc(3, text=doc().text),
                    doc(4), doc(5)], "ok", TODAY)
    data = c.snapshot(conn, TODAY)
    policy = work(data)
    assert policy["total"] == 3
    assert {t["label"] for t in policy["themes"]} == {"support", "implementation"}
    assert all(t["accounts"] == 3 for t in policy["themes"])
    serialized = json.dumps(data)
    assert "account1" not in serialized and "author_key" not in serialized
    assert "I support" not in serialized and "/r/india" not in serialized
    assert c.post_url(doc(source="x")) == "https://x.com/i/status/1"
    assert conn.execute("SELECT url FROM citizen_posts LIMIT 1").fetchone()[0].startswith("https://www.reddit.com/comments/")


def test_scope_spam_future_and_unknown_dates(conn):
    candidates = [
        doc(1, text="I support labour codes, get this giveaway click here to earn money"),
        doc(2, published=(TODAY + timedelta(days=1)).isoformat()),
        doc(3, published="bad"),
        doc(4, published=(TODAY - timedelta(days=30)).isoformat()),
        doc(5, text="I support the local swimming club and its implementation this month."),
    ]
    malicious = doc(6)
    malicious.url = "javascript:alert(1)"
    candidates.append(malicious)
    c.record_batch(conn, "reddit", "work", candidates, "ok", TODAY)
    assert work(c.snapshot(conn, TODAY))["total"] == 0


def test_viewpoints_are_conservative_and_policy_specific(conn):
    assert "support" not in c.labels_for("I don't support labour codes.")
    assert "opposition" in c.labels_for("I don't support labour codes.")
    assert "support" not in c.labels_for('The minister said "I support labour codes".')
    assert "support" not in c.labels_for("If I support labour codes I would lose my job.")
    c.record_batch(conn, "reddit", "work", [
        doc(n, text=f"I support the sports team. Labour codes cost too much in my district {n}.")
        for n in range(3)], "ok", TODAY)
    assert [t["label"] for t in work(c.snapshot(conn, TODAY))["themes"]] == ["cost"]


def test_small_sample_and_window(conn):
    c.record_batch(conn, "reddit", "work", [doc(), doc(2)], "ok", TODAY)
    assert work(c.snapshot(conn, TODAY))["themes"] == []
    assert work(c.snapshot(conn, TODAY + timedelta(days=30)))["total"] == 0
    assert work(c.snapshot(conn, TODAY - timedelta(days=1)))["total"] == 0


class Response:
    def __init__(self, status=200, headers=None, payload=None):
        self.status_code = status
        self.headers = requests.structures.CaseInsensitiveDict(headers or {})
        self.payload = payload if payload is not None else {"meta": {"result_count": 0}}

    def json(self):
        return self.payload


@pytest.mark.parametrize("headers", [
    {"Retry-After": "7200"},
    {"Retry-After": "Wed, 30 Sep 2026 03:00:00 GMT"},
    {"x-rate-limit-reset": "1790737200"},
])
def test_cooldown_is_persistent_no_early_retries(conn, monkeypatch, headers):
    now = 1790730000
    monkeypatch.setattr(c.time, "time", lambda: now)
    client = c.Collector(conn)
    calls = []
    monkeypatch.setattr(client.session, "request", lambda *a, **kw: calls.append(kw) or Response(429, headers))
    with pytest.raises(c.CollectionError, match="rate_limited"):
        client.request("x", "GET", "https://api.x.com")
    assert len(calls) == 1
    another = c.Collector(conn)
    monkeypatch.setattr(another.session, "request", lambda *a, **kw: pytest.fail("must honor saved cooldown"))
    with pytest.raises(c.CollectionError, match="rate_limited"):
        another.request("x", "GET", "https://api.x.com")
    assert conn.execute("SELECT until_at FROM citizen_cooldowns").fetchone()[0] > now
    client.session.close()
    another.session.close()


def test_zero_remaining_and_budget(conn, monkeypatch):
    client = c.Collector(conn)
    monkeypatch.setattr(client.session, "request", lambda *a, **kw: Response(headers={"x-rate-limit-remaining": "0"}))
    assert client.request("x", "GET", "https://api.x.com")["meta"]["result_count"] == 0
    with pytest.raises(c.CollectionError, match="rate_limited"):
        client.request("x", "GET", "https://api.x.com")
    client.n = 17
    with pytest.raises(c.CollectionError, match="budget"):
        client.request("reddit", "GET", "https://oauth.reddit.com")
    client.session.close()


def test_credentials_absent_no_network(conn, monkeypatch):
    for key in ("X_BEARER_TOKEN", "REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(requests.Session, "request", lambda *a, **kw: pytest.fail("no unauthenticated fallback"))
    c.refresh(conn, TODAY)
    data = c.snapshot(conn, TODAY)
    assert all(s["status"] == "not_configured" for d in data["domains"] for s in d["sources"].values())
    assert all(p["total"] == 0 for p in data["policies"])


def test_partial_failure_and_successful_rerun_skip(conn, monkeypatch):
    monkeypatch.setenv("X_BEARER_TOKEN", "test-token")
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.setattr(c.time, "sleep", lambda _: None)
    calls = []

    def request(self, method, url, **kw):
        calls.append(kw)
        if len(calls) == 2:
            return Response(503)
        return Response()

    monkeypatch.setattr(requests.Session, "request", request)
    c.refresh(conn, TODAY)
    data = c.snapshot(conn, TODAY)
    assert data["domains"][0]["sources"]["x"]["status"] == "ok"
    assert data["domains"][1]["sources"]["x"]["status"] == "unavailable"
    assert len(calls) == 2
    c.refresh(conn, TODAY)
    assert len(calls) == 9  # seven unsuccessful domains retried; successful domain skipped


def test_approved_connectors_collect_and_bound_queries(conn, monkeypatch):
    monkeypatch.setenv("X_BEARER_TOKEN", "x-test")
    monkeypatch.setenv("REDDIT_CLIENT_ID", "client-test")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret-test")
    monkeypatch.setattr(c.time, "sleep", lambda _: None)
    calls = []

    def request(self, method, url, **kw):
        calls.append((method, url))
        assert kw["allow_redirects"] is False and kw["timeout"] == 20
        if method == "POST":
            assert kw["auth"] == ("client-test", "secret-test")
            return Response(payload={"access_token": "reddit-test"})
        query = kw["params"].get("q", kw["params"].get("query"))
        assert len(query) <= 512
        if "oauth.reddit.com" in url:
            assert kw["headers"]["Authorization"] == "Bearer reddit-test"
            assert kw["params"]["limit"] == 25 and kw["params"]["restrict_sr"] == 1
            return Response(payload={"data": {"children": [{"data": {
                "title": "I oppose labour codes because compliance burdens small workers and firms",
                "author": "citizen", "subreddit": "india", "permalink": "/r/india/comments/abc/title",
                "created_utc": 1790726400, "score": 20, "upvote_ratio": .9, "num_comments": 4,
            }}]}})
        assert kw["headers"]["Authorization"] == "Bearer x-test"
        assert kw["params"]["max_results"] == 25
        assert "-is:retweet -is:reply" in query and "(India OR Indian)" in query
        return Response(payload={"meta": {"result_count": 1}, "data": [{
            "id": "123", "author_id": "456", "lang": "en", "created_at": "2026-09-30T01:00:00Z",
            "text": "In India I support labour codes because clear implementation helps workers.",
        }], "includes": {"users": [{"id": "456", "username": "person"}]}})

    monkeypatch.setattr(requests.Session, "request", request)
    c.refresh(conn, TODAY)
    assert len(calls) == 17
    data = c.snapshot(conn, TODAY)
    assert work(data)["platforms"] == {"reddit": 1, "x": 1}
    assert work(data)["themes"] == []
    assert all(source["status"] == "ok" for d in data["domains"] for source in d["sources"].values())
    assert "person" not in json.dumps(data)


@pytest.mark.parametrize("payload", [
    {"unexpected": []}, {"meta": {"result_count": 1}},
    {"meta": {"result_count": -1}}, {"meta": {"result_count": 0}, "errors": [{"title": "Partial failure"}]},
])
def test_invalid_payload_does_not_become_success(conn, monkeypatch, payload):
    monkeypatch.setenv("X_BEARER_TOKEN", "test")
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.setattr(c.time, "sleep", lambda _: None)
    monkeypatch.setattr(requests.Session, "request", lambda *a, **kw: Response(payload=payload))
    c.refresh(conn, TODAY)
    assert all(d["sources"]["x"]["status"] == "invalid_response" for d in c.snapshot(conn, TODAY)["domains"])


@pytest.mark.parametrize("lang", ["en", "hi"])
def test_render_page_and_escaped_links(conn, lang):
    for n in range(3):
        c.record_batch(conn, "reddit", "work", [doc(n)], "ok", TODAY)
    rendered = _env(lang).get_template("citizens.html").render(
        citizens=c.snapshot(conn, TODAY), root="", page="citizens.html",
        week="2026-W40", other_langs=[], scores=[], images={}, today=TODAY.isoformat())
    assert "citizens.json" in rendered
    assert "citizen_status_" not in rendered
    assert "citizen_label_" not in rendered
    assert "https://www.reddit.com/comments/" in rendered
    assert "I support" not in rendered
    assert "2026-09-30" in rendered
