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
    # every test starts with empty per-person windows (module-wide in main, D124)
    minute, day = main.person_limits()
    monkeypatch.setattr(main, "ask_person_limiter", minute)
    monkeypatch.setattr(main, "ask_person_daily_limiter", day)
    monkeypatch.setattr(main, "person_request_limiter", main.RateLimiter(600))
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


# --- D111: lifetimes, revoking one token, browser sessions, CSRF, security headers -------------
def test_a_token_expires_and_remembers_when_it_was_last_used(signed, monkeypatch):
    from datetime import timedelta

    token = auth.issue("eid_0000beef", days=1)
    assert auth.principal({"authorization": f"Bearer {token}"}) == "eid_0000beef"
    (entry,) = auth.listing()
    assert entry["person"] == "eid_0000beef" and entry["last_used"] is not None
    assert token not in str(auth.listing())  # the listing never shows a secret
    later = auth.now() + timedelta(days=2)
    monkeypatch.setattr(auth, "now", lambda: later)
    with pytest.raises(auth.AuthError, match="expired"):
        auth.principal({"authorization": f"Bearer {token}"})
    with pytest.raises(ValueError, match="at least a day"):
        auth.issue("eid_0000beef", days=0)


def test_one_token_can_be_revoked_and_the_others_stay(signed, capsys):
    laptop, ci = auth.issue("eid_0000beef", "laptop"), auth.issue("eid_0000beef", "ci")
    ids = {t["label"]: t["id"] for t in auth.listing()}
    with pytest.raises(auth.AuthError, match="at least 6"):
        auth.revoke_id(ids["ci"][:3])
    assert cli.main(["token", "revoke", "Ken Ito", "--id", ids["ci"]]) == 0
    assert auth.principal({"authorization": f"Bearer {laptop}"}) == "eid_0000beef"
    with pytest.raises(auth.AuthError, match="unknown or revoked"):
        auth.principal({"authorization": f"Bearer {ci}"})
    assert cli.main(["token", "list"]) == 0
    assert ids["laptop"] in capsys.readouterr().out


def test_a_browser_session_is_a_signed_cookie_tied_to_its_token(signed, monkeypatch):
    from datetime import timedelta

    token = auth.issue("eid_0000beef", days=10)
    cookie, person, max_age = auth.open_session(token)
    assert person == "eid_0000beef" and token not in cookie
    assert 9 * 86400 < max_age <= 10 * 86400  # never past the token's own expiry
    assert auth.principal({}, {auth.SESSION_COOKIE: cookie}) == "eid_0000beef"
    payload, _, sig = cookie.partition(".")
    forged = auth._b64(b'{"p": "eid_12345678", "t": "x", "e": 9999999999}')
    for bad in (f"{payload}.{sig[:-2]}xx", f"{forged}.{sig}", payload, "é.ü", "..."):
        with pytest.raises(auth.AuthError, match="isn't valid"):
            auth.principal({}, {auth.SESSION_COOKIE: bad})
    auth.revoke("eid_0000beef")  # revoking the token ends its sessions
    with pytest.raises(auth.AuthError, match="revoked or expired"):
        auth.principal({}, {auth.SESSION_COOKIE: cookie})
    cookie, _, _ = auth.open_session(auth.issue("eid_0000beef"))
    later = auth.now() + timedelta(days=auth.SESSION_DAYS + 1)
    monkeypatch.setattr(auth, "now", lambda: later)
    with pytest.raises(auth.AuthError, match="ended|expired"):
        auth.principal({}, {auth.SESSION_COOKIE: cookie})


def test_the_web_ui_signs_in_with_a_cookie_and_writes_need_the_csrf_header(signed, monkeypatch):
    responded = []

    def fake_respond(alert_id, verdict, person_id, *, graph=None, **_):
        responded.append(person_id)
        return {"status": verdict}

    monkeypatch.setattr(approve, "respond", fake_respond)
    monkeypatch.setattr(main, "demo_limiter", main.RateLimiter(10))
    token = auth.issue("eid_0000beef")
    browser = TestClient(main.app)
    assert browser.post("/auth/session").status_code == 401  # no token, no session
    opened = browser.post("/auth/session", headers={"Authorization": f"Bearer {token}"})
    assert opened.status_code == 200 and opened.json()["person"] == "eid_0000beef"
    set_cookie = opened.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "samesite=strict" in set_cookie
    # the cookie alone: reads work, a write from a page that can't set the header doesn't
    refused = browser.post("/alerts/approve", json={"alert_id": "alert:x"})
    assert refused.status_code == 403 and "x-yaan-csrf" in refused.json()["detail"]
    ok = browser.post("/alerts/approve", json={"alert_id": "alert:x"}, headers={"X-Yaan-CSRF": "1"})
    assert ok.json() == {"status": "approved"} and responded == ["eid_0000beef"]
    # a bearer token is not ambient, so it needs no CSRF header
    api = TestClient(main.app, headers={"Authorization": f"Bearer {token}"})
    assert api.post("/alerts/approve", json={"alert_id": "alert:y"}).status_code == 200
    browser.delete("/auth/session")
    assert browser.post(
        "/alerts/approve", json={"alert_id": "alert:z"}, headers={"X-Yaan-CSRF": "1"}
    ).status_code == 401


