"""The optional LiteLLM provider behind `app/llm.py` (D091, EXPERIMENTAL: never run against a
live model).

`LORE_LLM_PROVIDER=litellm` sends `complete()` and `converse()` through LiteLLM instead of the
Anthropic SDK, so any model LiteLLM supports can play a role: `LORE_LITELLM_MODEL_EXTRACT`,
`_ANSWER` and `_JUDGE` name them in LiteLLM's "provider/model" form (for example
`openai/gpt-4o-mini`). With the default provider (`anthropic`) this module is never imported and
llm.py behaves exactly as before: same requests, same cache keys.

What stays the same as the Anthropic path: the disk cache (replays are free), the worst-case
budget check and the spend ledger with its daily cap, the call log, cache-only mode, loud
failures (a refusal, a truncated answer, an unusable structured output, a model whose price
LiteLLM doesn't know), and Pydantic validation of structured output. Quote checks are
unchanged because they never lived in the gateway: callers verify quotes against sources.

What differs, on purpose:
- Cache keys carry `"provider": "litellm"` and the LiteLLM model name, so they can't collide
  with an Anthropic entry, and the Anthropic keys are not touched.
- Cost comes from `litellm.completion_cost`; the budget check needs `litellm.get_model_info`
  prices and refuses a model without them instead of guessing.
- Anthropic-only parameters are mapped or dropped: `effort` becomes `reasoning_effort`
  (low / medium / high; xhigh and max become high) with `drop_params=True`, so a model that
  has no such knob ignores it; `cache_control` and Anthropic's prompt-caching breakpoints are
  dropped (LiteLLM forwards `cache_control` only to providers that take it, and Lore's request
  keys don't depend on it); `thinking` is not sent, and thinking blocks in a replayed
  conversation are dropped, since no other provider can read them.
- `converse()` speaks Anthropic's message format to its caller (the agent loop) and OpenAI's to
  LiteLLM: tool definitions, tool_use / tool_result blocks and stop reasons are translated both
  ways. This translation is the part most likely to need a fix on first contact with a real
  provider.

Docs (retrieved 2026-10-18):
- https://docs.litellm.ai/docs/completion/input (completion parameters: messages, tools,
  response_format, max_tokens, reasoning_effort, drop_params)
- https://docs.litellm.ai/docs/completion/json_mode (response_format with a Pydantic class or a
  json_schema dict)
- https://docs.litellm.ai/docs/completion/function_call (tools / tool_calls in OpenAI format)
- https://docs.litellm.ai/docs/completion/token_usage (completion_cost, usage fields)
- https://docs.litellm.ai/docs/completion/output (response shape: choices[0].message,
  finish_reason, usage)
"""

import json
import time
from typing import Any

from pydantic import BaseModel, ValidationError

from app import llm
from app.llm import LLMError, LLMResult, Role, Turn

INSTALL = (
    "LORE_LLM_PROVIDER=litellm needs LiteLLM, which the default install leaves out: "
    "`uv pip install --python backend/.venv litellm` (or `pip install litellm`)"
)
EFFORTS = {"low", "medium", "high"}  # what LiteLLM's reasoning_effort takes


def _litellm() -> Any:
    """Imported here, never at module import: the default install doesn't have it."""
    try:
        import litellm
    except ImportError as e:
        raise LLMError(INSTALL) from e
    return litellm


def role_settings(role: Role) -> tuple[str, str]:
    """(LiteLLM model name, effort) for a role; a role without a model is a loud error."""
    s = llm.get_settings()
    model = {
        "extract": s.lore_litellm_model_extract,
        "answer": s.lore_litellm_model_answer,
        "judge": s.lore_litellm_model_judge,
    }[role]
    if not model:
        raise LLMError(
            f"LORE_LLM_PROVIDER=litellm but LORE_LITELLM_MODEL_{role.upper()} is not set "
            "(a LiteLLM model name such as openai/gpt-4o-mini)"
        )
    effort = {
        "extract": s.lore_extract_effort,
        "answer": s.lore_answer_effort,
        "judge": s.lore_judge_effort,
    }[role]
    return model, effort


# --- cost and budget -----------------------------------------------------------------------
def rates(litellm: Any, model: str) -> tuple[float, float]:
    """USD per input and per output token, or a loud refusal: no price, no call."""
    try:
        info = litellm.get_model_info(model)
        rin, rout = info["input_cost_per_token"], info["output_cost_per_token"]
    except Exception as e:  # unknown model, or a price field LiteLLM doesn't carry
        raise LLMError(
            f"litellm has no price for {model!r} ({type(e).__name__}: {e}); refusing the call. "
            "Pick a model LiteLLM prices, or register one with litellm.register_model"
        ) from e
    if rin is None or rout is None:
        raise LLMError(f"litellm has no input / output price for {model!r}; refusing the call")
    return float(rin), float(rout)


