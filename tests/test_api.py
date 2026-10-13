"""The HTTP API: gateway endpoints, degradation on invalid model output, errors, auth, the Mailpit bridge."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from relay.api.app import create_app
from relay.config import Settings
from relay.gateway.service import Gateway
from relay.llm.base import Completion, LLMError, Message, PolicyViolationError
from relay.ops.store import OpsStore
from tests.conftest import Poster


class Broken:
    def __init__(self, behaviour: str) -> None:
        self.behaviour = behaviour

    @property
    def label(self) -> str:
        return "openrouter/a/b:free"

    @property
    def is_local(self) -> bool:
        return True

    def complete(self, messages: Sequence[Message], *, max_tokens: int, temperature: float = 0.0) -> Completion:
        if self.behaviour == "garbage":
            return Completion(text="I think this is a lead!", model="a/b:free")
        if self.behaviour == "policy":
            raise PolicyViolationError("free-only guard")
        raise LLMError("upstream 502: provider down")


def client_with(settings: Settings, store: OpsStore, behaviour: str) -> TestClient:
    gateway = Gateway(settings, model=Broken(behaviour), on_call=store.record_call)
    return TestClient(create_app(settings, gateway=gateway, store=store))


EMAIL = {
    "from": {"name": "Lena", "email": "lena@fernway-tea.example"},
    "subject": "Quote",
    "text": "We need 1,500 mailer boxes, please send a quote.",
}


def test_health(api: TestClient) -> None:
    body = api.get("/health").json()
    assert body["status"] == "ok" and body["model"] == "fake/rules" and body["free_only"] is True


def test_classify_endpoint_records_the_call(api: TestClient) -> None:
    run = api.post("/ops/runs/start", json={"workflow": "triage", "idempotency_key": "m1"}).json()
    body = api.post("/ai/v1/classify-email", json={**EMAIL, "run_id": run["run_id"]}).json()
    assert body["category"] == "sales_lead" and body["route"] == "crm"
    detail = api.get(f"/ops/runs/{run['run_id']}").json()
    assert detail["calls"][0]["task"] == "classify_email" and detail["calls"][0]["cost_usd"] == 0.0


def test_invalid_model_output_degrades_to_review(settings: Settings, store: OpsStore) -> None:
    client = client_with(settings, store, "garbage")
    body = client.post("/ai/v1/classify-email", json=EMAIL).json()
    assert body["route"] == "review" and body["meta"]["schema_valid"] is False
    faq = client.post("/ai/v1/answer-faq", json={"question": "How much bleed do I need?"}).json()
    assert faq["decision"] == "escalate"
    invoice = client.post("/ai/v1/extract-invoice", json={"text": "Invoice"}).json()
    assert invoice["decision"] == "review" and "invalid_model_output" in invoice["issues"]
    assert any(not c["schema_valid"] for c in store.list_calls())


def test_provider_outage_is_a_503_for_n8n_to_retry(settings: Settings, store: OpsStore) -> None:
    response = client_with(settings, store, "down").post("/ai/v1/classify-email", json=EMAIL)
    assert response.status_code == 503 and response.json()["type"] == "llm_unavailable"
    assert store.list_calls()[0]["error_message"]


def test_guard_refusal_is_a_403(settings: Settings, store: OpsStore) -> None:
    response = client_with(settings, store, "policy").post("/ai/v1/classify-email", json=EMAIL)
    assert response.status_code == 403


def test_token_is_required_when_configured(store: OpsStore) -> None:
    settings = Settings(database_url="sqlite://", llm_ledger=None, api_token="s3cret")
    client = TestClient(create_app(settings, store=store))
    assert client.get("/ops/runs").status_code == 401
    assert client.get("/ops/runs", headers={"X-Relay-Token": "s3cret"}).status_code == 200
    assert client.get("/ops/runs", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert client.get("/health").status_code == 200


def test_ops_flow_over_http(api: TestClient, poster: Poster) -> None:
    run = api.post("/ops/runs/start", json={"workflow": "lead", "idempotency_key": "s1", "input": {"x": 1}}).json()
    item = api.post(
        "/ops/review",
        json={
            "run_id": run["run_id"],
            "kind": "lead_reply",
            "reasons": ["approve"],
            "resume_url": "http://localhost:5678/webhook-waiting/3",
        },
    ).json()
    assert api.get("/ops/review").json()[0]["id"] == item["id"]
    decided = api.post(f"/ops/review/{item['id']}/decision", json={"decision": "approve"}).json()
    assert decided["status"] == "approved" and poster.calls[-1][0].startswith("http://n8n:5678/webhook-waiting/3")
    assert api.post(f"/ops/review/{item['id']}/decision", json={"decision": "approve"}).status_code == 409
    assert api.post("/ops/review/nope/decision", json={"decision": "approve"}).status_code == 404
    assert api.post(f"/ops/review/{item['id']}/decision", json={"decision": "maybe"}).status_code == 422


def test_finish_with_bad_status_is_422(api: TestClient) -> None:
    run = api.post("/ops/runs/start", json={"workflow": "faq", "idempotency_key": "q"}).json()
    assert api.post(f"/ops/runs/{run['run_id']}/finish", json={"status": "nope"}).status_code == 422
    assert api.post("/ops/runs/start", json={"workflow": "unknown", "idempotency_key": "q"}).status_code == 422


def test_reset_can_be_disabled(store: OpsStore) -> None:
    settings = Settings(database_url="sqlite://", llm_ledger=None, allow_reset=False)
    assert TestClient(create_app(settings, store=store)).post("/ops/admin/reset").status_code == 403


def test_mailpit_bridge_forwards_only_inbox_mail(settings: Settings, store: OpsStore, poster: Poster) -> None:
    pages: dict[str, Any] = {
        "/api/v1/message/abc": {
            "MessageID": "<m@x>",
            "From": {"Name": "Lena", "Address": "lena@x.example"},
            "Subject": "Quote",
            "Text": "1,500 boxes",
            "Attachments": [{"PartID": "2", "FileName": "inv.pdf", "ContentType": "application/pdf"}],
        },
    }

    def fetch(url: str) -> httpx.Response:
        path = url.split("8025", 1)[1]
        if path.endswith("/part/2"):
            return httpx.Response(200, content=b"%PDF-1.4")
        return httpx.Response(200, json=pages[path])

    client = TestClient(create_app(settings, store=store, http_get=fetch))
    skipped = client.post("/inbound/mailpit", json={"ID": "abc", "To": [{"Address": "lena@x.example"}]}).json()
    assert skipped["forwarded"] is False
    sent = client.post("/inbound/mailpit", json={"ID": "abc", "To": [{"Address": "hello@kestrelprint.example"}]})
    assert sent.json()["forwarded"] is True
    url, body, headers = poster.calls[-1]
    assert url.endswith("/webhook/inbox-triage") and body["attachments"][0]["content_base64"] == "JVBERi0xLjQ="
    assert body["from"]["email"] == "lena@x.example" and headers["X-Relay-Token"]


@pytest.mark.parametrize("path", ["/ai/v1/enrich-lead", "/ai/v1/draft-reply", "/ai/v1/write-digest"])
def test_other_gateway_endpoints_answer_offline(api: TestClient, path: str) -> None:
    payload: dict[str, Any] = {
        "/ai/v1/enrich-lead": {"name": "Sean Murphy", "website": "tidepool-coffee.example", "quantity": 1500},
        "/ai/v1/draft-reply": {"lead": {"name": "Sean Murphy", "company": "Tidepool"}},
        "/ai/v1/write-digest": {"period": "week 41", "metrics": {"emails": 3}},
    }[path]
    assert api.post(path, json=payload).status_code == 200
