from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import requests

from radar import db, health, chatter, crosscheck, announcements, states


@pytest.fixture
def conn(tmp_path):
    with db.connect(tmp_path / "health.sqlite") as connection:
        yield connection


def test_health_preserves_success_and_never_invents_history(conn):
    now = datetime(2026, 10, 3, tzinfo=timezone.utc)
    assert all(row["status"] == "never" and row["stale"] for row in health.snapshot(conn, now)["sources"])
    health.record(conn, "chatter", "ok", "done", now)
    health.record(conn, "chatter", "partial", "rate limited", now + timedelta(days=1))
    row = next(r for r in health.snapshot(conn, now + timedelta(days=3))["sources"] if r["source"] == "chatter")
    assert row["successful_at"] == now.isoformat()
    assert row["status"] == "partial" and row["stale"]


def test_stage_isolation_and_redacted_error(conn):
    def broken():
        raise ValueError("https://private.example/?token=secret")
    assert not health.run_stage(conn, "prs", broken)
    assert health.run_stage(conn, "citizens", lambda: "not_configured")
    rows = {r["source"]: r for r in health.snapshot(conn)["sources"]}
    assert "secret" not in rows["prs"]["detail"]
    assert rows["citizens"]["status"] == "not_configured"
    assert rows["citizens"]["successful_at"] is None


def test_crosscheck_outage_retains_previous_evidence(conn, monkeypatch):
    conn.executescript(crosscheck.SCHEMA)
    conn.execute("INSERT INTO items (uid,month,sector,title,body,links,source_url) VALUES ('1','2026-09','','DPDP Rules','','[]','u')")
    conn.execute("INSERT INTO corroborations VALUES ('1','https://pib.gov.in/a','PIB','government','DPDP Rules','2026-09-01')")
    conn.execute("INSERT INTO crosscheck_runs VALUES ('1','q','2020-01-01',1)")
    conn.commit()
    def fail(*args):
        raise requests.Timeout()
    monkeypatch.setattr(crosscheck, "_fetch", fail)
    with pytest.raises(health.IncompleteCollection):
        crosscheck.crosscheck(conn, ["1"])
    assert conn.execute("SELECT COUNT(*) FROM corroborations").fetchone()[0] == 1
    assert conn.execute("SELECT checked_at FROM crosscheck_runs").fetchone()[0] == "2020-01-01"


