"""End to end through a running n8n (docker compose). Skipped unless RELAY_N8N_URL is set; CI starts the stack with
the offline model and sets it.

Covers: a webhook delivery routed to the CRM; an invoice PDF read by n8n's Extract from File and posted to
accounting; the same delivery again answered as a duplicate; a low-confidence email queued for review and resumed
by an approval (the Wait node continues the execution); the error workflow writing a dead letter when a mock
system is down, and the replay posting the bill exactly once."""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from relay.synthetic.emails import build_emails, email_payload

N8N = os.environ.get("RELAY_N8N_URL", "")
API = os.environ.get("RELAY_API_URL", "http://localhost:8100")
MOCKS = os.environ.get("RELAY_MOCKS_URL", "http://localhost:8200")
HOOK = {"X-Relay-Token": os.environ.get("RELAY_WEBHOOK_TOKEN", "relay-local-webhook-token")}
OPS = {"X-Relay-Token": os.environ.get("RELAY_API_TOKEN_VALUE", "relay-local-api-token")}

pytestmark = pytest.mark.skipif(not N8N, reason="needs the docker compose stack (set RELAY_N8N_URL)")


def hook(path: str, body: dict[str, Any]) -> httpx.Response:
    return httpx.post(f"{N8N}/webhook/{path}", json=body, headers=HOOK, timeout=120)


def wait_for(predicate: Any, seconds: float = 20.0) -> Any:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.5)
    raise AssertionError("condition not met in time")


@pytest.fixture(autouse=True)
def clean() -> None:
    httpx.post(f"{API}/ops/admin/reset", headers=OPS, timeout=30).raise_for_status()
    httpx.post(f"{MOCKS}/admin/reset", timeout=30).raise_for_status()


def email(email_id: str) -> dict[str, Any]:
    found = next(e for e in build_emails() if e.id == email_id)
    return email_payload(found, Path("data/invoices"))


def test_lead_email_is_routed_to_the_crm_once() -> None:
    first = hook("inbox-triage", email("em-001"))
    assert first.status_code == 200 and first.json()["route"] == "crm"
    leads = httpx.get(f"{MOCKS}/crm/leads").json()
    assert len(leads) == 1 and leads[0]["email"] == "lena@fernway-tea.example"
    second = hook("inbox-triage", email("em-001"))
    assert second.json()["status"] == "duplicate" and second.json()["run_id"] == first.json()["run_id"]
    assert len(httpx.get(f"{MOCKS}/crm/leads").json()) == 1


def test_invoice_email_reaches_accounting_through_extract_from_file() -> None:
    response = hook("inbox-triage", email("em-046"))
    assert response.status_code == 200 and response.json()["route"] == "accounting"
    bills = httpx.get(f"{MOCKS}/accounting/bills").json()
    assert [b["invoice_number"] for b in bills] == ["RE-2026-03296"] and bills[0]["total"] == 2205.18


def test_review_then_approval_resumes_the_execution() -> None:
    queued = hook("inbox-triage", email("em-073"))  # a supplier newsletter: category other -> review
    assert queued.status_code == 202 and queued.json()["status"] == "review"
    review_id = queued.json()["review_id"]
    decided = httpx.post(
        f"{API}/ops/review/{review_id}/decision",
        headers=OPS,
        timeout=60,
        json={"decision": "approve", "edits": {"category": "support"}},
    ).json()
    assert decided["resume"]["delivered"] is True
    wait_for(lambda: httpx.get(f"{MOCKS}/helpdesk/tickets").json())
    run = httpx.get(f"{API}/ops/runs/{queued.json()['run_id']}", headers=OPS).json()
    wait_for(lambda: httpx.get(f"{API}/ops/runs/{run['id']}", headers=OPS).json()["status"] == "approved")


def test_outage_goes_to_the_dead_letter_queue_and_replays_once() -> None:
    pdf = Path("data/invoices/pdf/inv-001.pdf")
    body = {"filename": pdf.name, "content_base64": base64.b64encode(pdf.read_bytes()).decode(), "source": "ci"}
    httpx.post(f"{MOCKS}/admin/chaos", json={"accounting": {"down": True}}).raise_for_status()
    failed = hook("invoice-intake", body)
    assert failed.status_code >= 500
    dead = wait_for(lambda: httpx.get(f"{API}/ops/dead-letters", headers=OPS).json())
    assert dead[0]["node"] == "Post bill to accounting"
    assert wait_for(lambda: httpx.get(f"{MOCKS}/chat/messages?channel=%23ops-alerts").json())
    httpx.post(f"{MOCKS}/admin/chaos", json={}).raise_for_status()
    replay = httpx.post(f"{API}/ops/dead-letters/{dead[0]['id']}/replay", headers=OPS, timeout=120).json()
    assert replay["ok"] is True
    bills = httpx.get(f"{MOCKS}/accounting/bills").json()
    assert [b["invoice_number"] for b in bills] == ["RE-2026-03295"]
