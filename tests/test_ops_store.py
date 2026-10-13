"""Idempotency, the review queue with n8n resume URLs, the dead-letter queue and replay, metrics."""

from __future__ import annotations

import httpx
import pytest

from relay.gateway.schemas import CallMeta
from relay.ops.store import ConflictError, NotFoundError, OpsStore
from tests.conftest import Poster


def test_same_key_twice_is_a_duplicate_with_the_first_result(store: OpsStore) -> None:
    first = store.start_run("triage", "<m1@x>", input={"subject": "hi"})
    store.finish_run(first["run_id"], "auto", route="crm", output={"category": "sales_lead"})
    second = store.start_run("triage", "<m1@x>")
    assert second["duplicate"] and second["run_id"] == first["run_id"]
    assert second["route"] == "crm" and second["output"]["category"] == "sales_lead"
    assert store.get_run(first["run_id"])["duplicates"] == 1


def test_keys_are_per_workflow(store: OpsStore) -> None:
    a = store.start_run("triage", "k")
    b = store.start_run("faq", "k")
    assert a["run_id"] != b["run_id"] and not b["duplicate"]


def test_a_failed_run_can_be_retried_as_a_new_attempt(store: OpsStore) -> None:
    run = store.start_run("invoice", "pdf:abc", execution_id="7", input={"filename": "a.pdf"})
    store.dead_letter("invoice", execution_id="7", node="Post bill", error="503")
    again = store.start_run("invoice", "pdf:abc", execution_id="9")
    assert not again["duplicate"] and again["run_id"] == run["run_id"] and again["attempt"] == 2


def test_finish_rejects_unknown_status(store: OpsStore) -> None:
    run = store.start_run("faq", "q1")
    with pytest.raises(ValueError):
        store.finish_run(run["run_id"], "done")
    with pytest.raises(NotFoundError):
        store.finish_run("nope", "auto")


def test_review_resumes_the_waiting_execution_on_the_internal_url(store: OpsStore, poster: Poster) -> None:
    run = store.start_run("triage", "m2")
    item = store.create_review(run["run_id"], "triage", title="Unsure", reasons=["confidence 0.5 below 0.75"],
                               resume_url="http://localhost:5678/webhook-waiting/42?signature=abc")
    assert store.get_run(run["run_id"])["status"] == "review"
    result = store.decide(item["id"], "approve", edits={"category": "support"}, reviewer="ivan")
    url, body, _ = poster.calls[-1]
    assert url == "http://n8n:5678/webhook-waiting/42?signature=abc"
    assert body["decision"] == "approve" and body["edits"] == {"category": "support"}
    assert result["status"] == "approved" and result["resume"]["delivered"]
    with pytest.raises(ConflictError):
        store.decide(item["id"], "reject")


def test_retried_execution_refreshes_its_pending_review(store: OpsStore) -> None:
    run = store.start_run("lead", "s1")
    first = store.create_review(run["run_id"], "lead_reply", resume_url="http://n8n/webhook-waiting/1")
    second = store.create_review(run["run_id"], "lead_reply", resume_url="http://n8n/webhook-waiting/2")
    assert first["id"] == second["id"] and len(store.list_reviews()) == 1


def test_failed_resume_marks_the_item_failed() -> None:
    from relay.ops.db import make_sessionmaker

    failing = OpsStore(make_sessionmaker("sqlite://"), poster=Poster(status=404))
    run = failing.start_run("triage", "m3")
    item = failing.create_review(run["run_id"], "triage", resume_url="http://n8n/webhook-waiting/5")
    assert failing.decide(item["id"], "approve")["status"] == "failed"


def test_review_without_resume_url_closes_the_run(store: OpsStore) -> None:
    run = store.start_run("triage", "m4")
    item = store.create_review(run["run_id"], "triage")
    store.decide(item["id"], "reject")
    assert store.get_run(run["run_id"])["status"] == "rejected"


def test_dead_letter_finds_the_run_by_execution_id_and_replays_its_input(store: OpsStore, poster: Poster) -> None:
    run = store.start_run("faq", "q9", execution_id="31", input={"question": "Hi?", "message_id": "q9"})
    dead = store.dead_letter("Relay - 04 Support FAQ auto-reply", execution_id="31", node="Escalate", error="boom")
    assert dead["workflow"] == "faq" and dead["run_id"] == run["run_id"] and dead["has_payload"]
    assert store.get_run(run["run_id"])["status"] == "error"
    result = store.replay(dead["id"])
    url, body, headers = poster.calls[-1]
    assert url == "http://n8n:5678/webhook/support-faq" and body["message_id"] == "q9"
    assert headers["X-Relay-Token"] == "hook-secret"
    assert result["ok"] and result["dead_letter"]["status"] == "replayed"


def test_dead_letter_without_a_run_cannot_be_replayed(store: OpsStore) -> None:
    dead = store.dead_letter("triage", execution_id="999", node="x", error="y")
    with pytest.raises(ConflictError):
        store.replay(dead["id"])
    assert store.resolve_dead_letter(dead["id"])["status"] == "resolved"


def test_replay_network_failure_is_reported(store: OpsStore) -> None:
    def broken(url: str, body: dict[str, object], headers: dict[str, str]) -> httpx.Response:
        raise httpx.ConnectError("refused")

    store.post = broken
    store.start_run("lead", "s2", execution_id="5", input={"name": "x"})
    dead = store.dead_letter("lead", execution_id="5", node="n", error="e")
    result = store.replay(dead["id"])
    assert not result["ok"] and "refused" in result["error"]


def test_metrics_per_workflow(store: OpsStore) -> None:
    for index, status in enumerate(["auto", "auto", "review", "error"]):
        run = store.start_run("triage", f"m{index}")
        if status == "review":
            store.create_review(run["run_id"], "triage")
        elif status == "error":
            store.dead_letter("triage", run_id=run["run_id"], node="n", error="e")
        else:
            store.finish_run(run["run_id"], status, route="crm")
        store.record_call(CallMeta(call_id=f"c{index}", task="classify_email", model="fake/rules",
                                   input_tokens=10, output_tokens=5, latency_ms=100), run["run_id"], "crm", {})
    store.start_run("triage", "m0")  # a duplicate delivery
    metrics = store.metrics()
    triage = metrics["workflows"]["triage"]
    assert (triage["runs"], triage["auto"], triage["review"], triage["errors"], triage["duplicates"]) == (4, 2, 1, 1, 1)
    assert triage["auto_rate"] == 0.5 and triage["llm_calls"] == 4 and triage["llm_tokens"] == 60
    assert metrics["totals"]["pending_review"] == 1 and metrics["totals"]["open_dead_letters"] == 1
    assert sum(day["runs"] for day in metrics["days"].values()) == 4


def test_calls_without_a_model_are_not_recorded(store: OpsStore) -> None:
    store.record_call(CallMeta(task="answer_faq"), None, "escalate", {})
    assert store.list_calls() == []


def test_reset_empties_everything(store: OpsStore) -> None:
    run = store.start_run("triage", "r")
    store.create_review(run["run_id"], "triage")
    store.reset()
    assert store.list_runs() == [] and store.list_reviews(status=None) == []
