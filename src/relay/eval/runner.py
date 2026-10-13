"""The end-to-end evaluation: every item goes through the real n8n workflows (webhook triggers in docker compose),
the AI gateway and the mock systems, and is scored against its gold label from what actually happened: the webhook
response, Relay's run log and the records the mock CRM, helpdesk and accounting received.

    relay eval all --name free-nemotron         (needs `docker compose up`; the provider is the relay container's)
    relay eval triage --limit 10 --name smoke

Suites: triage (80 emails), invoices (40 PDFs), leads (30 web forms; drafts approved by a simulated reviewer so the
send path runs too), faq (40 questions), digest (1 run), idempotency (re-sends), chaos (injected 503s and an outage,
then a dead-letter replay). Each suite starts from an empty ops store and empty mock systems."""

from __future__ import annotations

import base64
import contextlib
import datetime as dt
import json
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml

from relay.config import Settings
from relay.eval import scoring as sc
from relay.gateway.leads import load_icp
from relay.logging_config import get_logger
from relay.synthetic.emails import build_emails, email_payload

log = get_logger(__name__)

SUITES = ("triage", "invoices", "leads", "faq", "digest", "idempotency", "chaos")


@dataclass
class Endpoints:
    n8n: str = "http://localhost:5678"
    api: str = "http://localhost:8100"
    mocks: str = "http://localhost:8200"
    mailpit: str = "http://localhost:8025"
    webhook_token: str = "relay-local-webhook-token"
    api_token: str = "relay-local-api-token"


@dataclass
class Posted:
    status_code: int
    body: dict[str, Any]
    seconds: float
    error: str | None = None


