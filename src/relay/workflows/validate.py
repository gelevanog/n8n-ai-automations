"""Static validation of n8n workflow JSON against the node catalog of the pinned n8n version.

n8n/node-catalog.json is generated from `n8n export:nodes` of n8nio/n8n:2.42.7 (every node type with its versions
and credential types, plus parameter names for the types the workflows use). The checks catch what usually breaks
an import or a run: unknown node types or versions, unknown parameters, dangling connections, outputs an IF or
Switch does not have, unreachable nodes, `$('Node')` references to nodes that do not exist, unknown credentials,
webhooks without authentication or without a reachable Respond node, missing retry policy or idempotency keys on
writes, hard-coded hosts, and a missing error workflow."""

from __future__ import annotations

import json
import re
from collections import deque
from functools import lru_cache
from pathlib import Path
from typing import Any

CATALOG = Path("n8n/node-catalog.json")
TRIGGERS = frozenset(
    {
        "n8n-nodes-base.webhook",
        "n8n-nodes-base.scheduleTrigger",
        "n8n-nodes-base.errorTrigger",
        "n8n-nodes-base.manualTrigger",
        "n8n-nodes-base.executeWorkflowTrigger",
    }
)
STICKY = "n8n-nodes-base.stickyNote"
CREDENTIALS = {
    "relayWebhookAuth": "httpHeaderAuth",
    "relayApiToken": "httpHeaderAuth",
    "mailpitSmtp": "smtp",
    "relayPostgres": "postgres",
}
"""The credentials n8n/bootstrap/import.sh creates (id -> type)."""
ERROR_WORKFLOW_ID = "RelayErrorHandler"
NODE_REF = re.compile(r"\$\(\s*'([^']+)'\s*\)")


