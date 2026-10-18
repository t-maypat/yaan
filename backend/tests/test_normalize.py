"""S1: typed HERB records (2.5) and normalized rows (2.6), on an invented fixture.

fixtures/herb_product.json is written by hand in HERB's verified shape (herb_format.md),
with each quirk the recon found: a bot sender, file order ≠ time order, a timestamp tie,
the flat Slack variant, doc versions, a review dated before its PR, an external
EMP_ PR, and one link written two ways. No HERB content is copied.
"""

import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.ingest import normalize as s1
from app.ingest.herb_loader import load_product

FIXTURE = Path(__file__).parent / "fixtures" / "herb_product.json"


@pytest.fixture
def widget(tmp_path):
    folder = tmp_path / "products"
    folder.mkdir()
    return shutil.copy(FIXTURE, folder / "Widget.json")


@pytest.fixture
def rows(widget):
    artifacts, stats = s1.normalize_product(load_product(Path(widget)))
    return {a.id: a for a in artifacts}, [a.id for a in artifacts], stats


def test_parse_ts_reads_herb_time_as_utc():
    assert s1.parse_ts("2026-05-01T09:00:00") == (1777626000000, "2026-05-01T09:00:00Z")
    with pytest.raises(ValueError):
        s1.parse_ts("2026-05-01 09:00:00")  # any other format fails loudly


def test_parse_bool_accepts_only_herb_strings():
    assert s1.parse_bool("True") is True and s1.parse_bool("False") is False
    with pytest.raises(ValueError):
        s1.parse_bool("true")


def test_loader_accepts_the_flat_slack_shape(widget):
    flat = load_product(Path(widget)).slack[-1]
    assert flat.Message.User.userId == "eid_aaaa0003"
    assert flat.Channel.name == "planning-Widget" and flat.id == "flat-0001"


def test_loader_rejects_unknown_record_keys(widget):
    data = json.loads(Path(widget).read_text("utf-8"))
    data["prs"][0]["merged_at"] = "2026-05-04T00:00:00"
    Path(widget).write_text(json.dumps(data), "utf-8")
    with pytest.raises(ValidationError, match="merged_at"):
        load_product(Path(widget))


def test_ids_follow_the_conventions(rows):
    by_id, _, _ = rows
    assert {"msg:20260501-0-aaaaa", "mtg:wdAI_planning_1", "chat:wdAI_planning_1_chat"} <= set(
        by_id
    )
    assert {"doc:wdai_vision_document", "pr:github_com_apache_kafka_pull_77"} <= set(by_id)
    assert sum(i.startswith("url:") for i in by_id) == 2  # 3 rows, one link written twice


def test_rows_sort_by_time_then_kind_then_file_order(rows):
    by_id, order, _ = rows
    times = [by_id[i].ts_ms for i in order if by_id[i].ts_ms is not None]
    assert times == sorted(times)
    assert order[0] == "pr:github_com_apache_kafka_pull_77"  # 2024, though last in the file
    tie = [i for i in order if i in ("msg:20260501-1-ccccc", "msg:20260501-2-ddddd")]
    assert tie == ["msg:20260501-1-ccccc", "msg:20260501-2-ddddd"]  # HERB order kept on a tie
    meeting, chat = order.index("mtg:wdAI_planning_1"), order.index("chat:wdAI_planning_1_chat")
    assert meeting < chat  # same minute: kind order
    assert all(by_id[i].kind == "link" for i in order[-2:])  # timeless rows last


def test_bot_and_external_rows_are_kept_and_flagged(rows):
    by_id, _, _ = rows
    bot = by_id["msg:20260501-0-aaaaa"]
    assert bot.system and bot.author_eid is None and bot.meta["sender"] == "slack_admin_bot"
    ext = by_id["pr:github_com_apache_kafka_pull_77"]
    assert ext.external and ext.author_eid is None and ext.meta["login"] == "EMP_123456789"
    assert ext.meta["reviews"][0]["reviewer_eid"] is None
    internal = by_id["pr:github_com_salesforce_Widget_pull_1"]
    assert not internal.external and internal.author_eid == "eid_aaaa0002"


def test_pr_fields_are_typed(rows):
    pr = rows[0]["pr:github_com_salesforce_Widget_pull_1"].meta
    assert pr["merged"] is False and pr["mergeable"] is True and pr["number"] == 1
    assert pr["repo"] == "salesforce/Widget"
    review = pr["reviews"][0]
    assert review["ts_iso"] == "2026-05-03T09:15:00Z" and review["state"] == "CHANGES_REQUESTED"


def test_docs_carry_version_and_base(rows):
    by_id, _, _ = rows
    final = by_id["doc:final_wdai_vision_document"]
    assert final.meta["version"] == "final" and final.meta["base_id"] == "doc:wdai_vision_document"
    assert final.meta["feedback"] == "- Add numbers." and final.author_eid == "eid_aaaa0001"
    assert by_id["doc:wdai_vision_document"].meta["version"] == "base"


def test_meeting_chat_gets_its_time_and_meeting(rows):
    chat = rows[0]["chat:wdAI_planning_1_chat"]
    assert chat.ts_iso == "2026-05-07T15:30:00Z"
    assert chat.meta["meeting_id"] == "mtg:wdAI_planning_1"
    meeting = rows[0]["mtg:wdAI_planning_1"]
    assert meeting.participants == ["eid_aaaa0002", "eid_aaaa0003"]


def test_links_are_keyed_by_address(rows):
    by_id, _, stats = rows
    assert stats["duplicate_links_dropped"] == 1
    assert s1.link_id("https://GitHub.com/example/lib/") == s1.link_id(
        "https://github.com/example/lib"
    )
    lib = by_id[s1.link_id("https://github.com/example/lib")]
    assert lib.text == "An open-source library." and lib.external  # first row wins
    demo = by_id[s1.link_id("https://sf-internal.slack.com/archives/Widget/demo_1")]
    assert not demo.external


def test_text_stays_verbatim(rows):
    msg = rows[0]["msg:20260430-0-bbbbb"]
    assert msg.text.endswith("<https://github.com/salesforce/Widget/pull/1|PR 1>")


def test_jsonl_roundtrips_and_never_holds_oracle_or_questions(rows, tmp_path):
    by_id, order, _ = rows
    path = s1.write_jsonl([by_id[i] for i in order], tmp_path / "artifacts.jsonl")
    text = path.read_text("utf-8")
    assert "SECRET" not in text and "eid_secret01" not in text
    assert [a.id for a in s1.read_jsonl(path)] == order
