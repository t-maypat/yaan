"""The optional LiteLLM provider (D091) with litellm mocked: no network, no API keys, no
litellm install needed. Also proves the default provider's cache keys did not move."""

import json
import sys
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app import llm, llm_litellm
from app.config import Settings, get_settings


class Verdict(BaseModel):
    label: str


# Computed with the UNMODIFIED llm.py from the commit before this change (git show HEAD:...),
# so a drift in the Anthropic path's keys fails here and cached demo answers keep hitting.
KEY_COMPLETE = "69384aa3171d5f1dcb4563162adbd2af0d915cc54be22909ecb2b0673b5389c1"
KEY_CONVERSE = "af6cb6965dbce7c43d2cdfbbcddb86cd76addc2c62f273a082267fef40a20ec2"
CONVERSE_PAYLOAD = {
    "system": "S",
    "messages": [{"role": "user", "content": "hi"}],
    "tools": [{"name": "t", "input_schema": {"type": "object"}}],
    "schema": None,
    "effort": "medium",
    "max": 16000,
}


def test_the_anthropic_request_keys_are_pinned():
    key = llm.request_key(
        "claude-haiku-5-5",
        {"system": "S", "user": "U", "schema": None, "effort": "low", "max": 4096},
    )
    assert key == KEY_COMPLETE
    assert llm.request_key("claude-haiku-5-5", CONVERSE_PAYLOAD) == KEY_CONVERSE


def test_settings_default_to_anthropic_and_no_litellm_models():
    s = Settings(_env_file=None)
    assert s.lore_llm_provider == "anthropic"
    assert (s.lore_litellm_model_extract, s.lore_litellm_model_answer) == (None, None)
    with pytest.raises(ValueError):
        Settings(_env_file=None, lore_llm_provider="openai")


# --- the default path: untouched ----------------------------------------------------------
def anthropic_reply(text="OK"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        parsed_output=None,
        stop_reason="end_turn",
        stop_details=None,
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
        _request_id="req_test",
    )


@pytest.fixture
def anthropic_gateway(tmp_path, monkeypatch):
    settings = get_settings().model_copy(
        update={
            "cache_dir": tmp_path / "cache",
            "data_dir": tmp_path / "data",
            "anthropic_api_key": "test-key",
            "lore_llm_provider": "anthropic",
            "lore_extract_model": "claude-haiku-5-5",
            "lore_answer_model": "claude-haiku-5-5",
            "lore_extract_effort": "low",
            "lore_answer_effort": "medium",
        }
    )
    monkeypatch.setattr(llm, "get_settings", lambda: settings)
    llm._cache.cache_clear()
    calls = []

    def send(**kwargs):
        calls.append(kwargs)
        return anthropic_reply()

    monkeypatch.setattr(
        llm, "client", lambda: SimpleNamespace(messages=SimpleNamespace(create=send, parse=send))
    )

    def boom(**kw):
        raise AssertionError("the litellm path ran with the default provider")

    monkeypatch.setattr(llm_litellm, "complete", boom)
    monkeypatch.setattr(llm_litellm, "converse", boom)
    yield SimpleNamespace(settings=settings, calls=calls)
    llm._cache.cache_clear()


def test_the_default_provider_writes_the_same_cache_keys(anthropic_gateway):
    llm.complete(role="extract", system="S", user="U", purpose="t", max_tokens=4096)
    llm.converse(
        role="answer",
        system="S",
        messages=CONVERSE_PAYLOAD["messages"],
        tools=CONVERSE_PAYLOAD["tools"],
        purpose="t",
    )
    cache = llm._cache()
    assert cache.get(KEY_COMPLETE) is not None  # a cached demo answer under this key still hits
    assert cache.get(KEY_CONVERSE) is not None
    assert len(anthropic_gateway.calls) == 2
    llm.complete(role="extract", system="S", user="U", purpose="t", max_tokens=4096)
    assert len(anthropic_gateway.calls) == 2  # replayed from the key above


# --- the litellm path ----------------------------------------------------------------------
PRICED = {"openai/gpt-test": {"input_cost_per_token": 1e-6, "output_cost_per_token": 2e-6}}


