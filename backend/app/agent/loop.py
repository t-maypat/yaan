"""The answer agent's tool-calling loop (Phase 9.0; spec docs/CONTEXT.md §9.6).

Plain code, no agent framework: ask the model, run the tools it calls (all results go back
in one message), repeat until it writes the final JSON answer, at most 8 tool calls. Then the
deterministic validator checks every citation; one repair round, then whatever still fails is
dropped, and an answer with nothing left becomes an abstention. The history is append-only,
so Haiku 5.5's thinking blocks stay valid and the prompt cache keeps hitting.
"""

import json
import re
import time
from typing import Any

from pydantic import ValidationError

from app import db, llm
from app.agent import tools, validator
from app.agent.contract import SCHEMA, Answer
from app.agent.prompts import SYSTEM, repair_message, user_message

MAX_TOOL_CALLS = 8
MAX_TURNS = 12
MAX_RESULT_CHARS = 60_000  # one tool result; product lists stay well under it
ARTIFACT_ID = re.compile(r"^(msg|mtg|chat|doc|pr|url):")
TEXT_KEYS = ("text", "snippet", "title", "description", "url")


class ToolError(Exception):
    """A tool call that can't run as asked; the model gets the message and can retry."""


def call_tool(name: str, args: dict[str, Any], scope: str | None, graph: str | None) -> dict:
    product = args.get("product_id") or scope
    if name == "search_artifacts":
        return tools.agent_search(args["query"], args.get("kinds"), product, args.get("k"), graph)
    if name == "resolve_entity":
        return tools.resolve_entity(args["text"], product, graph)
    if name == "get_artifact":
        return tools.get_artifact(args["artifact_id"], graph)
    if not product:
        raise ToolError(f"{name} needs a product: resolve the product's name first")
    if name == "doc_discussion":
        return tools.doc_discussion(args["doc"], product, graph)
    if name == "issue_reports":
        return tools.issue_reports(product, graph)
    if name == "list_prs":
        return tools.list_prs(product, args.get("status"), graph)
    if name == "links_shared":
        return tools.links_shared(product, graph)
    raise ToolError(f"unknown tool {name!r}")


def collect(value: Any, seen: set[str], texts: dict[str, list[str]]) -> None:
    """Record every artifact a tool returned, with its texts (what citations may quote)."""
    if isinstance(value, dict):
        aid = value.get("artifact_id") or value.get("id")
        if isinstance(aid, str) and ARTIFACT_ID.match(aid):
            seen.add(aid)
            texts.setdefault(aid, []).extend(
                v for k in TEXT_KEYS if isinstance(v := value.get(k), str)
            )
            for f in value.get("fields") or []:
                texts[aid].append(f.get("text") or "")
        for v in value.values():
            collect(v, seen, texts)
    elif isinstance(value, list):
        for v in value:
            collect(v, seen, texts)
    elif isinstance(value, str) and ARTIFACT_ID.match(value):
        seen.add(value)  # e.g. ids of the messages a link was shared in


def fence(value: Any) -> Any:
    """Neutralize artifact tags inside tool data, as the baseline does (§9.6)."""
    if isinstance(value, str):
        return value.replace("</artifact", "&lt;/artifact").replace("<artifact", "&lt;artifact")
    if isinstance(value, dict):
        return {k: fence(v) for k, v in value.items()}
    if isinstance(value, list):
        return [fence(v) for v in value]
    return value


def run_tools(turn: llm.Turn, state: dict[str, Any], scope: str | None, graph: str | None) -> list:
    results = []
    for use in turn.tool_uses:
        entry: dict[str, Any] = {"tool": use["name"], "input": use["input"]}
        started = time.perf_counter()
        if state["calls"] >= MAX_TOOL_CALLS:
            content, error = "Tool budget used up: answer now with the evidence you have.", True
        else:
            state["calls"] += 1
            try:
                out = call_tool(use["name"], use["input"], scope, graph)
                collect(out, state["seen"], state["texts"])
                entry["cypher"] = out.get("cypher", [])
                content, error = json.dumps(fence(out), ensure_ascii=False, default=str), False
                if len(content) > MAX_RESULT_CHARS:
                    content = content[:MAX_RESULT_CHARS] + " … [result cut: narrow the request]"
            except (ToolError, db.QueryError) as e:  # shown to the model and kept in the trace
                content, error = f"Error: {e}", True
                entry["error"] = str(e)
        entry["ms"] = round((time.perf_counter() - started) * 1000, 1)
        state["trace"].append(entry)
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


def answer(question: str, product_id: str | None = None, graph: str | None = None) -> dict:
    """Answer one question with the tools, validated citations, or an abstention."""
    state: dict[str, Any] = {"calls": 0, "seen": set(), "texts": {}, "trace": []}
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": user_message(question, product_id)}
    ]
    turns: list[llm.Turn] = []

    def ask() -> llm.Turn:
        turn = llm.converse(
            role="answer",
            system=SYSTEM,
            messages=messages,
            tools=tools.TOOL_SPECS,
            output_schema=SCHEMA,
            purpose="lore",
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
        messages.append({"role": "user", "content": run_tools(turn, state, product_id, graph)})
    if final is None:
        raise llm.LLMError(f"no final answer after {MAX_TURNS} turns")

    def check(candidate: dict[str, Any]) -> validator.Report:
        cited = sorted({c["artifact_id"] for c in candidate["citations"]} & state["seen"])
        texts = {**state["texts"]}
        for aid, more in graph_texts(cited, graph).items():
            texts[aid] = texts.get(aid, []) + more
        return validator.check(candidate, state["seen"], texts)

    report = check(final)
    first_problems = list(report.problems)
    repaired = False
    if not report.ok:
        messages.append({"role": "user", "content": repair_message(report.problems)})
        turn = ask()
        if not turn.tool_uses:  # a repair that calls tools is ignored; the first answer stands
            final, repaired = parse(turn), True
            report = check(final)
    cleaned, dropped = validator.clean(final, report)
    if cleaned["abstain"]:
        searched = ", ".join(sorted({e["tool"] for e in state["trace"]})) or "no tools"
        cleaned = {
            "answer_markdown": f"Not found in Lore's graph: no evidence answers this question. "
            f"Searched with: {searched}.",
            "citations": [],
            "abstain": True,
        }
    return {
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
        "trace": state["trace"],
        "validator": {
            "first_problems": first_problems,
            "repaired": repaired,
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
