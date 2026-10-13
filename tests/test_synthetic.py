"""The synthetic datasets: deterministic, the documented sizes, internally consistent gold, fictional only."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

import yaml

from relay.synthetic import emails, invoices, invoices_hard, leads


def test_regenerating_gives_the_committed_files(tmp_path: Path) -> None:
    invoices.write_dataset(tmp_path / "invoices")
    invoices_hard.write_dataset(tmp_path / "invoices_hard")
    emails.write_dataset(tmp_path / "emails")
    leads.write_dataset(tmp_path / "leads")
    for relative in (
        "invoices/gold.jsonl",
        "emails/emails.jsonl",
        "leads/leads.jsonl",
        "leads/profiles.jsonl",
        "invoices/pdf/inv-001.pdf",
        "invoices/pdf/inv-040.pdf",
        "leads/sites/bloom-box-flowers.example/about.html",
    ):
        assert (tmp_path / relative).read_bytes() == (Path("data") / relative).read_bytes(), relative


def test_sizes() -> None:
    assert len(emails.build_emails()) == 80
    assert len(invoices.build_invoices()) == 40
    assert len(leads.build_leads()) == 30
    assert len(yaml.safe_load(Path("data/faq/questions.yaml").read_text(encoding="utf-8"))) == 40
    assert len(list(Path("data/kb").glob("*.md"))) == 20


def test_category_mix() -> None:
    counts: dict[str, int] = {}
    for email in emails.build_emails():
        counts[email.category] = counts.get(email.category, 0) + 1
    assert counts == {"sales_lead": 24, "support": 20, "invoice": 14, "spam": 14, "other": 8}


def test_clean_invoices_add_up_and_problem_invoices_do_not_all() -> None:
    for invoice in invoices.build_invoices():
        line_sum = sum((line.amount for line in invoice.lines), Decimal(0))
        consistent = (
            line_sum == invoice.subtotal
            and invoice.subtotal + invoice.vat_amount == invoice.total
            and all(invoices.money(line.quantity * line.unit_price) == line.amount for line in invoice.lines)
        )
        if invoice.expected_decision == "auto_post":
            assert consistent and invoice.total <= invoices.AUTO_POST_LIMIT, invoice.id
        assert (invoice.expected_decision == "review") == bool(invoice.issues)


def test_every_address_and_site_is_fictional() -> None:
    for email in emails.build_emails():
        assert email.from_email.endswith(".example"), email.from_email
    for lead in leads.build_leads():
        assert not lead.website or ".example" in lead.website
        assert lead.email.endswith(".example")
    for profile in leads.PROFILES:
        assert profile.domain.endswith(".example")


def test_attachments_exist_and_gold_matches_the_pdf() -> None:
    gold = {
        json.loads(line)["id"]: json.loads(line)
        for line in Path("data/invoices/gold.jsonl").read_text(encoding="utf-8").splitlines()
    }
    for email in emails.build_emails():
        if email.attachment:
            assert (Path("data/invoices/pdf") / f"{email.attachment}.pdf").is_file()
            number = email.fields.get("invoice_number")
            assert number in (None, gold[email.attachment]["invoice_number"])


def test_no_key_shaped_strings_in_data() -> None:
    pattern = re.compile(r"sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}")
    for path in Path("data").rglob("*"):
        if path.is_file() and path.suffix in {".jsonl", ".yaml", ".md", ".html"}:
            assert not pattern.search(path.read_text(encoding="utf-8")), path