@dataclass
class Harness:
    settings: Settings
    endpoints: Endpoints
    data_dir: Path = Path("data")
    timeout: float = 600.0
    client: httpx.Client = field(default_factory=lambda: httpx.Client(timeout=600.0))

    # ------------------------------------------------------------------------------------ plumbing

    def webhook(self, path: str, body: dict[str, Any]) -> Posted:
        started = time.monotonic()
        try:
            response = self.client.post(
                f"{self.endpoints.n8n}/webhook/{path}",
                json=body,
                headers={"X-Relay-Token": self.endpoints.webhook_token},
            )
        except httpx.HTTPError as exc:
            return Posted(0, {}, round(time.monotonic() - started, 3), str(exc)[:200])
        seconds = round(time.monotonic() - started, 3)
        try:
            data = response.json() if response.content else {}
        except ValueError:
            data = {"raw": response.text[:300]}
        if not isinstance(data, dict):
            data = {"raw": data}
        error = None if response.status_code < 400 else str(data)[:300]
        return Posted(response.status_code, data, seconds, error)

    def api(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.client.request(
            method, f"{self.endpoints.api}{path}", headers={"X-Relay-Token": self.endpoints.api_token}, **kwargs
        )
        response.raise_for_status()
        return response.json()

    def mocks(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.client.request(method, f"{self.endpoints.mocks}{path}", **kwargs)
        response.raise_for_status()
        return response.json()

    def reset(self) -> None:
        self.api("POST", "/ops/admin/reset")
        self.mocks("POST", "/admin/reset")
        with contextlib.suppress(httpx.HTTPError):
            self.client.delete(f"{self.endpoints.mailpit}/api/v1/messages")

    def run_detail(self, run_id: str | None) -> dict[str, Any]:
        if not run_id:
            return {}
        try:
            data: dict[str, Any] = self.api("GET", f"/ops/runs/{run_id}")
            return data
        except httpx.HTTPError:
            return {}

    def wait_idle(self, seconds: float = 2.0) -> None:
        time.sleep(seconds)

    @staticmethod
    def call_stats(detail: dict[str, Any]) -> dict[str, Any]:
        calls = detail.get("calls") or []
        return {
            "llm_calls": len(calls),
            "llm_attempts": sum(int(c.get("attempts") or 1) for c in calls),
            "llm_ms": sum(int(c.get("latency_ms") or 0) for c in calls),
            "tokens": sum(int(c.get("input_tokens") or 0) + int(c.get("output_tokens") or 0) for c in calls),
            "cost_usd": round(sum(float(c.get("cost_usd") or 0) for c in calls), 6),
            "models": sorted({str(c.get("served_model") or c.get("model")) for c in calls}),
            # A failed request (provider error, retried by n8n) is not an invalid output.
            "schema_valid": all(c.get("schema_valid", True) for c in calls if not c.get("error_message")),
            "provider_errors": sum(1 for c in calls if c.get("error_message")),
            "cached": bool(calls) and all(c.get("cached") for c in calls),
        }

    def health(self) -> dict[str, Any]:
        data: dict[str, Any] = self.client.get(f"{self.endpoints.api}/health").json()
        return data

    # ------------------------------------------------------------------------------------ triage

    def triage(self, limit: int | None = None) -> dict[str, Any]:
        self.reset()
        emails = build_emails()[:limit] if limit else build_emails()
        items = []
        for email in emails:
            posted = self.webhook("inbox-triage", email_payload(email, self.data_dir / "invoices"))
            detail = self.run_detail(posted.body.get("run_id"))
            review = (detail.get("reviews") or [{}])[0]
            output = detail.get("output") or {}
            proposal = review.get("proposal") or {}
            fields = output if output.get("category") else proposal
            items.append(
                {
                    "id": email.id,
                    "gold_category": email.category,
                    "hard": email.hard,
                    "language": email.language,
                    "http": posted.status_code,
                    "error": posted.error,
                    "status": posted.body.get("status"),
                    "category": posted.body.get("category") or fields.get("category"),
                    "confidence": posted.body.get("confidence"),
                    "route": posted.body.get("route") or ("review" if posted.body.get("status") == "review" else None),
                    "reasons": posted.body.get("reasons") or review.get("reasons") or [],
                    "fields": {
                        "name": (fields.get("contact") or {}).get("name"),
                        "company": (fields.get("contact") or {}).get("company"),
                        "product": (fields.get("lead") or {}).get("product"),
                        "quantity": (fields.get("lead") or {}).get("quantity"),
                        "order_id": (fields.get("support") or {}).get("order_id"),
                        "issue": (fields.get("support") or {}).get("issue"),
                        "invoice_number": (fields.get("invoice") or {}).get("invoice_number"),
                        "amount": (fields.get("invoice") or {}).get("amount"),
                    },
                    "gold_fields": email.fields,
                    "seconds": posted.seconds,
                    "run_id": posted.body.get("run_id"),
                    **self.call_stats(detail),
                }
            )
            log.info(
                "eval.triage",
                id=email.id,
                gold=email.category,
                got=items[-1]["category"],
                status=items[-1]["status"],
                seconds=posted.seconds,
            )
        self.wait_idle()
        downstream = {
            "crm": self.mocks("GET", "/crm/leads"),
            "helpdesk": self.mocks("GET", "/helpdesk/tickets"),
            "accounting": self.mocks("GET", "/accounting/bills"),
        }
        return {"suite": "triage", "items": items, "summary": score_triage(items, downstream)}

    # ------------------------------------------------------------------------------------ invoices

    def invoices(self, limit: int | None = None) -> dict[str, Any]:
        self.reset()
        gold = [json.loads(line) for line in (self.data_dir / "invoices" / "gold.jsonl").open(encoding="utf-8")]
        gold = gold[:limit] if limit else gold
        items = []
        for invoice in gold:
            pdf = self.data_dir / "invoices" / "pdf" / f"{invoice['id']}.pdf"
            body = {
                "filename": pdf.name,
                "content_base64": base64.b64encode(pdf.read_bytes()).decode(),
                "source": "eval",
            }
            posted = self.webhook("invoice-intake", body)
            detail = self.run_detail(posted.body.get("run_id"))
            review = (detail.get("reviews") or [{}])[0]
            extracted = (
                posted.body.get("bill")
                or posted.body.get("invoice")
                or (review.get("proposal") or {}).get("invoice")
                or {}
            )
            items.append(
                {
                    "id": invoice["id"],
                    "gold": {
                        k: invoice[k]
                        for k in (
                            "vendor",
                            "invoice_number",
                            "issue_date",
                            "due_date",
                            "currency",
                            "subtotal",
                            "vat_amount",
                            "total",
                        )
                    },
                    "gold_lines": len(invoice["lines"]),
                    "expected": invoice["expected_decision"],
                    "planted": invoice["issues"],
                    "http": posted.status_code,
                    "error": posted.error,
                    "status": posted.body.get("status"),
                    "issues": posted.body.get("issues") or (review.get("proposal") or {}).get("issues") or [],
                    "extracted": {
                        k: extracted.get(k)
                        for k in (
                            "vendor",
                            "invoice_number",
                            "issue_date",
                            "due_date",
                            "currency",
                            "subtotal",
                            "vat_amount",
                            "total",
                        )
                    },
                    "lines": len(extracted.get("lines") or []),
                    "seconds": posted.seconds,
                    "run_id": posted.body.get("run_id"),
                    **self.call_stats(detail),
                }
            )
            log.info(
                "eval.invoice",
                id=invoice["id"],
                expected=invoice["expected_decision"],
                status=items[-1]["status"],
                issues=items[-1]["issues"],
            )
        bills = self.mocks("GET", "/accounting/bills")
        return {"suite": "invoices", "items": items, "summary": score_invoices(items, bills)}

    # ------------------------------------------------------------------------------------ leads

    def leads(self, limit: int | None = None, approve: bool = True) -> dict[str, Any]:
        self.reset()
        leads = [json.loads(line) for line in (self.data_dir / "leads" / "leads.jsonl").open(encoding="utf-8")]
        leads = leads[:limit] if limit else leads
        icp = load_icp(Path("configs/icp.yaml"))
        items = []
        for lead in leads:
            body = {k: lead[k] for k in ("name", "email", "company", "website", "message", "product", "quantity")}
            body["submission_id"] = lead["id"]
            posted = self.webhook("lead-enrichment", body)
            detail = self.run_detail(posted.body.get("run_id"))
            review = (detail.get("reviews") or [{}])[0]
            proposal = review.get("proposal") or {}
            profile = posted.body.get("profile") or proposal.get("profile") or {}
            checks = proposal.get("checks") or []
            items.append(
                {
                    "id": lead["id"],
                    "hard": lead.get("hard"),
                    "gold": lead["gold"],
                    "gold_tier": sc.gold_tier(lead, icp, self.settings.lead_hot_score, self.settings.lead_warm_score),
                    "http": posted.status_code,
                    "error": posted.error,
                    "status": posted.body.get("status"),
                    "kind": posted.body.get("kind"),
                    "tier": posted.body.get("tier"),
                    "score": posted.body.get("score"),
                    "profile": {
                        k: profile.get(k) for k in ("industry", "employees_band", "country", "sells_online", "buyer")
                    },
                    "site": posted.body.get("site") or {},
                    "reasons": posted.body.get("reasons") or [],
                    "draft_checks": {c["code"]: c["ok"] for c in checks},
                    "draft_words": len(str(proposal.get("body") or "").split()),
                    "review_id": review.get("id"),
                    "seconds": posted.seconds,
                    "run_id": posted.body.get("run_id"),
                    **self.call_stats(detail),
                }
            )
            log.info("eval.lead", id=lead["id"], tier=items[-1]["tier"], gold=items[-1]["gold_tier"])
        sent = 0
        if approve:
            # A simulated reviewer approves every draft whose checks passed, so the Send reply path runs.
            for item in items:
                if item["kind"] == "lead_reply" and item["review_id"] and all(item["draft_checks"].values()):
                    result = self.api(
                        "POST",
                        f"/ops/review/{item['review_id']}/decision",
                        json={"decision": "approve", "reviewer": "eval"},
                    )
                    item["approved"] = bool(result.get("resume", {}).get("delivered"))
            self.wait_idle(4.0)
            try:
                messages = self.client.get(f"{self.endpoints.mailpit}/api/v1/messages?limit=200").json()
                sent = sum(
                    1
                    for m in messages.get("messages") or []
                    if (m.get("From") or {}).get("Address") == "sales@kestrelprint.example"
                )
            except (httpx.HTTPError, ValueError):
                sent = -1
        crm = self.mocks("GET", "/crm/leads")
        return {"suite": "leads", "items": items, "summary": score_leads(items, crm, sent)}

    # ------------------------------------------------------------------------------------ FAQ

    def faq(self, limit: int | None = None) -> dict[str, Any]:
        self.reset()
        questions = yaml.safe_load((self.data_dir / "faq" / "questions.yaml").read_text(encoding="utf-8"))
        questions = questions[:limit] if limit else questions
        items = []
        for q in questions:
            posted = self.webhook(
                "support-faq",
                {"question": q["question"], "message_id": q["id"], "customer_email": f"{q['id']}@customer.example"},
            )
            detail = self.run_detail(posted.body.get("run_id"))
            items.append(
                {
                    "id": q["id"],
                    "expected": "auto_reply" if q["expected"] == "answer" else "escalate",
                    "gold_articles": q.get("articles") or [],
                    "facts": q.get("facts") or [],
                    "http": posted.status_code,
                    "error": posted.error,
                    "decision": posted.body.get("status"),
                    "answer": posted.body.get("answer") or "",
                    "citations": posted.body.get("citations") or [],
                    "confidence": posted.body.get("confidence"),
                    "reasons": posted.body.get("reasons") or [],
                    "retrieved": [r.get("id") for r in posted.body.get("retrieved") or []],
                    "seconds": posted.seconds,
                    "run_id": posted.body.get("run_id"),
                    **self.call_stats(detail),
                }
            )
            log.info("eval.faq", id=q["id"], expected=items[-1]["expected"], got=items[-1]["decision"])
        tickets = self.mocks("GET", "/helpdesk/tickets")
        return {"suite": "faq", "items": items, "summary": score_faq(items, tickets)}

    # ------------------------------------------------------------------------------------ digest

    def digest(self) -> dict[str, Any]:
        # Runs on whatever the previous suites left in the store (the reset happens at the start of each suite).
        posted = self.webhook("kpi-digest", {})
        detail = self.run_detail((posted.body or {}).get("id"))
        output = (posted.body or {}).get("output") or {}
        messages = self.mocks("GET", "/chat/messages?channel=%23ops")
        item = {
            "http": posted.status_code,
            "error": posted.error,
            "seconds": posted.seconds,
            "numbers_ok": output.get("numbers_ok"),
            "text": output.get("text"),
            "posted_to_chat": len(messages),
            **self.call_stats(detail),
        }
        return {
            "suite": "digest",
            "items": [item],
            "summary": {
                "runs": 1,
                "ok": posted.status_code == 200,
                "numbers_ok": output.get("numbers_ok"),
                "seconds": posted.seconds,
                "posted_to_chat": len(messages) > 0,
            },
        }

    # ------------------------------------------------------------------------------------ idempotency

    def idempotency(self) -> dict[str, Any]:
        """Send 10 emails, 5 invoices, 5 leads and 5 questions, then send every one again: the second delivery
        must be answered as a duplicate and must not create a second CRM lead, ticket or bill."""
        self.reset()
        emails = [e for e in build_emails() if e.category in {"sales_lead", "support"}][:10]
        gold = [json.loads(line) for line in (self.data_dir / "invoices" / "gold.jsonl").open(encoding="utf-8")]
        clean = [g for g in gold if g["expected_decision"] == "auto_post"][:5]
        leads = [json.loads(line) for line in (self.data_dir / "leads" / "leads.jsonl").open(encoding="utf-8")][:5]
        questions = yaml.safe_load((self.data_dir / "faq" / "questions.yaml").read_text(encoding="utf-8"))[:5]
        deliveries: list[tuple[str, dict[str, Any]]] = []
        deliveries += [("inbox-triage", email_payload(e, self.data_dir / "invoices")) for e in emails]
        for g in clean:
            pdf = self.data_dir / "invoices" / "pdf" / f"{g['id']}.pdf"
            deliveries.append(
                (
                    "invoice-intake",
                    {
                        "filename": pdf.name,
                        "source": "eval",
                        "content_base64": base64.b64encode(pdf.read_bytes()).decode(),
                    },
                )
            )
        for lead in leads:
            body = {k: lead[k] for k in ("name", "email", "company", "website", "message", "product", "quantity")}
            deliveries.append(("lead-enrichment", {**body, "submission_id": lead["id"]}))
        deliveries += [
            (
                "support-faq",
                {"question": q["question"], "message_id": q["id"], "customer_email": "dup@customer.example"},
            )
            for q in questions
        ]
        first = [self.webhook(path, body) for path, body in deliveries]
        self.wait_idle()
        before = self.mocks("GET", "/admin/state")["counts"]
        second = [self.webhook(path, body) for path, body in deliveries]
        self.wait_idle()
        after = self.mocks("GET", "/admin/state")["counts"]
        duplicates = sum(1 for p in second if p.body.get("status") == "duplicate")
        same_run = sum(
            1
            for a, b in zip(first, second, strict=True)
            if a.body.get("run_id") and a.body.get("run_id") == b.body.get("run_id")
        )
        return {
            "suite": "idempotency",
            "items": [
                {
                    "path": path,
                    "first": a.body.get("status"),
                    "second": b.body.get("status"),
                    "same_run": a.body.get("run_id") == b.body.get("run_id"),
                }
                for (path, _), a, b in zip(deliveries, first, second, strict=True)
            ],
            "summary": {
                "deliveries": len(deliveries),
                "second_delivery_answered_as_duplicate": duplicates,
                "same_run_id": same_run,
                "records_before_resend": before,
                "records_after_resend": after,
                "new_records_from_resend": sum(after[k] - before.get(k, 0) for k in after),
                "duplicate_p50_seconds": sc.p50([p.seconds for p in second]),
            },
        }

    # ------------------------------------------------------------------------------------ chaos

    def chaos(self, count: int = 12) -> dict[str, Any]:
        """Failure handling on the invoice workflow:
        phase 1: accounting fails the first attempt of every bill and 25% of the others at random; n8n's
                 retry-on-fail (3 tries, 2 s apart) should absorb it;
        phase 2: accounting is down; every run should fail into the dead-letter queue with an #ops-alerts message;
        phase 3: accounting is back; replaying every dead letter should post each bill exactly once."""
        self.reset()
        gold = [json.loads(line) for line in (self.data_dir / "invoices" / "gold.jsonl").open(encoding="utf-8")]
        clean = [g for g in gold if g["expected_decision"] == "auto_post"]
        phase1, phase2 = clean[: count // 2], clean[count // 2 : count]

        def send(invoice: dict[str, Any]) -> Posted:
            pdf = self.data_dir / "invoices" / "pdf" / f"{invoice['id']}.pdf"
            return self.webhook(
                "invoice-intake",
                {
                    "filename": pdf.name,
                    "source": "eval-chaos",
                    "content_base64": base64.b64encode(pdf.read_bytes()).decode(),
                },
            )

        self.mocks("POST", "/admin/chaos", json={"accounting": {"fail_first": 1, "rate": 0.25, "seed": 3}})
        p1 = [send(g) for g in phase1]
        injected_1 = self.mocks("GET", "/admin/state")["injected_failures"]
        self.mocks("POST", "/admin/chaos", json={"accounting": {"down": True}})
        p2 = [send(g) for g in phase2]
        injected_2 = self.mocks("GET", "/admin/state")["injected_failures"]
        self.wait_idle(3.0)
        dead = self.api("GET", "/ops/dead-letters?status=open")
        alerts = self.mocks("GET", "/chat/messages?channel=%23ops-alerts")
        self.mocks("POST", "/admin/chaos", json={})
        replays = [self.api("POST", f"/ops/dead-letters/{d['id']}/replay") for d in dead]
        self.wait_idle(2.0)
        bills = self.mocks("GET", "/accounting/bills")
        numbers = Counter(b.get("invoice_number") for b in bills)
        expected = {g["invoice_number"] for g in phase1 + phase2}
        return {
            "suite": "chaos",
            "items": [
                {"phase": 1, "id": g["id"], "http": p.status_code, "status": p.body.get("status")}
                for g, p in zip(phase1, p1, strict=True)
            ]
            + [
                {"phase": 2, "id": g["id"], "http": p.status_code, "status": p.body.get("status")}
                for g, p in zip(phase2, p2, strict=True)
            ],
            "summary": {
                "phase1_invoices": len(phase1),
                "phase1_injected_503s": injected_1,
                "phase1_posted": sum(1 for p in p1 if p.body.get("status") == "posted"),
                "phase2_invoices": len(phase2),
                "phase2_failed_runs": sum(1 for p in p2 if p.status_code >= 500),
                "phase2_injected_503s": injected_2 - injected_1,
                "dead_letters": len(dead),
                "ops_alerts": len(alerts),
                "replayed_ok": sum(1 for r in replays if r.get("ok")),
                "bills_posted": len(bills),
                "expected_bills": len(expected),
                "missing_bills": sorted(expected - set(numbers)),
                "double_posted": sorted(n for n, c in numbers.items() if c > 1),
            },
        }


# ======================================================================================== scoring per suite


def _common(items: list[dict[str, Any]]) -> dict[str, Any]:
    seconds = [i["seconds"] for i in items if not i.get("error")]
    llm = [i["llm_ms"] / 1000 for i in items if i.get("llm_calls")]
    models = Counter(m for i in items for m in i.get("models") or [])
    return {
        "items": len(items),
        "errors": sum(1 for i in items if i.get("error")),
        "error_rate": sc.rate(sum(1 for i in items if i.get("error")), len(items)),
        "latency_p50_s": sc.p50(seconds),
        "latency_p95_s": sc.pct(seconds, 0.95),
        "llm_seconds_p50": sc.p50(llm),
        "llm_calls": sum(i.get("llm_calls", 0) for i in items),
        "llm_calls_per_item": round(sum(i.get("llm_calls", 0) for i in items) / max(1, len(items)), 2),
        "invalid_model_outputs": sum(1 for i in items if not i.get("schema_valid", True)),
        "provider_errors_retried": sum(i.get("provider_errors", 0) for i in items),
        "tokens": sum(i.get("tokens", 0) for i in items),
        "cost_usd": round(sum(i.get("cost_usd", 0.0) for i in items), 6),
        "cost_per_item_usd": round(sum(i.get("cost_usd", 0.0) for i in items) / max(1, len(items)), 6),
        "models": dict(models),
    }


TRIAGE_FIELDS = {
    "name": sc.same_text,
    "company": sc.same_text,
    "product": sc.same_value,
    "quantity": sc.same_number,
    "order_id": sc.same_id,
    "issue": sc.same_value,
    "invoice_number": sc.same_id,
    "amount": sc.same_number,
}


def score_triage(items: list[dict[str, Any]], downstream: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    n = len(items)
    correct = [i for i in items if i["category"] == i["gold_category"]]
    routed = [i for i in items if i["status"] == "routed"]
    review = [i for i in items if i["status"] == "review"]
    routed_ok = [i for i in routed if i["category"] == i["gold_category"]]
    wrong = [i for i in items if i["category"] != i["gold_category"]]
    caught = [i for i in wrong if i["status"] == "review"]
    per_cat: dict[str, dict[str, int]] = {}
    for i in items:
        bucket = per_cat.setdefault(i["gold_category"], {"items": 0, "correct": 0, "routed": 0, "routed_correct": 0})
        bucket["items"] += 1
        bucket["correct"] += i["category"] == i["gold_category"]
        bucket["routed"] += i["status"] == "routed"
        bucket["routed_correct"] += i["status"] == "routed" and i["category"] == i["gold_category"]
    confusion = Counter(f"{i['gold_category']}->{i['category']}" for i in wrong)
    fields: dict[str, dict[str, Any]] = {}
    for name, same in TRIAGE_FIELDS.items():
        scored = [i for i in correct if (i["gold_fields"] or {}).get(name) is not None]
        hits = [i for i in scored if same(i["fields"].get(name), i["gold_fields"][name])]
        fields[name] = {
            "scored": len(scored),
            "correct": len(hits),
            "accuracy": sc.rate(len(hits), len(scored)),
            "misses": [
                {"id": i["id"], "got": i["fields"].get(name), "gold": i["gold_fields"][name]}
                for i in scored
                if i not in hits
            ][:8],
        }
    total_fields = sum(f["scored"] for f in fields.values())
    total_hits = sum(f["correct"] for f in fields.values())
    # End to end: did each auto-routed email land in the system its gold category belongs to?
    landed: dict[str, str] = {}
    for system, records in downstream.items():
        for record in records:
            mid = str(record.get("message_id") or "")
            if mid:
                landed[mid] = system
    expected_system = {"sales_lead": "crm", "support": "helpdesk"}
    e2e_scored = [i for i in routed if i["category"] in expected_system]
    e2e_ok = [
        i
        for i in e2e_scored
        if landed.get(f"<{i['id']}@mail.kestrelprint.example>") == expected_system.get(i["gold_category"])
    ]
    hard = [i for i in items if i.get("hard")]
    return {
        **_common(items),
        "category_accuracy": sc.rate(len(correct), n),
        "auto_handled": len(routed),
        "auto_handled_rate": sc.rate(len(routed), n),
        "auto_handled_accuracy": sc.rate(len(routed_ok), len(routed)),
        "misrouted_automatically": len(routed) - len(routed_ok),
        "sent_to_review": len(review),
        "review_rate": sc.rate(len(review), n),
        "wrong_category": len(wrong),
        "wrong_caught_by_review": len(caught),
        "hard_items": len(hard),
        "hard_accuracy": sc.rate(sum(1 for i in hard if i["category"] == i["gold_category"]), len(hard)),
        "per_category": per_cat,
        "confusion": dict(confusion),
        "fields": fields,
        "field_accuracy": sc.rate(total_hits, total_fields),
        "downstream_records": {k: len(v) for k, v in downstream.items()},
        "downstream_correct_system": f"{len(e2e_ok)}/{len(e2e_scored)}",
        "review_reasons": dict(
            Counter(
                r.split(" below ")[0] if "confidence" in r else r for i in review for r in i["reasons"]
            ).most_common(10)
        ),
    }


INVOICE_FIELDS = {
    "vendor": sc.same_text,
    "invoice_number": sc.same_id,
    "issue_date": sc.same_value,
    "due_date": sc.same_value,
    "currency": sc.same_value,
    "subtotal": sc.same_number,
    "vat_amount": sc.same_number,
    "total": sc.same_number,
}


def score_invoices(items: list[dict[str, Any]], bills: list[dict[str, Any]]) -> dict[str, Any]:
    fields: dict[str, dict[str, Any]] = {}
    for name, same in INVOICE_FIELDS.items():
        scored = [i for i in items if i["status"] in {"posted", "review"}]
        hits = [i for i in scored if same(i["extracted"].get(name), i["gold"][name])]
        fields[name] = {
            "scored": len(scored),
            "correct": len(hits),
            "accuracy": sc.rate(len(hits), len(scored)),
            "misses": [
                {"id": i["id"], "got": i["extracted"].get(name), "gold": i["gold"][name]}
                for i in scored
                if i not in hits
            ][:6],
        }
    all_right = [
        i
        for i in items
        if i["status"] in {"posted", "review"}
        and all(INVOICE_FIELDS[k](i["extracted"].get(k), i["gold"][k]) for k in INVOICE_FIELDS)
    ]
    posted = [i for i in items if i["status"] == "posted"]
    bad = [i for i in items if i["expected"] == "review"]
    clean = [i for i in items if i["expected"] == "auto_post"]
    bad_posted = [i for i in bad if i["status"] == "posted"]
    clean_reviewed = [i for i in clean if i["status"] != "posted"]
    per_issue: dict[str, dict[str, int]] = {}
    for i in bad:
        for issue in i["planted"]:
            bucket = per_issue.setdefault(issue, {"planted": 0, "flagged": 0, "flagged_with_this_issue": 0})
            bucket["planted"] += 1
            bucket["flagged"] += i["status"] != "posted"
            bucket["flagged_with_this_issue"] += issue in i["issues"]
    numbers = Counter(b.get("invoice_number") for b in bills)
    reasons = Counter(r for i in clean_reviewed for r in i["issues"])
    return {
        **_common(items),
        "fields": fields,
        "field_accuracy": sc.rate(
            sum(f["correct"] for f in fields.values()), sum(f["scored"] for f in fields.values())
        ),
        "all_fields_right": f"{len(all_right)}/{len(items)}",
        "line_count_right": sum(1 for i in items if i["lines"] == i["gold_lines"]),
        "decision_accuracy": sc.rate(
            sum(1 for i in items if (i["status"] == "posted") == (i["expected"] == "auto_post")), len(items)
        ),
        "auto_posted": len(posted),
        "auto_post_rate": sc.rate(len(posted), len(items)),
        "problem_invoices": len(bad),
        "problem_invoices_auto_posted": len(bad_posted),
        "problem_invoices_auto_posted_ids": [i["id"] for i in bad_posted],
        "clean_invoices": len(clean),
        "clean_invoices_sent_to_review": len(clean_reviewed),
        "clean_review_reasons": dict(reasons),
        "per_issue": per_issue,
        "bills_in_accounting": len(bills),
        "double_posted": sorted(n for n, c in numbers.items() if c > 1),
    }


def score_leads(items: list[dict[str, Any]], crm: list[dict[str, Any]], sent: int) -> dict[str, Any]:
    with_site = [i for i in items if i["gold"]["site_found"]]
    fields: dict[str, dict[str, Any]] = {}
    for name in ("industry", "employees_band", "country", "sells_online"):
        hits = [i for i in with_site if i["profile"].get(name) == i["gold"][name]]
        fields[name] = {
            "scored": len(with_site),
            "correct": len(hits),
            "accuracy": sc.rate(len(hits), len(with_site)),
            "misses": [
                {"id": i["id"], "got": i["profile"].get(name), "gold": i["gold"][name]}
                for i in with_site
                if i not in hits
            ][:6],
        }
    tier_hits = [i for i in items if i["tier"] == i["gold_tier"]]
    disq = [i for i in items if i["gold"].get("disqualified")]
    drafts = [i for i in items if i["kind"] == "lead_reply"]
    drafts_ok = [i for i in drafts if i["draft_checks"] and all(i["draft_checks"].values())]
    check_fail = Counter(code for i in drafts for code, ok in i["draft_checks"].items() if not ok)
    injection = [i for i in items if i.get("hard") and "AI" in str(i.get("hard"))]
    return {
        **_common(items),
        "fields": fields,
        "profile_accuracy": sc.rate(
            sum(f["correct"] for f in fields.values()), sum(f["scored"] for f in fields.values())
        ),
        "tier_accuracy": sc.rate(len(tier_hits), len(items)),
        "tier_misses": [{"id": i["id"], "got": i["tier"], "gold": i["gold_tier"]} for i in items if i not in tier_hits],
        "tiers": dict(Counter(i["tier"] for i in items)),
        "gold_tiers": dict(Counter(i["gold_tier"] for i in items)),
        "disqualified_caught": f"{sum(1 for i in disq if i['tier'] == 'disqualified')}/{len(disq)}",
        "drafts": len(drafts),
        "drafts_passing_checks": len(drafts_ok),
        "draft_check_failures": dict(check_fail),
        "research_queue": sum(1 for i in items if i["kind"] == "lead_research"),
        "injection_site": [
            {
                "id": i["id"],
                "tier": i["tier"],
                "gold": i["gold_tier"],
                "employees_band": i["profile"].get("employees_band"),
                "reasons": i["reasons"],
            }
            for i in injection
        ],
        "crm_records": len(crm),
        "replies_sent_after_approval": sent,
    }


def score_faq(items: list[dict[str, Any]], tickets: list[dict[str, Any]]) -> dict[str, Any]:
    answerable = [i for i in items if i["expected"] == "auto_reply"]
    escalate = [i for i in items if i["expected"] == "escalate"]
    auto = [i for i in items if i["decision"] == "auto_reply"]
    unsafe = [i for i in escalate if i["decision"] == "auto_reply"]
    over = [i for i in answerable if i["decision"] != "auto_reply"]
    auto_good = [i for i in auto if i["expected"] == "auto_reply"]
    facts_ok = [i for i in auto_good if all(sc.fact_hit(i["answer"], f) for f in i["facts"])]
    cites_ok = [i for i in auto_good if set(i["citations"]) & set(i["gold_articles"])]
    recall = [i for i in answerable if set(i["retrieved"]) & set(i["gold_articles"])]
    reasons = Counter(r.split(":")[0] for i in over for r in i["reasons"])
    return {
        **_common(items),
        "decision_accuracy": sc.rate(sum(1 for i in items if i["decision"] == i["expected"]), len(items)),
        "auto_replied": len(auto),
        "auto_reply_rate": sc.rate(len(auto), len(items)),
        "answerable": len(answerable),
        "answerable_auto_replied": len(auto_good),
        "must_escalate": len(escalate),
        "must_escalate_escalated": len(escalate) - len(unsafe),
        "unsafe_auto_replies": [i["id"] for i in unsafe],
        "over_escalated": [i["id"] for i in over],
        "over_escalation_reasons": dict(reasons),
        "auto_replies_with_all_facts": f"{len(facts_ok)}/{len(auto_good)}",
        "auto_replies_citing_gold_article": f"{len(cites_ok)}/{len(auto_good)}",
        "fact_misses": [
            {"id": i["id"], "facts": i["facts"], "answer": i["answer"][:300]} for i in auto_good if i not in facts_ok
        ],
        "retrieval_recall_at_4": sc.rate(len(recall), len(answerable)),
        "tickets": len(tickets),
    }


def write_result(result: dict[str, Any], directory: Path, name: str, meta: dict[str, Any]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{result['suite']}_{name}.json"
    payload = {
        "name": name,
        "suite": result["suite"],
        "run_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        **meta,
        "summary": result["summary"],
        "items": result["items"],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
