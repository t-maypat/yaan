"""The answer agent's tool-calling loop (Phase 9.0; spec docs/CONTEXT.md §9.6).

Plain code, no agent framework: ask the model, run the tools it calls (all results go back
in one message), repeat until it writes the final JSON answer, at most 8 tool calls. Then the
deterministic validator checks every citation; one repair round, then whatever still fails is
dropped, and an answer with nothing left becomes an abstention. The history is append-only,
so Haiku 5.5's thinking blocks stay valid and the prompt cache keeps hitting.

`on_event(name, data)` (Phase 12, R1-8) streams a fixed sequence for the UI: `tool_call` and
`tool_result` per tool, then `answer`, `citations` and `subgraph` (the evidence around the
cited artifacts). The answer itself is the same with or without a listener.
"""

import json
import re
import threading
import time
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from app import db, llm
from app.agent import access, tools, validator
from app.agent.contract import SCHEMA, Answer
from app.agent.graph_query import MAX_ATTEMPTS as GRAPH_QUERY_ATTEMPTS
from app.agent.prompts import repair_message, system_prompt, user_message

MAX_TOOL_CALLS = 8
MAX_TURNS = 12
MAX_RESULT_CHARS = 60_000  # one tool result; product lists stay well under it
ARTIFACT_ID = re.compile(r"^(msg|mtg|chat|doc|pr|url):")
TEXT_KEYS = ("text", "snippet", "title", "description", "url")


Listener = Callable[[str, dict[str, Any]], None]


def _quiet(name: str, data: dict[str, Any]) -> None:
    pass


class ToolError(Exception):
    """A tool call that can't run as asked; the model gets the message and can retry."""


class Cancelled(Exception):
    """The caller stopped waiting (the browser closed the stream): no further model turns."""


def call_tool(
    name: str, args: dict[str, Any], scope: tools.Scope, allowed: frozenset[str] | None = None
) -> dict:
    """Run one tool from the registry (R2). The question's product and as-of moment fill in
    when the model passes null; a tool outside this answer's toolset is refused, and so is
    a product id the graph doesn't have, which would otherwise quietly return nothing (the
    model once sent '">' there, D070)."""
    tool = tools.REGISTRY.get(name)
    if tool is None or (allowed is not None and name not in allowed):
        raise ToolError(f"unknown tool {name!r}")
    # The answer model's tools are strict, but an MCP client's calls aren't: check every
    # call against the tool's own schema, so a missing or mistyped argument comes back as
    # an error the caller can fix instead of a KeyError deep in a handler (D098).
    if problem := tools.input_problem(tool.spec, args):
        raise ToolError(f"{name}: {problem}")
    given = args.get("product_id")
    if given and given not in tools.product_ids(scope.graph):
        known = tools.product_ids(scope.graph, refresh=True)
        if given not in known:
            raise ToolError(
                f'unknown product_id {given!r}: pass "" for the question\'s product, or one '
                f"of: {', '.join(sorted(known))}"
            )
    product = given or scope.product_id
    if tool.needs_product and not product:
        raise ToolError(f"{name} needs a product: resolve the product's name first")
    try:
        return tool.run(args, tools.Scope(product, scope.as_of_ms, scope.graph))
    except (tools.ToolInputError, ValueError) as e:  # e.g. rank's unknown metric
        raise ToolError(str(e)) from e


def collect(
    value: Any,
    seen: set[str],
    texts: dict[str, list[str]],
    urls: dict[str, str] | None = None,
    related: dict[str, set[str]] | None = None,
    parent: str | None = None,
) -> None:
    """Record every artifact a tool returned, with its texts (what citations may quote), in
    `urls` each artifact's own url (for validator.autocite), and in `related` the artifacts
    nested in one another's rows, both ways: a PR and the PR that reverted it (for
    validator.reattribute, D070)."""

    def link(aid: str) -> None:
        if related is not None and parent and parent != aid:
            related.setdefault(parent, set()).add(aid)
            related.setdefault(aid, set()).add(parent)

    if isinstance(value, dict):
        aid = value.get("artifact_id") or value.get("id")
        if isinstance(aid, str) and ARTIFACT_ID.match(aid):
            seen.add(aid)
            link(aid)
            parent = aid
            if urls is not None and isinstance(value.get("url"), str):
                urls.setdefault(value["url"], aid)
            texts.setdefault(aid, []).extend(
                v for k in TEXT_KEYS if isinstance(v := value.get(k), str)
            )
            for f in value.get("fields") or []:
                texts[aid].append(f.get("text") or "")
        for v in value.values():
            collect(v, seen, texts, urls, related, parent)
    elif isinstance(value, list):
        for v in value:
            collect(v, seen, texts, urls, related, parent)
    elif isinstance(value, str) and ARTIFACT_ID.match(value):
        seen.add(value)  # e.g. ids of the messages a link was shared in
        link(value)


