"""The single gateway for LLM calls. Built in Phase 4; Batch API path in Phase 7.

Anthropic SDK 1.x. Disk cache keyed by hash(model, prompt, schema version); token and
cost accounting with a daily spend cap; structured outputs validated against Pydantic
models; a clear error when credentials are missing. Load the claude-api skill first.
Spec: docs/CONTEXT.md §8; facts in docs/decisions.md (API facts).

How a call goes (D033):
1. The request is hashed; a cached answer is returned at no cost.
2. The spend ledger checks the worst case (every input token plus `max_tokens` of output at
   list price) against LORE_DAILY_SPEND_CAP_USD and refuses the call if it could cross it. In
   org mode answers also have a share of that cap, so questions can't use up what reading new
   items needs, and each person a daily budget of their own (D124).
3. The call runs (`messages.parse` when a Pydantic `output` is given: the API constrains the
   response to its schema). Refusals and truncated answers raise; nothing is silently empty.
4. The real cost, from the response's usage, goes into the ledger, a JSONL call log and the
   cache. The system prompt is marked for prompt caching (it repeats across calls).
"""

import hashlib
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from app.config import get_settings

Role = Literal["extract", "answer", "judge"]
SCHEMA_VERSION = 1  # bump when a prompt or output model changes meaning, to bypass old cache
PROMPT_LIMIT_TOKENS = 100_000  # Haiku 5.5 doubles its price above this (pricing page)
CHARS_PER_TOKEN_WORST = 3.0  # conservative: Haiku 5.5's tokenizer counts ~30% more than 4.5's
_FILES_LOCK = threading.Lock()  # the eval runs calls in threads; ledger and log are shared files

# USD per million tokens: input, output, cache write (5 min), cache read.
# Source: platform.claude.com/docs/en/about-claude/pricing, read 2026-10-13.
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01),
    "claude-haiku-4-5": (1.00, 5.00, 1.25, 0.10),
    "claude-sonnet-5-5": (2.00, 10.00, 2.50, 0.10),
}


class LLMError(RuntimeError):
    """An LLM call that didn't produce a usable answer."""


class MissingCredentials(LLMError):
    pass


class SpendCapReached(LLMError):
    pass


class PersonCapReached(SpendCapReached):
    """One person's answers reached their daily budget; everyone else can still ask."""


class Refused(LLMError):
    pass


class CacheOnly(LLMError):
    """LORE_LLM_CACHE_ONLY is on (a public demo) and this call isn't cached: no API call."""


def refuse_uncached() -> None:
    """A public deployment replays cached answers only, so strangers can't spend the key."""
    if get_settings().lore_llm_cache_only:
        raise CacheOnly(
            "This public demo replays answers Lore has already worked out, and this question "
            "isn't one of them. Pick a suggested question, or run Lore yourself to ask anything."
        )


@dataclass(frozen=True)
class LLMResult:
    text: str
    parsed: dict[str, Any] | None
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float
    cached: bool  # served from Lore's disk cache: no call, no cost
    request_id: str | None


def prices() -> dict[str, tuple[float, float, float, float]]:
    """PRICES, with LORE_PRICES_FILE's entries on top (D121)."""
    path = get_settings().lore_prices_file
    if not path:
        return PRICES
    import yaml

    if not path.is_file():
        raise LLMError(f"LORE_PRICES_FILE {path} doesn't exist")
    extra = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out = dict(PRICES)
    for model, p in extra.items():
        if not (isinstance(p, list) and len(p) == 4 and all(isinstance(x, int | float) for x in p)):
            raise LLMError(
                f"LORE_PRICES_FILE: {model} needs [input, output, cache write, cache read] in USD "
                "per million tokens"
            )
        out[str(model)] = tuple(float(x) for x in p)
    return out


def cost_usd(model: str, inp: int, out: int, cache_write: int = 0, cache_read: int = 0) -> float:
    table = prices()
    if model not in table:
        raise LLMError(
            f"no price for model {model!r}: add it to llm.PRICES, or to LORE_PRICES_FILE"
        )
    p_in, p_out, p_write, p_read = table[model]
    return (inp * p_in + out * p_out + cache_write * p_write + cache_read * p_read) / 1e6


