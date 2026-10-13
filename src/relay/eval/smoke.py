"""Model smoke test: one email, one invoice and one FAQ question per candidate free model, straight through the
gateway (no n8n), to pick the main model before the full run. Every call goes through the budget wrapper and the
ledger like any other."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from relay.config import Settings
from relay.gateway.schemas import FaqRequest, InvoiceRequest, TriageRequest
from relay.gateway.service import Gateway
from relay.llm.base import LLMError
from relay.llm.factory import budgeted, build_chat_model
from relay.synthetic.emails import build_emails


def smoke(settings: Settings, models: list[str]) -> dict[str, Any]:
    emails = {e.id: e for e in build_emails()}
    email = emails["em-018"]  # a complaint and a new order in one email (gold: sales_lead)
    text = "\n".join(p.extract_text() for p in PdfReader(Path("data/invoices/pdf/inv-002.pdf")).pages)
    rows = []
    triage_request = TriageRequest.model_validate(
        {"from": {"name": email.from_name, "email": email.from_email}, "subject": email.subject, "text": email.body}
    )
    for name in models:
        model = budgeted(
            build_chat_model(settings, provider="openrouter", model=name, fallback_models=[]),
            settings,
            tag=f"smoke:{name}",
        )
        gateway = Gateway(settings, model=model)
        row: dict[str, Any] = {"model": name}
        for task in ("triage", "invoice", "faq"):
            started = time.monotonic()
            try:
                entry: dict[str, Any] = {"ok": True}
                if task == "triage":
                    triage = gateway.classify_email(triage_request)
                    entry.update(
                        category=triage.category,
                        correct=triage.category == "sales_lead",
                        schema_valid=triage.meta.schema_valid,
                    )
                elif task == "invoice":
                    invoice = gateway.extract_invoice(InvoiceRequest(text=text, filename="inv-002.pdf"))
                    entry.update(
                        total=invoice.invoice.total,
                        correct=invoice.invoice.total == 2205.18,
                        schema_valid=invoice.meta.schema_valid,
                    )
                else:
                    faq = gateway.answer_faq(FaqRequest(question="How much bleed should I add?"))
                    entry.update(
                        decision=faq.decision,
                        correct="3 mm" in faq.answer or "3mm" in faq.answer,
                        schema_valid=faq.meta.schema_valid,
                    )
                entry["seconds"] = round(time.monotonic() - started, 1)
                row[task] = entry
            except LLMError as exc:
                row[task] = {"ok": False, "seconds": round(time.monotonic() - started, 1), "error": str(exc)[:200]}
        rows.append(row)
    return {"models": rows}
