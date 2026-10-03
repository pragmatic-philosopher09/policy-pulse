import copy
import json
from datetime import date

import pytest
import requests

from radar import analysis as a, db
from radar.analysis_eval import CASES, check_case


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "analysis.sqlite")
    month = date.today().strftime("%Y-%m")
    connection.execute(
        "INSERT INTO items(uid,month,sector,title,body,links,source_url) VALUES (?,?,?,?,?,?,?)",
        ("prs-1", month, "Labour", "Rules notified", "The Ministry notified rules under the labour codes.",
         "[]", "https://prsindia.org/policy/monthly-policy-review"))
    connection.execute("INSERT INTO item_topics VALUES ('prs-1','work',1)")
    connection.execute("INSERT INTO months(month,source_url,n_items) VALUES (?,'https://prsindia.org/',1)", (month,))
    connection.commit()
    yield connection
    connection.close()


def output(sources, raw=False):
    result = {"insufficient": False, "claims": [
        {"kind": "record", "summary": "The Ministry notified labour code rules.",
         "citations": [{"source_id": sources[0]["id"], "excerpt_id": "e1",
                        **({} if raw else {"quote": sources[0]["text"]})}]}], "excluded": []}
    if raw:
        del result["insufficient"]
    return result


class FakeModel:
    model = "test-local:8b"
    generation_options = {}
    digest = "a" * 64
    calls = 0
    fail = False

    def identity(self):
        return self.digest

    def generate(self, topic, kind, sources):
        self.calls += 1
        if self.fail:
            raise a.AnalysisError("Local model unavailable")
        return output(sources, raw=True)


def test_draft_cache_and_explicit_review_gate(conn, monkeypatch):
    client = FakeModel()
    sources = a.record_sources(conn, "work")
    row = a.draft(conn, "work", sources, client=client)
    assert a.published(conn) == {}
    assert a.draft(conn, "work", sources, client=client)["id"] == row["id"]
    assert client.calls == 1
    a.approve(conn, row["id"])
    assert a.published(conn)["work"]["claims"][0]["citations"][0]["url"].startswith("https://prsindia.org/")
    client.digest = "b" * 64
    revised = a.draft(conn, "work", sources, client=client)
    assert revised["id"] != row["id"] and client.calls == 2
    assert a.published(conn)["work"]["id"] == row["id"]  # new drafts don't displace approved notes
    monkeypatch.setattr(a, "OPTIONS", {**a.OPTIONS, "seed": 43})
    client.fail = True
    with pytest.raises(a.AnalysisError):
        a.draft(conn, "work", sources, client=client)
    assert a.published(conn)["work"]["id"] == row["id"]
    assert conn.execute("SELECT status FROM analysis_attempts WHERE topic='work'").fetchone()[0] == "failed"


def test_sources_changed_invalidates_approval_and_publication(conn):
    row = a.draft(conn, "work", a.record_sources(conn, "work"), client=FakeModel())
    a.approve(conn, row["id"])
    conn.execute("UPDATE items SET body='The Ministry withdrew these draft labour rules.'")
    assert a.published(conn) == {}
    with pytest.raises(a.AnalysisError, match="changed"):
        a.approve(conn, row["id"])
    with pytest.raises(a.AnalysisError, match="current PRS"):
        a.draft(conn, "work", json.loads(row["sources"]), client=FakeModel())


def test_synthetic_cannot_be_approved_or_exported(conn):
    row = a.draft(conn, "synthetic:small", CASES[2]["documents"], "synthetic", client=FakeModel())
    assert json.loads(row["result"])["insufficient"]
    with pytest.raises(a.AnalysisError, match="synthetic"):
        a.approve(conn, row["id"])
    conn.execute("UPDATE analysis_drafts SET approved_at='2026-01-01'")
    assert a.published(conn) == {}
    with pytest.raises(a.AnalysisError, match="not enabled"):
        a.draft(conn, "work", [], "reddit")


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(extra="unexpected"),
    lambda r: r.update(insufficient="false"),
    lambda r: r.update(insufficient=True),
    lambda r: r.update(claims=[]),
    lambda r: r.update(excluded=["missing"]),
    lambda r: r.update(excluded=["prs-1"]),
    lambda r: r["claims"][0].update(kind="support"),
    lambda r: r["claims"][0].update(summary="short"),
    lambda r: r["claims"][0].update(citations=[]),
    lambda r: r["claims"][0]["citations"][0].update(source_id="invented"),
    lambda r: r["claims"][0]["citations"][0].update(excerpt_id="invented"),
    lambda r: r["claims"][0]["citations"][0].update(quote="Every Indian supports this new policy."),
    lambda r: r["claims"][0]["citations"][0].update(quote="short"),
    lambda r: r["claims"][0]["citations"].append(copy.deepcopy(r["claims"][0]["citations"][0])),
])
def test_rejects_invalid_output(conn, mutation):
    sources = a.record_sources(conn, "work")
    result = output(sources)
    mutation(result)
    with pytest.raises(a.AnalysisError):
        a.validate(result, sources, "policy_record")