def completion_reply(
    content="OK", tool_calls=None, finish="stop", prompt=100, completion=20, cached=0
):
    return SimpleNamespace(
        id="chatcmpl-1",
        choices=[
            SimpleNamespace(
                finish_reason=finish,
                message=SimpleNamespace(content=content, tool_calls=tool_calls),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=prompt,
            completion_tokens=completion,
            prompt_tokens_details=SimpleNamespace(cached_tokens=cached),
        ),
    )


def tool_call(call_id, name, args):
    return SimpleNamespace(
        id=call_id, type="function", function=SimpleNamespace(name=name, arguments=args)
    )


@pytest.fixture
def lite(tmp_path, monkeypatch):
    settings = get_settings().model_copy(
        update={
            "cache_dir": tmp_path / "cache",
            "data_dir": tmp_path / "data",
            "lore_llm_provider": "litellm",
            "lore_litellm_model_extract": "openai/gpt-test",
            "lore_litellm_model_answer": "openai/gpt-test",
            "lore_litellm_model_judge": None,
            "lore_extract_effort": "low",
            "lore_answer_effort": "xhigh",
            "lore_daily_spend_cap_usd": 0.05,
            "lore_llm_cache_only": False,
        }
    )
    monkeypatch.setattr(llm, "get_settings", lambda: settings)
    llm._cache.cache_clear()
    calls, replies = [], []
    state = {"cost": 0.0005}

    def completion(**kwargs):
        calls.append(kwargs)
        return replies.pop(0) if replies else completion_reply()

    def get_model_info(model):
        if model not in PRICED:
            raise Exception("This model isn't mapped yet.")
        return PRICED[model]

    def completion_cost(completion_response):
        if state["cost"] == "boom":
            raise Exception("no cost map")
        return state["cost"]

    fake = SimpleNamespace(
        completion=completion, get_model_info=get_model_info, completion_cost=completion_cost
    )
    monkeypatch.setitem(sys.modules, "litellm", fake)
    # a stand-in for the Anthropic client that fails the test if it is touched
    monkeypatch.setattr(llm, "client", lambda: pytest.fail("the Anthropic client was used"))
    yield SimpleNamespace(settings=settings, calls=calls, replies=replies, state=state)
    llm._cache.cache_clear()


def test_complete_goes_through_litellm_and_is_billed_logged_and_cached(lite):
    first = llm.complete(role="extract", system="s", user="u", purpose="t")
    assert first.text == "OK" and not first.cached and first.model == "openai/gpt-test"
    assert first.cost_usd == pytest.approx(0.0005) and llm.spent_today() == pytest.approx(0.0005)
    assert (first.input_tokens, first.output_tokens, first.request_id) == (100, 20, "chatcmpl-1")
    call = lite.calls[0]
    assert call["model"] == "openai/gpt-test" and call["max_tokens"] == 4096
    assert call["messages"] == [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
    ]
    assert call["reasoning_effort"] == "low" and call["drop_params"] is True
    assert "response_format" not in call
    again = llm.complete(role="extract", system="s", user="u", purpose="t")
    assert again.cached and again.cost_usd == 0.0 and len(lite.calls) == 1
    log = (lite.settings.data_dir / "logs" / "llm_calls.jsonl").read_text("utf-8").splitlines()
    assert len(log) == 2 and '"cached": true' in log[1]


def test_litellm_keys_carry_the_provider_and_cannot_hit_an_anthropic_entry(lite):
    llm.complete(role="extract", system="s", user="u", purpose="t")
    plain = llm.request_key(
        "openai/gpt-test",
        {"system": "s", "user": "u", "schema": None, "effort": "low", "max": 4096},
    )
    with_provider = llm.request_key(
        "openai/gpt-test",
        {
            "provider": "litellm",
            "system": "s",
            "user": "u",
            "schema": None,
            "effort": "low",
            "max": 4096,
        },
    )
    assert llm._cache().get(plain) is None and llm._cache().get(with_provider) is not None


def test_structured_output_is_validated_with_pydantic(lite):
    lite.replies.append(completion_reply(content='{"label": "x"}'))
    out = llm.complete(role="extract", system="s", user="u", output=Verdict, purpose="t")
    assert out.parsed == {"label": "x"} and lite.calls[0]["response_format"] is Verdict
    lite.replies.append(completion_reply(content='{"label": 3}'))
    with pytest.raises(llm.LLMError, match="not valid Verdict"):
        llm.complete(role="extract", system="s", user="bad", output=Verdict, purpose="t")
    assert llm.spent_today() == pytest.approx(0.001)  # the unusable answer was still billed


def test_a_cut_off_or_declined_answer_raises(lite):
    lite.replies.append(completion_reply(finish="length"))
    with pytest.raises(llm.LLMError, match="cut off"):
        llm.complete(role="extract", system="s", user="1", purpose="t")
    lite.replies.append(completion_reply(finish="content_filter"))
    with pytest.raises(llm.Refused):
        llm.complete(role="extract", system="s", user="2", purpose="t")


def test_a_model_litellm_cannot_price_is_refused_before_any_call(lite):
    lite.settings.lore_litellm_model_extract = "mystery/model"
    with pytest.raises(llm.LLMError, match="no price"):
        llm.complete(role="extract", system="s", user="u", purpose="t")
    assert lite.calls == []


def test_a_response_litellm_cannot_cost_records_the_worst_case_and_fails(lite):
    lite.state["cost"] = "boom"
    with pytest.raises(llm.LLMError, match="completion_cost"):
        llm.complete(role="extract", system="s", user="u", purpose="t", max_tokens=1000)
    worst = int(2 / llm.CHARS_PER_TOKEN_WORST) * 1e-6 + 1000 * 2e-6
    assert llm.spent_today() == pytest.approx(worst, abs=1e-6)
    lite.state["cost"] = None
    with pytest.raises(llm.LLMError, match="None"):
        llm.complete(role="extract", system="s", user="v", purpose="t", max_tokens=1000)


def test_the_spend_cap_refuses_before_calling(lite):
    llm.record_spend(0.0499)
    with pytest.raises(llm.SpendCapReached, match="cap"):
        llm.complete(role="extract", system="s", user="u", purpose="t")
    assert lite.calls == []


def test_cache_only_replays_and_never_reaches_litellm(lite):
    llm.complete(role="extract", system="s", user="warm", purpose="t")
    lite.settings.lore_llm_cache_only = True
    assert llm.complete(role="extract", system="s", user="warm", purpose="t").cached
    with pytest.raises(llm.CacheOnly):
        llm.complete(role="extract", system="s", user="new", purpose="t")
    assert len(lite.calls) == 1


def test_a_role_without_a_model_and_a_missing_install_fail_with_instructions(lite, monkeypatch):
    with pytest.raises(llm.LLMError, match="LORE_LITELLM_MODEL_JUDGE"):
        llm.complete(role="judge", system="s", user="u", purpose="t")
    monkeypatch.setitem(sys.modules, "litellm", None)  # `import litellm` raises ImportError
    with pytest.raises(llm.LLMError, match=r"uv pip install --python backend/.venv litellm"):
        llm.complete(role="extract", system="s", user="u", purpose="t")


# --- converse ------------------------------------------------------------------------------
TOOLS = [
    {
        "name": "search",
        "description": "Find things",
        "strict": True,
        "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
    }
]
HISTORY = [
    {"role": "user", "content": "What changed?"},
    {
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "text", "text": "Let me look."},
            {"type": "tool_use", "id": "call_1", "name": "search", "input": {"q": "csv"}},
        ],
    },
    {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "call_1",
                "content": "found 2",
                "is_error": False,
            },
            {"type": "text", "text": "Go on."},
        ],
    },
]