@lru_cache(maxsize=2)
def load_catalog(path: Path = CATALOG) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _outputs(node: dict[str, Any], catalog: dict[str, Any]) -> int:
    kind = node["type"]
    if kind == "n8n-nodes-base.if":
        return 2
    if kind == "n8n-nodes-base.switch":
        return len(((node.get("parameters") or {}).get("rules") or {}).get("values") or [])
    if kind == STICKY:
        return 0
    return 1


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def validate_workflow(workflow: dict[str, Any], catalog: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for key in ("id", "name", "nodes", "connections", "settings"):
        if key not in workflow:
            problems.append(f"missing top-level key {key!r}")
    if problems:
        return problems
    nodes: list[dict[str, Any]] = workflow["nodes"]
    by_name = {n["name"]: n for n in nodes}
    if len(by_name) != len(nodes):
        problems.append("duplicate node names")
    if len({n["id"] for n in nodes}) != len(nodes):
        problems.append("duplicate node ids")
    known = catalog["nodes"]

    for node in nodes:
        name, kind = node["name"], node["type"]
        entry = known.get(kind)
        if entry is None:
            problems.append(f"{name}: unknown node type {kind}")
            continue
        if node.get("typeVersion") not in entry["versions"]:
            problems.append(f"{name}: {kind} has no version {node.get('typeVersion')} (has {entry['versions']})")
        allowed = set((entry.get("parameters") or {}).get(str(node.get("typeVersion")), []))
        if allowed:
            unknown = set(node.get("parameters") or {}) - allowed
            if unknown:
                problems.append(f"{name}: unknown parameter(s) {sorted(unknown)}")
        position = node.get("position")
        if not (
            isinstance(position, list) and len(position) == 2 and all(isinstance(p, int | float) for p in position)
        ):
            problems.append(f"{name}: position must be [x, y]")
        for cred_type, ref in (node.get("credentials") or {}).items():
            expected = CREDENTIALS.get(ref.get("id"))
            if expected is None:
                problems.append(f"{name}: unknown credential id {ref.get('id')!r}")
            elif expected != cred_type:
                problems.append(f"{name}: credential {ref.get('id')} is {expected}, used as {cred_type}")
            generic = kind == "n8n-nodes-base.httpRequest" or kind == "n8n-nodes-base.webhook"
            if not generic and cred_type not in entry["credentials"]:
                problems.append(f"{name}: {kind} does not accept {cred_type} credentials")
        params = node.get("parameters") or {}
        for text in _strings(params):
            if text.count("{{") != text.count("}}"):
                problems.append(f"{name}: unbalanced {{{{ }}}} in an expression")
            for ref in NODE_REF.findall(text):
                if ref not in by_name:
                    problems.append(f"{name}: expression references unknown node {ref!r}")
        if kind == "n8n-nodes-base.webhook":
            if not node.get("webhookId"):
                problems.append(f"{name}: webhook without webhookId")
            if params.get("authentication") != "headerAuth":
                problems.append(f"{name}: webhook without header authentication")
        if kind == "n8n-nodes-base.wait" and params.get("resume") == "webhook" and not node.get("webhookId"):
            problems.append(f"{name}: wait-for-webhook without webhookId")
        if kind == "n8n-nodes-base.httpRequest":
            url = str(params.get("url", ""))
            if re.search(r"https?://(?!\{\{)", url):
                problems.append(f"{name}: hard-coded host in URL {url[:60]!r} (use $env)")
            if not node.get("retryOnFail"):
                problems.append(f"{name}: HTTP request without retry on fail")
            writes = params.get("method", "GET") == "POST" and "MOCKS_URL" in url
            headers = [h.get("name") for h in ((params.get("headerParameters") or {}).get("parameters") or [])]
            if writes and "Idempotency-Key" not in headers:
                problems.append(f"{name}: write to a mock system without an Idempotency-Key header")

    # Connections
    incoming: dict[str, int] = {n["name"]: 0 for n in nodes}
    graph: dict[str, list[str]] = {n["name"]: [] for n in nodes}
    for source, outputs in workflow["connections"].items():
        if source not in by_name:
            problems.append(f"connection from unknown node {source!r}")
            continue
        main = outputs.get("main") or []
        if len(main) > _outputs(by_name[source], catalog):
            problems.append(f"{source}: connects {len(main)} outputs but has {_outputs(by_name[source], catalog)}")
        for branch in main:
            for link in branch or []:
                target = link.get("node")
                if target not in by_name:
                    problems.append(f"{source}: connects to unknown node {target!r}")
                    continue
                incoming[target] += 1
                graph[source].append(target)
    triggers = [n["name"] for n in nodes if n["type"] in TRIGGERS]
    if not triggers:
        problems.append("no trigger node")
    reachable: set[str] = set()
    queue = deque(triggers)
    while queue:
        current = queue.popleft()
        if current in reachable:
            continue
        reachable.add(current)
        queue.extend(graph[current])
    for node in nodes:
        if node["type"] == STICKY:
            continue
        if node["name"] not in reachable:
            problems.append(f"{node['name']}: not reachable from a trigger")
        if node["type"] not in TRIGGERS and incoming[node["name"]] == 0:
            problems.append(f"{node['name']}: no incoming connection")

    for node in nodes:
        if (
            node["type"] == "n8n-nodes-base.webhook"
            and (node.get("parameters") or {}).get("responseMode") == "responseNode"
        ):
            seen: set[str] = set()
            queue = deque([node["name"]])
            found = False
            while queue:
                current = queue.popleft()
                if current in seen:
                    continue
                seen.add(current)
                if by_name[current]["type"] == "n8n-nodes-base.respondToWebhook":
                    found = True
                    break
                queue.extend(graph[current])
            if not found:
                problems.append(f"{node['name']}: responseMode responseNode but no Respond to Webhook node follows")

    if workflow["id"] != ERROR_WORKFLOW_ID and workflow["settings"].get("errorWorkflow") != ERROR_WORKFLOW_ID:
        problems.append(f"settings.errorWorkflow is not {ERROR_WORKFLOW_ID}")
    if workflow["settings"].get("executionOrder") != "v1":
        problems.append("settings.executionOrder should be v1")
    return problems


def webhook_paths(workflows: list[dict[str, Any]]) -> list[str]:
    return [
        str((n.get("parameters") or {}).get("path"))
        for wf in workflows
        for n in wf["nodes"]
        if n["type"] == "n8n-nodes-base.webhook"
    ]
