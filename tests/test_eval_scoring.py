from __future__ import annotations

from pathlib import Path

from relay.eval import scoring as sc
from relay.eval.runner import score_faq, score_invoices, score_triage
from relay.gateway.leads import load_icp


def test_text_comparison_ignores_case_accents_and_legal_suffixes() -> None:
    assert sc.same_text("Fernway Tea", "Fernway Tea GmbH")
    assert sc.same_text("Elif Yilmaz", "Elif Yılmaz")
    assert sc.same_text("luma cosmetica", "Luma Cosmética S.L.")
    assert not sc.same_text("Brightbark", "Fernway Tea GmbH")
    assert sc.same_text(None, None) and not sc.same_text("x", None)


def test_ids_and_numbers() -> None:
    assert sc.same_id("kp 47715", "KP-47715") and not sc.same_id("KP-47716", "KP-47715")
    assert sc.same_number(2205.18, 2205.18) and sc.same_number("2205.18", 2205.181) and not sc.same_number(None, 1)


def test_fact_alternatives() -> None:
    assert sc.fact_hit("Add 3mm of bleed", "3 mm|3mm") and not sc.fact_hit("Add bleed", "3 mm|3mm")


def test_gold_tier_uses_the_same_rules() -> None:
    icp = load_icp(Path("configs/icp.yaml"))
    lead = {
        "quantity": 6000,
        "gold": {
            "site_found": True,
            "industry": "cosmetics_beauty",
            "employees_band": "11-50",
            "country": "AT",
            "sells_online": True,
            "disqualified": None,
        },
    }
    assert sc.gold_tier(lead, icp, 70, 40) == "hot"
    assert sc.gold_tier({**lead, "gold": {**lead["gold"], "disqualified": "competitor"}}, icp, 70, 40) == "disqualified"


def item(**kw: object) -> dict[str, object]:
    base = {
        "id": "em-1",
        "gold_category": "support",
        "category": "support",
        "status": "routed",
        "reasons": [],
        "fields": {"order_id": "KP-1"},
        "gold_fields": {"order_id": "KP-1"},
        "seconds": 1.0,
        "llm_calls": 1,
        "llm_ms": 900,
        "tokens": 10,
        "cost_usd": 0.0,
        "models": ["m"],
        "schema_valid": True,
        "hard": None,
    }
    return {**base, **kw}


def test_triage_scoring_counts_automation_precision_and_catches() -> None:
    items = [
        item(),
        item(id="em-2", category="sales_lead", status="review"),
        item(id="em-3", gold_category="spam", category="spam", status="routed", fields={}, gold_fields={}),
    ]
    summary = score_triage(items, {"crm": [], "helpdesk": [{"message_id": "<em-1@mail.kestrelprint.example>"}]})
    assert summary["category_accuracy"] == round(2 / 3, 4)
    assert summary["auto_handled"] == 2 and summary["auto_handled_accuracy"] == 1.0
    assert summary["wrong_caught_by_review"] == 1 and summary["downstream_correct_system"] == "1/1"
    assert summary["fields"]["order_id"]["accuracy"] == 1.0


def test_invoice_scoring_flags_bad_invoices_that_were_posted() -> None:
    gold = {
        "vendor": "V",
        "invoice_number": "1",
        "issue_date": "2026-01-01",
        "due_date": "2026-01-31",
        "currency": "EUR",
        "subtotal": 10.0,
        "vat_amount": 0.0,
        "total": 10.0,
    }
    items = [
        {
            "id": "a",
            "gold": gold,
            "gold_lines": 1,
            "expected": "review",
            "planted": ["vat_mismatch"],
            "status": "posted",
            "issues": [],
            "extracted": gold,
            "lines": 1,
            "seconds": 1.0,
        },
        {
            "id": "b",
            "gold": gold,
            "gold_lines": 1,
            "expected": "auto_post",
            "planted": [],
            "status": "posted",
            "issues": [],
            "extracted": {**gold, "total": 11.0},
            "lines": 1,
            "seconds": 1.0,
        },
    ]
    summary = score_invoices(items, [{"invoice_number": "1"}, {"invoice_number": "1"}])
    assert summary["problem_invoices_auto_posted"] == 1 and summary["double_posted"] == ["1"]
    assert summary["fields"]["total"]["accuracy"] == 0.5


def test_faq_scoring() -> None:
    items = [
        {
            "id": "f1",
            "expected": "auto_reply",
            "decision": "auto_reply",
            "answer": "Add 3 mm bleed",
            "facts": ["3 mm"],
            "citations": ["kb-04"],
            "gold_articles": ["kb-04"],
            "retrieved": ["kb-04"],
            "reasons": [],
            "seconds": 1.0,
        },
        {
            "id": "f2",
            "expected": "escalate",
            "decision": "auto_reply",
            "answer": "x",
            "facts": [],
            "citations": [],
            "gold_articles": [],
            "retrieved": [],
            "reasons": [],
            "seconds": 1.0,
        },
    ]
    summary = score_faq(items, [])
    assert summary["unsafe_auto_replies"] == ["f2"] and summary["auto_replies_with_all_facts"] == "1/1"
