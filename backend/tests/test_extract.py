"""Phase 7: extraction units (S5) and quote verification (S6). No LLM, no DB."""

from app.ingest import extract, verify
from app.ingest.normalize import Artifact


def msg(id_, text, channel="bug-ann-Widget", ts=0, author="eid_a", system=False):
    return Artifact(
        id=id_,
        kind="message",
        product_hint="Widget",
        channel_id="ch-1",
        ts_ms=ts,
        ts_iso="2026-01-01T00:00:00Z",
        author_eid=author,
        text=text,
        system=system,
        raw_ref="Widget:slack[0]",
        meta={"channel_name": channel, "channel_id": "ch-1"},
    )


def test_units_group_by_channel_and_conversation_key_in_time_order():
    arts = [
        msg("msg:20260102-1-bb", "second", ts=2),
        msg("msg:20260102-0-aa", "first", ts=1),
        msg("msg:20260105-0-cc", "other conversation", ts=5),
        msg("msg:20260102-0-dd", "same key, other channel", channel="develop-ann-Widget", ts=3),
        msg("msg:20260102-2-ee", "planning is out of scope", channel="planning-Widget", ts=4),
        msg("msg:20260102-3-ff", "@x joined", ts=6, system=True),
    ]
    us = extract.units("Widget", arts)
    assert [u.id for u in us] == [
        "unit:Widget:bug-ann-Widget:20260102",
        "unit:Widget:develop-ann-Widget:20260102",
        "unit:Widget:bug-ann-Widget:20260105",
    ]
    assert [m.text for m in us[0].messages] == ["first", "second"]
    assert "[1] eid_a: second" in us[0].render()


def test_long_conversations_are_cut_into_parts(monkeypatch):
    monkeypatch.setattr(extract, "UNIT_CHARS", 50)
    arts = [msg(f"msg:20260102-{i}-x{i}", "y" * 30, ts=i) for i in range(4)]
    us = extract.units("Widget", arts)
    assert [u.id.rsplit(":", 1)[-1] for u in us] == ["0", "1", "2", "3"]


def unit(*texts):
    arts = tuple(
        msg(f"msg:20260102-{i}-h{i}", t, ts=i, author=f"eid_{i}") for i, t in enumerate(texts)
    )
    return extract.Unit("unit:Widget:bug-ann-Widget:20260102", "Widget", "bug-ann-Widget", arts)


def test_quote_matching_ignores_case_and_whitespace_and_stores_offsets():
    u = unit("Hi team", "The bug titled 'Slow  Charts' has been\nresolved.")
    ok, row = verify.check(
        {"type": "issue_resolved", "msg": 1, "quote": "bug titled 'slow charts' has been resolved"},
        u,
    )
    assert ok and not row["moved"]
    assert row["artifact_id"] == "msg:20260102-1-h1" and row["speaker"] == "eid_1"
    text = u.messages[1].text
    assert text[row["start"] : row["end"]].lower().startswith("bug titled")


def test_a_quote_from_another_message_is_re_pinned():
    u = unit("Please revert PR#2.", "Will do.")
    ok, row = verify.check({"type": "pr_reverted", "msg": 1, "quote": "Please revert PR#2"}, u)
    assert ok and row["moved"] and row["msg"] == 0


def test_paraphrased_empty_or_overlong_quotes_are_dropped():
    u = unit("Please revert PR#2.")
    assert verify.check({"type": "pr_reverted", "msg": 0, "quote": "revert pull request 2"}, u)[1][
        "drop"
    ]
    assert (
        verify.check({"type": "pr_reverted", "msg": 0, "quote": "  "}, u)[1]["drop"]
        == "empty quote"
    )
    ok, row = verify.check({"type": "pr_reverted", "msg": 9, "quote": "x" * 500}, u)
    assert not ok and "longer" in row["drop"]
