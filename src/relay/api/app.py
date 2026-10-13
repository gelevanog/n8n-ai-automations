"""Relay's HTTP API: the AI gateway (/ai/v1) the workflows call, the ops API (/ops) for runs, idempotency, the review
queue, dead letters and metrics, and the Mailpit bridge (/inbound/mailpit) that turns a received email into the
inbox-triage webhook call. The dashboard and the MCP server use the same endpoints / store."""

from __future__ import annotations

import base64
import datetime as dt
import json
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from relay import __version__
from relay.config import Settings, get_settings
from relay.gateway.schemas import (
    CallMeta,
    DigestRequest,
    DigestResponse,
    DraftRequest,
    DraftResponse,
    FaqRequest,
    FaqResponse,
    InvoiceRequest,
    InvoiceResponse,
    LeadRequest,
    LeadResponse,
    TriageRequest,
    TriageResponse,
)
from relay.gateway.service import Gateway
from relay.gateway.structured import StructuredOutputError
from relay.llm.base import BudgetExceededError, LLMError, PolicyViolationError
from relay.logging_config import get_logger
from relay.ops.db import make_sessionmaker
from relay.ops.store import ConflictError, NotFoundError, OpsStore

log = get_logger(__name__)
INBOX = "hello@kestrelprint.example"


class StartRun(BaseModel):
    workflow: str = Field(pattern=r"^(triage|invoice|lead|faq|digest)$")
    idempotency_key: str = Field(min_length=1, max_length=400)
    execution_id: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    source: str = "webhook"
    title: str = ""


class FinishRun(BaseModel):
    status: str
    route: str | None = None
    output: dict[str, Any] | None = None
    title: str | None = None


class CreateReview(BaseModel):
    run_id: str
    kind: str
    title: str = ""
    reasons: list[str] = Field(default_factory=list)
    proposal: dict[str, Any] = Field(default_factory=dict)
    resume_url: str | None = None


class Decision(BaseModel):
    decision: str = Field(pattern=r"^(approve|reject)$")
    edits: dict[str, Any] = Field(default_factory=dict)
    reviewer: str = "dashboard"
    note: str = ""


class DeadLetterIn(BaseModel):
    workflow: str = "unknown"
    execution_id: str | None = None
    node: str = ""
    error: str = ""
    run_id: str | None = None


