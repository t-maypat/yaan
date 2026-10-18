"""Outgoing messages, rendered for each tool (D083).

A drafted Action stays stored as plain text that cites artifact ids, word for word: that's the
audit trail the Inbox shows and the validator checks. Only at send time does each tool get it in
its own format: Slack Block Kit, Jira's document format (ADF), GitHub Markdown. Every cited id
becomes a link to its source when a connected tool holds it ("Slack message", "Jira KAN-1",
"PR #1"). A HERB id, which no tool holds, stays visible as the id.

A draft line that quotes evidence has the shape  <lead>: "<quote>" (<id>)<tail>  (drafts.cite);
everything else is text with optional "(<id>, <detail>)" references.
"""

import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

ID = r"(?:msg|pr|doc|mtg|chat|url|iss):[^\s(),]+"
CITE = re.compile(rf'"(?P<quote>[^"]+)" \((?P<id>{ID})\)')
REF = re.compile(rf"\((?P<id>{ID})(?P<extra>(?:, [^()]*)?)\)")
NOTIFY = re.compile(
    r"^(?P<name>[^,]+), '(?P<title>.+)' changed on (?P<when>\d{4}-\d{2}-\d{2}): "
    rf'"(?P<quote>.+)" \((?P<id>{ID})\)\. You\'re on it because (?P<why>.+?)\. (?P<ask>.+)$',
    re.S,
)

Link = tuple[str, str | None]  # (label, url)
Resolve = Callable[[str], Link | None]

KIND = {  # alert kind -> (title words, emoji)
    "regression": ("Regression", ":rotating_light:"),
    "reopened": ("Reopened", ":arrows_counterclockwise:"),
    "reversal": ("Reversed decision", ":leftwards_arrow_with_hook:"),
    "conflict": ("Unconfirmed change", ":warning:"),
    "outside_group": ("Dismissed from outside the team", ":warning:"),
    "key_person": ("Key-person risk", ":bust_in_silhouette:"),
    "bus_factor": ("Bus-factor risk", ":bust_in_silhouette:"),
}
REASON = {  # blast-radius reasons, short, for a card's "notifies" line
    "authored_fix": "wrote the fix",
    "reviewed_fix": "reviewed the fix",
    "assigned": "assignee",
    "decided_old_state": "stated the earlier status",
    "authored_pr": "authored the PR",
    "reviewed_pr": "reviewed the PR",
}
FOOTER = "Sent by Lore after a person approved it. Every quote is checked word for word."


def resolver(connectors: Iterable[Any]) -> Resolve:
    """The first connected tool that holds an artifact id gives its label and url."""
    tools = list(connectors)

    def find(artifact_id: str) -> Link | None:
        for c in tools:
            link = getattr(c, "link", None)
            if link and (hit := link(artifact_id)):
                return hit
        return None

    return find


def nice_date(iso_day: str) -> str:
    """2026-10-18 -> 18 Oct 2026 (no %-d: Windows strftime lacks it)."""
    d = datetime.strptime(iso_day[:10], "%Y-%m-%d").replace(tzinfo=UTC)
    return f"{d.day} {d.strftime('%b %Y')}"


def split(line: str) -> tuple[str, str | None, str | None, str]:
    """A draft line as (lead, quote, cited id, tail); quote and id are None without a quote."""
    m = CITE.search(line)
    if not m:
        return line, None, None, ""
    lead = line[: m.start()].rstrip().rstrip(":").rstrip()
    tail = line[m.end() :].strip()
    return lead, m["quote"], m["id"], "" if tail in (".", "") else tail.lstrip(". ").strip()


def kind_words(kind: str | None) -> tuple[str, str]:
    return KIND.get(kind or "", ((kind or "alert").replace("_", " ").capitalize(), ":bell:"))


# --- Slack (mrkdwn + Block Kit) ---------------------------------------------------------------
def slack_link(artifact_id: str, resolve: Resolve | None) -> str:
    hit = resolve(artifact_id) if resolve else None
    if hit and hit[1]:
        return f"<{hit[1]}|{hit[0]}>"
    return hit[0] if hit else f"`{artifact_id}`"


def slack_refs(text: str, resolve: Resolve | None) -> str:
    return REF.sub(lambda m: f"({slack_link(m['id'], resolve)}{m['extra']})", text)


