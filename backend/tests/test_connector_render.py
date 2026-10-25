"""Outgoing messages per tool (D083), from the drafts the live run of 18 Oct actually sent:
cited ids become links to their sources, quotes stay word for word, nothing is lost."""

import json

from app.connectors import render

SLACK_ID = "msg:synthetic-slack-C0CA7PW05V2-1792331455.215619"
JIRA_ID = "msg:synthetic-jira-KAN-1-change-10001"
PR_ID = "pr:synthetic-github-t-maypat-lore-sandbox-1"
QUOTE = (
    "CUST-0003 has reopened 'CSV export times out on large dashboards': the CSV export times "
    "out on large dashboards again since this morning's release."
)
NOTE = (
    f"Hannah Garcia, 'CSV export times out on large dashboards' changed on 2026-10-18: "
    f'"{QUOTE}" ({SLACK_ID}). You\'re on it because you wrote the fix, you are the assignee and '
    "you stated the earlier status. Please take a look."
)
TICKET = "\n".join(
    [
        f"Now 'reopened' since 2026-10-18: \"{QUOTE}\" ({SLACK_ID}).",
        "Previously 'resolved': \"Jira KAN-1 'CSV export times out on large dashboards' moved "
        f'from To Do to Done" ({JIRA_ID}).',
        f"Fix to recheck: Fix CSV export timeout on large dashboards ({PR_ID}, merged).",
        "Reviewed by Bob Johnson.",
    ]
)
LINKS = {
    SLACK_ID: ("Message in #vizforce-bugs", "https://tpos.slack.com/archives/C0CA7PW05V2/p1"),
    JIRA_ID: ("Jira KAN-1", "https://x.atlassian.net/browse/KAN-1"),
    PR_ID: ("PR #1", "https://github.com/t-maypat/lore-sandbox/pull/1"),
}


def resolve(aid):
    return LINKS.get(aid)


def test_split_takes_a_line_apart_at_its_quote():
    lead, quote, aid, tail = render.split(TICKET.split("\n")[0])
    assert (lead, quote, aid, tail) == ("Now 'reopened' since 2026-10-18", QUOTE, SLACK_ID, "")
    assert render.split("Reviewed by Bob Johnson.") == ("Reviewed by Bob Johnson.", None, None, "")


def test_a_slack_notification_names_the_person_quotes_and_links_the_source():
    text, blocks = render.slack_notify(
        {"kind": "notify_owner", "target_name": "Hannah Garcia", "draft": NOTE}, resolve
    )
    flat = json.dumps(blocks)
    assert (
        text == "Hannah Garcia: 'CSV export times out on large dashboards' changed on 18 Oct 2026"
    )
    assert f">{QUOTE}" in blocks[1]["text"]["text"]
    assert "<https://tpos.slack.com/archives/C0CA7PW05V2/p1|Message in #vizforce-bugs>" in flat
    assert SLACK_ID not in flat  # no raw id once it has a link
    assert blocks[2]["type"] == "context" and "you wrote the fix" in flat


def test_an_unknown_shape_still_posts_with_ids_linked():
    _, blocks = render.slack_notify(
        {"kind": "notify_owner", "target_name": "Ann", "draft": f"See ({PR_ID}, merged)."}, resolve
    )
    assert "(<https://github.com/t-maypat/lore-sandbox/pull/1|PR #1>, merged)" in json.dumps(blocks)


def test_github_markdown_quotes_under_their_lead_with_links():
    md = render.markdown({"id": "act:7", "kind": "ticket_comment", "draft": TICKET}, resolve)
    assert md.startswith("### Yaan Sentinel · Follow-up")
    assert md.endswith("<!-- yaan-action:act:7 -->")  # found again on a retry (D110)
    assert "- **Now 'reopened' since 2026-10-18** · [Message in #vizforce-bugs](" in md
    assert f"  > {QUOTE}" in md
    assert "([PR #1](https://github.com/t-maypat/lore-sandbox/pull/1), merged)" in md
    assert "- Reviewed by Bob Johnson." in md


def test_jira_adf_is_a_panel_a_list_and_a_footer_with_linked_sources():
    doc = render.jira_adf({"id": "act:7", "kind": "ticket_comment", "draft": TICKET}, resolve)
    panel, items, footer = doc["content"]
    assert panel["attrs"]["panelType"] == "info" and len(items["content"]) == 4
    assert footer["content"][-1]["text"] == " ref yaan-action:act:7"
    flat = json.dumps(doc)
    assert '"href": "https://github.com/t-maypat/lore-sandbox/pull/1"' in flat
    assert f"“{QUOTE}”" in json.dumps(doc, ensure_ascii=False)


def test_an_id_no_tool_holds_stays_visible():
    herb = "msg:20270101-1-abc"
    fix = {"id": "a", "kind": "doc_fix", "draft": f'It said "fixed" ({herb}).'}
    md = render.markdown(fix, resolve)
    assert f"`{herb}`" in md
    adf = json.dumps(
        render.jira_adf({"id": "a", "kind": "doc_fix", "draft": f"See ({herb})."}, resolve)
    )
    assert herb in adf and '"code"' in adf


def test_alert_card_has_header_fields_quote_and_who_it_notifies():
    alert = {
        "id": "alert:1",
        "kind": "regression",
        "severity": "high",
        "summary": "Regression: x",
        "subject_title": "CSV export times out on large dashboards",
        "product_id": "VizForce",
        "from_value": "resolved",
        "to_value": "reopened",
        "radius_size": 10,
        "quote": QUOTE,
        "artifact_id": SLACK_ID,
        "actor_name": "Ian Davis",
        "actor_role": "Engineering Lead",
        "notifies": [{"name": "Bob Johnson", "reasons": ["reviewed_fix"]}],
        "created_iso": "2026-10-18T13:51:30Z",
    }
    blocks = render.alert_blocks(alert, resolve=resolve)
    flat = json.dumps(blocks, ensure_ascii=False)
    assert blocks[0]["text"]["text"].startswith(":rotating_light: Regression: CSV export")
    assert "resolved → *reopened*" in flat and "*Severity*\\nHigh" in flat
    assert "— Ian Davis, Engineering Lead · <https://tpos.slack.com" in flat
    assert "Bob Johnson (reviewed the fix)" in flat
    assert "alert:1" not in flat.replace('"value": "alert:1"', "")  # the id only in buttons