def create_app(
    settings: Settings | None = None,
    *,
    gateway: Gateway | None = None,
    store: OpsStore | None = None,
    http_get: Callable[[str], httpx.Response] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    store = store or OpsStore(
        make_sessionmaker(settings.database_url), n8n_url=settings.n8n_url, webhook_token=settings.n8n_webhook_token
    )
    gateway = gateway or Gateway(settings, on_call=store.record_call)
    if gateway.on_call is None:
        gateway.on_call = store.record_call
    fetch = http_get or (lambda url: httpx.get(url, timeout=20.0))

    app = FastAPI(title="Relay API", version=__version__, description=__doc__)
    app.state.store = store
    app.state.gateway = gateway
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def auth(
        authorization: str | None = Header(default=None), x_relay_token: str | None = Header(default=None)
    ) -> None:
        if not settings.api_token:
            return
        supplied = x_relay_token or (authorization or "").removeprefix("Bearer ").strip()
        if not secrets.compare_digest(supplied.encode(), settings.api_token.encode()):
            raise HTTPException(401, "missing or wrong API token")

    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": f"not found: {exc}"})

    @app.exception_handler(ConflictError)
    async def _conflict(_: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(PolicyViolationError)
    async def _policy(_: Request, exc: PolicyViolationError) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(exc), "type": "policy"})

    @app.exception_handler(StructuredOutputError)
    async def _invalid(_: Request, exc: StructuredOutputError) -> JSONResponse:
        # 422 (not 5xx): retrying the same input will not help; the workflow sends the item to review instead.
        return JSONResponse(status_code=422, content={"detail": str(exc), "type": "invalid_model_output"})

    @app.exception_handler(BudgetExceededError)
    async def _budget(_: Request, exc: BudgetExceededError) -> JSONResponse:
        return JSONResponse(status_code=429, content={"detail": str(exc), "type": "budget"})

    @app.exception_handler(LLMError)
    async def _llm(_: Request, exc: LLMError) -> JSONResponse:
        # 503: transient (rate limits, provider outages); n8n's retry-on-fail and then the dead-letter queue handle it.
        return JSONResponse(status_code=503, content={"detail": str(exc)[:400], "type": "llm_unavailable"})

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__, "model": gateway.model.label, "free_only": settings.free_only}

    # ------------------------------------------------------------------------------ AI gateway

    def _failed(task: str, run_id: str | None, exc: Exception) -> None:
        store.record_call(
            CallMeta(task=task, model=gateway.model.label, schema_valid=False),
            run_id,
            "error",
            {},
            error=str(exc)[:500],
        )

    def guarded(task: str, run_id: str | None, call: Callable[[], Any]) -> Any:
        try:
            return call()
        except LLMError as exc:
            _failed(task, run_id, exc)
            raise

    @app.post("/ai/v1/classify-email", response_model=TriageResponse, dependencies=[Depends(auth)])
    def classify_email(request: TriageRequest) -> TriageResponse:
        result: TriageResponse = guarded("classify_email", request.run_id, lambda: gateway.classify_email(request))
        return result

    @app.post("/ai/v1/extract-invoice", response_model=InvoiceResponse, dependencies=[Depends(auth)])
    def extract_invoice(request: InvoiceRequest) -> InvoiceResponse:
        result: InvoiceResponse = guarded("extract_invoice", request.run_id, lambda: gateway.extract_invoice(request))
        return result

    @app.post("/ai/v1/enrich-lead", response_model=LeadResponse, dependencies=[Depends(auth)])
    def enrich_lead(request: LeadRequest) -> LeadResponse:
        result: LeadResponse = guarded("enrich_lead", request.run_id, lambda: gateway.enrich_lead(request))
        return result

    @app.post("/ai/v1/draft-reply", response_model=DraftResponse, dependencies=[Depends(auth)])
    def draft_reply(request: DraftRequest) -> DraftResponse:
        result: DraftResponse = guarded("draft_reply", request.run_id, lambda: gateway.draft_reply(request))
        return result

    @app.post("/ai/v1/answer-faq", response_model=FaqResponse, dependencies=[Depends(auth)])
    def answer_faq(request: FaqRequest) -> FaqResponse:
        result: FaqResponse = guarded("answer_faq", request.run_id, lambda: gateway.answer_faq(request))
        return result

    @app.post("/ai/v1/write-digest", response_model=DigestResponse, dependencies=[Depends(auth)])
    def write_digest(request: DigestRequest) -> DigestResponse:
        result: DigestResponse = guarded("write_digest", request.run_id, lambda: gateway.write_digest(request))
        return result

    @app.get("/ai/v1/calls", dependencies=[Depends(auth)])
    def calls(task: str | None = None, limit: int = Query(default=100, le=500)) -> list[dict[str, Any]]:
        return store.list_calls(task=task, limit=limit)

    # ------------------------------------------------------------------------------ ops

    @app.post("/ops/runs/start", dependencies=[Depends(auth)])
    def start_run(body: StartRun) -> dict[str, Any]:
        return store.start_run(
            body.workflow,
            body.idempotency_key,
            execution_id=body.execution_id,
            input=body.input,
            source=body.source,
            title=body.title,
        )

    @app.post("/ops/runs/{run_id}/finish", dependencies=[Depends(auth)])
    def finish_run(run_id: str, body: FinishRun) -> dict[str, Any]:
        try:
            return store.finish_run(run_id, body.status, route=body.route, output=body.output, title=body.title)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/ops/runs", dependencies=[Depends(auth)])
    def list_runs(
        workflow: str | None = None, status: str | None = None, limit: int = Query(default=100, le=500), offset: int = 0
    ) -> list[dict[str, Any]]:
        return store.list_runs(workflow=workflow, status=status, limit=limit, offset=offset)

    @app.get("/ops/runs/{run_id}", dependencies=[Depends(auth)])
    def get_run(run_id: str) -> dict[str, Any]:
        return store.get_run(run_id)

    @app.post("/ops/review", dependencies=[Depends(auth)])
    def create_review(body: CreateReview) -> dict[str, Any]:
        return store.create_review(
            body.run_id,
            body.kind,
            title=body.title,
            reasons=body.reasons,
            proposal=body.proposal,
            resume_url=body.resume_url,
        )

    @app.get("/ops/review", dependencies=[Depends(auth)])
    def list_reviews(
        status: str | None = "pending", workflow: str | None = None, limit: int = Query(default=100, le=500)
    ) -> list[dict[str, Any]]:
        return store.list_reviews(status=status or None, workflow=workflow, limit=limit)

    @app.get("/ops/review/{review_id}", dependencies=[Depends(auth)])
    def get_review(review_id: str) -> dict[str, Any]:
        return store.get_review(review_id)

    @app.post("/ops/review/{review_id}/decision", dependencies=[Depends(auth)])
    def decide(review_id: str, body: Decision) -> dict[str, Any]:
        return store.decide(review_id, body.decision, edits=body.edits, reviewer=body.reviewer, note=body.note)

    @app.post("/ops/dead-letters", dependencies=[Depends(auth)])
    def dead_letter(body: DeadLetterIn) -> dict[str, Any]:
        log.warning("dead_letter", workflow=body.workflow, node=body.node, execution_id=body.execution_id)
        return store.dead_letter(
            body.workflow, execution_id=body.execution_id, node=body.node, error=body.error, run_id=body.run_id
        )

    @app.get("/ops/dead-letters", dependencies=[Depends(auth)])
    def list_dead_letters(status: str | None = "open", limit: int = Query(default=100, le=500)) -> list[dict[str, Any]]:
        return store.list_dead_letters(status=status or None, limit=limit)

    @app.post("/ops/dead-letters/{dead_letter_id}/replay", dependencies=[Depends(auth)])
    def replay(dead_letter_id: str) -> dict[str, Any]:
        return store.replay(dead_letter_id)

    @app.post("/ops/dead-letters/{dead_letter_id}/resolve", dependencies=[Depends(auth)])
    def resolve(dead_letter_id: str) -> dict[str, Any]:
        return store.resolve_dead_letter(dead_letter_id)

    @app.get("/ops/metrics", dependencies=[Depends(auth)])
    def metrics(days: int | None = Query(default=None, ge=1, le=365)) -> dict[str, Any]:
        since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days) if days else None
        return store.metrics(since=since)

    @app.get("/ops/eval", dependencies=[Depends(auth)])
    def eval_summary() -> dict[str, Any]:
        path = Path(settings.results_dir) / "summary.json"
        if not path.exists():
            raise HTTPException(404, "no evaluation summary yet (run `relay eval`)")
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return data

    # ------------------------------------------------------------------------------ Mailpit bridge

    @app.post("/inbound/mailpit")
    async def mailpit_webhook(request: Request) -> dict[str, Any]:
        """Mailpit calls this for every message it receives. Mail to the shared inbox is fetched with its
        attachments and posted to the inbox-triage workflow as an inbound-email webhook (Postmark-style JSON)."""
        summary = await request.json()
        message_id = summary.get("ID")
        recipients = [str(t.get("Address", "")).lower() for t in summary.get("To") or []]
        if not message_id or INBOX not in recipients:
            return {"forwarded": False, "reason": "not addressed to the shared inbox"}
        message = fetch(f"{settings.mailpit_url}/api/v1/message/{message_id}").json()
        attachments = []
        for part in message.get("Attachments") or []:
            content = fetch(f"{settings.mailpit_url}/api/v1/message/{message_id}/part/{part['PartID']}").content
            attachments.append(
                {
                    "filename": part.get("FileName") or "attachment",
                    "content_type": part.get("ContentType") or "application/octet-stream",
                    "content_base64": base64.b64encode(content).decode("ascii"),
                }
            )
        sender = message.get("From") or {}
        payload = {
            "message_id": message.get("MessageID") or message_id,
            "from": {"name": sender.get("Name", ""), "email": sender.get("Address", "")},
            "to": INBOX,
            "subject": message.get("Subject", ""),
            "text": message.get("Text", ""),
            "attachments": attachments,
            "source": "mailpit",
        }
        response = store.post(
            f"{settings.n8n_url}/webhook/inbox-triage", payload, {"X-Relay-Token": settings.n8n_webhook_token}
        )
        return {"forwarded": response.status_code < 400, "status_code": response.status_code}

    return app


def create_default_app() -> FastAPI:
    from relay.logging_config import configure_logging

    configure_logging()
    return create_app()