def slack_lines(draft: str, resolve: Resolve | None) -> str:
    """A multi-line draft as mrkdwn: each quote on its own quoted line, under its lead."""
    out = []
    for line in filter(str.strip, draft.split("\n")):
        lead, quote, aid, tail = split(line)
        if quote is None:
            out.append(slack_refs(lead, resolve))
            continue
        out.append(f"{slack_refs(lead, resolve)} · {slack_link(aid or '', resolve)}")
        out.append(f">{quote}")
        if tail:
            out.append(slack_refs(tail, resolve))
    return "\n".join(out)


def slack_notify(action: dict[str, Any], resolve: Resolve | None) -> tuple[str, list[dict]]:
    """A notification to a person in the blast radius: (fallback text, blocks)."""
    draft = action["draft"]
    m = NOTIFY.match(draft)
    if not m:
        name = action.get("target_name") or action.get("target_id") or "someone"
        return f"For {name}", [
            section(f":bell: *For {name}*\n{slack_lines(draft, resolve)}"),
            context("Lore Sentinel · approved follow-up"),
        ]
    title, when = m["title"], nice_date(m["when"])
    blocks = [
        section(f":bell: *{m['name']}*, *{title}* changed on {when}."),
        section(f">{m['quote']}\n{slack_link(m['id'], resolve)}"),
        context(f"You're on it because {m['why']}. {m['ask']}"),
    ]
    return f"{m['name']}: '{title}' changed on {when}", blocks


def slack_correction(action: dict[str, Any], resolve: Resolve | None) -> list[dict]:
    return [
        section(f":pencil2: *Correction from Lore*\n{slack_lines(action['draft'], resolve)}"),
        context(FOOTER),
    ]


def section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text[:3000]}}


def context(text: str) -> dict[str, Any]:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text[:3000]}]}


def alert_blocks(
    alert: dict[str, Any], decided: str | None = None, resolve: Resolve | None = None
) -> list[dict[str, Any]]:
    """An alert as a Slack card: what changed, the evidence, who it reaches, Approve / Dismiss
    (or the verdict once given)."""
    words, emoji = kind_words(alert.get("kind"))
    title = alert.get("subject_title") or alert["summary"]
    header = f"{emoji} {words}: {title}"
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": header[:150], "emoji": True}}
    ]
    fields = []
    if alert.get("from_value") and alert.get("to_value"):
        old, new = (alert[k].replace("_", " ") for k in ("from_value", "to_value"))
        fields.append(f"*Status*\n{old} → *{new}*")
    fields.append(f"*Severity*\n{(alert.get('severity') or '?').capitalize()}")
    if alert.get("product_id"):
        fields.append(f"*Product*\n{alert['product_id']}")
    if alert.get("radius_size") is not None:
        fields.append(f"*Blast radius*\n{alert['radius_size']} people and items")
    blocks.append(
        {"type": "section", "fields": [{"type": "mrkdwn", "text": f} for f in fields[:10]]}
    )
    if alert.get("quote"):
        who = alert.get("actor_name")
        role = f", {alert['actor_role']}" if alert.get("actor_role") else ""
        by = f"— {who}{role}" if who else ""
        src = slack_link(alert["artifact_id"], resolve) if alert.get("artifact_id") else ""
        blocks.append(section(f">{alert['quote']}\n{' · '.join(x for x in (by, src) if x)}"))
    people = [
        f"{p['name']} ({', '.join(REASON.get(r, r) for r in p.get('reasons') or [])})"
        for p in alert.get("notifies") or []
    ]
    if people:
        blocks.append(context(f"*Notifies* {'; '.join(people)}"))
    if decided:
        blocks.append({"type": "divider"})
        blocks.append(context(decided))
        return blocks
    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "style": "primary",
                    "text": {"type": "plain_text", "text": "Approve and send"},
                    "action_id": "lore_approve",
                    "value": alert["id"],
                    "confirm": {
                        "title": {"type": "plain_text", "text": "Send the follow-ups?"},
                        "text": {
                            "type": "mrkdwn",
                            "text": "Lore posts its drafted comments and notifications to the "
                            "tools that hold them, and records your approval as a decision.",
                        },
                        "confirm": {"type": "plain_text", "text": "Approve and send"},
                        "deny": {"type": "plain_text", "text": "Cancel"},
                    },
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Dismiss"},
                    "action_id": "lore_dismiss",
                    "value": alert["id"],
                },
            ],
        }
    )
    stamp = (alert.get("created_iso") or alert.get("ts_iso") or "")[:16].replace("T", " ")
    blocks.append(context(f"Lore Sentinel · {words.lower()} · {stamp} UTC".strip()))
    return blocks


