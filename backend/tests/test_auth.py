"""Who is asking (D100): sign-in modes, tokens, and the API acting as the signed-in person.
No database: the routes' graph reads are faked."""

import pytest
from fastapi.testclient import TestClient

from app import auth, cli, main, org
from app.agent import loop
from app.config import get_settings
from app.sentinel import approve

PEOPLE = """
people:
  - name: Priya Shah
    email: priya@acme.example
    role: Engineering Lead
  - name: Ken Ito
    id: eid_0000beef
    email: ken@acme.example
"""


@pytest.fixture
def signed(tmp_path, monkeypatch):
    """Org mode with token sign-in, a roster and a data dir of the test's own."""
    people = tmp_path / "people.yaml"
    people.write_text(PEOPLE, encoding="utf-8")
    monkeypatch.setenv("LORE_DATA_MODE", "org")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(org, "PEOPLE_FILE", people)
    for cached in (get_settings, org.roster, org.accounts):
        cached.cache_clear()
    yield tmp_path
    for cached in (get_settings, org.roster, org.accounts):
        cached.cache_clear()


def test_the_demo_needs_no_sign_in_and_org_mode_needs_a_token(signed, monkeypatch):
    assert auth.mode() == "token"
    monkeypatch.setenv("LORE_DATA_MODE", "demo")
    get_settings.cache_clear()
    assert auth.mode() == "none" and auth.principal({}) is None


def test_a_token_names_its_person_until_revoked(signed):
    token = auth.issue("eid_0000beef", label="laptop")
    assert token.startswith("yaan_")
    assert token not in auth.tokens_path().read_text(encoding="utf-8")  # only its hash is kept
    assert auth.principal({"authorization": f"Bearer {token}"}) == "eid_0000beef"
    with pytest.raises(auth.AuthError, match="sign in"):
        auth.principal({})
    with pytest.raises(auth.AuthError, match="unknown or revoked"):
        auth.principal({"authorization": "Bearer yaan_guess"})
    assert auth.revoke("eid_0000beef") == 1
    with pytest.raises(auth.AuthError, match="unknown or revoked"):
        auth.principal({"authorization": f"Bearer {token}"})


def test_a_proxy_header_counts_only_with_the_proxy_secret(signed, monkeypatch):
    monkeypatch.setenv("LORE_AUTH", "header")
    get_settings.cache_clear()
    with pytest.raises(auth.AuthError, match="needs LORE_AUTH_PROXY_SECRET"):
        auth.principal({"x-forwarded-email": "ken@acme.example"})
    monkeypatch.setenv("LORE_AUTH_PROXY_SECRET", "s3cret")
    get_settings.cache_clear()
    with pytest.raises(auth.AuthError, match="only from the sign-in proxy"):
        auth.principal({"x-forwarded-email": "ken@acme.example"})  # someone around the proxy
    proxied = {"x-lore-proxy-secret": "s3cret"}
    assert auth.principal({**proxied, "x-forwarded-email": "KEN@acme.example"}) == "eid_0000beef"
    with pytest.raises(auth.AuthError, match="not in people.yaml"):
        auth.principal({**proxied, "x-forwarded-email": "eve@evil.example"})


def test_the_cli_issues_a_token_for_a_listed_person(signed, capsys):
    assert cli.main(["token", "issue", "Ken Ito", "--label", "laptop"]) == 0
    token = capsys.readouterr().out.strip().splitlines()[-1]
    assert auth.principal({"authorization": f"Bearer {token}"}) == "eid_0000beef"
    assert cli.main(["token", "issue", "nobody"]) == 1
    assert cli.main(["token", "revoke", "ken@acme.example"]) == 0
    assert "revoked 1" in capsys.readouterr().out


def test_signed_in_requests_act_as_their_person_only(signed, monkeypatch):
    asked, responded = [], []

    def fake_answer(question, product_id=None, *, viewer_id=None, on_event=loop._quiet, **_):
        asked.append(viewer_id)
        on_event("answer", {"answer_markdown": "ok", "abstain": True})
        return {}

    def fake_respond(alert_id, verdict, person_id, *, graph=None, **_):
        responded.append(person_id)
        return {"status": verdict}

    monkeypatch.setattr(loop, "answer", fake_answer)
    monkeypatch.setattr(approve, "respond", fake_respond)
    monkeypatch.setattr(main, "ask_limiter", main.RateLimiter(10))
    monkeypatch.setattr(main, "demo_limiter", main.RateLimiter(10))
    ken = {"Authorization": f"Bearer {auth.issue('eid_0000beef')}"}
    client = TestClient(main.app, headers=ken)

    assert client.post("/ask", json={"question": "Which bugs?"}).status_code == 200
    other = client.post("/ask", json={"question": "Which bugs?", "viewer_id": "eid_12345678"})
    assert other.status_code == 403 and "signed in as eid_0000beef" in other.json()["detail"]
    assert asked == ["eid_0000beef"]  # the viewer is the signed-in person, never the body

    assert client.post("/alerts/approve", json={"alert_id": "alert:x"}).json() == {
        "status": "approved"
    }
    spoof = client.post(
        "/alerts/dismiss", json={"alert_id": "alert:x", "person_id": "eid_aaaaaaaa"}
    )
    assert spoof.status_code == 403
    assert responded == ["eid_0000beef"]

    anon = TestClient(main.app)
    assert anon.post("/ask", json={"question": "Which bugs?"}).status_code == 401
    assert anon.get("/alerts").status_code == 401


def test_me_says_how_people_sign_in_and_who_you_are(signed, monkeypatch):
    def run(name, params, graph=None, **_):
        props = {"name": "Ken Ito", "role": "QA Specialist"} if name == "entity_card" else {}
        return type("R", (), {"rows": [{"props": props}]})

    monkeypatch.setattr(main.db, "run", run)
    ken = {"Authorization": f"Bearer {auth.issue('eid_0000beef')}"}
    got = TestClient(main.app, headers=ken).get("/me").json()
    assert got == {
        "auth": "token",
        "mode": "org",
        "you": {"id": "eid_0000beef", "name": "Ken Ito", "role": "QA Specialist"},
    }
    refused = TestClient(main.app).get("/me")
    assert refused.status_code == 401 and refused.json()["auth"] == "token"