def test_messages_and_tools_translate_to_openai_format():
    out = llm_litellm.to_openai_messages("SYS", HISTORY)
    assert out == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "What changed?"},
        {
            "role": "assistant",
            "content": "Let me look.",  # the thinking block is dropped
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "search", "arguments": '{"q": "csv"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "found 2"},  # before the text
        {"role": "user", "content": "Go on."},
    ]
    failed = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "c",
                    "content": [{"type": "text", "text": "boom"}],
                    "is_error": True,
                }
            ],
        }
    ]
    assert llm_litellm.to_openai_messages("S", failed)[1]["content"] == "ERROR: boom"
    assert llm_litellm.to_openai_tools(TOOLS) == [
        {
            "type": "function",
            "function": {
                "name": "search",
                "description": "Find things",
                "parameters": TOOLS[0]["input_schema"],
                "strict": True,
            },
        }
    ]


def test_converse_returns_anthropic_blocks_and_a_stop_reason(lite):
    lite.replies.append(
        completion_reply(
            content="Searching.", tool_calls=[tool_call("call_2", "search", '{"q": "x"}')]
        )
    )
    turn = llm.converse(role="answer", system="SYS", messages=HISTORY, tools=TOOLS, purpose="t")
    assert turn.stop_reason == "tool_use" and turn.model == "openai/gpt-test"
    assert turn.content == [
        {"type": "text", "text": "Searching."},
        {"type": "tool_use", "id": "call_2", "name": "search", "input": {"q": "x"}},
    ]
    assert turn.tool_uses[0]["input"] == {"q": "x"}
    call = lite.calls[0]
    assert call["tools"][0]["function"]["name"] == "search"
    assert call["reasoning_effort"] == "high"  # xhigh has no LiteLLM equivalent
    assert "response_format" not in call and call["messages"][0]["role"] == "system"
    again = llm.converse(role="answer", system="SYS", messages=HISTORY, tools=TOOLS, purpose="t")
    assert again.cached and len(lite.calls) == 1


def test_the_final_turn_ends_and_the_output_schema_becomes_response_format(lite):
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    lite.replies.append(completion_reply(content='{"answer": "done"}'))
    turn = llm.converse(
        role="answer",
        system="S",
        messages=[{"role": "user", "content": "q"}],
        tools=TOOLS,
        purpose="t",
        output_schema=schema,
    )
    assert turn.stop_reason == "end_turn" and turn.text == '{"answer": "done"}'
    assert lite.calls[0]["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "answer", "schema": schema, "strict": True},
    }


def test_converse_fails_loudly_on_bad_tool_arguments_and_empty_replies(lite):
    lite.replies.append(
        completion_reply(content=None, tool_calls=[tool_call("c", "search", "{oops")])
    )
    with pytest.raises(llm.LLMError, match="aren't JSON"):
        llm.converse(role="answer", system="S", messages=HISTORY, tools=TOOLS, purpose="t")
    lite.replies.append(completion_reply(content=None, tool_calls=None))
    with pytest.raises(llm.LLMError, match="neither text nor a tool call"):
        llm.converse(role="answer", system="S", messages=HISTORY[:1], tools=TOOLS, purpose="t")
    json.dumps(TOOLS)  # the fixtures stay plain JSON
