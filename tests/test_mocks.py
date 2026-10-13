"""The mock systems: Idempotency-Key, duplicate bills, injected failures, fixture sites."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_idempotency_key_returns_the_first_record(mocks: TestClient) -> None:
    first = mocks.post("/crm/leads", json={"name": "Lena", "email": "l@x.example"}, headers={"Idempotency-Key": "r1"})
    again = mocks.post("/crm/leads", json={"name": "Lena", "email": "l@x.example"}, headers={"Idempotency-Key": "r1"})
    assert first.status_code == 201 and again.status_code == 200 and first.json()["id"] == again.json()["id"]
    assert len(mocks.get("/crm/leads").json()) == 1


def test_duplicate_bill_is_a_409(mocks: TestClient) -> None:
    bill = {"vendor": "Inkwell Supplies GmbH", "total": 577.86, "currency": "EUR", "dedupe_key": "abc"}
    assert mocks.post("/accounting/bills", json=bill).status_code == 201
    clash = mocks.post("/accounting/bills", json=bill)
    assert clash.status_code == 409 and clash.json()["error"] == "duplicate"
    assert len(mocks.get("/accounting/bills?dedupe_key=abc").json()) == 1
    assert mocks.get("/accounting/bills?dedupe_key=zzz").json() == []


def test_validation(mocks: TestClient) -> None:
    assert mocks.post("/accounting/bills", json={"vendor": "x"}).status_code == 422
    assert mocks.post("/helpdesk/tickets", json={}).status_code == 422
    assert mocks.post("/chat/messages", json={"channel": "#x"}).status_code == 422
    assert (
        mocks.post("/crm/leads", content=b"not json", headers={"content-type": "application/json"}).status_code == 400
    )


def test_fail_first_then_succeed(mocks: TestClient) -> None:
    mocks.post("/admin/chaos", json={"helpdesk": {"fail_first": 1}})
    headers = {"Idempotency-Key": "t1"}
    assert mocks.post("/helpdesk/tickets", json={"subject": "a"}, headers=headers).status_code == 503
    assert mocks.post("/helpdesk/tickets", json={"subject": "a"}, headers=headers).status_code == 201
    assert mocks.get("/admin/state").json()["injected_failures"] == 1


def test_outage_and_reset(mocks: TestClient) -> None:
    mocks.post("/admin/chaos", json={"chat": {"down": True}})
    assert mocks.post("/chat/messages", json={"channel": "#ops", "text": "x"}).status_code == 503
    mocks.post("/admin/reset")
    assert mocks.post("/chat/messages", json={"channel": "#ops", "text": "x"}).status_code == 201
    assert mocks.post("/admin/chaos", json={"bank": {"down": True}}).status_code == 422


def test_numbers_are_sequential_per_system(mocks: TestClient) -> None:
    a = mocks.post("/helpdesk/tickets", json={"subject": "a"}).json()
    b = mocks.post("/helpdesk/tickets", json={"subject": "b"}).json()
    assert (a["number"], b["number"]) == ("T-1001", "T-1002")


def test_fixture_sites(mocks: TestClient) -> None:
    assert "Tidepool" in mocks.get("/sites/tidepool-coffee.example/index.html").text
    assert mocks.get("/sites/tidepool-coffee.example").status_code == 200
    assert mocks.get("/sites/tidepool-coffee.example/secret.html").status_code == 404
    assert mocks.get("/sites/google.com/index.html").status_code == 404
    assert mocks.get("/sites/quantum-brands.example/index.html").status_code == 404
