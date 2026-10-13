"""Local stand-ins for the third-party systems the workflows write to, so nothing leaves the machine:

  /crm/leads              a CRM (HubSpot / Pipedrive-like): leads with tier, score and the draft reply
  /helpdesk/tickets       a helpdesk (Zendesk / Freshdesk-like): tickets with the triage fields or FAQ escalation
  /accounting/bills       accounting (QuickBooks / Xero-like): supplier bills; a duplicate dedupe_key is a 409
  /chat/messages          team chat (Slack-like): channel messages from the workflows (#sales, #finance, #ops-alerts)
  /sites/<domain>/<page>  the fictional company websites lead enrichment reads

Every write honours an `Idempotency-Key` header (the same key returns the first record, as Stripe's API does), and
`/admin/chaos` injects failures (503s for the first N attempts per key, a random rate, or a hard outage) so the
retry, dead-letter and replay paths can be exercised and measured."""

from __future__ import annotations

import datetime as dt
import random
import re
import threading
from pathlib import Path
from typing import Any, ClassVar

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import JSON, DateTime, Integer, String, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from relay.config import Settings, get_settings
from relay.ops.db import make_engine, new_id

COLLECTIONS = {"crm": "leads", "helpdesk": "tickets", "accounting": "bills", "chat": "messages"}


class MockBase(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSON}


class Record(MockBase):
    __tablename__ = "mock_records"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    system: Mapped[str] = mapped_column(String(16), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), index=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(64), index=True)
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC))


class ChaosRule(BaseModel):
    fail_first: int = Field(default=0, ge=0, le=10, description="503 for the first N attempts of each key")
    rate: float = Field(default=0.0, ge=0, le=1, description="random 503 rate")
    down: bool = Field(default=False, description="every request fails (an outage)")
    seed: int = 7


