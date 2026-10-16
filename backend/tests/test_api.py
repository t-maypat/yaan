"""Phase 12.1 (step 1, R1-8): the /ask event stream and the rate limit. No LLM, no DB."""

import json

from fastapi.testclient import TestClient

from app import main
from app.agent import loop


def events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        name, data = block.split("\n", 1)
        out.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return out


def fake_answer(question, product_id=None, graph=None, on_event=loop._quiet):
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


def test_ask_rejects_empty_or_huge_questions():
    client = TestClient(main.app)
    assert client.post("/ask", json={"question": ""}).status_code == 422
    assert client.post("/ask", json={"question": "x" * 501}).status_code == 422
