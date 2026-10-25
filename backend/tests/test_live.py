"""The live path's guards (11.6) and the synthetic injections (11.7, D057). No database, no LLM:
the end-to-end run (`yaan demo reset` + `yaan demo inject`) is checked by hand in D057."""

import json

import pytest

from app.config import REPO_ROOT
from app.sentinel import live

INJECT = sorted((REPO_ROOT / "exports" / "inject").glob("*.json"))
MSG = {
    "id": "msg:synthetic-1",
    "product": "Widget",
    "channel_id": "ch-bug",
    "channel_name": "bug-Widget",
    "author_eid": "eid_a1",
    "ts_iso": "2027-03-09T10:00:00Z",
    "text": "Customer CUST-0001 has reopened 'Slow export'.",
}


def test_live_writes_refuse_the_main_graph():
    with pytest.raises(live.LiveError, match="demo graph only"):
        live.demo_graph("lore_herb")
    assert live.demo_graph(None) == "lore_demo"


def test_a_message_becomes_a_synthetic_artifact():
    a = live.artifact(MSG)
    assert (a.kind, a.ts_ms, a.ts_iso) == ("message", 1804586400000, "2027-03-09T10:00:00Z")
    assert a.meta == {"channel_name": "bug-Widget", "sender": "eid_a1", "synthetic": True}
    assert a.raw_ref == "synthetic:msg:synthetic-1"


@pytest.mark.parametrize(
    "change, error",
    [
        ({"id": "msg:20270309-0-abc"}, "must start with"),
        ({"ts_iso": "9 March"}, "does not match"),
    ],
)
def test_a_message_that_could_pass_for_herb_or_is_malformed_is_refused(change, error):
    with pytest.raises(ValueError, match=error):
        live.artifact({**MSG, **change})
    with pytest.raises(live.LiveError, match="lacks"):
        live.artifact({k: v for k, v in MSG.items() if k != "text"})


@pytest.mark.parametrize("path", INJECT, ids=lambda p: p.name)
def test_every_injection_is_labeled_synthetic_and_parses(path):
    msg = json.loads(path.read_text(encoding="utf-8"))
    assert msg["_label"].startswith("SYNTHETIC")
    assert live.artifact(msg).id.startswith(live.SYNTHETIC_PREFIX)


def test_the_injections_run_in_story_order():
    assert [p.name for p in INJECT] == ["01-regression.json", "02-unauthorized-dismissal.json"]
    times = [live.artifact(json.loads(p.read_text(encoding="utf-8"))).ts_ms for p in INJECT]
    assert times == sorted(times)