def test_limits_empty_sources_and_origin(conn):
    with pytest.raises(a.AnalysisError):
        a.validate_sources(CASES[0]["documents"] * 2)
    duplicate = [CASES[0]["documents"][0]] * 2
    with pytest.raises(a.AnalysisError):
        a.validate_sources(duplicate)
    conn.execute("UPDATE items SET source_url='https://prsindia.org.evil.example/entry'")
    assert a.record_sources(conn, "work") == []
    client = FakeModel()
    row = a.draft(conn, "work", [], client=client)
    assert json.loads(row["result"])["insufficient"] and client.calls == 0
    with pytest.raises(a.AnalysisError, match="Insufficient"):
        a.approve(conn, row["id"])


class Response:
    def __init__(self, data=None, status=200, raw=None):
        self.status_code = status
        self.raw = raw if raw is not None else json.dumps(data).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, size):
        yield self.raw


def test_local_transport_no_proxies_redirects_or_cloud(monkeypatch):
    client = a.Ollama()
    assert client.session.trust_env is False
    seen = []
    def request(method, url, **kwargs):
        seen.append((method, url, kwargs))
        return Response({"models": [{"name": client.model, "digest": "a" * 64, "size": 10}]})
    monkeypatch.setattr(client.session, "request", request)
    assert client.identity() == "a" * 64
    assert seen[0][1] == "http://127.0.0.1:11434/api/tags"
    assert seen[0][2]["allow_redirects"] is False
    for data in ({"models": None}, {"models": ["bad"]}, {"models": []},
                 {"models": [{"name": client.model, "digest": "bad", "size": 10}]},
                 {"models": [{"name": client.model, "digest": "a" * 64, "size": 10,
                              "remote_host": "https://ollama.com"}]}):
        monkeypatch.setattr(client.session, "request", lambda *args, **kwargs: Response(data))
        with pytest.raises(a.AnalysisError):
            client.identity()
    client.close()


@pytest.mark.parametrize("response", [
    Response({}, status=302), Response({}, status=500), Response(raw=b"not-json"),
    Response(raw=b"x" * (a.MAX_RESPONSE + 1)), Response([]),
])
def test_transport_rejects_bad_envelopes(monkeypatch, response):
    client = a.Ollama()
    monkeypatch.setattr(client.session, "request", lambda *args, **kwargs: response)
    with pytest.raises(a.AnalysisError):
        client.request("GET", "/api/tags")
    client.close()


@pytest.mark.parametrize("data", [
    {"done": False, "message": {"content": "{}"}},
    {"done": True, "done_reason": "length", "message": {"content": "{}"}},
    {"done": True, "message": []},
    {"done": True, "message": {"content": "<think>reasoning</think> {}"}},
])
def test_generation_rejects_incomplete_or_unstructured(monkeypatch, data):
    client = a.Ollama()
    monkeypatch.setattr(client, "request", lambda *args, **kwargs: data)
    with pytest.raises(a.AnalysisError):
        client.generate("work", "policy_record", [])
    client.close()


def test_timeout_redacts_internal_exception(monkeypatch):
    client = a.Ollama()
    def fail(*args, **kwargs):
        raise requests.Timeout("private response text")
    monkeypatch.setattr(client.session, "request", fail)
    with pytest.raises(a.AnalysisError) as error:
        client.identity()
    assert "private" not in str(error.value)
    client.close()


def test_evaluation_checks_attribution_and_exclusion():
    result = {"insufficient": False, "claims": [], "excluded": []}
    assert check_case(CASES[0], result)
    assert check_case(CASES[1], result)
    assert not check_case(CASES[2], {"insufficient": True, "claims": [], "excluded": []})