# --- spend ledger --------------------------------------------------------------------------
# The ledger is {day: usd spent, ..., "_held": {hold id: {"day", "usd", "until", "role",
# "payer"}}, "_answers": {day: usd}, "_payers": {today: {person: usd}}}. A call holds its worst
# case before it runs and settles to the real cost after, all under one file lock shared by
# every process (the API, an eval, the connector loop), so concurrent calls can't each pass the
# cap check and overshoot it together (D098). A hold left by a crashed process lapses after
# HOLD_S. Three budgets (D124): the daily cap over everything; in org mode, answers' share of it
# (the rest is kept for extraction, so the graph doesn't go stale because people asked a lot);
# and each person's answers, charged to whoever `paying` names.
HOLD_S = 600  # longer than one call with its retries (120 s timeout, 3 retries)
HELD = "_held"
ANSWERS = "_answers"
PAYERS = "_payers"
_payer: ContextVar[str | None] = ContextVar("llm_payer", default=None)


@contextmanager
def paying(person: str | None):
    """Charge the calls made inside the block to `person` as well (their daily budget,
    LORE_PERSON_SPEND_CAP_USD, org mode). None: nobody's budget but the shared ones."""
    token = _payer.set(person)
    try:
        yield
    finally:
        _payer.reset(token)


def ledger_path() -> Path:
    return get_settings().cache_dir / "llm_spend.json"


def _lock(path: Path):
    from filelock import FileLock

    path.parent.mkdir(parents=True, exist_ok=True)
    return FileLock(str(path.with_name(path.name + ".lock")), timeout=30)


