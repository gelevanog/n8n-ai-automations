"""FAQ: retrieval, escalation policy, gate (citations, numbers, confidence)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from relay.config import Settings
from relay.gateway.faq import gate, load_rules
from relay.gateway.kb import load_kb, tokens
from relay.gateway.schemas import CallMeta, FaqOutput, FaqRequest
from relay.gateway.service import Gateway

KB = load_kb(Path("data/kb"))
RULES = load_rules(Path("configs/escalation.yaml"))
QUESTIONS = yaml.safe_load(Path("data/faq/questions.yaml").read_text(encoding="utf-8"))
SETTINGS = Settings(llm_ledger=None)


def test_kb_has_twenty_articles_and_finds_the_right_one() -> None:
    assert len(KB.articles) == 20
    assert KB.search("How much bleed should I add?")[0].article.id == "kb-04-artwork-files"


def test_stemming_and_stopwords() -> None:
    assert tokens("The boxes are shipping") == ["box", "shipp"]


def test_retrieval_recall_at_4_on_answerable_questions() -> None:
    answerable = [q for q in QUESTIONS if q["expected"] == "answer"]
    hits = sum(1 for q in answerable if {h.article.id for h in KB.search(q["question"], 4)} & set(q["articles"]))
    assert hits / len(answerable) >= 0.9


@pytest.mark.parametrize(
    ("question", "code"),
    [
        ("Where is my order KP-48213?", "order_specific"),
        ("I want a full refund today", "refund_or_dispute"),
        ("Our lawyer says you infringed our trademark", "legal"),
        ("Delete all personal data under GDPR", "data_protection"),
        ("Someone else logged into our account", "security"),
        ("Give me a discount code", "discount_request"),
    ],
)
def test_escalation_rules(question: str, code: str) -> None:
    assert code in {r.code for r in RULES if r.pattern.search(question)}


def run_gate(output: FaqOutput, question: str) -> object:
    hits = KB.search(question)
    matched = [r for r in RULES if r.pattern.search(question)]
    return gate(output, FaqRequest(question=question), SETTINGS, hits, matched, KB, CallMeta())


def test_a_grounded_cited_answer_is_sent() -> None:
    out = FaqOutput(
        answerable=True, answer="Please add 3 mm bleed on all sides.", citations=["kb-04-artwork-files"], confidence=0.9
    )
    assert run_gate(out, "How much bleed should I add?").decision == "auto_reply"  # type: ignore[attr-defined]


def test_answers_with_unsupported_numbers_or_foreign_citations_escalate() -> None:
    q = "How much bleed should I add?"
    wrong_number = FaqOutput(
        answerable=True, answer="Add 5 mm bleed.", citations=["kb-04-artwork-files"], confidence=0.9
    )
    result = run_gate(wrong_number, q)
    assert result.decision == "escalate" and any("numbers" in r for r in result.reasons)  # type: ignore[attr-defined]
    foreign = FaqOutput(answerable=True, answer="Add 3 mm bleed.", citations=["kb-99-made-up"], confidence=0.9)
    assert run_gate(foreign, q).decision == "escalate"  # type: ignore[attr-defined]
    unsure = FaqOutput(answerable=True, answer="Add 3 mm bleed.", citations=["kb-04-artwork-files"], confidence=0.4)
    assert run_gate(unsure, q).decision == "escalate"  # type: ignore[attr-defined]


def test_policy_escalation_skips_the_model(gateway: Gateway) -> None:
    result = gateway.answer_faq(FaqRequest(question="Where is my order KP-48213? It is late."))
    assert result.decision == "escalate" and result.meta.model == "" and result.retrieved


def test_rules_model_on_the_question_set(gateway: Gateway) -> None:
    unsafe = [
        q["id"]
        for q in QUESTIONS
        if q["expected"] == "escalate"
        and gateway.answer_faq(FaqRequest(question=q["question"])).decision == "auto_reply"
    ]
    assert len(unsafe) <= 2, unsafe
