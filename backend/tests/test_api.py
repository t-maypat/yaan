"""Phase 12.1: the /ask event stream and the rate limit (step 1, R1-8); the Inbox and live-demo
endpoints (step 2, D058). No LLM, no DB: the Sentinel functions are faked."""

import json

from fastapi.testclient import TestClient

from app import main
from app.agent import loop
from app.config import Settings
from app.sentinel import approve, blast_radius, engine, live

ASKED: list = []  # the as_of_ms each faked answer got


def events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        name, data = block.split("\n", 1)
        out.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return out


def fake_answer(question, product_id=None, graph=None, on_event=loop._quiet, as_of_ms=None):
    ASKED.append(as_of_ms)
    on_event("tool_call", {"tool": "list_issues", "input": {"status": "unresolved"}})
    on_event("tool_result", {"tool": "list_issues", "ms": 3.0, "cypher": ["product_issues.cypher"]})
    on_event("answer", {"answer_markdown": f"{question} [1]", "abstain": False})
    on_event("citations", {"citations": [{"n": 1, "artifact_id": "msg:x", "quote": "q"}]})
    on_event("subgraph", {"nodes": [], "edges": []})
    return {}


def test_ask_streams_the_fixed_event_sequence(monkeypatch):
    monkeypatch.setattr(loop, "answer", fake_answer)
    monkeypatch.setattr(main, "ask_limiter", main.RateLimiter(10))
    r = TestClient(main.app).post("/ask", json={"question": "Which bugs?", "product_id": "W"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    got = events(r.text)
    assert [n for n, _ in got] == [
        "tool_call",
        "tool_result",
        "answer",
        "citations",
        "subgraph",
        "done",
    ]
    assert got[2][1]["answer_markdown"] == "Which bugs? [1]"


def test_an_agent_failure_is_an_error_event_not_an_answer(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("spend cap reached")

    monkeypatch.setattr(loop, "answer", boom)
    monkeypatch.setattr(main, "ask_limiter", main.RateLimiter(10))
    got = events(TestClient(main.app).post("/ask", json={"question": "Which bugs?"}).text)
    assert got == [
        ("error", {"error": "RuntimeError", "message": "spend cap reached"}),
        ("done", {}),
    ]


def test_ask_is_rate_limited_per_ip(monkeypatch):
    monkeypatch.setattr(loop, "answer", fake_answer)
    monkeypatch.setattr(main, "ask_limiter", main.RateLimiter(2))
    client = TestClient(main.app)
    codes = [client.post("/ask", json={"question": "Which bugs?"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    assert main.RateLimiter(1).allow("ip", now=0) and main.RateLimiter(1).allow("ip", now=61)


def test_query_returns_the_file_a_tool_ran_and_nothing_else():
    client = TestClient(main.app)
    got = client.get("/query/product_issues.cypher").json()
    assert got["name"] == "product_issues.cypher"
    assert got["text"].startswith("// product_issues.cypher")
    assert client.get("/query/no_such_query").status_code == 404
    assert client.get("/query/..%2F..%2F.env").status_code == 404


def test_products_lists_the_graphs_products(monkeypatch):
    rows = [{"id": "W", "name": "W", "aliases": ["wdAI"], "artifacts": 3}]
    monkeypatch.setattr(main.db, "run", lambda name, params, **k: type("R", (), {"rows": rows}))
    assert TestClient(main.app).get("/products").json()["products"] == rows


def test_ask_rejects_empty_or_huge_questions():
    client = TestClient(main.app)
    assert client.post("/ask", json={"question": ""}).status_code == 422
    assert client.post("/ask", json={"question": "x" * 501}).status_code == 422


ALERT = {"id": "alert:dec:x", "decision_id": "dec:x", "kind": "regression", "summary": "s"}


def test_alerts_and_detail_read_the_api_graph(monkeypatch):
    seen = []

    def fake_list(status=None, product_id=None, *, graph=None):
        seen.append(graph)
        return [ALERT]

    monkeypatch.setattr(engine, "list_alerts", fake_list)
    monkeypatch.setattr(engine, "alert_actions", lambda i, graph=None: [{"id": "a1"}])
    monkeypatch.setattr(blast_radius, "impact_of", lambda d, graph=None: {"decision_id": d})
    monkeypatch.setattr(main.db, "run", lambda name, params, **k: type("R", (), {"rows": []}))
    client = TestClient(main.app)
    assert client.get("/alerts").json()["alerts"] == [ALERT]
    got = client.get("/alerts/detail", params={"id": "alert:dec:x"}).json()
    assert got["radius"] == {"decision_id": "dec:x"} and got["actions"] == [{"id": "a1"}]
    assert client.get("/alerts/detail", params={"id": "nope"}).status_code == 404
    assert set(seen) == {"lore_demo"}


def test_approve_and_dismiss_go_to_the_demo_graph_and_errors_are_409(monkeypatch):
    calls = []

    def fake_respond(alert_id, verdict, person_id, *, graph=None, **kw):
        calls.append((alert_id, verdict, person_id, graph))
        if alert_id == "closed":
            raise approve.AlertError("alert 'closed' is approved, not open")
        return {"status": verdict}

    monkeypatch.setattr(approve, "respond", fake_respond)
    monkeypatch.setattr(main, "demo_limiter", main.RateLimiter(10))
    client = TestClient(main.app)
    body = {"alert_id": "alert:dec:x", "person_id": "eid_a1"}
    assert client.post("/alerts/approve", json=body).json() == {"status": "approved"}
    assert client.post("/alerts/dismiss", json=body).json() == {"status": "dismissed"}
    r = client.post("/alerts/approve", json={**body, "alert_id": "closed"})
    assert r.status_code == 409 and "not open" in r.json()["detail"]
    assert {c[3] for c in calls} == {"lore_demo"}


def test_demo_writes_refuse_the_main_graph(monkeypatch):
    monkeypatch.setattr(main, "get_settings", lambda: Settings(lore_api_graph="lore_herb"))
    monkeypatch.setattr(main, "demo_limiter", main.RateLimiter(10))
    r = TestClient(main.app).post("/demo/reset")
    assert r.status_code == 409 and "demo graph only" in r.json()["detail"]


def test_only_the_labeled_injection_files_can_be_injected(monkeypatch):
    monkeypatch.setattr(live, "ingest", lambda msg, graph=None: {"artifact_id": msg["id"]})
    monkeypatch.setattr(live, "injected_ids", lambda graph: {"msg:synthetic-20270309-1"})
    monkeypatch.setattr(main, "demo_limiter", main.RateLimiter(10))
    client = TestClient(main.app)
    listed = client.get("/demo/injections").json()["injections"]
    assert [(i["name"], i["injected"]) for i in listed] == [
        ("01-regression", True),
        ("02-unauthorized-dismissal", False),
    ]
    ok = client.post("/demo/inject/02-unauthorized-dismissal").json()
    assert ok == {"artifact_id": "msg:synthetic-20270309-2"}
    assert client.post("/demo/inject/nope").status_code == 404
    # refused either way: no route matches it, and the built UI's static files take no POST
    assert client.post("/demo/inject/..%2F..%2F.env").status_code in (404, 405)
    assert client.get("/..%2F.env").status_code == 404  # static files stay inside frontend/dist


def test_demo_writes_are_rate_limited(monkeypatch):
    monkeypatch.setattr(live, "reset_demo", lambda graph=None: {"graph": graph})
    monkeypatch.setattr(main, "demo_limiter", main.RateLimiter(1))
    client = TestClient(main.app)
    assert [client.post("/demo/reset").status_code for _ in range(2)] == [200, 429]


def test_ingest_artifact_needs_the_secret(monkeypatch):
    monkeypatch.setattr(live, "ingest", lambda msg, graph=None: {"ok": True})
    monkeypatch.setattr(main, "demo_limiter", main.RateLimiter(10))
    client = TestClient(main.app)
    monkeypatch.setattr(main, "get_settings", lambda: Settings(lore_ingest_secret=""))
    assert client.post("/ingest/artifact", json={}).status_code == 503
    monkeypatch.setattr(main, "get_settings", lambda: Settings(lore_ingest_secret="s3"))
    assert client.post("/ingest/artifact", json={}).status_code == 401
    r = client.post("/ingest/artifact", json={}, headers={"X-Lore-Secret": "s3"})
    assert r.json() == {"ok": True}


def test_ask_passes_the_timelines_as_of_moment(monkeypatch):
    monkeypatch.setattr(loop, "answer", fake_answer)
    monkeypatch.setattr(main, "ask_limiter", main.RateLimiter(10))
    client = TestClient(main.app)
    body = {"question": "Status?", "product_id": "W", "as_of_ms": 1_800_000_000_000}
    client.post("/ask", json=body)
    assert ASKED[-1] == 1_800_000_000_000
    assert client.post("/ask", json={"question": "Status?", "as_of_ms": -1}).status_code == 422


def fake_db(tables):
    """db.run faked by query name: tables = {name: rows}."""
    return lambda name, params, **k: type("R", (), {"rows": tables[name]})


def test_lineage_finds_the_subjects_product_and_404s_otherwise(monkeypatch):
    from app.agent import tools

    calls = []

    def lineage(subject, product, as_of_ms, graph):
        calls.append((subject, product, as_of_ms))
        return {"subjects": [{"id": subject}], "cypher": ["decision_chain.cypher"]}

    monkeypatch.setattr(tools, "decision_lineage", lineage)
    monkeypatch.setattr(main.db, "run", fake_db({"subject_product": [{"product_id": "W"}]}))
    client = TestClient(main.app)
    r = client.get("/decision/iss:W:abc/lineage?as_of_ms=5")
    assert r.status_code == 200 and calls == [("iss:W:abc", "W", 5)]
    assert r.json()["cypher"] == ["subject_product.cypher", "decision_chain.cypher"]
    assert client.get("/decision/eid_1/lineage").status_code == 404  # not an issue or PR
    monkeypatch.setattr(main.db, "run", fake_db({"subject_product": [{"product_id": None}]}))
    assert client.get("/decision/pr:nope/lineage").status_code == 404


def test_entity_counts_links_and_profiles_people(monkeypatch):
    from app.agent import tools

    card = {"labels": ["Person", "Entity"], "props": {"id": "eid_1", "name": "Ann"}}
    rows = [{**card, "rels": ["AUTHORED", "AUTHORED", "REVIEWED"]}]
    monkeypatch.setattr(main.db, "run", fake_db({"entity_card": rows}))
    monkeypatch.setattr(tools, "person_profile", lambda p, product, graph: {"person": p})
    out = TestClient(main.app).get("/entity/eid_1").json()
    assert out["links"] == {"AUTHORED": 2, "REVIEWED": 1} and out["profile"] == {"person": "eid_1"}
    assert TestClient(main.app).get("/entity/msg:1").status_code == 404  # an artifact
    monkeypatch.setattr(main.db, "run", fake_db({"entity_card": []}))
    assert TestClient(main.app).get("/entity/nobody").status_code == 404


def test_graph_health_and_eval_latest(monkeypatch):
    from app.ingest import health

    monkeypatch.setattr(health, "report", lambda graph: {"total_nodes": 7})
    assert TestClient(main.app).get("/graph/health").json()["total_nodes"] == 7
    out = TestClient(main.app).get("/eval/latest").json()  # the committed eval/results files
    systems = {r["system"] for r in out["systems"]}
    assert {"baseline", "lore", "lore-search"} <= systems
    assert out["drift"]["lore"]["split"] == "drift" and out["reference"]["avg"] == 43.41