def _read_ledger(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


REPLACE_TRIES = 40  # x 50 ms: Windows refuses a rename while anyone holds the file open


def _write_ledger(path: Path, days: dict[str, Any]) -> None:
    # Atomic: a reader never sees a half-written ledger.
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(days, indent=1) + "\n", encoding="utf-8", newline="\n")
    for attempt in range(REPLACE_TRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # another process (an older Lore, an antivirus scan) has it open
            if attempt == REPLACE_TRIES - 1:
                raise
            time.sleep(0.05)


def spent_today(path: Path | None = None) -> float:
    path = path or ledger_path()
    with _FILES_LOCK, _lock(path):  # a read mid-write once crashed a held-out judge run
        return float(_read_ledger(path).get(today(), 0.0))


def record_spend(amount: float, path: Path | None = None) -> None:
    path = path or ledger_path()
    with _FILES_LOCK, _lock(path):
        days = _read_ledger(path)
        days[today()] = round(days.get(today(), 0.0) + amount, 6)
        _write_ledger(path, days)


def today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def worst_case(model: str, prompt_chars: int, max_tokens: int) -> float:
    """A call's worst-case cost: every input token plus `max_tokens` of output at list price."""
    est_input = int(prompt_chars / CHARS_PER_TOKEN_WORST)
    if est_input > PROMPT_LIMIT_TOKENS and model == "claude-haiku-5-5":
        raise LLMError(f"prompt of ~{est_input:,} tokens is over Haiku 5.5's cheap-price limit")
    return cost_usd(model, est_input, max_tokens)


def check_budget(
    model: str, prompt_chars: int, max_tokens: int, role: Role | None = None
) -> float:
    """Worst-case cost of a call; raises if it could take today's spend over a cap. Only a
    check: `spend` is what holds the amount while the call runs."""
    worst = worst_case(model, prompt_chars, max_tokens)
    with _FILES_LOCK, _lock(ledger_path()):
        _refuse_over_cap(_read_ledger(ledger_path()), worst, role, _payer.get())
    return worst


def _live_holds(days: dict[str, Any], now: float) -> dict[str, dict[str, Any]]:
    return {k: h for k, h in (days.get(HELD) or {}).items() if h["until"] > now}


def answer_cap() -> float | None:
    """Answers' share of the daily cap: LORE_ANSWER_SPEND_CAP_USD, else half the cap in org
    mode. None in the demo and the eval: there answers and extraction share one cap, as ever."""
    s = get_settings()
    if s.lore_answer_spend_cap_usd is not None:
        return s.lore_answer_spend_cap_usd
    return s.lore_daily_spend_cap_usd / 2 if s.lore_data_mode == "org" else None


def person_cap() -> float | None:
    """What one person's answers may cost a day (org mode); None in the demo."""
    s = get_settings()
    return s.lore_person_spend_cap_usd if s.lore_data_mode == "org" else None


def _refuse_over_cap(
    days: dict[str, Any], worst: float, role: str | None = None, payer: str | None = None
) -> None:
    cap = get_settings().lore_daily_spend_cap_usd
    holds = [h for h in _live_holds(days, time.time()).values() if h["day"] == today()]
    spent = float(days.get(today(), 0.0))
    held = sum(h["usd"] for h in holds)
    if spent + held + worst > cap:
        in_flight = f" plus ${held:.4f} held by calls in flight" if held else ""
        raise SpendCapReached(
            f"today's LLM spend is ${spent:.4f}{in_flight}; this call could add ${worst:.4f}, "
            f"over the ${cap:.2f} cap (LORE_DAILY_SPEND_CAP_USD in .env). Raise the cap or wait "
            "until 00:00 UTC."
        )
    share = answer_cap() if role == "answer" else None
    if share is not None:
        used = float((days.get(ANSWERS) or {}).get(today(), 0.0))
        used += sum(h["usd"] for h in holds if h.get("role") == "answer")
        if used + worst > share:
            raise SpendCapReached(
                f"answers have used ${used:.4f} of today's ${share:.2f} share of the "
                f"${cap:.2f} cap (LORE_ANSWER_SPEND_CAP_USD); the rest is kept for reading new "
                "items. Raise it or wait until 00:00 UTC."
            )
    mine = person_cap() if payer else None
    if mine is not None:
        used = float(((days.get(PAYERS) or {}).get(today()) or {}).get(payer, 0.0))
        used += sum(h["usd"] for h in holds if h.get("payer") == payer)
        if used + worst > mine:
            raise PersonCapReached(
                f"your questions today have cost ${used:.4f}, and the next could add "
                f"${worst:.4f}: over your ${mine:.2f} a day (LORE_PERSON_SPEND_CAP_USD). Try "
                "again after 00:00 UTC, or ask an admin to raise it."
            )


@contextmanager
def spend(worst: float, path: Path | None = None, role: Role | None = None):
    """Hold `worst` against today's caps for the length of one call; yields `pay(amount)`,
    which settles the hold at the real cost. A call that fails before `pay` costs nothing (no
    response, nothing billed) and its hold is released. `role` and the `paying` person decide
    which budgets besides the daily cap the call counts against."""
    path = path or ledger_path()
    hold = f"{os.getpid()}-{threading.get_ident()}-{time.time_ns()}"
    payer = _payer.get()
    with _FILES_LOCK, _lock(path):
        days = _read_ledger(path)
        _refuse_over_cap(days, worst, role, payer)
        days[HELD] = _live_holds(days, time.time()) | {
            hold: {
                "day": today(),
                "usd": worst,
                "until": time.time() + HOLD_S,
                "role": role,
                "payer": payer,
            }
        }
        _write_ledger(path, days)
    paid = False

    def pay(amount: float) -> None:
        nonlocal paid
        _settle(path, hold, amount, role, payer)
        paid = True

    try:
        yield pay
    finally:
        if not paid:
            _settle(path, hold, 0.0, role, payer)


def _settle(
    path: Path, hold: str, amount: float, role: str | None = None, payer: str | None = None
) -> None:
    with _FILES_LOCK, _lock(path):
        days = _read_ledger(path)
        held = _live_holds(days, time.time())
        held.pop(hold, None)
        days[HELD] = held
        if amount:
            day = today()
            days[day] = round(days.get(day, 0.0) + amount, 6)
            if role == "answer":
                answers = days.setdefault(ANSWERS, {})
                answers[day] = round(answers.get(day, 0.0) + amount, 6)
            if payer:  # only today's: a person's budget starts fresh each day
                people = (days.get(PAYERS) or {}).get(day) or {}
                people[payer] = round(people.get(payer, 0.0) + amount, 6)
                days[PAYERS] = {day: people}
        _write_ledger(path, days)


def spent_by(person: str, path: Path | None = None) -> float:
    """What `person`'s answers have cost today."""
    path = path or ledger_path()
    with _FILES_LOCK, _lock(path):
        return float(((_read_ledger(path).get(PAYERS) or {}).get(today()) or {}).get(person, 0.0))


# --- quotes --------------------------------------------------------------------------------
def normalize(text: str) -> str:
    """Case-folded, with runs of whitespace collapsed: the form quotes are compared in."""
    return re.sub(r"\s+", " ", text).strip().casefold()


def quote_in(quote: str, source: str) -> bool:
    """Is `quote` a verbatim substring of `source`, ignoring case and whitespace? (S6)"""
    q = normalize(quote)
    return bool(q) and q in normalize(source)


# --- the call ------------------------------------------------------------------------------
@lru_cache
def client():
    import anthropic

    key = get_settings().anthropic_api_key
    if not key:
        raise MissingCredentials(
            "ANTHROPIC_API_KEY is not set. Create a key at platform.claude.com (API keys), "
            "then add the line ANTHROPIC_API_KEY=<key> to .env in the repo root."
        )
    return anthropic.Anthropic(api_key=key, max_retries=3, timeout=120.0)


@lru_cache
def _cache():
    from diskcache import Cache

    return Cache(str(get_settings().cache_dir / "llm"))


def request_key(model: str, payload: dict[str, Any]) -> str:
    blob = json.dumps({"v": SCHEMA_VERSION, "model": model, **payload}, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def complete(
    *,
    role: Role,
    system: str,
    user: str,
    purpose: str,
    output: type[BaseModel] | None = None,
    max_tokens: int = 4096,
    use_cache: bool = True,
) -> LLMResult:
    """One LLM call through the gateway. `purpose` names it in the call log (e.g. "baseline")."""
    s = get_settings()
    if s.lore_llm_provider == "litellm":  # optional, off by default (D091); lazy import
        from app import llm_litellm

        return llm_litellm.complete(
            role=role,
            system=system,
            user=user,
            purpose=purpose,
            output=output,
            max_tokens=max_tokens,
            use_cache=use_cache,
        )
    model, effort = {
        "extract": (s.lore_extract_model, s.lore_extract_effort),
        "answer": (s.lore_answer_model, s.lore_answer_effort),
        "judge": (s.lore_judge_model, s.lore_judge_effort),
    }[role]
    schema = output.model_json_schema() if output else None
    key = request_key(
        model,
        {"system": system, "user": user, "schema": schema, "effort": effort, "max": max_tokens},
    )
    cache = _cache() if use_cache else None
    if cache is not None and (hit := cache.get(key)) is not None:
        result = LLMResult(**{**hit, "cached": True, "cost_usd": 0.0})
        log_call(purpose, result)
        return result

    refuse_uncached()
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user}],
        "output_config": {"effort": effort},
    }
    with spend(worst_case(model, len(system) + len(user), max_tokens), role=role) as pay:
        started = time.perf_counter()
        if output is not None:
            response = client().messages.parse(output_format=output, **kwargs)
        else:
            response = client().messages.create(**kwargs)
        elapsed_ms = (time.perf_counter() - started) * 1000

        usage = response.usage
        write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cost = cost_usd(model, usage.input_tokens, usage.output_tokens, write, read)
        pay(cost)  # billed even when the answer is unusable

    if response.stop_reason == "refusal":
        raise Refused(f"{model} declined the request ({purpose}): {response.stop_details}")
    if response.stop_reason == "max_tokens":
        raise LLMError(f"{purpose}: answer cut off at max_tokens={max_tokens}; raise it")
    text = "".join(b.text for b in response.content if b.type == "text")
    parsed = None
    if output is not None:
        if response.parsed_output is None:
            raise LLMError(f"{purpose}: no structured output in the response")
        parsed = response.parsed_output.model_dump()

    result = LLMResult(
        text=text,
        parsed=parsed,
        model=model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=read,
        cache_write_tokens=write,
        cost_usd=round(cost, 6),
        cached=False,
        request_id=getattr(response, "_request_id", None),
    )
    if cache is not None:
        cache.set(key, {k: v for k, v in result.__dict__.items() if k != "cached"})
    log_call(purpose, result, elapsed_ms)
    return result


