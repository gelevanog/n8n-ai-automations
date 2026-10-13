"""The AI gateway as one object: the configured model, the reference data and the six tasks.

Workflows call it over HTTP (relay.api), Claude over MCP (relay.mcp_server) and the tests directly. Every task call
is reported to `on_call` (the ops store records it: task, run, model, latency, tokens, cost, decision)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from relay.config import Settings
from relay.gateway import digest, faq, invoice, leads, triage
from relay.gateway.kb import load_kb
from relay.gateway.schemas import (
    CallMeta,
    Check,
    DigestOutput,
    DigestRequest,
    DigestResponse,
    DraftRequest,
    DraftResponse,
    FaqRequest,
    FaqResponse,
    InvoiceOutput,
    InvoiceRequest,
    InvoiceResponse,
    LeadRequest,
    LeadResponse,
    TriageRequest,
    TriageResponse,
)
from relay.gateway.sites import SiteFetcher
from relay.gateway.structured import StructuredOutputError
from relay.llm.base import ChatModel
from relay.llm.factory import model_for

CallHook = Callable[[CallMeta, str | None, str, dict[str, Any]], None]
"""(meta, run_id, decision, summary) -> None"""

INVALID = "the model's reply did not match the schema, even after a corrective retry"


class Gateway:
    def __init__(self, settings: Settings, *, model: ChatModel | None = None, on_call: CallHook | None = None) -> None:
        self.settings = settings
        self.model = model or model_for(settings, tag="gateway")
        self.on_call = on_call
        data = settings.data_dir
        self.vendor_book = triage.load_vendors(Path("configs/vendors.yaml"))
        self.vendor_records = invoice.load_vendor_records(Path("configs/vendors.yaml"))
        self.icp = leads.load_icp(Path("configs/icp.yaml"))
        self.kb = load_kb(data / "kb")
        self.rules = faq.load_rules(Path("configs/escalation.yaml"))
        self.fetcher = SiteFetcher(data / "leads" / "sites", settings.sites_base_url)

    def _report(self, meta: CallMeta, run_id: str | None, decision: str, summary: dict[str, Any]) -> None:
        if self.on_call is not None:
            self.on_call(meta, run_id, decision, summary)

    # A reply that never fits the schema does not crash the workflow: the item degrades to the human path
    # (review, approval or escalation) with the reason, and the call is logged with schema_valid = false.

    def classify_email(self, request: TriageRequest) -> TriageResponse:
        try:
            result = triage.classify_email(self.model, request, self.settings, self.vendor_book)
        except StructuredOutputError as exc:
            result = TriageResponse(
                category="other",
                confidence=0.0,
                summary="(no valid classification)",
                decision="review",
                route="review",
                reasons=[INVALID],
                meta=exc.meta,
            )
        self._report(
            result.meta, request.run_id, result.route, {"category": result.category, "confidence": result.confidence}
        )
        return result

    def extract_invoice(self, request: InvoiceRequest) -> InvoiceResponse:
        try:
            result = invoice.extract_invoice(self.model, request, self.settings, self.vendor_records)
        except StructuredOutputError as exc:
            result = InvoiceResponse(
                invoice=InvoiceOutput(confidence=0.0),
                confidence=0.0,
                decision="review",
                issues=["invalid_model_output"],
                meta=exc.meta,
            )
        self._report(result.meta, request.run_id, result.decision, {"issues": result.issues})
        return result

    def enrich_lead(self, request: LeadRequest) -> LeadResponse:
        result = leads.enrich_lead(self.model, request, self.settings, self.fetcher, self.icp)
        self._report(result.meta, request.run_id, result.decision, {"tier": result.tier, "score": result.score})
        return result

    def draft_reply(self, request: DraftRequest) -> DraftResponse:
        try:
            result = leads.draft_reply(self.model, request, self.settings)
        except StructuredOutputError as exc:
            result = DraftResponse(
                subject="", body="", ok=False, meta=exc.meta, checks=[Check(code="schema", ok=False, detail=INVALID)]
            )
        self._report(result.meta, request.run_id, "ok" if result.ok else "checks_failed", {})
        return result

    def answer_faq(self, request: FaqRequest) -> FaqResponse:
        try:
            result = faq.answer_faq(self.model, request, self.settings, self.kb, self.rules)
        except StructuredOutputError as exc:
            result = FaqResponse(
                answerable=False,
                answer="",
                confidence=0.0,
                escalation_reason=INVALID,
                decision="escalate",
                reasons=[INVALID],
                meta=exc.meta,
            )
        self._report(result.meta, request.run_id, result.decision, {"citations": result.citations})
        return result

    def write_digest(self, request: DigestRequest) -> DigestResponse:
        try:
            result = digest.write_digest(self.model, request, self.settings)
        except StructuredOutputError as exc:
            fallback: DigestOutput = digest.template(request)
            text = fallback.headline + "\n" + "\n".join(f"- {b}" for b in fallback.bullets)
            result = DigestResponse(
                headline=fallback.headline, bullets=fallback.bullets, text=text, numbers_ok=True, meta=exc.meta
            )
        self._report(result.meta, request.run_id, "ok" if result.numbers_ok else "template", {})
        return result