def test_chatter_cooldown_survives_new_client_and_budget_is_hard(conn, monkeypatch, tmp_path):
    monkeypatch.setattr(chatter, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(chatter, "MAX_REQUESTS", 1)
    monkeypatch.setattr(chatter.time, "time", lambda: 1000)
    calls = []
    monkeypatch.setattr(chatter.requests, "get", lambda *a, **k: calls.append(a) or
                        SimpleNamespace(status_code=429, headers={"Retry-After": "7200"}))
    http = chatter._Http(conn)
    assert http.get("https://example.org/a") is None
    assert http.get("https://other.example/b") is None
    assert len(calls) == 1 and http.n == 1
    assert chatter._Http(conn).get("https://example.org/c") is None
    assert len(calls) == 1
    assert conn.execute("SELECT until_at FROM chatter_cooldowns").fetchone()[0] == 8200


def test_chatter_invalid_payload_is_failure(monkeypatch):
    monkeypatch.setattr(chatter, "RSS_FEEDS", (("Outlet", "https://example.org/feed"),))
    monkeypatch.setattr(chatter, "MASTODON_TAGS", ())
    monkeypatch.setattr(chatter, "CHATTER_QUERIES", {})
    http = chatter._Http()
    monkeypatch.setattr(http, "get", lambda *a: "<html>Access denied</html>")
    assert chatter.fetch_all(http) == []
    assert http.failures == {"example.org"}


def test_chatter_cache_reuse_does_not_spend_budget(conn, monkeypatch, tmp_path):
    monkeypatch.setattr(chatter, "CACHE_DIR", tmp_path)
    calls = []
    monkeypatch.setattr(chatter.requests, "get", lambda *a, **k: calls.append(a) or
                        SimpleNamespace(status_code=200, text="<rss/>"))
    http = chatter._Http(conn)
    assert http.get("https://example.org/feed") == "<rss/>"
    assert http.get("https://example.org/feed") == "<rss/>"
    assert len(calls) == 1 and http.n == 1 and http.cached == 1


def test_stale_chatter_clusters_not_shown(conn):
    conn.executescript(chatter.SCHEMA)
    conn.execute("""INSERT INTO chatter_clusters
        (id,topic,label,label_url,credibility,n_docs,n_outlets,n_authors,platforms,outlets,engagement,last_seen)
        VALUES ('old','work','Old news','https://example.org','confirmed',2,2,0,'[]','[]',0,'2000-01-01')""")
    assert chatter.by_topic(conn)["work"]["total"] == 0


def test_announcements_empty_is_not_success(conn, monkeypatch):
    monkeypatch.setattr(announcements, "fetch", lambda *a, **k: "<html>blocked</html>")
    with pytest.raises(health.IncompleteCollection):
        announcements.refresh(conn)


def test_all_state_pages_refresh(monkeypatch):
    calls = []
    monkeypatch.setattr(states, "fetch", lambda url, **kw: calls.append(kw["force"]) or "html")
    batches = iter([[{}] * 50, [{}]])
    monkeypatch.setattr(states, "parse_list", lambda _: next(batches))
    assert len(states._fetch_all("https://example.org/{year}/{page}", 2026, True)) == 51
    assert calls == [True, True]


def test_daily_collect_builds_even_when_stage_fails(conn, monkeypatch):
    from radar import __main__ as cli, build_site
    import radar.citizens
    monkeypatch.setattr(cli.db, "connect", lambda: conn)
    calls = []
    def fail(*args):
        calls.append("announcements")
        raise requests.Timeout()
    monkeypatch.setattr(cli, "refresh_announcements", fail)
    monkeypatch.setattr(cli, "refresh_chatter", lambda _: calls.append("chatter"))
    monkeypatch.setattr(cli, "refresh_citizens", lambda _: calls.append("citizens"))
    monkeypatch.setattr(radar.citizens, "snapshot", lambda _: {"domains": [{"sources": {"x": {"status": "not_configured"}}}]})
    monkeypatch.setattr(build_site, "build", lambda: calls.append("build"))
    assert not cli.collect(daily=True)
    assert calls == ["announcements", "chatter", "citizens", "build"]


@pytest.mark.parametrize("lang", ["en", "hi"])
def test_health_page_localization(conn, lang):
    from radar.build_site import _env
    rendered = _env(lang).get_template("health.html").render(
        health=health.snapshot(conn), week="2026-W40", root="", page="health.html",
        other_langs=[], images={}, scores=[])
    assert "health_status_" not in rendered
    assert "health_source_" not in rendered
    assert "health.json" in rendered


def test_chatter_coverage_tolerates_a_few_dead_hosts():
    from radar.chatter import assess_coverage, _Http
    http = _Http()
    http.attempted = {f"h{i}.example" for i in range(20)} | {"pib.gov.in", "www.newsonair.gov.in"}
    http.failures = {"h1.example", "h2.example", "h3.example", "api.gdeltproject.org"}
    http.attempted |= http.failures
    http.n = 118
    assert assess_coverage([object()] * 1952, http) is None


def test_chatter_coverage_flags_real_degradation():
    from radar.chatter import assess_coverage, _Http
    http = _Http()
    http.attempted = {f"h{i}.example" for i in range(10)} | {"pib.gov.in"}
    # too few documents
    assert "documents" in assess_coverage([object()] * 12, http)
    # most hosts down
    http.failures = {f"h{i}.example" for i in range(6)}
    assert "hosts unreachable" in assess_coverage([object()] * 1000, http)
    # all government feeds down
    http.failures = {"pib.gov.in"}
    assert "government feeds" in assess_coverage([object()] * 1000, http)