# --- GitHub (Markdown) ------------------------------------------------------------------------
def md_link(artifact_id: str, resolve: Resolve | None) -> str:
    hit = resolve(artifact_id) if resolve else None
    if hit and hit[1]:
        return f"[{hit[0]}]({hit[1]})"
    return hit[0] if hit else f"`{artifact_id}`"


def markdown(action: dict[str, Any], resolve: Resolve | None) -> str:
    title = "Correction" if action["kind"] == "doc_fix" else "Follow-up"
    out = [f"### Lore Sentinel · {title}", ""]
    for line in filter(str.strip, action["draft"].split("\n")):
        lead, quote, aid, tail = split(line)
        lead = REF.sub(lambda m: f"({md_link(m['id'], resolve)}{m['extra']})", lead)
        if quote is None:
            out.append(f"- {lead}")
            continue
        out.append(f"- **{lead}** · {md_link(aid or '', resolve)}")
        out.append(f"  > {quote}")
        if tail:
            out.append(f"  {tail}")
    out += ["", f"<sub>{FOOTER}</sub>"]
    return "\n".join(out)


# --- Jira (Atlassian Document Format) ---------------------------------------------------------
def adf_link(artifact_id: str, resolve: Resolve | None) -> dict[str, Any]:
    hit = resolve(artifact_id) if resolve else None
    if hit and hit[1]:
        return {
            "type": "text",
            "text": hit[0],
            "marks": [{"type": "link", "attrs": {"href": hit[1]}}],
        }
    return {"type": "text", "text": hit[0] if hit else artifact_id, "marks": [{"type": "code"}]}


def adf_inline(text: str, resolve: Resolve | None, strong: bool = False) -> list[dict[str, Any]]:
    """Text with "(id, detail)" references turned into linked labels."""
    nodes: list[dict[str, Any]] = []
    marks = [{"type": "strong"}] if strong else []

    def add(t: str) -> None:
        if t:
            nodes.append({"type": "text", "text": t, **({"marks": marks} if marks else {})})

    at = 0
    for m in REF.finditer(text):
        add(text[at : m.start()] + "(")
        nodes.append(adf_link(m["id"], resolve))
        add(m["extra"] + ")")
        at = m.end()
    add(text[at:])
    return nodes


def jira_adf(action: dict[str, Any], resolve: Resolve | None) -> dict[str, Any]:
    """A panel naming Lore, then one list item per draft line, each quote in italics with a
    link to its source."""
    correction = action["kind"] == "doc_fix"
    items = []
    for line in filter(str.strip, action["draft"].split("\n")):
        lead, quote, aid, tail = split(line)
        paras = []
        if quote is None:
            paras.append({"type": "paragraph", "content": adf_inline(lead, resolve)})
        else:
            first = adf_inline(lead, resolve, strong=True)
            first += [{"type": "text", "text": " · "}, adf_link(aid or "", resolve)]
            paras.append({"type": "paragraph", "content": first})
            q = {"type": "text", "text": f"“{quote}”", "marks": [{"type": "em"}]}
            paras.append({"type": "paragraph", "content": [q]})
            if tail:
                paras.append({"type": "paragraph", "content": adf_inline(tail, resolve)})
        items.append({"type": "listItem", "content": paras})
    head = "Correction" if correction else "Follow-up"
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "panel",
                "attrs": {"panelType": "warning" if correction else "info"},
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "type": "text",
                                "text": f"Lore Sentinel · {head}",
                                "marks": [{"type": "strong"}],
                            }
                        ],
                    }
                ],
            },
            {"type": "bulletList", "content": items},
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": FOOTER, "marks": [{"type": "em"}]}],
            },
        ],
    }