def test_topic_template_reviewed_notes_are_escaped_and_localised(conn, monkeypatch, tmp_path):
    from radar import build_site
    from radar.i18n import STRINGS
    sources = a.record_sources(conn, "work")
    client = FakeModel()
    def generate(*args):
        result = output(sources, raw=True)
        result["claims"][0]["summary"] = "<script>alert('unsafe')</script>"
        return result
    monkeypatch.setattr(client, "generate", generate)
    row = a.draft(conn, "work", sources, client=client)
    a.approve(conn, row["id"])
    monkeypatch.setattr(build_site.db, "connect", lambda: conn)
    monkeypatch.setattr(build_site, "OUT", tmp_path / "site")
    monkeypatch.setattr(build_site, "editor_note", lambda *args: "")
    monkeypatch.setattr(a.Ollama, "__init__", lambda *args: pytest.fail("Build must not invoke Ollama"))
    build_site.build()
    for lang, prefix in (("en", ""), ("hi", "hi/")):
        html = (build_site.OUT / f"{prefix}topic/work.html").read_text()
        assert STRINGS[lang]["analysis_h"] in html
        assert "<script>alert('unsafe')</script>" not in html
        assert "&lt;script&gt;" in html
    exported = json.loads((build_site.OUT / "analysis.json").read_text())
    assert exported["work"]["id"] == row["id"]
    assert "synthetic" not in json.dumps(exported)


def test_excerpt_selection_never_uses_model_generated_quotes(conn):
    sources = a.record_sources(conn, "work")
    result = a.resolve_citations(output(sources, raw=True), sources)
    assert result == output(sources)
    for field in ("source_id", "excerpt_id"):
        wrong = output(sources, raw=True)
        wrong["claims"][0]["citations"][0][field] = "invented"
        with pytest.raises(a.AnalysisError):
            a.resolve_citations(wrong, sources)
    with pytest.raises(a.AnalysisError):
        a.resolve_citations(output(sources), sources)
    long_source = {**sources[0], "text": "A long sentence about labour policy " * 30}
    for text in a.excerpts(long_source).values():
        assert len(text) <= 240 and text in a.normalize(long_source["text"])


def test_hinglish_semantic_regression_not_hidden_by_citation_matching():
    result = {"insufficient": False, "excluded": [], "claims": [
        {"kind": "support", "summary": "Allow easy retrieval of consent.", "citations": [{"source_id": "s1"}]},
        {"kind": "concern", "summary": "Compliance costs.", "citations": [{"source_id": "s2"}]},
        {"kind": "question", "summary": "Hindi availability.", "citations": [{"source_id": "s3"}]},
    ]}
    assert "Missing required meaning in support summary" in check_case(CASES[3], result)


def test_repeated_text_does_not_meet_sample_floor(conn):
    sources = [{**CASES[2]["documents"][0], "id": f"s{i}"} for i in range(3)]
    client = FakeModel()
    row = a.draft(conn, "synthetic:copies", sources, "synthetic", client=client)
    assert json.loads(row["result"])["insufficient"] and client.calls == 0


def test_schema_constrains_categories_and_available_excerpt_ids(conn):
    sources = a.record_sources(conn, "work")
    schema = a.schema_for(sources, "policy_record")
    claim = schema["properties"]["claims"]["items"]["properties"]
    assert claim["kind"]["enum"] == ["record"]
    citation = claim["citations"]["items"]["oneOf"][0]["properties"]
    assert citation["source_id"]["const"] == "prs-1"
    assert citation["excerpt_id"]["enum"] == ["e1"]
    assert "record" not in a.schema_for(sources, "synthetic")["properties"]["claims"]["items"]["properties"]["kind"]["enum"]
    assert "insufficient" not in schema["properties"]


def test_synthetic_cli_uses_isolated_database_and_rejects_public_report(tmp_path, monkeypatch):
    from radar import __main__, analysis_eval
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(__main__.db, "connect", lambda: pytest.fail("Evaluation must not open the real dataset"))
    def evaluate(connection, model):
        connection.executescript(a.SCHEMA)
        assert connection.execute("PRAGMA database_list").fetchone()[2] == ""
        return {"passed": True, "synthetic_only": True}
    monkeypatch.setattr(analysis_eval, "evaluate", evaluate)
    __main__.main(["analyze", "--evaluate", "--output", ".cache/review.json"])
    assert json.loads((tmp_path / ".cache/review.json").read_text())["synthetic_only"]
    with pytest.raises(SystemExit) as error:
        __main__.main(["analyze", "--evaluate", "--output", "docs/review.json"])
    assert error.value.code == 1 and not (tmp_path / "docs").exists()