def check_budget(litellm: Any, model: str, prompt_chars: int, max_tokens: int) -> float:
    """Worst case (every input token plus `max_tokens` of output at list price) against the
    daily cap, like llm.check_budget."""
    rin, rout = rates(litellm, model)
    worst = int(prompt_chars / llm.CHARS_PER_TOKEN_WORST) * rin + max_tokens * rout
    cap = llm.get_settings().lore_daily_spend_cap_usd
    spent = llm.spent_today()
    if spent + worst > cap:
        raise llm.SpendCapReached(
            f"today's LLM spend is ${spent:.4f}; this call could add ${worst:.4f}, over the "
            f"${cap:.2f} cap (LORE_DAILY_SPEND_CAP_USD in .env). Raise the cap or wait until "
            "00:00 UTC."
        )
    return worst


def bill(litellm: Any, response: Any, model: str, worst: float, purpose: str) -> float:
    """The real cost from LiteLLM into the ledger. If LiteLLM can't say, the worst case is
    recorded (never understate spend) and the call fails loudly."""
    try:
        cost = litellm.completion_cost(completion_response=response)
    except Exception as e:
        cost = None
        reason = f"{type(e).__name__}: {e}"
    else:
        reason = "it returned None"
    if cost is None:
        llm.record_spend(worst)
        raise LLMError(
            f"{purpose}: litellm.completion_cost couldn't price a {model!r} response ({reason}); "
            f"recorded the worst case, ${worst:.4f}, in the ledger"
        )
    llm.record_spend(float(cost))
    return float(cost)


def usage_of(response: Any) -> tuple[int, int, int]:
    """(input, output, cached input) tokens as LiteLLM reports them. Unlike Anthropic's
    `input_tokens`, `prompt_tokens` includes cached tokens."""
    u = response.usage
    details = getattr(u, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) or 0
    return int(u.prompt_tokens), int(u.completion_tokens), int(cached)


def effort_param(effort: str) -> str:
    return effort if effort in EFFORTS else "high"


def check_finish(response: Any, model: str, purpose: str, max_tokens: int) -> Any:
    """The message of a usable answer; a refusal or a cut-off raises like the Anthropic path."""
    choice = response.choices[0]
    reason = choice.finish_reason
    if reason == "content_filter":
        raise llm.Refused(f"{model} declined the request ({purpose}): finish_reason={reason}")
    if reason == "length":
        raise LLMError(f"{purpose}: answer cut off at max_tokens={max_tokens}; raise it")
    return choice.message


# --- complete ------------------------------------------------------------------------------
def complete(
    *,
    role: Role,
    system: str,
    user: str,
    purpose: str,
    output: type[BaseModel] | None,
    max_tokens: int,
    use_cache: bool,
) -> LLMResult:
    """llm.complete through LiteLLM; same contract and locks."""
    model, effort = role_settings(role)
    schema = output.model_json_schema() if output else None
    key = llm.request_key(
        model,
        {
            "provider": "litellm",
            "system": system,
            "user": user,
            "schema": schema,
            "effort": effort,
            "max": max_tokens,
        },
    )
    cache = llm._cache() if use_cache else None
    if cache is not None and (hit := cache.get(key)) is not None:
        result = LLMResult(**{**hit, "cached": True, "cost_usd": 0.0})
        llm.log_call(purpose, result)
        return result

    llm.refuse_uncached()
    litellm = _litellm()
    worst = check_budget(litellm, model, len(system) + len(user), max_tokens)
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "reasoning_effort": effort_param(effort),
        "drop_params": True,  # let a model without a reasoning knob ignore it
    }
    if output is not None:
        kwargs["response_format"] = output
    started = time.perf_counter()
    response = litellm.completion(**kwargs)
    elapsed_ms = (time.perf_counter() - started) * 1000

    cost = bill(litellm, response, model, worst, purpose)  # billed even if unusable
    inp, out, cached = usage_of(response)
    message = check_finish(response, model, purpose, max_tokens)
    text = message.content or ""
    parsed = None
    if output is not None:
        try:
            parsed = output.model_validate_json(text).model_dump()
        except ValidationError as e:
            raise LLMError(f"{purpose}: the response is not valid {output.__name__}: {e}") from e

    result = LLMResult(
        text=text,
        parsed=parsed,
        model=model,
        input_tokens=inp,
        output_tokens=out,
        cache_read_tokens=cached,
        cache_write_tokens=0,
        cost_usd=round(cost, 6),
        cached=False,
        request_id=getattr(response, "id", None),
    )
    if cache is not None:
        cache.set(key, {k: v for k, v in result.__dict__.items() if k != "cached"})
    llm.log_call(purpose, result, elapsed_ms)
    return result