@dataclass(frozen=True)
class Turn:
    """One assistant turn of a tool-using conversation (converse)."""

    content: list[dict[str, Any]]  # the assistant's blocks as request params, thinking kept
    stop_reason: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float
    cached: bool
    request_id: str | None

    @property
    def tool_uses(self) -> list[dict[str, Any]]:
        return [b for b in self.content if b["type"] == "tool_use"]

    @property
    def text(self) -> str:
        return "".join(b["text"] for b in self.content if b["type"] == "text")


def block_param(block: Any) -> dict[str, Any]:
    """A response content block as the request param that sends it back unchanged.

    Haiku 5.5 thinks by default; its thinking blocks (empty text, a signature) must go back
    exactly as received with the tool results, so the history stays append-only.
    """
    if block.type == "text":
        return {"type": "text", "text": block.text}
    if block.type == "thinking":
        return {"type": "thinking", "thinking": block.thinking, "signature": block.signature}
    if block.type == "redacted_thinking":
        return {"type": "redacted_thinking", "data": block.data}
    if block.type == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    raise LLMError(f"unexpected content block type {block.type!r}")


def converse(
    *,
    role: Role,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    purpose: str,
    output_schema: dict[str, Any] | None = None,
    max_tokens: int = 16000,
    use_cache: bool = True,
) -> Turn:
    """One turn of a tool-using conversation through the gateway (the agent loop, 9.0).

    Same locks as `complete`: the disk cache (keyed by the whole conversation, so a re-run
    replays every turn for free), the worst-case budget check, the ledger and the call log.
    Prompt caching: an explicit breakpoint on the system prompt caches tools + system, and
    the top-level automatic breakpoint caches the growing conversation, so each turn pays
    full price only for what it adds. `output_schema` constrains the final text answer; tool
    turns are unaffected. The caller appends `content` unchanged to keep thinking valid.
    """
    s = get_settings()
    if s.lore_llm_provider == "litellm":  # optional, off by default (D091); lazy import
        from app import llm_litellm

        return llm_litellm.converse(
            role=role,
            system=system,
            messages=messages,
            tools=tools,
            purpose=purpose,
            output_schema=output_schema,
            max_tokens=max_tokens,
            use_cache=use_cache,
        )
    model, effort = {
        "extract": (s.lore_extract_model, s.lore_extract_effort),
        "answer": (s.lore_answer_model, s.lore_answer_effort),
        "judge": (s.lore_judge_model, s.lore_judge_effort),
    }[role]
    payload = {
        "system": system,
        "messages": messages,
        "tools": tools,
        "schema": output_schema,
        "effort": effort,
        "max": max_tokens,
    }
    key = request_key(model, payload)
    cache = _cache() if use_cache else None
    if cache is not None and (hit := cache.get(key)) is not None:
        turn = Turn(**{**hit, "cached": True, "cost_usd": 0.0})
        log_call(purpose, turn)
        return turn

    refuse_uncached()
    prompt_chars = len(system) + len(json.dumps(messages)) + len(json.dumps(tools))
    output_config: dict[str, Any] = {"effort": effort}
    if output_schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": output_schema}
    with spend(worst_case(model, prompt_chars, max_tokens), role=role) as pay:
        started = time.perf_counter()
        response = client().messages.create(
            model=model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=tools,
            messages=messages,
            output_config=output_config,
            cache_control={"type": "ephemeral"},
        )
        elapsed_ms = (time.perf_counter() - started) * 1000

        usage = response.usage
        write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cost = cost_usd(model, usage.input_tokens, usage.output_tokens, write, read)
        pay(cost)  # billed even when the turn is unusable

    if response.stop_reason == "refusal":
        raise Refused(f"{model} declined the request ({purpose}): {response.stop_details}")
    if response.stop_reason == "max_tokens":
        raise LLMError(f"{purpose}: turn cut off at max_tokens={max_tokens}; raise it")
    turn = Turn(
        content=[block_param(b) for b in response.content],
        stop_reason=response.stop_reason,
        model=model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=read,
        cache_write_tokens=write,
        cost_usd=round(cost, 6),
        cached=False,
        request_id=getattr(response, "_request_id", None),
    )
    if cache is not None:
        cache.set(key, {k: v for k, v in turn.__dict__.items() if k != "cached"})
    log_call(purpose, turn, elapsed_ms)
    return turn


def log_call(purpose: str, r: LLMResult | Turn, elapsed_ms: float = 0.0) -> None:
    """Append one line per call to data/logs/llm_calls.jsonl (tokens and cost per answer, §7.2)."""
    path = get_settings().data_dir / "logs" / "llm_calls.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "purpose": purpose,
        "model": r.model,
        "cached": r.cached,
        "input_tokens": r.input_tokens,
        "output_tokens": r.output_tokens,
        "cache_read_tokens": r.cache_read_tokens,
        "cost_usd": r.cost_usd,
        "ms": round(elapsed_ms, 1),
        "request_id": r.request_id,
    }
    with _FILES_LOCK, path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(entry) + "\n")
