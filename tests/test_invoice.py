"""Invoice checks: every planted problem in the synthetic set is caught, clean invoices pass."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader

from relay.config import Settings
from relay.gateway.invoice import check_invoice, dedupe_key, load_vendor_records, match_vendor, parse_date
from relay.gateway.schemas import InvoiceOutput, InvoiceRequest
from relay.gateway.service import Gateway

GOLD = [json.loads(line) for line in Path("data/invoices/gold.jsonl").read_text(encoding="utf-8").splitlines()]
VENDORS = load_vendor_records(Path("configs/vendors.yaml"))
SETTINGS = Settings(llm_ledger=None)


def text_of(invoice_id: str) -> str:
    return "\n".join(p.extract_text() for p in PdfReader(f"data/invoices/pdf/{invoice_id}.pdf").pages)


def as_output(gold: dict[str, Any], confidence: float = 0.95) -> InvoiceOutput:
    return InvoiceOutput.model_validate(
        {
            **{
                k: gold[k]
                for k in (
                    "vendor",
                    "vendor_vat_id",
                    "invoice_number",
                    "issue_date",
                    "due_date",
                    "currency",
                    "subtotal",
                    "vat_rate",
                    "vat_amount",
                    "total",
                )
            },
            "lines": gold["lines"],
            "confidence": confidence,
        }
    )


@pytest.mark.parametrize("gold", GOLD, ids=[g["id"] for g in GOLD])
def test_checks_on_perfect_extraction_match_the_gold_decision(gold: dict[str, Any]) -> None:
    result = check_invoice(as_output(gold), text_of(gold["id"]), SETTINGS, VENDORS)
    if gold["issues"] == ["duplicate"]:
        # A duplicate is only visible to the accounting system; the workflow asks it with dedupe_key.
        original = next(g for g in GOLD if g["id"] == gold["duplicate_of"])
        assert result.dedupe_key == dedupe_key(original["vendor"], original["invoice_number"])
        return
    assert result.decision == gold["expected_decision"], result.issues
    for issue in gold["issues"]:
        assert issue in result.issues


def test_low_confidence_and_unknown_vendor_need_review() -> None:
    gold = GOLD[0]
    assert "low_confidence" in check_invoice(as_output(gold, 0.3), text_of(gold["id"]), SETTINGS, VENDORS).issues
    stranger = as_output(gold).model_copy(update={"vendor": "Quick Pay Ltd"})
    assert "unknown_vendor" in check_invoice(stranger, text_of(gold["id"]), SETTINGS, VENDORS).issues


def test_a_total_that_is_not_in_the_document_is_flagged() -> None:
    gold = GOLD[0]
    invented = as_output(gold).model_copy(update={"total": 999.99, "subtotal": 999.99, "lines": []})
    result = check_invoice(invented, text_of(gold["id"]), SETTINGS, VENDORS)
    assert "ungrounded_total" in result.issues


def test_vat_id_mismatch_is_flagged() -> None:
    gold = next(g for g in GOLD if g["vendor_key"] == "veldhoven" and not g["issues"])
    changed = as_output(gold).model_copy(update={"vendor_vat_id": "NL000000000B99"})
    assert "vat_id_mismatch" in check_invoice(changed, text_of(gold["id"]), SETTINGS, VENDORS).issues


@pytest.mark.parametrize(
    ("value", "iso"),
    [
        ("2026-06-06", "2026-06-06"),
        ("06.06.2026", "2026-06-06"),
        ("06/06/2026", "2026-06-06"),
        ("Jun 06, 2026", "2026-06-06"),
    ],
)
def test_dates_in_every_layout(value: str, iso: str) -> None:
    parsed = parse_date(value)
    assert parsed is not None and parsed.isoformat() == iso


def test_vendor_matching_ignores_legal_suffixes() -> None:
    record = match_vendor("Inkwell Supplies", VENDORS)
    assert record is not None and record.name == "Inkwell Supplies GmbH"
    assert match_vendor("Totally Unknown", VENDORS) is None


def test_dedupe_key_is_stable_across_spelling() -> None:
    assert dedupe_key("Swiftline Logistics Ltd", "SL2026-02912") == dedupe_key("SWIFTLINE LOGISTICS", "sl 2026 02912")


def test_rules_model_reads_all_four_layouts(gateway: Gateway) -> None:
    for gold in GOLD[:8]:
        result = gateway.extract_invoice(InvoiceRequest(text=text_of(gold["id"]), filename=gold["id"]))
        assert result.invoice.total == gold["total"]
        assert result.invoice.invoice_number == gold["invoice_number"]


HARD = [json.loads(line) for line in Path("data/invoices_hard/gold.jsonl").read_text(encoding="utf-8").splitlines()]


def hard_text(invoice_id: str) -> str:
    return "\n".join(p.extract_text() for p in PdfReader(f"data/invoices_hard/pdf/{invoice_id}.pdf").pages)


@pytest.mark.parametrize("gold", HARD, ids=[g["id"] for g in HARD])
def test_hard_set_checks_on_perfect_extraction(gold: dict[str, Any]) -> None:
    output = InvoiceOutput.model_validate(
        {
            "document_type": "credit_note" if gold["style"] == "C" else "invoice",
            **{
                k: gold[k]
                for k in (
                    "vendor",
                    "invoice_number",
                    "issue_date",
                    "due_date",
                    "currency",
                    "subtotal",
                    "vat_rate",
                    "vat_amount",
                    "total",
                    "vat_breakdown",
                    "amount_paid",
                )
            },
            "vendor_vat_id": gold["printed_vat_id"],
            "lines": gold["lines"],
            "confidence": 0.95,
        }
    )
    result = check_invoice(output, hard_text(gold["id"]), SETTINGS, VENDORS)
    assert result.decision == gold["expected_decision"], result.issues
    for issue in gold["issues"]:
        assert issue in result.issues
