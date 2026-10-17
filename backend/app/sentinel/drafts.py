"""Drafted follow-ups for an alert (11.3). Spec: docs/CONTEXT.md §9.5, D055.

Three kinds of Action (CONTEXT §9.2), each a fixed template filled from the alert and its blast
radius, never LLM text. Each draft quotes the triggering message word for word and names its
artifact id, so a reader can check it against the source:
- `ticket_comment` on the subject (the issue or PR): what changed, the evidence for the old and
  new state, the fix to recheck, who is affected;
- `doc_fix` on the artifact that stated the replaced state (e.g. the message that said "fixed"):
  a correction to post under it. Not for conflicts, where the state did not change;
- `notify_owner`, one per person in the radius, saying why they are in it.
"""

from typing import Any

# Why a person is in the radius (blast_radius `why`), as the notification says it.
REASON_TEXT = {
    "authored_fix": "you wrote the fix",
    "reviewed_fix": "you reviewed the fix",
    "assigned": "you are the assignee",
    "decided_old_state": "you stated the earlier status",
    "authored_pr": "you authored this PR",
    "reviewed_pr": "you reviewed this PR",
}


def said(value: str | None) -> str:
    return (value or "unknown").replace("_", " ")


def and_list(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def cite(quote: str | None, artifact_id: str | None) -> str:
    return f'"{quote}" ({artifact_id})' if quote else f"({artifact_id})"


def drafts_for(alert: dict[str, Any], radius: dict[str, Any]) -> list[dict[str, Any]]:
    """The Action rows for one alert row (engine.run's upsert_alerts row) and its radius."""
    title, when = alert["subject_title"], alert["ts_iso"][:10]
    new = cite(alert["quote"], alert["artifact_id"])
    old = radius["evidence_old_state"][:1]
    lines = []
    if alert["change"] == "conflict":
        who = alert["actor_name"] or "An unknown speaker"
        role = f" ({alert['actor_role']})" if alert["actor_role"] else ""
        asked = said(alert["to_value"])
        lines.append(f"Unconfirmed: {who}{role} said {new} on {when}, asking for '{asked}'.")
        lines.append(
            f"The status stays '{said(alert['from_value'])}' until someone authorized confirms."
        )
    elif alert["change"] == "outside_group":
        who = alert["actor_name"] or "someone"
        lines.append(f"Dismissed on {when} by {who}: {new}.")
        lines.append(f"Check it with the people who did the work: {alert['reason']}.")
    else:
        lines.append(f"Now '{said(alert['to_value'])}' since {when}: {new}.")
        if old:
            lines.append(
                f"Previously '{said(old[0]['detail'])}': {cite(old[0]['quote'], old[0]['id'])}."
            )
    for pr in radius["prs"]:
        lines.append(f"Fix to recheck: {pr['name']} ({pr['id']}, {pr['detail']}).")
    people = radius["people"]
    reviewers = sorted(
        p["name"] for p in people if {"reviewed_fix", "reviewed_pr"} & set(p["reasons"])
    )
    if reviewers:
        lines.append(f"Reviewed by {and_list(reviewers)}.")
    affected = [
        f"{c['name']} ({co['name']})" if co else c["name"]
        for c in radius["customers"]
        for co in ([x for x in radius["companies"] if x["via"] == c["id"]] or [None])
    ]
    if affected:
        lines.append(f"Reported by {and_list(affected)}.")
    siblings = radius["open_issues_same_component"] + radius["other_issues_same_component"]
    if siblings and alert["kind"] == "regression":
        names = "; ".join(f"{s['name']} ({s['detail']})" for s in siblings[:5])
        lines.append(f"Same component, may share the cause: {names}.")
    base = {"alert_id": alert["id"], "quote": alert["quote"], "artifact_id": alert["artifact_id"]}
    out = [
        {
            **base,
            "kind": "ticket_comment",
            "target_kind": "issue" if alert["subject_id"].startswith("iss:") else "artifact",
            "target_id": alert["subject_id"],
            "target_name": title,
            "draft": "\n".join(lines),
        }
    ]
    if alert["change"] not in ("conflict", "outside_group") and old:
        out.append(
            {
                **base,
                "kind": "doc_fix",
                "target_kind": "artifact",
                "target_id": old[0]["id"],
                "target_name": old[0]["name"],
                "draft": (
                    f"Correction: '{title}' is no longer '{said(old[0]['detail'])}'. "
                    f"It is '{said(alert['to_value'])}' since {when}: {new}."
                ),
            }
        )
    for p in people:
        why = [REASON_TEXT[r] for r in p["reasons"] if r in REASON_TEXT]
        if not why:
            continue
        ask = {
            "conflict": "Please confirm or reject the change.",
            "outside_group": "Please confirm the dismissal or reopen it.",
        }.get(alert["change"], "Please take a look.")
        out.append(
            {
                **base,
                "kind": "notify_owner",
                "target_kind": "person",
                "target_id": p["id"],
                "target_name": p["name"],
                "draft": (
                    f"{p['name']}, '{title}' changed on {when}: {new}. "
                    f"You're on it because {and_list(why)}. {ask}"
                ),
            }
        )
    for a in out:
        a["id"] = f"action:{alert['id']}:{a['kind']}:{a['target_id']}"
    return out


def key_person_drafts(alert: dict[str, Any], radius: dict[str, Any]) -> list[dict[str, Any]]:
    """Drafts for a key_person alert (11.5, D062): a note to the person's manager naming the
    areas only they know and the best backup for each, and a note to the person."""
    areas = radius["areas"]
    names = "; ".join(f"{a['name']} ({a['detail']})" for a in areas[:8])
    more = f" and {len(areas) - 8} more" if len(areas) > 8 else ""
    pairs = []
    for a in areas[:5]:
        best = (radius["backups"].get(a["id"]) or [None])[0]
        if best:
            pairs.append(f"{a['name']}: {best['name']}")
    suggestion = (
        f" Possible backups, by who else knows each area: {'; '.join(pairs)}." if pairs else ""
    )
    base = {"alert_id": alert["id"], "quote": None, "artifact_id": None}
    person = alert["subject_title"]
    out = []
    for m in radius["manager"][:1]:
        out.append(
            {
                **base,
                "kind": "notify_owner",
                "target_kind": "person",
                "target_id": m["id"],
                "target_name": m["name"],
                "draft": (
                    f"{m['name']}, {person} is the only expert on {len(areas)} areas: "
                    f"{names}{more}. If they left, nobody would have a record of work on "
                    f"those areas ({alert['reason']}).{suggestion} Consider pairing someone "
                    "with them on the busiest ones."
                ),
            }
        )
    out.append(
        {
            **base,
            "kind": "notify_owner",
            "target_kind": "person",
            "target_id": alert["subject_id"],
            "target_name": person,
            "draft": (
                f"{person}, you're the only person with a record of work on {len(areas)} areas: "
                f"{names}{more}. Could you walk someone through the ones that matter most?"
                f"{suggestion}"
            ),
        }
    )
    for a in out:
        a["id"] = f"action:{alert['id']}:{a['kind']}:{a['target_id']}"
    return out
