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
    for name in models:
        model = budgeted(build_chat_model(settings, provider="openrouter", model=name, fallback_models=[]),
                         settings, tag=f"smoke:{name}")
        gateway = Gateway(settings, model=model)
        row: dict[str, Any] = {"model": name}
        for task, call in (
            ("triage", lambda g=gateway: g.classify_email(TriageRequest.model_validate(
                {"from": {"name": email.from_name, "email": email.from_email}, "subject": email.subject,
                 "text": email.body}))),
            ("invoice", lambda g=gateway: g.extract_invoice(InvoiceRequest(text=text, filename="inv-002.pdf"))),
            ("faq", lambda g=gateway: g.answer_faq(FaqRequest(question="How much bleed should I add?"))),
        ):
            started = time.monotonic()
            try:
                result = call()
                row[task] = {"ok": True, "seconds": round(time.monotonic() - started, 1),
                             "schema_valid": result.meta.schema_valid}
                if task == "triage":
                    row[task]["category"] = result.category  # type: ignore[union-attr]
                    row[task]["correct"] = result.category == "sales_lead"  # type: ignore[union-attr]
                elif task == "invoice":
                    row[task]["total"] = result.invoice.total  # type: ignore[union-attr]
                    row[task]["correct"] = result.invoice.total == 2205.18  # type: ignore[union-attr]
                else:
                    row[task]["decision"] = result.decision  # type: ignore[union-attr]
                    row[task]["correct"] = "3 mm" in result.answer or "3mm" in result.answer  # type: ignore[union-attr]
            except LLMError as exc:
                row[task] = {"ok": False, "seconds": round(time.monotonic() - started, 1), "error": str(exc)[:200]}
        rows.append(row)
    return {"models": rows}