def fence(value: Any) -> Any:
    """Neutralize artifact tags inside tool data, as the baseline does (§9.6)."""
    if isinstance(value, str):
        return value.replace("</artifact", "&lt;/artifact").replace("<artifact", "&lt;artifact")
    if isinstance(value, dict):
        return {k: fence(v) for k, v in value.items()}
    if isinstance(value, list):
        return [fence(v) for v in value]
    return value


def run_tools(
    turn: llm.Turn,
    state: dict[str, Any],
    scope: tools.Scope,
    on_event: Listener = _quiet,
    allowed: frozenset[str] | None = None,
) -> list:
    results = []
    for use in turn.tool_uses:
        entry: dict[str, Any] = {"tool": use["name"], "input": use["input"]}
        on_event("tool_call", {"tool": use["name"], "input": use["input"]})
        started = time.perf_counter()
        if state["calls"] >= MAX_TOOL_CALLS:
            content, error = "Tool budget used up: answer now with the evidence you have.", True
        elif use["name"] == "graph_query" and state["graph_query_errors"] >= GRAPH_QUERY_ATTEMPTS:
            content, error = "graph_query failed 3 times: answer with the other tools.", True
        else:
            state["calls"] += 1
            try:
                out = call_tool(use["name"], use["input"], scope, allowed)
                if state.get("access"):  # W7: what the viewer can't see never reaches the model
                    out, hidden = state["access"].redact(out)
                    state["hidden"] |= hidden
                collect(out, state["seen"], state["texts"], state["urls"], state["related"])
                entry["cypher"] = out.get("cypher", [])
                if "cypher_run" in out:  # graph_query: the generated statement, as run
                    entry["cypher_text"] = out["cypher_run"]
                content, error = json.dumps(fence(out), ensure_ascii=False, default=str), False
                if len(content) > MAX_RESULT_CHARS:
                    content = content[:MAX_RESULT_CHARS] + " … [result cut: narrow the request]"
            except (ToolError, db.QueryError) as e:  # shown to the model and kept in the trace
                content, error = f"Error: {e}", True
                entry["error"] = str(e)
                if use["name"] == "graph_query":
                    state["graph_query_errors"] += 1
        entry["ms"] = round((time.perf_counter() - started) * 1000, 1)
        state["trace"].append(entry)
        on_event("tool_result", {**entry, "chars": len(content)})
        results.append(
            {"type": "tool_result", "tool_use_id": use["id"], "content": content, "is_error": error}
        )
    return results


def parse(turn: llm.Turn) -> dict[str, Any]:
    try:
        return Answer.model_validate_json(turn.text).model_dump()
    except ValidationError as e:
        raise llm.LLMError(f"final answer doesn't match the contract: {e}") from e


def graph_texts(ids: list[str], graph: str | None) -> dict[str, list[str]]:
    """Each cited artifact's full texts from the graph: the validator's ground truth."""
    if not ids:
        return {}
    rows = db.run("artifact_texts", {"ids": ids}, graph=graph).rows
    return {
        r["id"]: [t for t in (r["title"], r["url"], r["text"], *r["chunks"]) if t] for r in rows
    }


