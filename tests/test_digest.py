from __future__ import annotations

from collections.abc import Sequence

from relay.config import Settings
from relay.gateway.digest import write_digest
from relay.gateway.schemas import DigestRequest
from relay.llm.base import Completion, Message

METRICS = {
    "emails_triaged": 42,
    "emails_routed_automatically": 30,
    "invoice_total_posted_eur": 12450.5,
    "auto_rate": 0.714,
}


class Says:
    def __init__(self, text: str) -> None:
        self.text = text

    @property
    def label(self) -> str:
        return "fake/rules"

    @property
    def is_local(self) -> bool:
        return True

    def complete(self, messages: Sequence[Message], *, max_tokens: int, temperature: float = 0.0) -> Completion:
        return Completion(text=self.text, model="fake/rules")


def test_numbers_from_the_metrics_pass_rounding_and_percent_included() -> None:
    model = Says(
        '{"headline": "Busy week: 42 emails", "bullets": ["30 routed automatically (71%)", '
        '"EUR 12,450.50 of invoices posted"]}'
    )
    result = write_digest(
        model, DigestRequest(period="2026-10-06 to 2026-10-13", metrics=METRICS), Settings(llm_ledger=None)
    )
    assert result.numbers_ok and "42 emails" in result.text


def test_an_invented_number_replaces_the_digest_with_the_template() -> None:
    model = Says('{"headline": "Record week: 99 emails", "bullets": ["revenue up 15%"]}')
    result = write_digest(
        model, DigestRequest(period="2026-10-06 to 2026-10-13", metrics=METRICS), Settings(llm_ledger=None)
    )
    assert not result.numbers_ok and "99" in result.unsupported_numbers and "99" not in result.text