# --- converse: Anthropic format <-> OpenAI format ------------------------------------------
def result_text(content: Any) -> str:
    """A tool_result's content (a string or a list of text blocks) as one string."""
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content or [] if b.get("type") == "text")


def to_openai_messages(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anthropic messages (text, tool_use, tool_result, thinking blocks) as OpenAI messages.
    Thinking blocks are dropped; each tool_result becomes a `tool` message, which must come
    right after the assistant message that called it, so they precede any user text."""
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        blocks = (
            [{"type": "text", "text": m["content"]}]
            if isinstance(m["content"], str)
            else m["content"]
        )
        if m["role"] == "assistant":
            text = "".join(b["text"] for b in blocks if b["type"] == "text")
            msg: dict[str, Any] = {"role": "assistant", "content": text or None}
            calls = [
                {
                    "id": b["id"],
                    "type": "function",
                    "function": {"name": b["name"], "arguments": json.dumps(b["input"])},
                }
                for b in blocks
                if b["type"] == "tool_use"
            ]
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
            continue
        for b in blocks:
            if b["type"] == "tool_result":
                body = result_text(b.get("content"))
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": b["tool_use_id"],
                        "content": f"ERROR: {body}" if b.get("is_error") else body,
                    }
                )
        text = "".join(b["text"] for b in blocks if b["type"] == "text")
        if text:
            out.append({"role": "user", "content": text})
    return out


def to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anthropic tool definitions (name, description, input_schema, strict) as OpenAI tools."""
    out = []
    for t in tools:
        fn: dict[str, Any] = {
            "name": t["name"],
            "description": t.get("description", ""),
            "parameters": t["input_schema"],
        }
        if "strict" in t:
            fn["strict"] = t["strict"]
        out.append({"type": "function", "function": fn})
    return out


def from_openai_message(message: Any, purpose: str) -> list[dict[str, Any]]:
    """An OpenAI assistant message as Anthropic content blocks (text, tool_use)."""
    content: list[dict[str, Any]] = []
    if message.content:
        content.append({"type": "text", "text": message.content})
    for call in getattr(message, "tool_calls", None) or []:
        try:
            args = json.loads(call.function.arguments or "{}")
        except json.JSONDecodeError as e:
            raise LLMError(
                f"{purpose}: tool call {call.function.name!r} has arguments that aren't JSON: "
                f"{call.function.arguments!r}"
            ) from e
        content.append(
            {"type": "tool_use", "id": call.id, "name": call.function.name, "input": args}
        )
    if not content:
        raise LLMError(f"{purpose}: the response has neither text nor a tool call")
    return content


def converse(
    *,
    role: Role,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    purpose: str,
    output_schema: dict[str, Any] | None,
    max_tokens: int,
    use_cache: bool,
) -> Turn:
    """llm.converse through LiteLLM; the caller still sees Anthropic-format blocks."""
    model, effort = role_settings(role)
    key = llm.request_key(
        model,
        {
            "provider": "litellm",
            "system": system,
            "messages": messages,
            "tools": tools,
            "schema": output_schema,
            "effort": effort,
            "max": max_tokens,
        },
    )
    cache = llm._cache() if use_cache else None
    if cache is not None and (hit := cache.get(key)) is not None:
        turn = Turn(**{**hit, "cached": True, "cost_usd": 0.0})
        llm.log_call(purpose, turn)
        return turn

    llm.refuse_uncached()
    litellm = _litellm()
    prompt_chars = len(system) + len(json.dumps(messages)) + len(json.dumps(tools))
    worst = check_budget(litellm, model, prompt_chars, max_tokens)
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": to_openai_messages(system, messages),
        "reasoning_effort": effort_param(effort),
        "drop_params": True,
    }
    if tools:
        kwargs["tools"] = to_openai_tools(tools)
    if output_schema is not None:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "answer", "schema": output_schema, "strict": True},
        }
    started = time.perf_counter()
    response = litellm.completion(**kwargs)
    elapsed_ms = (time.perf_counter() - started) * 1000

    cost = bill(litellm, response, model, worst, purpose)
    inp, out, cached = usage_of(response)
    message = check_finish(response, model, purpose, max_tokens)
    content = from_openai_message(message, purpose)
    called = any(b["type"] == "tool_use" for b in content)
    turn = Turn(
        content=content,
        stop_reason="tool_use" if called else "end_turn",
        model=model,
        input_tokens=inp,
        output_tokens=out,
        cache_read_tokens=cached,
        cache_write_tokens=0,
        cost_usd=round(cost, 6),
        cached=False,
        request_id=getattr(response, "id", None),
    )
    if cache is not None:
        cache.set(key, {k: v for k, v in turn.__dict__.items() if k != "cached"})
    llm.log_call(purpose, turn, elapsed_ms)
    return turn