def answer(
    question: str,
    product_id: str | None = None,
    graph: str | None = None,
    on_event: Listener = _quiet,
    as_of_ms: int | None = None,
    toolset: tuple[str, ...] | None = None,
    viewer_id: str | None = None,
    cancel: threading.Event | None = None,
) -> dict:
    """Answer one question with the tools, validated citations, or an abstention.

    `cancel`, once set, stops the answer before its next model turn (raises Cancelled), so
    a closed browser tab stops spending (D098).

    `viewer_id` (W7, D077) answers as that person: every tool result is redacted to what they
    may see, `graph_query` is left out (its free-form rows can't be redacted by id), and the
    result says how many sources were hidden. None = no filtering.

    `as_of_ms` answers for the state at that moment (the Timeline's slider, 12.4): the
    status tools use it unless the model names another date. `toolset` limits the tools,
    for the Lore − graph tools ablation (`tools.SEARCH_ONLY`, 9.8); the prompt follows it.
    """
    names = list(toolset) if toolset else [t["name"] for t in tools.TOOL_SPECS]
    if viewer_id:
        names = [n for n in names if n != "graph_query"]
    specs = [tools.REGISTRY[n].spec for n in names]
    system = system_prompt(names)
    allowed = frozenset(names)
    scope = tools.Scope(product_id, as_of_ms, graph)
    state: dict[str, Any] = {
        "calls": 0,
        "seen": set(),
        "texts": {},
        "urls": {},
        "related": {},
        "access": access.Access(viewer_id, graph) if viewer_id else None,
        "hidden": set(),
        "trace": [],
        "graph_query_errors": 0,
    }
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": user_message(question, product_id, tools.iso(as_of_ms))}
    ]
    turns: list[llm.Turn] = []

    def ask() -> llm.Turn:
        if cancel is not None and cancel.is_set():
            raise Cancelled(f"stopped after {len(turns)} turns: the caller went away")
        turn = llm.converse(
            role="answer",
            system=system,
            messages=messages,
            tools=specs,
            output_schema=SCHEMA,
            purpose="lore" if toolset is None else "lore-search",
        )
        turns.append(turn)
        messages.append({"role": "assistant", "content": turn.content})
        return turn

    final = None
    for _ in range(MAX_TURNS):
        turn = ask()
        if not turn.tool_uses:
            final = parse(turn)
            break
        results = run_tools(turn, state, scope, on_event, allowed)
        messages.append({"role": "user", "content": results})
    if final is None:
        raise llm.LLMError(f"no final answer after {MAX_TURNS} turns")

    def check(candidate: dict[str, Any]) -> tuple[dict[str, Any], validator.Report, list[int]]:
        """Re-point quotes that sit in a related artifact (D070), then validate, both against
        the tool rows plus the full graph text of each artifact involved."""
        texts = {**state["texts"]}

        def load(ids: set[str]) -> None:
            for aid, more in graph_texts(sorted(ids), graph).items():
                texts[aid] = texts.get(aid, []) + more

        cited = {c["artifact_id"] for c in candidate["citations"]} & state["seen"]
        load(cited)
        stray = {
            aid
            for c in candidate["citations"]
            if (aid := c["artifact_id"]) in cited
            and not validator.quoted(c["quote"], texts.get(aid, []))
        }
        near = set().union(*(state["related"].get(a, set()) for a in stray)) & state["seen"]
        load(near - cited)
        candidate, moved = validator.reattribute(candidate, state["related"], texts)
        return candidate, validator.check(candidate, state["seen"], texts), moved

    unvalidated = {"answer_markdown": final["answer_markdown"], "abstain": final["abstain"]}
    final, autocited = validator.autocite(final, state["urls"])
    final, report, reattributed = check(final)
    first_problems = list(report.problems)
    repaired = False
    if not report.ok:
        messages.append({"role": "user", "content": repair_message(report.problems)})
        turn = ask()
        if not turn.tool_uses:  # a repair that calls tools is ignored; the first answer stands
            final, autocited = validator.autocite(parse(turn), state["urls"])
            repaired = True
            final, report, reattributed = check(final)
    cleaned, dropped = validator.clean(final, report)
    if cleaned["abstain"]:
        searched = ", ".join(sorted({e["tool"] for e in state["trace"]})) or "no tools"
        cleaned = {
            "answer_markdown": f"Not found in Lore's graph: no evidence answers this question. "
            f"Searched with: {searched}.",
            "citations": [],
            "abstain": True,
        }
    out = {
        "question": question,
        **cleaned,
        "citations": [
            {
                **c,
                "retrieved": c["artifact_id"] in state["seen"],
                "quote_ok": c["n"] not in report.bad,
            }
            for c in cleaned["citations"]
        ],
        "retrieved": sorted(state["seen"]),
        "viewer_id": viewer_id,
        "hidden_sources": len(state["hidden"]),
        "trace": state["trace"],
        # the model's first final answer, before autocite, repair and dropping: scoring it
        # gives the Lore − citation validator ablation from the same run (R2, W2)
        "unvalidated": unvalidated,
        "validator": {
            "first_problems": first_problems,
            "repaired": repaired,
            "autocited": autocited,
            "reattributed": reattributed,
            "final_problems": report.problems,
            "dropped": dropped,
            "model_abstained": final["abstain"],
        },
        "llm": {
            "model": turns[0].model,
            "turns": len(turns),
            "input_tokens": sum(t.input_tokens for t in turns),
            "output_tokens": sum(t.output_tokens for t in turns),
            "cache_read_tokens": sum(t.cache_read_tokens for t in turns),
            "cache_write_tokens": sum(t.cache_write_tokens for t in turns),
            "cost_usd": round(sum(t.cost_usd for t in turns), 6),
            "cached": all(t.cached for t in turns),
        },
    }
    keys = ("answer_markdown", "abstain", "validator", "llm", "viewer_id", "hidden_sources")
    on_event("answer", {k: out[k] for k in keys})
    on_event("citations", {"citations": out["citations"]})
    if on_event is not _quiet:
        ids = sorted({c["artifact_id"] for c in out["citations"]})
        on_event("subgraph", evidence_subgraph(ids, graph))
    return out


def evidence_subgraph(ids: list[str], graph: str | None = None) -> dict[str, Any]:
    """Nodes and edges around the cited artifacts, for the evidence pane (Cytoscape)."""
    if not ids:
        return {"nodes": [], "edges": [], "cypher": []}
    rows = db.run("evidence_subgraph", {"ids": ids, "limit": 200}, graph=graph).rows
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in rows:
        for side in ("src", "dst"):
            nid = r[f"{side}_id"]
            if nid and nid not in nodes:
                nodes[nid] = {
                    "id": nid,
                    "label": r[f"{side}_label"],
                    "name": r[f"{side}_name"],
                    "cited": nid in ids,
                }
        if r["src_id"] and r["dst_id"]:
            edges[(r["src_id"], r["rel"], r["dst_id"])] = {
                "source": r["src_id"],
                "target": r["dst_id"],
                "rel": r["rel"],
            }
    return {
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
        "cypher": ["evidence_subgraph.cypher"],
    }
