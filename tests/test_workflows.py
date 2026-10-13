"""The n8n workflow JSON: committed files equal the builder's output, and every workflow passes the validator
against the node catalog of the pinned n8n version. The validator itself is tested on broken workflows."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest

from relay.workflows.builder import ALL, ERROR_WORKFLOW_ID, build_all, render
from relay.workflows.validate import CREDENTIALS, load_catalog, validate_workflow, webhook_paths

DIR = Path("n8n/workflows")
CATALOG = load_catalog()
BUILT = build_all()


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((DIR / name).read_text(encoding="utf-8"))
    return data


@pytest.mark.parametrize("name", list(ALL))
def test_committed_json_is_the_builders_output(name: str) -> None:
    assert (DIR / name).read_text(encoding="utf-8") == render(BUILT[name]), "run `relay workflows build`"


@pytest.mark.parametrize("name", list(ALL))
def test_every_workflow_passes_the_validator(name: str) -> None:
    assert validate_workflow(load(name), CATALOG) == []


def test_catalog_is_from_the_pinned_n8n_version() -> None:
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    assert f"n8nio/n8n:{CATALOG['n8n_version']}" in compose
    assert CATALOG["node_types"] > 800


def test_webhook_paths_are_unique_and_match_the_ops_store() -> None:
    from relay.ops.store import WEBHOOK_PATHS

    paths = webhook_paths(list(BUILT.values()))
    assert len(paths) == len(set(paths))
    assert set(WEBHOOK_PATHS.values()) <= set(paths)


@pytest.mark.parametrize("name", [n for n in ALL if n not in {"00-error-handler.json", "05-kpi-digest.json"}])
def test_idempotency_is_the_first_step_after_the_trigger(name: str) -> None:
    workflow = BUILT[name]
    trigger = next(n for n in workflow["nodes"] if n["type"] == "n8n-nodes-base.webhook")
    first = workflow["connections"][trigger["name"]]["main"][0][0]["node"]
    if first == "Fingerprint PDF":
        first = workflow["connections"][first]["main"][0][0]["node"]
    assert first == "Start run (idempotency)"


def test_all_ids_and_versions_are_stable() -> None:
    again = build_all()
    assert again == BUILT


def test_import_script_creates_every_credential_and_publishes_all() -> None:
    script = Path("n8n/bootstrap/import.sh").read_text(encoding="utf-8")
    for cred_id, cred_type in CREDENTIALS.items():
        assert f'"id": "{cred_id}"' in script and f'"type": "{cred_type}"' in script
    assert "n8n publish:workflow" in script and ERROR_WORKFLOW_ID not in script.split("publish:workflow")[0][-200:]
    assert not re.search(r'"value": "[^$"]', script), "credential values must come from the environment"


def test_human_review_uses_wait_nodes_resumed_by_webhook() -> None:
    for name in ("01-inbox-triage.json", "02-invoice-intake.json", "03-lead-enrichment.json"):
        nodes = BUILT[name]["nodes"]
        assert any(n["type"] == "n8n-nodes-base.wait" and n["parameters"]["resume"] == "webhook" for n in nodes)
        queue = [n for n in nodes if n["name"].startswith("Queue")]
        assert queue and all("$execution.resumeUrl" in n["parameters"]["jsonBody"] for n in queue)


def test_reply_is_only_sent_after_approval() -> None:
    workflow = BUILT["03-lead-enrichment.json"]
    sources = [
        s
        for s, out in workflow["connections"].items()
        for branch in out["main"]
        for link in branch
        if link["node"] == "Send reply"
    ]
    assert sources == ["Approved with a reply?"]


# ---- the validator catches broken workflows -----------------------------------------------------------------


def broken(mutate: Any) -> list[str]:
    workflow = copy.deepcopy(BUILT["04-support-faq.json"])
    mutate(workflow)
    return validate_workflow(workflow, CATALOG)


def node(workflow: dict[str, Any], name: str) -> dict[str, Any]:
    return next(n for n in workflow["nodes"] if n["name"] == name)


def test_unknown_node_type() -> None:
    assert any(
        "unknown node type" in p for p in broken(lambda w: node(w, "Respond").update(type="n8n-nodes-base.nope"))
    )


def test_unsupported_version() -> None:
    assert any("no version" in p for p in broken(lambda w: node(w, "Respond").update(typeVersion=9)))


def test_unknown_parameter() -> None:
    assert any("unknown parameter" in p for p in broken(lambda w: node(w, "Respond")["parameters"].update(bogus=1)))


def test_dangling_connection() -> None:
    problems = broken(
        lambda w: w["connections"]["Finish run"]["main"][0].append({"node": "Ghost", "type": "main", "index": 0})
    )
    assert any("unknown node 'Ghost'" in p for p in problems)


def test_unreachable_node() -> None:
    problems = broken(lambda w: w["connections"].pop("Finish run"))
    assert any("Respond: not reachable" in p or "Respond: no incoming" in p for p in problems)


def test_reference_to_a_missing_node() -> None:
    def mutate(w: dict[str, Any]) -> None:
        node(w, "Respond")["parameters"]["responseBody"] = "={{ $('Nowhere').first().json }}"

    assert any("unknown node 'Nowhere'" in p for p in broken(mutate))


def test_missing_error_workflow() -> None:
    assert any("errorWorkflow" in p for p in broken(lambda w: w["settings"].pop("errorWorkflow")))


def test_write_without_idempotency_key_and_without_retry() -> None:
    def mutate(w: dict[str, Any]) -> None:
        escalate = node(w, "Escalate to helpdesk")
        escalate["parameters"].pop("headerParameters")
        escalate["parameters"].pop("sendHeaders")
        escalate.pop("retryOnFail")

    problems = broken(mutate)
    assert any("Idempotency-Key" in p for p in problems) and any("retry" in p for p in problems)


def test_hard_coded_host() -> None:
    def mutate(w: dict[str, Any]) -> None:
        node(w, "Escalate to helpdesk")["parameters"]["url"] = "=http://10.0.0.5:8200/helpdesk/tickets"

    assert any("hard-coded host" in p for p in broken(mutate))


def test_unauthenticated_webhook_and_unknown_credential() -> None:
    def mutate(w: dict[str, Any]) -> None:
        trigger = node(w, "Customer question")
        trigger["parameters"]["authentication"] = "none"
        trigger["credentials"] = {"httpHeaderAuth": {"id": "someoneElse", "name": "x"}}

    problems = broken(mutate)
    assert any("header authentication" in p for p in problems) and any("unknown credential" in p for p in problems)


def test_if_with_three_outputs() -> None:
    def mutate(w: dict[str, Any]) -> None:
        w["connections"]["Safe to auto-reply?"]["main"].append([{"node": "Respond", "type": "main", "index": 0}])

    assert any("outputs" in p for p in broken(mutate))
