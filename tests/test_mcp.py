"""The MCP server through the protocol (in-process client): tools and annotations, workflow runs via the n8n
webhook, the review queue, the gateway tools, errors as tool errors; and the stdio transport."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient
from mcp import Client, StdioServerParameters

from relay.config import Settings
from relay.mcp_server import RelayTools, build_server


def run[T](tools: RelayTools, fn: Callable[[Client], Awaitable[T]]) -> T:
    async def main() -> T:
        async with Client(build_server(tools)) as client:
            return await fn(client)

    return asyncio.run(main())


def call(tools: RelayTools, name: str, arguments: dict[str, Any]) -> tuple[bool, Any]:
    async def go(client: Client) -> tuple[bool, Any]:
        result = await client.call_tool(name, arguments)
        content = result.structured_content
        if content is None:
            content = " ".join(getattr(c, "text", "") for c in result.content)
        return bool(result.is_error), content

    return run(tools, go)


def n8n_stub(seen: list[httpx.Request]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/support-faq"):
            return httpx.Response(200, json={"status": "auto_reply", "answer": "Add 3 mm bleed.",
                                             "attachments": [{"content_base64": "QUJD"}]})
        return httpx.Response(500, json={"message": "Error in workflow"})

    return httpx.Client(base_url="http://n8n", transport=httpx.MockTransport(handler))


def make_tools(api: TestClient, seen: list[httpx.Request] | None = None) -> RelayTools:
    return RelayTools(Settings(llm_ledger=None, n8n_webhook_token="hook-secret"), api=api,
                      n8n=n8n_stub(seen if seen is not None else []))


def test_tools_are_listed_with_annotations(api: TestClient) -> None:
    async def go(client: Client) -> Any:
        return (await client.list_tools()).tools

    tools = {t.name: t for t in run(make_tools(api), go)}
    assert set(tools) == {"list_workflows", "run_workflow", "list_runs", "get_run", "list_review_queue",
                          "approve_item", "reject_item", "list_dead_letters", "replay_dead_letter", "get_metrics",
                          "classify_email", "extract_invoice", "answer_faq"}
    for name in ("list_runs", "get_run", "list_review_queue", "get_metrics", "list_workflows"):
        assert tools[name].annotations and tools[name].annotations.read_only_hint is True
    assert tools["approve_item"].annotations and tools["approve_item"].annotations.destructive_hint is True
    assert all(t.description for t in tools.values())


def test_run_workflow_posts_to_the_webhook_with_the_secret(api: TestClient) -> None:
    seen: list[httpx.Request] = []
    error, body = call(make_tools(api, seen), "run_workflow",
                       {"workflow": "faq", "payload": {"question": "Bleed?", "message_id": "m1"}})
    assert not error and body["status"] == "auto_reply"
    assert body["attachments"][0]["content_base64"] == "<base64 omitted>"
    assert seen[0].headers["X-Relay-Token"] == "hook-secret"
    assert json.loads(seen[0].content)["source"] == "mcp"


def test_a_failed_workflow_is_reported_not_raised(api: TestClient) -> None:
    error, body = call(make_tools(api), "run_workflow", {"workflow": "triage", "payload": {}})
    assert not error and body["status"] == "failed" and "dead-letter" in body["hint"]


def test_review_queue_and_approval(api: TestClient) -> None:
    run_id = api.post("/ops/runs/start", json={"workflow": "triage", "idempotency_key": "m"}).json()["run_id"]
    item = api.post("/ops/review", json={"run_id": run_id, "kind": "triage", "reasons": ["unsure"]}).json()
    tools = make_tools(api)
    _, queue = call(tools, "list_review_queue", {})
    assert queue["pending"] == 1 and queue["items"][0]["id"] == item["id"] and "untrusted" in queue["note"]
    error, decided = call(tools, "approve_item", {"review_id": item["id"], "edits": {"category": "support"}})
    assert not error and decided["status"] == "approved"
    error, message = call(tools, "approve_item", {"review_id": item["id"]})
    assert error and "already approved" in message


def test_gateway_tools_have_no_side_effects(api: TestClient) -> None:
    tools = make_tools(api)
    _, triage = call(tools, "classify_email", {"subject": "Quote", "text": "We need 1,500 mailer boxes.",
                                               "from_email": "lena@fernway-tea.example"})
    assert triage["category"] == "sales_lead"
    _, faq = call(tools, "answer_faq", {"question": "Where is my order KP-48213?"})
    assert faq["decision"] == "escalate"
    _, runs = call(tools, "list_runs", {})
    assert runs["runs"] == []


def test_unknown_ids_are_tool_errors(api: TestClient) -> None:
    error, message = call(make_tools(api), "get_run", {"run_id": "0123456789ab"})
    assert error and "not found" in message
    error, _ = call(make_tools(api), "get_run", {"run_id": "../etc"})
    assert error


def test_unreachable_api_gives_a_helpful_error() -> None:
    tools = RelayTools(Settings(llm_ledger=None), api=httpx.Client(base_url="http://127.0.0.1:1"),
                       n8n=httpx.Client(base_url="http://127.0.0.1:1"))
    error, message = call(tools, "get_metrics", {})
    assert error and "docker compose up" in message


def test_stdio_transport_lists_tools() -> None:
    relay = Path(sys.executable).with_name("relay")

    async def main() -> list[str]:
        params = StdioServerParameters(command=str(relay), args=["mcp"])
        async with Client(params) as client:
            return [t.name for t in (await client.list_tools()).tools]

    assert "run_workflow" in asyncio.run(main())
