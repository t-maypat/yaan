"""The single gateway for LLM calls. Built in Phase 4; Batch API path in Phase 7.

Anthropic SDK 1.x. Disk cache keyed by hash(model, prompt, schema version); token and
cost accounting with a daily spend cap; structured outputs validated against Pydantic
models; a clear error when credentials are missing. Load the claude-api skill first.
Spec: docs/CONTEXT.md §8; facts in docs/decisions.md (API facts).

How a call goes (D033):
1. The request is hashed; a cached answer is returned at no cost.
2. The spend ledger checks the worst case (every input token plus `max_tokens` of output at
   list price) against LORE_DAILY_SPEND_CAP_USD and refuses the call if it could cross it.
3. The call runs (`messages.parse` when a Pydantic `output` is given: the API constrains the
   response to its schema). Refusals and truncated answers raise; nothing is silently empty.
4. The real cost, from the response's usage, goes into the ledger, a JSONL call log and the
   cache. The system prompt is marked for prompt caching (it repeats across calls).
"""

import hashlib
import json
import re
import threading
import time
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


class Refused(LLMError):
    pass


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


def cost_usd(model: str, inp: int, out: int, cache_write: int = 0, cache_read: int = 0) -> float:
    if model not in PRICES:
        raise LLMError(f"no price for model {model!r}: add it to llm.PRICES from the pricing page")
    p_in, p_out, p_write, p_read = PRICES[model]
    return (inp * p_in + out * p_out + cache_write * p_write + cache_read * p_read) / 1e6


# --- spend ledger --------------------------------------------------------------------------
def ledger_path() -> Path:
    return get_settings().cache_dir / "llm_spend.json"


def spent_today(path: Path | None = None) -> float:
    path = path or ledger_path()
    if not path.exists():
        return 0.0
    return float(json.loads(path.read_text(encoding="utf-8")).get(today(), 0.0))


def record_spend(amount: float, path: Path | None = None) -> None:
    path = path or ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _FILES_LOCK:
        days = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        days[today()] = round(days.get(today(), 0.0) + amount, 6)
        path.write_text(json.dumps(days, indent=1) + "\n", encoding="utf-8", newline="\n")


def today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def check_budget(model: str, prompt_chars: int, max_tokens: int) -> float:
    """Worst-case cost of a call; raises if it could take today's spend over the cap."""
    est_input = int(prompt_chars / CHARS_PER_TOKEN_WORST)
    if est_input > PROMPT_LIMIT_TOKENS and model == "claude-haiku-5-5":
        raise LLMError(f"prompt of ~{est_input:,} tokens is over Haiku 5.5's cheap-price limit")
    worst = cost_usd(model, est_input, max_tokens)
    cap = get_settings().lore_daily_spend_cap_usd
    spent = spent_today()
    if spent + worst > cap:
        raise SpendCapReached(
            f"today's LLM spend is ${spent:.4f}; this call could add ${worst:.4f}, over the "
            f"${cap:.2f} cap (LORE_DAILY_SPEND_CAP_USD in .env). Raise the cap or wait until "
            "00:00 UTC."
        )
    return worst


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

    check_budget(model, len(system) + len(user), max_tokens)
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user}],
        "output_config": {"effort": effort},
    }
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
    record_spend(cost)  # billed even when the answer is unusable

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


def log_call(purpose: str, r: LLMResult, elapsed_ms: float = 0.0) -> None:
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