def test_every_response_carries_the_security_headers(signed):
    r = TestClient(main.app).get("/health")
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_approvals_are_rate_limited_per_person_not_per_address(signed, monkeypatch):
    monkeypatch.setattr(approve, "respond", lambda *a, **k: {"status": "approved"})
    monkeypatch.setattr(main, "demo_limiter", main.RateLimiter(1))
    ken = TestClient(main.app, headers={"Authorization": f"Bearer {auth.issue('eid_0000beef')}"})
    priya_id = org.roster()
    priya = next(e for e, p in priya_id.items() if p["name"] == "Priya Shah")
    pri = TestClient(main.app, headers={"Authorization": f"Bearer {auth.issue(priya)}"})
    assert ken.post("/alerts/approve", json={"alert_id": "a"}).status_code == 200
    assert ken.post("/alerts/approve", json={"alert_id": "b"}).status_code == 429
    # the same address (the test client's), another person: her own budget
    assert pri.post("/alerts/approve", json={"alert_id": "c"}).status_code == 200


# --- the token file under concurrency (F6, D123) -----------------------------------------------
def test_concurrent_first_uses_never_fail_and_never_corrupt_the_file(signed):
    """The review's reproduction: at the top of an hour every token's first use stamps
    last_used. Before D123 the threads shared one temp file: 500s, then a corrupt file."""
    import json
    import threading

    tokens = [auth.issue(f"eid_{i:08x}") for i in range(24)]
    errors: list[Exception] = []

    def use(token: str) -> None:
        try:
            auth.check_token(token)
        except Exception as e:  # any failure at all is the bug
            errors.append(e)

    for _ in range(4):
        auth._touched.clear()  # a new hour
        threads = [threading.Thread(target=use, args=(t,)) for t in tokens]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert errors == []
    assert len(json.loads(auth.tokens_path().read_text(encoding="utf-8"))) == len(tokens)
    assert all(t["last_used"] for t in auth.listing())


def test_a_last_used_stamp_never_brings_back_a_revoked_token(signed):
    token = auth.issue("eid_0000beef")
    digest, _ = auth.check_token(token)  # a request read the entry...
    auth.revoke("eid_0000beef")  # ...the admin revoked it...
    auth.stamp_used(digest)  # ...then the request stamped its use
    assert auth.listing() == []
    with pytest.raises(auth.AuthError, match="unknown or revoked"):
        auth.check_token(token)


def test_a_busy_token_file_skips_the_stamp_and_never_fails_the_request(signed, monkeypatch):
    from app import filestate

    token = auth.issue("eid_0000beef")
    auth._touched.clear()
    with filestate.locked(auth.tokens_path()):  # the CLI holds the file
        monkeypatch.setattr(auth, "STAMP_WAIT_S", 0.05)
        import threading

        out: list[str] = []
        t = threading.Thread(target=lambda: out.append(auth.check_token(token)[1]["person"]))
        t.start()
        t.join()
    assert out == ["eid_0000beef"]
    assert auth._digest(token) not in auth._touched  # the next request tries the stamp again


# --- limits per person, not per address (F5, D124) ------------------------------------------
def test_two_people_behind_one_address_each_have_their_own_question_limit(signed, monkeypatch):
    """The review's reproduction: two signed-in people behind one office address (or one TLS
    proxy) shared 6 questions a minute, so the 7th between them got 429."""
    monkeypatch.setattr(loop, "answer", lambda *a, **k: {})
    monkeypatch.setattr(main, "ask_person_limiter", main.RateLimiter(2))
    priya = next(e for e, p in org.roster().items() if p["name"] == "Priya Shah")
    ken = TestClient(main.app, headers={"Authorization": f"Bearer {auth.issue('eid_0000beef')}"})
    pri = TestClient(main.app, headers={"Authorization": f"Bearer {auth.issue(priya)}"})
    ask = {"question": "Which bugs?"}
    assert [ken.post("/ask", json=ask).status_code for _ in range(3)] == [200, 200, 429]
    assert pri.post("/ask", json=ask).status_code == 200  # the same address, her own limit
    assert "2 questions per minute" in ken.post("/ask", json=ask).json()["detail"]


def test_answers_are_charged_to_the_signed_in_person(signed, monkeypatch):
    payers = []
    monkeypatch.setattr(loop, "answer", lambda *a, payer=None, **k: payers.append(payer) or {})
    ken = TestClient(main.app, headers={"Authorization": f"Bearer {auth.issue('eid_0000beef')}"})
    assert ken.post("/ask", json={"question": "Which bugs?"}).status_code == 200
    assert payers == ["eid_0000beef"]


def test_the_request_budget_is_per_person_and_skips_the_web_ui_files(signed, monkeypatch):
    monkeypatch.setattr(main, "person_request_limiter", main.RateLimiter(2))
    monkeypatch.setattr(main, "request_limiter", main.RateLimiter(1))
    ken = TestClient(main.app, headers={"Authorization": f"Bearer {auth.issue('eid_0000beef')}"})
    codes = [ken.get("/query/product_issues.cypher").status_code for _ in range(3)]
    assert codes == [200, 200, 429]  # his own two, not the address's one
    anon = TestClient(main.app)
    assert anon.get("/query/product_issues.cypher").status_code == 401  # counted: the address's one
    assert anon.get("/query/product_issues.cypher").status_code == 429  # guessing is limited
    assert anon.get("/no-such-ui-file.js").status_code != 429  # UI files aren't counted