class Chaos:
    def __init__(self) -> None:
        self.rules: dict[str, ChaosRule] = {}
        self.attempts: dict[str, int] = {}
        self.rngs: dict[str, random.Random] = {}
        self.injected = 0
        self.lock = threading.Lock()

    def set(self, rules: dict[str, ChaosRule]) -> None:
        with self.lock:
            self.rules = rules
            self.attempts.clear()
            self.rngs = {name: random.Random(rule.seed) for name, rule in rules.items()}
            self.injected = 0

    def should_fail(self, system: str, key: str) -> bool:
        rule = self.rules.get(system)
        if rule is None:
            return False
        with self.lock:
            if rule.down:
                self.injected += 1
                return True
            attempt_key = f"{system}:{key}"
            self.attempts[attempt_key] = self.attempts.get(attempt_key, 0) + 1
            if self.attempts[attempt_key] <= rule.fail_first:
                self.injected += 1
                return True
            if rule.rate and self.rngs[system].random() < rule.rate:
                self.injected += 1
                return True
        return False


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = make_engine(settings.mocks_database_url)
    MockBase.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    chaos = Chaos()
    sites_dir = settings.data_dir / "leads" / "sites"
    app = FastAPI(title="Relay mocks", version="0.1.0", description=__doc__)
    counters_lock = threading.Lock()

    def _write(system: str, data: dict[str, Any], key: str | None, dedupe: str | None) -> tuple[int, dict[str, Any]]:
        if chaos.should_fail(system, key or dedupe or repr(sorted(data.items()))[:200]):
            raise HTTPException(503, f"{system} is temporarily unavailable (injected failure)")
        with counters_lock, sessions.begin() as session:
            session_: Session = session
            if key:
                existing = session_.scalar(select(Record).where(Record.system == system, Record.idempotency_key == key))
                if existing is not None:
                    return 200, _view(existing)
            if dedupe:
                clash = session_.scalar(select(Record).where(Record.system == system, Record.dedupe_key == dedupe))
                if clash is not None:
                    raise HTTPException(409, {"error": "duplicate", "existing_id": clash.id})
            seq = (
                session_.scalar(select(Record.seq).where(Record.system == system).order_by(Record.seq.desc()).limit(1))
                or 0
            ) + 1
            record = Record(system=system, idempotency_key=key, dedupe_key=dedupe, data=data, seq=seq)
            session_.add(record)
            session_.flush()
            return 201, _view(record)

    def _view(record: Record) -> dict[str, Any]:
        prefix = {"crm": "LEAD", "helpdesk": "T", "accounting": "BILL", "chat": "MSG"}[record.system]
        return {
            "id": record.id,
            "number": f"{prefix}-{1000 + record.seq}",
            **record.data,
            "created_at": record.created_at.isoformat(timespec="seconds") if record.created_at else None,
        }

    def _list(system: str, **filters: str | None) -> list[dict[str, Any]]:
        with sessions() as session:
            records = list(session.scalars(select(Record).where(Record.system == system).order_by(Record.seq)))
        items = [_view(r) for r in records]
        for name, value in filters.items():
            if value is not None:
                items = [i for i in items if str(i.get(name)) == value]
        return items

    async def _body(request: Request) -> dict[str, Any]:
        try:
            data = await request.json()
        except ValueError as exc:
            raise HTTPException(400, "body must be JSON") from exc
        if not isinstance(data, dict):
            raise HTTPException(400, "body must be a JSON object")
        return data

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # ---- CRM
    @app.post("/crm/leads", status_code=201)
    async def create_lead(
        request: Request, response: Response, idempotency_key: str | None = Header(default=None)
    ) -> dict[str, Any]:
        data = await _body(request)
        if not data.get("email") and not data.get("name"):
            raise HTTPException(422, "a lead needs a name or an email")
        status, record = _write("crm", {"status": "new", **data}, idempotency_key, None)
        response.status_code = status
        return record

    @app.get("/crm/leads")
    def list_leads(email: str | None = None, tier: str | None = None) -> list[dict[str, Any]]:
        return _list("crm", email=email, tier=tier)

    # ---- helpdesk
    @app.post("/helpdesk/tickets", status_code=201)
    async def create_ticket(
        request: Request, response: Response, idempotency_key: str | None = Header(default=None)
    ) -> dict[str, Any]:
        data = await _body(request)
        if not data.get("subject"):
            raise HTTPException(422, "a ticket needs a subject")
        status, record = _write("helpdesk", {"status": "open", "priority": "normal", **data}, idempotency_key, None)
        response.status_code = status
        return record

    @app.get("/helpdesk/tickets")
    def list_tickets(status: str | None = None) -> list[dict[str, Any]]:
        return _list("helpdesk", status=status)

    # ---- accounting
    @app.post("/accounting/bills", status_code=201)
    async def create_bill(
        request: Request, response: Response, idempotency_key: str | None = Header(default=None)
    ) -> Any:
        data = await _body(request)
        for name in ("vendor", "total", "currency"):
            if data.get(name) in (None, ""):
                raise HTTPException(422, f"a bill needs {name}")
        try:
            status, record = _write(
                "accounting",
                {"status": data.get("status", "draft"), **data},
                idempotency_key,
                str(data.get("dedupe_key") or "") or None,
            )
        except HTTPException as exc:
            if exc.status_code == 409:
                return JSONResponse(status_code=409, content=exc.detail)
            raise
        response.status_code = status
        return record

    @app.get("/accounting/bills")
    def list_bills(dedupe_key: str | None = None, vendor: str | None = None) -> list[dict[str, Any]]:
        return _list("accounting", dedupe_key=dedupe_key, vendor=vendor)

    # ---- chat
    @app.post("/chat/messages", status_code=201)
    async def post_message(
        request: Request, response: Response, idempotency_key: str | None = Header(default=None)
    ) -> dict[str, Any]:
        data = await _body(request)
        if not data.get("channel") or not data.get("text"):
            raise HTTPException(422, "a message needs a channel and text")
        status, record = _write("chat", data, idempotency_key, None)
        response.status_code = status
        return record

    @app.get("/chat/messages")
    def list_messages(channel: str | None = None) -> list[dict[str, Any]]:
        return _list("chat", channel=channel)

    # ---- fixture websites
    @app.get("/sites/{domain}/{page}", response_class=HTMLResponse)
    def site(domain: str, page: str) -> HTMLResponse:
        if not re.fullmatch(r"[a-z0-9.-]+\.example", domain) or page not in {"index.html", "about.html"}:
            raise HTTPException(404, "not found")
        path = sites_dir / domain / page
        if not path.is_file():
            raise HTTPException(404, "not found")
        return HTMLResponse(path.read_text(encoding="utf-8"))

    @app.get("/sites/{domain}", response_class=HTMLResponse)
    def site_home(domain: str) -> HTMLResponse:
        return site(domain, "index.html")

    # ---- admin
    @app.get("/admin/state")
    def state() -> dict[str, Any]:
        with sessions() as session:
            records = list(session.scalars(select(Record)))
        counts = {system: sum(1 for r in records if r.system == system) for system in COLLECTIONS}
        return {
            "counts": counts,
            "chaos": {k: v.model_dump() for k, v in chaos.rules.items()},
            "injected_failures": chaos.injected,
        }

    @app.post("/admin/chaos")
    def set_chaos(rules: dict[str, ChaosRule]) -> dict[str, Any]:
        unknown = set(rules) - set(COLLECTIONS)
        if unknown:
            raise HTTPException(422, f"unknown systems: {sorted(unknown)}")
        chaos.set(rules)
        return state()

    @app.post("/admin/reset")
    def reset() -> dict[str, Any]:
        with sessions.begin() as session:
            session.query(Record).delete()
        chaos.set({})
        return state()

    return app


def sites_path(settings: Settings) -> Path:
    return settings.data_dir / "leads" / "sites"


def create_default_app() -> FastAPI:
    from relay.logging_config import configure_logging

    configure_logging()
    return create_app()
