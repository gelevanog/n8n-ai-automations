"""The free-only guard, the explicit paid opt-in, the OpenAI-compatible request body and the budget wrapper
(cache, retries, ledger, call budget, provider-403 fallback)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import httpx
import pytest

from relay.config import Settings
from relay.llm.base import BudgetExceededError, Completion, LLMError, Message, PolicyViolationError, RetryableLLMError
from relay.llm.budget import BudgetedModel, CallLedger, DiskCache
from relay.llm.factory import build_chat_model
from relay.llm.guards import ensure_free_body, ensure_free_models, ensure_served_free
from relay.llm.openai_compat import OpenAICompatibleModel

KEY = "test-" + "key"  # built at runtime: no key-shaped literal in the repository
MESSAGES: list[Message] = [{"role": "user", "content": "hi"}]


@pytest.mark.parametrize(
    "model", ["openai/gpt-5-mini", "nvidia/nemotron:free:online", "anthropic/claude-sonnet-5", "qwen/qwen3-coder"]
)
def test_guard_refuses_non_free_ids(model: str) -> None:
    with pytest.raises(PolicyViolationError):
        ensure_free_models([model])


def test_guard_accepts_free_ids() -> None:
    ensure_free_models(["nvidia/nemotron-3-super-120b-a12b:free", "google/gemma-4-31b-it:free"])


def test_guard_checks_fallbacks_plugins_and_web_search() -> None:
    with pytest.raises(PolicyViolationError):
        ensure_free_body({"model": "a/b:free", "models": ["a/b:free", "c/d"]})
    with pytest.raises(PolicyViolationError):
        ensure_free_body({"model": "a/b:free", "plugins": [{"id": "web"}]})
    with pytest.raises(PolicyViolationError):
        ensure_free_body({"model": "a/b:free", "web_search_options": {}})


def test_guard_rejects_a_paid_served_model() -> None:
    ensure_served_free("a/b:free")
    ensure_served_free(None)
    with pytest.raises(PolicyViolationError):
        ensure_served_free("openai/gpt-5")


def test_factory_refuses_paid_openrouter_model_before_reading_a_key() -> None:
    settings = Settings(
        llm_provider="openrouter", llm_model="openai/gpt-5-mini", openrouter_api_key=None, llm_ledger=None
    )
    with pytest.raises(PolicyViolationError):
        build_chat_model(settings)


def test_paid_models_need_the_explicit_opt_in() -> None:
    settings = Settings(
        llm_provider="openrouter",
        llm_model="openai/gpt-5-mini",
        openrouter_api_key=KEY,
        allow_paid_models=True,
        llm_ledger=None,
    )
    assert settings.free_only is False
    model = build_chat_model(settings)
    assert model.label == "openrouter/openai/gpt-5-mini"
    assert Settings(llm_ledger=None).free_only is True


def test_compat_provider_is_guarded_too() -> None:
    settings = Settings(
        llm_provider="compat", llm_model="some-model", llm_base_url="http://x/v1", llm_api_key=KEY, llm_ledger=None
    )
    with pytest.raises(PolicyViolationError):
        build_chat_model(settings)


def test_openrouter_body_has_fallbacks_and_reasoning() -> None:
    model = OpenAICompatibleModel(
        kind="openrouter",
        base_url="https://openrouter.ai/api/v1",
        model="a/b:free",
        api_key=KEY,
        fallback_models=["c/d:free"],
        reasoning_effort="low",
    )
    body = model.body(MESSAGES, max_tokens=100, temperature=0)
    assert body["models"] == ["a/b:free", "c/d:free"]
    assert body["reasoning"] == {"effort": "low"}


def _transport(*responses: httpx.Response) -> httpx.MockTransport:
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        return queue.pop(0)

    return httpx.MockTransport(handler)


def test_openai_compat_parses_a_reply_and_classifies_errors() -> None:
    ok = httpx.Response(
        200,
        json={
            "model": "a/b:free",
            "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2},
        },
    )
    model = OpenAICompatibleModel(
        kind="openrouter",
        base_url="http://x",
        model="a/b:free",
        api_key=KEY,
        transport=_transport(
            ok,
            httpx.Response(429, json={"error": {"message": "slow"}}),
            httpx.Response(400, json={"error": {"message": "bad"}}),
        ),
    )
    completion = model.complete(MESSAGES, max_tokens=10)
    assert (completion.text, completion.input_tokens, completion.output_tokens) == ("{}", 5, 2)
    with pytest.raises(RetryableLLMError):
        model.complete(MESSAGES, max_tokens=10)
    with pytest.raises(LLMError):
        model.complete(MESSAGES, max_tokens=10)


def test_openai_compat_rejects_answer_from_paid_model() -> None:
    paid = httpx.Response(200, json={"model": "openai/gpt-5", "choices": [{"message": {"content": "x"}}]})
    model = OpenAICompatibleModel(
        kind="openrouter", base_url="http://x", model="a/b:free", api_key=KEY, transport=_transport(paid)
    )
    with pytest.raises(PolicyViolationError):
        model.complete(MESSAGES, max_tokens=10)


class Scripted:
    """A cloud-like model that replays a script of results/exceptions."""

    def __init__(self, *script: object) -> None:
        self.script = list(script)
        self.calls = 0

    @property
    def label(self) -> str:
        return "openrouter/a/b:free"

    @property
    def is_local(self) -> bool:
        return False

    def complete(self, messages: Sequence[Message], *, max_tokens: int, temperature: float = 0.0) -> Completion:
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        assert isinstance(item, str)
        return Completion(text=item, model="a/b:free", input_tokens=3, output_tokens=1)


def test_budget_wrapper_retries_records_and_caches(tmp_path: Path) -> None:
    ledger = CallLedger(tmp_path / "calls.jsonl", max_calls=10)
    inner = Scripted(RetryableLLMError("rate limited", retry_after=0.0), "answer")
    model = BudgetedModel(
        inner, ledger=ledger, cache=DiskCache(tmp_path / "cache"), throttle=None, retry_base_seconds=0.0, tag="t"
    )
    assert model.complete(MESSAGES, max_tokens=5).text == "answer"
    again = model.complete(MESSAGES, max_tokens=5)
    assert again.cached and inner.calls == 2  # second call served from the disk cache
    rows = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert [r["status"] for r in rows] == ["retryable_error", "ok"]
    assert all("messages" not in r and "prompt" not in r for r in rows)  # the ledger never stores prompts


def test_budget_wrapper_enforces_the_call_budget(tmp_path: Path) -> None:
    ledger = CallLedger(tmp_path / "calls.jsonl", max_calls=1)
    model = BudgetedModel(Scripted("a", "b"), ledger=ledger, cache=None, throttle=None, tag="t")
    model.complete(MESSAGES, max_tokens=5)
    with pytest.raises(BudgetExceededError):
        model.complete([{"role": "user", "content": "other"}], max_tokens=5)


def test_budget_wrapper_asks_the_fallback_after_a_provider_403(tmp_path: Path) -> None:
    ledger = CallLedger(None, max_calls=10)
    fallback = BudgetedModel(Scripted("from fallback"), ledger=ledger, cache=None, throttle=None, tag="f")
    model = BudgetedModel(
        Scripted(LLMError("upstream 403: Access denied by security policy")),
        ledger=ledger,
        cache=None,
        throttle=None,
        tag="t",
        policy_fallback=fallback,
    )
    assert model.complete(MESSAGES, max_tokens=5).text == "from fallback"


def test_policy_violations_are_never_retried(tmp_path: Path) -> None:
    inner = Scripted(PolicyViolationError("no"), "never")
    model = BudgetedModel(inner, ledger=CallLedger(None, 10), cache=None, throttle=None, tag="t")
    with pytest.raises(PolicyViolationError):
        model.complete(MESSAGES, max_tokens=5)
    assert inner.calls == 1