@pytest.mark.parametrize("thinking,expected", [
    (None, {}), ({"values": [False, True]}, {"think": False}),
    ({"values": [False]}, {"think": False}),
    ({"values": ["low", "high"]}, {}),
])
def test_thinking_controls_are_discovered_not_assumed(monkeypatch, thinking, expected):
    client = a.Ollama()
    payloads = []
    def request(method, path, payload=None, **kwargs):
        if path == "/api/tags":
            return {"models": [{"name": client.model, "size": 10, "digest": "a" * 64}]}
        if path == "/api/show":
            return {} if thinking is None else {"thinking": thinking}
        payloads.append(payload)
        return {"done": True, "message": {"content": '{"insufficient":true,"claims":[],"excluded":[]}'}}
    monkeypatch.setattr(client, "request", request)
    assert client.identity() == "a" * 64
    assert client.generation_options == expected
    client.generate("topic", "synthetic", CASES[0]["documents"])
    assert {k: v for k, v in payloads[0].items() if k == "think"} == expected
    client.close()


def test_generation_options_change_cache_key(conn):
    client = FakeModel()
    sources = a.record_sources(conn, "work")
    first = a.draft(conn, "work", sources, client=client)
    client.generation_options = {"think": False}
    second = a.draft(conn, "work", sources, client=client)
    assert first["id"] != second["id"] and client.calls == 2
    assert json.loads(second["generation_options"]) == {"options": a.OPTIONS, "think": False}


def test_legacy_drafts_migrate_without_inventing_generation_settings(conn):
    legacy = a.SCHEMA.replace(",\n    generation_options TEXT NOT NULL DEFAULT '{}'", "")
    conn.executescript(legacy)
    assert "generation_options" not in {r["name"] for r in conn.execute("PRAGMA table_info(analysis_drafts)")}
    conn.execute("INSERT INTO analysis_drafts VALUES ('old','work','policy_record','old','digest','v1','hash','now',NULL,'[]','{}')")
    conn.commit()
    a.initialize(conn)
    a.initialize(conn)
    assert conn.execute("SELECT generation_options FROM analysis_drafts WHERE id='old'").fetchone()[0] == "{}"


def test_expanded_acceptance_catches_negated_support():
    case = next(case for case in CASES if case["id"] == "negation-and-conditional")
    result = {"insufficient": False, "excluded": [], "claims": [
        {"kind": "support", "summary": "Support for these rules.", "citations": [{"source_id": "s1"}]}]}
    assert "Incorrectly attributed support" in check_case(case, result)


def test_invalid_thinking_metadata_fails_explicitly(monkeypatch):
    client = a.Ollama()
    def request(method, path, *args, **kwargs):
        return ({"models": [{"name": client.model, "size": 10, "digest": "a" * 64}]}
                if path == "/api/tags" else {"thinking": {"values": "false"}})
    monkeypatch.setattr(client, "request", request)
    with pytest.raises(a.AnalysisError, match="thinking controls"):
        client.identity()
    client.close()


def test_older_ollama_qwen_thinking_capability(monkeypatch):
    client = a.Ollama("qwen3:14b")
    def request(method, path, *args, **kwargs):
        return ({"models": [{"name": client.model, "size": 10, "digest": "a" * 64}]}
                if path == "/api/tags" else {"details": {"family": "qwen3"}, "capabilities": ["completion", "thinking"]})
    monkeypatch.setattr(client, "request", request)
    client.identity()
    assert client.generation_options == {"think": False}
    client.close()


def test_metadata_limit_is_separate_from_generation_limit(monkeypatch):
    client = a.Ollama()
    response = Response({"license": "x" * 58_000})
    monkeypatch.setattr(client.session, "request", lambda *args, **kwargs: response)
    assert client.request("POST", "/api/show", max_bytes=a.MAX_METADATA)["license"]
    with pytest.raises(a.AnalysisError, match="size limit"):
        client.request("POST", "/api/chat")
    client.close()


def test_abstention_is_derived_not_independently_generated(conn):
    sources = a.record_sources(conn, "work")
    assert a.resolve_citations({"claims": [], "excluded": []}, sources)["insufficient"] is True
    assert a.resolve_citations(output(sources, raw=True), sources)["insufficient"] is False
    inconsistent = {**output(sources, raw=True), "insufficient": True}
    with pytest.raises(a.AnalysisError, match="output fields"):
        a.resolve_citations(inconsistent, sources)
