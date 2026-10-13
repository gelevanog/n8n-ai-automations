"""Operations on the ops store, shared by the HTTP API, the MCP server and the evaluation.

Idempotency: `start_run` is the first call of every workflow. The same (workflow, key) twice returns the first run
with `duplicate: true`, and the workflow answers from it without touching the CRM, helpdesk or accounting again. A
run that ended in `error` may be started again (a replay from the dead-letter queue, or the sender retrying); that
counts as a new attempt on the same run.

Human review: a workflow that needs a person creates a review item with the URL of its waiting n8n execution (a Wait
node, resume on webhook). Approving or rejecting posts the decision (and any edited fields) to that URL, and the
workflow carries on from where it stopped."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from statistics import median
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from relay.ops.db import DeadLetter, GatewayCall, ReviewItem, Run, new_id, now

FINAL = frozenset({"auto", "review", "approved", "rejected", "escalated", "error"})
WEBHOOK_PATHS = {
    "triage": "inbox-triage",
    "invoice": "invoice-intake",
    "lead": "lead-enrichment",
    "faq": "support-faq",
    "digest": "kpi-digest",
}

Poster = Callable[[str, dict[str, Any], dict[str, str]], httpx.Response]


def _default_post(url: str, body: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
    return httpx.post(url, json=body, headers=headers, timeout=60.0)


class NotFoundError(LookupError):
    pass


class ConflictError(RuntimeError):
    pass


def iso(value: dt.datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.UTC)
    return value.isoformat(timespec="seconds")


def run_dict(run: Run, *, full: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": run.id,
        "workflow": run.workflow,
        "status": run.status,
        "route": run.route,
        "title": run.title,
        "source": run.source,
        "execution_id": run.execution_id,
        "attempts": run.attempts,
        "duplicates": run.duplicates,
        "started_at": iso(run.started_at),
        "finished_at": iso(run.finished_at),
        "latency_ms": run.latency_ms,
        "error_message": run.error,
    }
    if full:
        data["input"] = run.input
        data["output"] = run.output
        data["idempotency_key"] = run.idempotency_key
    return data


def review_dict(item: ReviewItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "run_id": item.run_id,
        "workflow": item.workflow,
        "kind": item.kind,
        "title": item.title,
        "reasons": item.reasons,
        "proposal": item.proposal,
        "status": item.status,
        "decision": item.decision,
        "decided_by": item.decided_by,
        "created_at": iso(item.created_at),
        "decided_at": iso(item.decided_at),
        "can_resume": bool(item.resume_url),
    }


def dead_letter_dict(item: DeadLetter) -> dict[str, Any]:
    return {
        "id": item.id,
        "workflow": item.workflow,
        "run_id": item.run_id,
        "execution_id": item.execution_id,
        "node": item.node,
        "error_message": item.error,
        "status": item.status,
        "replays": item.replays,
        "created_at": iso(item.created_at),
        "updated_at": iso(item.updated_at),
        "has_payload": bool(item.payload),
    }


def call_dict(call: GatewayCall) -> dict[str, Any]:
    return {
        "id": call.id,
        "run_id": call.run_id,
        "task": call.task,
        "model": call.model,
        "served_model": call.served_model,
        "latency_ms": call.latency_ms,
        "input_tokens": call.input_tokens,
        "output_tokens": call.output_tokens,
        "cost_usd": call.cost_usd,
        "cached": call.cached,
        "attempts": call.attempts,
        "schema_valid": call.schema_valid,
        "decision": call.decision,
        "error_message": call.error,
        "created_at": iso(call.created_at),
    }


def _pct(values: list[int], q: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


class OpsStore:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        n8n_url: str = "http://localhost:5678",
        webhook_token: str = "",
        poster: Poster | None = None,
    ) -> None:
        self.sessions = sessions
        self.n8n_url = n8n_url.rstrip("/")
        self.webhook_token = webhook_token
        self.post = poster or _default_post

    # ------------------------------------------------------------------------------------------ runs

    def start_run(
        self,
        workflow: str,
        idempotency_key: str,
        *,
        execution_id: str | None = None,
        input: dict[str, Any] | None = None,
        source: str = "webhook",
        title: str = "",
    ) -> dict[str, Any]:
        key = idempotency_key.strip()[:200] or "-"
        with self.sessions.begin() as session:
            run = session.scalar(select(Run).where(Run.workflow == workflow, Run.idempotency_key == key))
            if run is not None and run.status != "error":
                run.duplicates += 1
                return {
                    "run_id": run.id,
                    "duplicate": True,
                    "status": run.status,
                    "route": run.route,
                    "output": run.output,
                }
            if run is not None:  # a failed run is retried as a new attempt on the same run
                run.attempts += 1
                run.status = "running"
                run.error = None
                run.execution_id = execution_id
                run.started_at = now()
                run.finished_at = None
                if input:
                    run.input = input
                return {"run_id": run.id, "duplicate": False, "status": run.status, "attempt": run.attempts}
            run = Run(
                workflow=workflow,
                idempotency_key=key,
                execution_id=execution_id,
                input=input or {},
                source=source,
                title=title[:300],
            )
            session.add(run)
            session.flush()
            return {"run_id": run.id, "duplicate": False, "status": run.status, "attempt": 1}

    def finish_run(
        self,
        run_id: str,
        status: str,
        *,
        route: str | None = None,
        output: dict[str, Any] | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        if status not in FINAL:
            raise ValueError(f"unknown status {status!r}")
        with self.sessions.begin() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise NotFoundError(run_id)
            run.status = status
            run.route = route or run.route
            if output is not None:
                run.output = {**(run.output or {}), **output}
            if title:
                run.title = title[:300]
            run.finished_at = now()
            started = run.started_at if run.started_at.tzinfo else run.started_at.replace(tzinfo=dt.UTC)
            run.latency_ms = int((run.finished_at - started).total_seconds() * 1000)
            data = run_dict(run)
            data["output"] = run.output
            return data

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise NotFoundError(run_id)
            data = run_dict(run, full=True)
            data["calls"] = [
                call_dict(c)
                for c in session.scalars(
                    select(GatewayCall).where(GatewayCall.run_id == run_id).order_by(GatewayCall.created_at)
                )
            ]
            data["reviews"] = [
                review_dict(r)
                for r in session.scalars(
                    select(ReviewItem).where(ReviewItem.run_id == run_id).order_by(ReviewItem.created_at)
                )
            ]
            data["dead_letters"] = [
                dead_letter_dict(d)
                for d in session.scalars(
                    select(DeadLetter).where(DeadLetter.run_id == run_id).order_by(DeadLetter.created_at)
                )
            ]
            return data

    def list_runs(
        self, *, workflow: str | None = None, status: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        query = select(Run).order_by(Run.started_at.desc()).limit(min(limit, 500)).offset(offset)
        if workflow:
            query = query.where(Run.workflow == workflow)
        if status:
            query = query.where(Run.status == status)
        with self.sessions() as session:
            return [run_dict(r) for r in session.scalars(query)]

    # ------------------------------------------------------------------------------------------ review queue

    def create_review(
        self,
        run_id: str,
        kind: str,
        *,
        title: str = "",
        reasons: list[str] | None = None,
        proposal: dict[str, Any] | None = None,
        resume_url: str | None = None,
    ) -> dict[str, Any]:
        with self.sessions.begin() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise NotFoundError(run_id)
            existing = session.scalar(
                select(ReviewItem).where(ReviewItem.run_id == run_id, ReviewItem.status == "pending")
            )
            if existing is not None:  # a retried execution re-creates its review: refresh it, do not duplicate
                existing.resume_url = resume_url or existing.resume_url
                existing.proposal = proposal or existing.proposal
                existing.reasons = reasons or existing.reasons
                return review_dict(existing)
            item = ReviewItem(
                run_id=run_id,
                workflow=run.workflow,
                kind=kind,
                title=title[:300] or run.title,
                reasons=reasons or [],
                proposal=proposal or {},
                resume_url=resume_url,
            )
            session.add(item)
            run.status = "review"
            session.flush()
            return review_dict(item)

    def list_reviews(
        self, *, status: str | None = "pending", workflow: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        query = select(ReviewItem).order_by(ReviewItem.created_at.desc()).limit(min(limit, 500))
        if status:
            query = query.where(ReviewItem.status == status)
        if workflow:
            query = query.where(ReviewItem.workflow == workflow)
        with self.sessions() as session:
            return [review_dict(r) for r in session.scalars(query)]

    def get_review(self, review_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            item = session.get(ReviewItem, review_id)
            if item is None:
                raise NotFoundError(review_id)
            data = review_dict(item)
            run = session.get(Run, item.run_id)
            data["run"] = run_dict(run, full=True) if run else None
            return data

    def internal_url(self, url: str) -> str:
        """n8n builds resume URLs from its public WEBHOOK_URL; call it on the address Relay can reach."""
        parts = urlsplit(url)
        base = urlsplit(self.n8n_url)
        return urlunsplit((base.scheme, base.netloc, parts.path, parts.query, ""))

    def decide(
        self,
        review_id: str,
        decision: str,
        *,
        edits: dict[str, Any] | None = None,
        reviewer: str = "reviewer",
        note: str = "",
    ) -> dict[str, Any]:
        if decision not in {"approve", "reject"}:
            raise ValueError("decision must be 'approve' or 'reject'")
        with self.sessions.begin() as session:
            item = session.get(ReviewItem, review_id)
            if item is None:
                raise NotFoundError(review_id)
            if item.status != "pending":
                raise ConflictError(f"review {review_id} is already {item.status}")
            body = {
                "decision": decision,
                "edits": edits or {},
                "review_id": item.id,
                "reviewer": reviewer,
                "note": note,
            }
            resume_url = item.resume_url
            item.status = "approved" if decision == "approve" else "rejected"
            item.decision = body
            item.decided_by = reviewer[:80]
            item.decided_at = now()
            run = session.get(Run, item.run_id)
            if run is not None and not resume_url:
                run.status = item.status
                run.finished_at = now()
        delivered: dict[str, Any] = {"delivered": False}
        if resume_url:
            try:
                response = self.post(self.internal_url(resume_url), body, {})
                delivered = {"delivered": response.status_code < 400, "status_code": response.status_code}
            except httpx.HTTPError as exc:
                delivered = {"delivered": False, "error": str(exc)[:200]}
            if not delivered["delivered"]:
                with self.sessions.begin() as session:
                    failed = session.get(ReviewItem, review_id)
                    if failed is not None:
                        failed.status = "failed"
                        failed.decision = {**failed.decision, "delivery": delivered}
        result = self.get_review(review_id)
        result["resume"] = delivered
        return result

    # ------------------------------------------------------------------------------------------ dead letters

    def dead_letter(
        self,
        workflow: str,
        *,
        execution_id: str | None = None,
        node: str = "",
        error: str = "",
        run_id: str | None = None,
    ) -> dict[str, Any]:
        with self.sessions.begin() as session:
            run = session.get(Run, run_id) if run_id else None
            if run is None and execution_id:
                run = session.scalar(select(Run).where(Run.execution_id == str(execution_id)))
            payload: dict[str, Any] = {}
            if run is not None:
                run.status = "error"
                run.error = f"{node}: {error}"[:2000]
                run.finished_at = now()
                payload = run.input or {}
                workflow = run.workflow
            item = DeadLetter(
                workflow=workflow,
                run_id=run.id if run else None,
                execution_id=execution_id,
                node=node[:120],
                error=error[:4000],
                payload=payload,
            )
            session.add(item)
            session.flush()
            return dead_letter_dict(item)

    def list_dead_letters(self, *, status: str | None = "open", limit: int = 100) -> list[dict[str, Any]]:
        query = select(DeadLetter).order_by(DeadLetter.created_at.desc()).limit(min(limit, 500))
        if status:
            query = query.where(DeadLetter.status == status)
        with self.sessions() as session:
            return [dead_letter_dict(d) for d in session.scalars(query)]

    def replay(self, dead_letter_id: str) -> dict[str, Any]:
        """Post the original input to the workflow's webhook again. The run's idempotency key is unchanged and the
        run is in `error`, so the workflow processes it as a new attempt, not as a duplicate."""
        with self.sessions() as session:
            item = session.get(DeadLetter, dead_letter_id)
            if item is None:
                raise NotFoundError(dead_letter_id)
            if not item.payload:
                raise ConflictError("this dead letter has no stored payload to replay")
            payload, workflow = dict(item.payload), item.workflow
        path = WEBHOOK_PATHS.get(workflow)
        if path is None:
            raise ConflictError(f"no webhook known for workflow {workflow!r}")
        try:
            response = self.post(f"{self.n8n_url}/webhook/{path}", payload, {"X-Relay-Token": self.webhook_token})
            ok = response.status_code < 400
            result: dict[str, Any] = {"status_code": response.status_code}
            try:
                result["response"] = response.json()
            except ValueError:
                result["response"] = response.text[:500]
        except httpx.HTTPError as exc:
            ok, result = False, {"error": str(exc)[:300]}
        with self.sessions.begin() as session:
            item = session.get(DeadLetter, dead_letter_id)
            assert item is not None
            item.replays += 1
            item.updated_at = now()
            if ok:
                item.status = "replayed"
        return {"ok": ok, **result, "dead_letter": self.get_dead_letter(dead_letter_id)}

    def get_dead_letter(self, dead_letter_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            item = session.get(DeadLetter, dead_letter_id)
            if item is None:
                raise NotFoundError(dead_letter_id)
            data = dead_letter_dict(item)
            data["payload_keys"] = sorted(item.payload.keys())
            return data

    def resolve_dead_letter(self, dead_letter_id: str) -> dict[str, Any]:
        with self.sessions.begin() as session:
            item = session.get(DeadLetter, dead_letter_id)
            if item is None:
                raise NotFoundError(dead_letter_id)
            item.status = "resolved"
            item.updated_at = now()
        return self.get_dead_letter(dead_letter_id)

    # ------------------------------------------------------------------------------------------ gateway calls

    def record_call(
        self, meta: Any, run_id: str | None, decision: str, summary: dict[str, Any], error: str | None = None
    ) -> None:
        if not meta.model:  # answered by a rule without a model call (e.g. an FAQ escalation policy)
            return
        with self.sessions.begin() as session:
            session.add(
                GatewayCall(
                    id=meta.call_id or new_id(),
                    run_id=run_id,
                    task=meta.task,
                    model=meta.model,
                    served_model=meta.served_model,
                    latency_ms=meta.latency_ms,
                    input_tokens=meta.input_tokens,
                    output_tokens=meta.output_tokens,
                    cost_usd=meta.cost_usd,
                    cached=meta.cached,
                    attempts=meta.attempts,
                    schema_valid=meta.schema_valid,
                    decision=decision[:32],
                    summary=summary,
                    error=error,
                )
            )

    def list_calls(self, *, task: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query = select(GatewayCall).order_by(GatewayCall.created_at.desc()).limit(min(limit, 500))
        if task:
            query = query.where(GatewayCall.task == task)
        with self.sessions() as session:
            return [call_dict(c) for c in session.scalars(query)]

    # ------------------------------------------------------------------------------------------ metrics

    def metrics(self, *, since: dt.datetime | None = None) -> dict[str, Any]:
        with self.sessions() as session:
            run_query = select(Run)
            call_query = select(GatewayCall)
            if since is not None:
                run_query = run_query.where(Run.started_at >= since)
                call_query = call_query.where(GatewayCall.created_at >= since)
            runs = list(session.scalars(run_query))
            calls = list(session.scalars(call_query))
            pending = (
                session.scalar(select(func.count()).select_from(ReviewItem).where(ReviewItem.status == "pending")) or 0
            )
            open_dlq = (
                session.scalar(select(func.count()).select_from(DeadLetter).where(DeadLetter.status == "open")) or 0
            )
        call_by_run: dict[str, list[GatewayCall]] = {}
        for call in calls:
            if call.run_id:
                call_by_run.setdefault(call.run_id, []).append(call)
        workflows: dict[str, dict[str, Any]] = {}
        for name in WEBHOOK_PATHS:
            subset = [r for r in runs if r.workflow == name]
            done = [r for r in subset if r.status != "running"]
            latencies = [r.latency_ms for r in done if r.latency_ms is not None and r.status in {"auto", "escalated"}]
            run_calls = [c for r in subset for c in call_by_run.get(r.id, [])]
            count = len(subset)
            auto = sum(1 for r in subset if r.status == "auto")
            human = sum(1 for r in subset if r.status in {"review", "approved", "rejected", "escalated"})
            workflows[name] = {
                "runs": count,
                "auto": auto,
                "review": human,
                "pending_review": sum(1 for r in subset if r.status == "review"),
                "errors": sum(1 for r in subset if r.status == "error"),
                "duplicates": sum(r.duplicates for r in subset),
                "auto_rate": round(auto / count, 3) if count else None,
                "latency_p50_ms": int(median(latencies)) if latencies else None,
                "latency_p95_ms": _pct(latencies, 0.95),
                "llm_calls": len(run_calls),
                "llm_tokens": sum(c.input_tokens + c.output_tokens for c in run_calls),
                "cost_usd": round(sum(c.cost_usd for c in run_calls), 6),
                "cost_per_run_usd": round(sum(c.cost_usd for c in run_calls) / count, 6) if count else None,
            }
        models: dict[str, int] = {}
        for call in calls:
            models[call.served_model or call.model] = models.get(call.served_model or call.model, 0) + 1
        days: dict[str, dict[str, int]] = {}
        for run in runs:
            day = (run.started_at if run.started_at.tzinfo else run.started_at.replace(tzinfo=dt.UTC)).date()
            bucket = days.setdefault(day.isoformat(), {"runs": 0, "auto": 0, "review": 0, "errors": 0})
            bucket["runs"] += 1
            if run.status == "auto":
                bucket["auto"] += 1
            elif run.status in {"review", "approved", "rejected", "escalated"}:
                bucket["review"] += 1
            elif run.status == "error":
                bucket["errors"] += 1
        total = len(runs)
        return {
            "totals": {
                "runs": total,
                "auto": sum(w["auto"] for w in workflows.values()),
                "review": sum(w["review"] for w in workflows.values()),
                "errors": sum(w["errors"] for w in workflows.values()),
                "duplicates": sum(w["duplicates"] for w in workflows.values()),
                "pending_review": pending,
                "open_dead_letters": open_dlq,
                "llm_calls": len(calls),
                "cached_calls": sum(1 for c in calls if c.cached),
                "invalid_outputs": sum(1 for c in calls if not c.schema_valid),
                "cost_usd": round(sum(c.cost_usd for c in calls), 6),
            },
            "workflows": workflows,
            "models": models,
            "days": dict(sorted(days.items())),
        }

    def reset(self) -> None:
        """Delete everything (the evaluation starts from an empty store)."""
        with self.sessions.begin() as session:
            for model in (GatewayCall, DeadLetter, ReviewItem, Run):
                session.query(model).delete()
