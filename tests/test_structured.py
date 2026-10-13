"""Structured output: lenient parsing, strict validation, one corrective retry, metadata and cost."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from relay.config import Settings
from relay.gateway.schemas import FaqOutput, InvoiceOutput, TriageOutput
from relay.gateway.structured import Prices, StructuredOutputError, complete_structured, load_prices
from relay.llm.base import Completion, Message
from relay.llm.jsonparse import JSONReplyError, parse_json_object


class Replies:
    def __init__(self, *texts: str, label: str = "fake/rules") -> None:
        self.texts = list(texts)
        self.seen: list[list[Message]] = []
        self._label = label

    @property
    def label(self) -> str:
        return self._label

    @property
    def is_local(self) -> bool:
        return True

    def complete(self, messages: Sequence[Message], *, max_tokens: int, temperature: float = 0.0) -> Completion:
        self.seen.append(list(messages))
        return Completion(text=self.texts.pop(0), model=self._label, input_tokens=100, output_tokens=20)


SETTINGS = Settings(llm_ledger=None)
MSGS: list[Message] = [{"role": "system", "content": "RELAY_TASK: x"}, {"role": "user", "content": "y"}]


@pytest.mark.parametrize(
    "text",
    [
        '```json\n{"answerable": true, "citations": []}\n```',
        '<think>let me see</think>{"answerable": true, "citations": [],}',
        'Sure! Here it is: {"answerable": true, "citations": []} Hope that helps.',
    ],
)
def test_lenient_parsing(text: str) -> None:
    assert parse_json_object(text)["answerable"] is True


def test_no_json_raises() -> None:
    with pytest.raises(JSONReplyError):
        parse_json_object("I cannot help with that.")


def test_valid_reply_first_time() -> None:
    model = Replies('{"answerable": false, "confidence": 0.2}')
    value, meta = complete_structured(model, MSGS, FaqOutput, SETTINGS, task="answer_faq")
    assert value.answerable is False and meta.attempts == 1 and meta.schema_valid
    assert meta.input_tokens == 100 and meta.cost_usd == 0.0


def test_invalid_reply_gets_one_corrective_retry_with_the_errors() -> None:
    model = Replies(
        '{"category": "lead", "confidence": 2}', '{"category": "sales_lead", "confidence": 0.9, "summary": "a quote"}'
    )
    value, meta = complete_structured(model, MSGS, TriageOutput, SETTINGS, task="classify_email")
    assert value.category == "sales_lead" and meta.attempts == 2
    correction = model.seen[1][-1]["content"]
    assert "did not match the required JSON schema" in correction and "category" in correction


def test_still_invalid_after_retry_raises_with_metadata() -> None:
    model = Replies("nope", "still nope")
    with pytest.raises(StructuredOutputError) as info:
        complete_structured(model, MSGS, TriageOutput, SETTINGS, task="classify_email")
    assert info.value.meta.schema_valid is False and info.value.meta.attempts == 2


def test_vat_rate_in_percent_is_normalised() -> None:
    assert InvoiceOutput.model_validate({"vat_rate": 21}).vat_rate == 0.21
    assert InvoiceOutput.model_validate({"vat_rate": "9%"}).vat_rate == 0.09
    assert InvoiceOutput.model_validate({"vat_rate": 0.0}).vat_rate == 0.0


def test_prices_free_models_cost_nothing_and_unknown_paid_models_do() -> None:
    prices = load_prices(Path("configs/prices.yaml"))
    assert prices.cost("nvidia/nemotron-3-super-120b-a12b:free", 1_000_000, 1_000_000) == 0.0
    assert prices.cost("fake/rules", 10, 10) == 0.0
    assert prices.cost("openai/gpt-5-mini", 1_000_000, 0) == 0.25
    assert prices.cost("someone/unknown-model", 1_000_000, 0) == Prices(
        free=(0, 0), default=(3.0, 15.0), models={}
    ).cost("x/y", 1_000_000, 0)
