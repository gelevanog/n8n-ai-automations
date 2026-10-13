"""Generates the n8n workflow JSON in n8n/workflows/ (the files you import into n8n).

The workflows are written as Python so the six of them stay consistent (the same idempotency step, retry policy,
review pattern and error workflow everywhere) and so a test can check the committed JSON is exactly what this file
produces. The JSON is plain n8n export format: import it with `n8n import:workflow`, or paste a file into the editor.

Shared patterns:
  * every webhook needs the X-Relay-Token header (n8n Header Auth credential "Relay webhook secret");
  * step 1 is "Start run" (idempotency): the same message / file / form twice returns the first result;
  * AI steps are HTTP Request nodes to the Relay AI gateway (portable to Make or Zapier as plain HTTP modules);
  * every call to the gateway and the mock systems retries 3 times with a wait (retry on fail);
  * writes to the mock systems carry an Idempotency-Key (the run id), so a retry never creates a second record;
  * human review = "Queue for review" (the waiting execution's resume URL) + a Wait node resumed by the dashboard;
  * any unhandled failure runs "Relay - Error handler": dead-letter queue entry + #ops-alerts message.
"""

from __future__ import annotations

import itertools
import json
import uuid
from pathlib import Path
from typing import Any

WORKFLOW_DIR = Path("n8n/workflows")
NS = uuid.UUID("6f0f4d9e-3c47-4b1a-9a61-7b0f4f1d2a10")
ERROR_WORKFLOW_ID = "RelayErrorHandler"
TAG_IDS = {
    "relay": "relayTagRelay001",
    "ai": "relayTagAi000001",
    "ops": "relayTagOps00001",
    "email": "relayTagEmail001",
    "finance": "relayTagFinance1",
    "sales": "relayTagSales001",
    "support": "relayTagSupport1",
    "reporting": "relayTagReport01",
}

RELAY_API = {"httpHeaderAuth": {"id": "relayApiToken", "name": "Relay API token"}}
WEBHOOK_SECRET = {"httpHeaderAuth": {"id": "relayWebhookAuth", "name": "Relay webhook secret"}}
SMTP = {"smtp": {"id": "mailpitSmtp", "name": "Mailpit SMTP"}}
POSTGRES = {"postgres": {"id": "relayPostgres", "name": "Relay Postgres"}}

API = "{{ $env.RELAY_API_URL }}"
MOCKS = "{{ $env.MOCKS_URL }}"
N8N_INTERNAL = "{{ $env.N8N_INTERNAL_URL }}"
SALES_FROM = "sales@kestrelprint.example"
SUPPORT_FROM = "support@kestrelprint.example"


def _id(*parts: str) -> str:
    return str(uuid.uuid5(NS, "/".join(parts)))


class Workflow:
    def __init__(self, wid: str, name: str, tags: list[str], description: str) -> None:
        self.id = wid
        self.name = name
        self.tags = tags
        self.description = description
        self.nodes: list[dict[str, Any]] = []
        self.connections: dict[str, dict[str, list[list[dict[str, Any]]]]] = {}

    # ---------------------------------------------------------------------------------------- primitives

    def node(
        self, name: str, kind: str, version: float, position: tuple[int, int], parameters: dict[str, Any], **extra: Any
    ) -> str:
        if any(n["name"] == name for n in self.nodes):
            raise ValueError(f"duplicate node name {name!r} in {self.name}")
        node: dict[str, Any] = {
            "id": _id(self.id, name),
            "name": name,
            "type": kind if "." in kind else f"n8n-nodes-base.{kind}",
            "typeVersion": version,
            "position": list(position),
            "parameters": parameters,
        }
        node.update(extra)
        self.nodes.append(node)
        return name

    def connect(self, source: str, target: str, output: int = 0) -> None:
        outputs = self.connections.setdefault(source, {}).setdefault("main", [])
        while len(outputs) <= output:
            outputs.append([])
        outputs[output].append({"node": target, "type": "main", "index": 0})

    def chain(self, *names: str) -> None:
        for source, target in itertools.pairwise(names):
            self.connect(source, target)

    def note(
        self, name: str, position: tuple[int, int], content: str, width: int = 360, height: int = 200, color: int = 7
    ) -> None:
        self.node(
            name, "stickyNote", 1, position, {"content": content, "height": height, "width": width, "color": color}
        )

    # ---------------------------------------------------------------------------------------- node helpers

    def webhook(self, name: str, position: tuple[int, int], path: str, *, response: str = "responseNode") -> str:
        return self.node(
            name,
            "webhook",
            2,
            position,
            {
                "httpMethod": "POST",
                "path": path,
                "authentication": "headerAuth",
                "responseMode": response,
                "options": {},
            },
            webhookId=_id(self.id, name, "webhook"),
            credentials=WEBHOOK_SECRET,
        )

    def http(
        self,
        name: str,
        position: tuple[int, int],
        *,
        method: str = "POST",
        url: str,
        body: str | None = None,
        auth: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        retry: bool = True,
        full_response: bool = False,
        never_error: bool = False,
        timeout_ms: int = 120_000,
        notes: str | None = None,
    ) -> str:
        params: dict[str, Any] = {"method": method, "url": f"={url}"}
        if auth is not None:
            params["authentication"] = "genericCredentialType"
            params["genericAuthType"] = "httpHeaderAuth"
        if headers:
            params["sendHeaders"] = True
            params["headerParameters"] = {
                "parameters": [{"name": key, "value": value} for key, value in headers.items()]
            }
        if body is not None:
            params["sendBody"] = True
            params["specifyBody"] = "json"
            params["jsonBody"] = f"={{{{ JSON.stringify({body}) }}}}"
        options: dict[str, Any] = {"timeout": timeout_ms}
        if full_response or never_error:
            options["response"] = {"response": {"fullResponse": full_response, "neverError": never_error}}
        params["options"] = options
        extra: dict[str, Any] = {}
        if auth is not None:
            extra["credentials"] = auth
        if retry:
            extra.update(retryOnFail=True, maxTries=3, waitBetweenTries=2000)
        if notes:
            extra.update(notes=notes, notesInFlow=True)
        return self.node(name, "httpRequest", 4.2, position, params, **extra)

    def api(
        self,
        name: str,
        position: tuple[int, int],
        path: str,
        body: str,
        *,
        method: str = "POST",
        notes: str | None = None,
        retry: bool = True,
    ) -> str:
        return self.http(
            name, position, method=method, url=f"{API}{path}", body=body, auth=RELAY_API, notes=notes, retry=retry
        )

    def condition(self, left: str, operation: str, right: Any = None, kind: str = "string") -> dict[str, Any]:
        operator: dict[str, Any] = {"type": kind, "operation": operation}
        if operation in {"true", "false", "exists", "notExists", "empty", "notEmpty"}:
            operator["singleValue"] = True
        cond: dict[str, Any] = {
            "id": _id(self.id, left, operation, str(right)),
            "leftValue": left,
            "operator": operator,
        }
        cond["rightValue"] = "" if right is None else right
        return cond

    def if_(self, name: str, position: tuple[int, int], *conditions: dict[str, Any], combinator: str = "and") -> str:
        return self.node(
            name,
            "if",
            2.2,
            position,
            {
                "conditions": {
                    "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
                    "conditions": list(conditions),
                    "combinator": combinator,
                },
                "looseTypeValidation": True,
                "options": {},
            },
        )

    def switch(self, name: str, position: tuple[int, int], value: str, cases: list[str]) -> str:
        rules = []
        for case in cases:
            rules.append(
                {
                    "conditions": {
                        "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
                        "conditions": [self.condition(value, "equals", case)],
                        "combinator": "and",
                    },
                    "renameOutput": True,
                    "outputKey": case,
                }
            )
        return self.node(name, "switch", 3.2, position, {"rules": {"values": rules}, "options": {}})

    def respond(self, name: str, position: tuple[int, int], body: str, code: int = 200) -> str:
        options: dict[str, Any] = {} if code == 200 else {"responseCode": code}
        return self.node(
            name,
            "respondToWebhook",
            1.1,
            position,
            {
                "respondWith": "json",
                "responseBody": f"={{{{ JSON.stringify({body}) }}}}",
                "options": options,
            },
        )

    def wait(self, name: str, position: tuple[int, int]) -> str:
        return self.node(
            name,
            "wait",
            1.1,
            position,
            {"resume": "webhook", "httpMethod": "POST", "options": {}},
            webhookId=_id(self.id, name, "wait"),
        )

    def set_json(self, name: str, position: tuple[int, int], expression: str) -> str:
        return self.node(
            name,
            "set",
            3.4,
            position,
            {
                "mode": "raw",
                "jsonOutput": f"={{{{ JSON.stringify({expression}) }}}}",
                "options": {},
            },
        )

    def email(self, name: str, position: tuple[int, int], *, from_email: str, to: str, subject: str, text: str) -> str:
        return self.node(
            name,
            "emailSend",
            2.1,
            position,
            {
                "fromEmail": from_email,
                "toEmail": f"={to}",
                "subject": f"={subject}",
                "emailFormat": "text",
                "text": f"={text}",
                "options": {"appendAttribution": False},
            },
            credentials=SMTP,
            retryOnFail=True,
            maxTries=3,
            waitBetweenTries=2000,
        )

    def chat(self, name: str, position: tuple[int, int], channel: str, text: str, key: str) -> str:
        return self.http(
            name,
            position,
            url=f"{MOCKS}/chat/messages",
            body=f"{{ channel: '{channel}', text: {text} }}",
            headers={"Idempotency-Key": f"={{{{ {key} }}}}"},
        )

    # ---------------------------------------------------------------------------------------- export

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "active": True,
            "isArchived": False,
            "nodes": self.nodes,
            "connections": self.connections,
            "settings": {
                "executionOrder": "v1",
                "saveManualExecutions": True,
                "saveDataErrorExecution": "all",
                "saveDataSuccessExecution": "all",
                "callerPolicy": "workflowsFromSameOwner",
                **({"errorWorkflow": ERROR_WORKFLOW_ID} if self.id != ERROR_WORKFLOW_ID else {}),
            },
            "pinData": {},
            "meta": {"templateCredsSetupCompleted": True, "description": self.description},
            "tags": [{"id": TAG_IDS[tag], "name": tag} for tag in self.tags],
            "versionId": _id(self.id, "version", json.dumps([n["name"] for n in self.nodes])),
        }


def start_run(
    wf: Workflow, position: tuple[int, int], workflow: str, key: str, title: str, source: str, trigger: str
) -> str:
    return wf.api(
        "Start run (idempotency)",
        position,
        "/ops/runs/start",
        f"{{ workflow: '{workflow}', idempotency_key: {key}, execution_id: $execution.id, "
        f"input: $('{trigger}').first().json.body, source: {source}, title: {title} }}",
        notes="Same key twice = duplicate: answered from the first run",
    )


RUN_ID = "$('Start run (idempotency)').first().json.run_id"


# ================================================================================================ 00 error handler


def error_handler() -> Workflow:
    wf = Workflow(
        ERROR_WORKFLOW_ID,
        "Relay - Error handler",
        ["relay", "ops"],
        "Runs when any Relay workflow fails after its retries: dead-letter queue entry + team alert.",
    )
    wf.note(
        "About",
        (-40, -260),
        "## Error handler\nSet as the **error workflow** of every Relay workflow.\n\n"
        "1. Records the failed execution in Relay's **dead-letter queue** (with the run's original input, so it "
        "can be replayed from the dashboard or MCP).\n2. Posts an alert to **#ops-alerts**.",
        420,
        200,
        3,
    )
    trigger = wf.node("Workflow failed", "errorTrigger", 1, (0, 0), {})
    dlq = wf.api(
        "Record dead letter",
        (240, 0),
        "/ops/dead-letters",
        "{ workflow: $json.workflow.name, execution_id: String($json.execution.id || ''), "
        "node: $json.execution.lastNodeExecuted || '', error: ($json.execution.error || {}).message || "
        "'unknown error' }",
    )
    alert = wf.chat(
        "Alert #ops-alerts",
        (480, 0),
        "#ops-alerts",
        "`:rotating_light: ${$('Workflow failed').first().json.workflow.name} failed at "
        "\"${$('Workflow failed').first().json.execution.lastNodeExecuted}\": "
        "${($('Workflow failed').first().json.execution.error || {}).message}. Dead letter ${$json.id} "
        "- replay it from the Relay dashboard.`",
        "'dlq-' + $('Record dead letter').first().json.id",
    )
    wf.chain(trigger, dlq, alert)
    return wf


# ================================================================================================ 01 inbox triage

TRIAGE_ROUTE = "{ sales_lead: 'crm', support: 'helpdesk', invoice: 'accounting', spam: 'archive', other: 'archive' }"


def inbox_triage() -> Workflow:
    wf = Workflow(
        "RelayInboxTriage",
        "Relay - 01 Inbox triage",
        ["relay", "ai", "email"],
        "Inbound email -> AI classification and field extraction -> CRM / helpdesk / accounting / archive; "
        "low confidence -> human review.",
    )
    trig = "Inbound email"
    wf.note(
        "About",
        (-60, -420),
        "## Inbox triage\nInbound email (Postmark-style JSON; the Mailpit bridge sends "
        "it for mail to hello@kestrelprint.example) is **classified by the AI gateway**: sales lead, support, "
        "invoice, spam or other, with the fields each queue needs.\n\nThe gateway **checks every value against "
        "the email**, only routes invoices from known vendors, and sends anything unsure, unknown or "
        "suspicious (text addressed to an AI) to the **review queue**. A reviewer's decision resumes this "
        "execution at *Wait for reviewer*.",
        520,
        260,
        4,
    )
    wf.webhook(trig, (0, 0), "inbox-triage")
    body = f"$('{trig}').first().json.body"
    start = start_run(
        wf,
        (220, 0),
        "triage",
        f"{body}.message_id || ({body}.from.email + '|' + {body}.subject)",
        f"{body}.subject || ''",
        f"{body}.source || 'webhook'",
        trig,
    )
    dup = wf.if_("Duplicate?", (440, 0), wf.condition("={{ $json.duplicate }}", "true", kind="boolean"))
    wf.respond(
        "Respond: duplicate",
        (660, -180),
        "{ status: 'duplicate', run_id: $json.run_id, route: $json.route, previous_status: $json.status }",
    )
    classify = wf.api(
        "Classify email (AI gateway)",
        (660, 0),
        "/ai/v1/classify-email",
        f"{{ message_id: {body}.message_id, from: {body}.from, subject: {body}.subject, text: {body}.text, "
        f"attachments: ({body}.attachments || []).map(a => ({{ filename: a.filename, content_type: a.content_type }})), "
        f"run_id: {RUN_ID} }}",
        notes="Category + fields, grounded and gated",
    )
    decision = wf.set_json("Triage decision", (880, 0), "{ ...$json, run_id: " + RUN_ID + ", reviewed: false }")
    route = wf.switch("Route", (1100, 0), "={{ $json.route }}", ["crm", "helpdesk", "accounting", "archive", "review"])
    wf.chain(trig, start, dup)
    wf.connect(dup, "Respond: duplicate", 0)
    wf.connect(dup, classify, 1)
    wf.chain(classify, decision, route)

    # The item being routed: the reviewer's version after a review, else the gateway's (Set nodes have one output,
    # so `$()` on them is unambiguous; a Switch or IF output is not).
    r = (
        "($('Apply review decision').isExecuted ? $('Apply review decision').first().json : "
        "$('Triage decision').first().json)"
    )
    key = RUN_ID
    # crm
    lead = wf.http(
        "Create CRM lead",
        (1360, -360),
        url=f"{MOCKS}/crm/leads",
        body=f"{{ name: {r}.contact.name, email: {r}.contact.email, company: {r}.contact.company, "
        f"phone: {r}.contact.phone, source: 'email', product: {r}.lead.product, "
        f"quantity: {r}.lead.quantity, deadline: {r}.lead.deadline, summary: {r}.summary, "
        f"message_id: {body}.message_id }}",
        headers={"Idempotency-Key": f"={{{{ {key} }}}}"},
    )
    sales = wf.chat(
        "Notify #sales",
        (1580, -360),
        "#sales",
        f"`New lead from email: ${{{r}.contact.name || {r}.contact.email}} "
        f"(${{{r}.contact.company || 'no company'}}) - ${{{r}.summary}}`",
        f"{key} + '-sales'",
    )
    # helpdesk
    ticket = wf.http(
        "Create helpdesk ticket",
        (1360, -180),
        url=f"{MOCKS}/helpdesk/tickets",
        body=f"{{ subject: {body}.subject, requester_name: {r}.contact.name, "
        f"requester_email: {r}.contact.email, order_id: {r}.support.order_id, "
        f"issue: {r}.support.issue, priority: {r}.urgency === 'high' ? 'high' : 'normal', "
        f"summary: {r}.summary, language: {r}.language, body: {body}.text, "
        f"message_id: {body}.message_id }}",
        headers={"Idempotency-Key": f"={{{{ {key} }}}}"},
    )
    # accounting
    has_pdf = wf.if_(
        "Invoice PDF attached?",
        (1360, 0),
        wf.condition(f"={{{{ {r}.has_invoice_attachment }}}}", "true", kind="boolean"),
    )
    forward = wf.http(
        "Forward PDF to invoice intake",
        (1580, -40),
        url=f"{N8N_INTERNAL}/webhook/invoice-intake",
        body=f"{{ source: 'email', message_id: {body}.message_id, sender: {body}.from, "
        f"filename: ({body}.attachments || []).find(a => (a.filename || '').toLowerCase()"
        f".endsWith('.pdf')).filename, content_base64: ({body}.attachments || []).find(a => "
        f"(a.filename || '').toLowerCase().endsWith('.pdf')).content_base64 }}",
        auth=WEBHOOK_SECRET,
        notes="Runs workflow 02 and waits for its result",
    )
    finance = wf.chat(
        "Notify #finance (no PDF)",
        (1580, 120),
        "#finance",
        f"`Supplier email without a PDF from ${{{r}.invoice.vendor || {r}.contact.email}}: "
        f"${{{r}.summary}} (invoice ${{{r}.invoice.invoice_number || '-'}}, "
        f"${{{r}.invoice.currency || ''}} ${{{r}.invoice.amount ?? '-'}})`",
        f"{key} + '-finance'",
    )
    # archive: nothing to call
    archive = wf.set_json("Archive (spam / other)", (1360, 260), f"{{ archived: true, category: {r}.category }}")
    # review
    queue = wf.api(
        "Queue for review",
        (1360, 480),
        "/ops/review",
        f"{{ run_id: {key}, kind: 'triage', title: {body}.subject, reasons: {r}.reasons, "
        f"proposal: {{ category: {r}.category, confidence: {r}.confidence, summary: {r}.summary, "
        f"contact: {r}.contact, lead: {r}.lead, support: {r}.support, invoice: {r}.invoice, "
        f"from: {body}.from, text: ({body}.text || '').slice(0, 4000), checks: {r}.checks }}, "
        f"resume_url: $execution.resumeUrl }}",
        notes="Resume URL of this execution",
    )
    queued = wf.respond(
        "Respond: queued for review",
        (1580, 480),
        f"{{ status: 'review', run_id: {key}, review_id: $json.id, category: {r}.category, "
        f"confidence: {r}.confidence, reasons: {r}.reasons }}",
        code=202,
    )
    waiting = wf.wait("Wait for reviewer", (1800, 480))
    approved = wf.if_("Approved?", (2020, 480), wf.condition("={{ $json.body.decision }}", "equals", "approve"))
    apply_review = wf.set_json(
        "Apply review decision",
        (2240, 400),
        f"{{ ...$('Triage decision').first().json, ...($json.body.edits || {{}}), "
        f"route: ({TRIAGE_ROUTE})[($json.body.edits || {{}}).category || $('Triage decision').first().json.category], "
        f"reviewed: true }}",
    )
    rejected = wf.api(
        "Finish run: rejected",
        (2240, 600),
        f"/ops/runs/{{{{ {key} }}}}/finish",
        "{ status: 'rejected', route: 'none', output: { reviewer: $json.body.reviewer } }",
    )
    # shared end
    finish = wf.api(
        "Finish run",
        (1800, -120),
        f"/ops/runs/{{{{ {key} }}}}/finish",
        f"{{ status: {r}.reviewed ? 'approved' : 'auto', route: {r}.route, "
        f"output: {{ category: {r}.category, confidence: {r}.confidence, route: {r}.route, contact: {r}.contact, "
        f"lead: {r}.lead, support: {r}.support, invoice: {r}.invoice, "
        f"downstream: $json.id || $json.number || null }} }}",
    )
    reviewed = wf.if_("Reviewed?", (2020, -120), wf.condition(f"={{{{ {r}.reviewed }}}}", "true", kind="boolean"))
    done = wf.respond(
        "Respond: routed",
        (2240, -40),
        f"{{ status: 'routed', run_id: {key}, route: {r}.route, category: {r}.category, "
        f"confidence: {r}.confidence, result: $('Finish run').first().json }}",
    )
    wf.node("Done (after review)", "noOp", 1, (2240, -200), {})

    wf.connect(route, lead, 0)
    wf.connect(route, ticket, 1)
    wf.connect(route, has_pdf, 2)
    wf.connect(route, archive, 3)
    wf.connect(route, queue, 4)
    wf.chain(lead, sales, finish)
    wf.connect(ticket, finish)
    wf.connect(has_pdf, forward, 0)
    wf.connect(has_pdf, finance, 1)
    wf.connect(forward, finish)
    wf.connect(finance, finish)
    wf.connect(archive, finish)
    wf.chain(queue, queued, waiting, approved)
    wf.connect(approved, apply_review, 0)
    wf.connect(approved, rejected, 1)
    wf.connect(apply_review, route)
    wf.connect(finish, reviewed)
    wf.connect(reviewed, "Done (after review)", 0)
    wf.connect(reviewed, done, 1)
    return wf


# ================================================================================================ 02 invoice intake


def invoice_intake() -> Workflow:
    wf = Workflow(
        "RelayInvoiceIntake",
        "Relay - 02 Invoice intake",
        ["relay", "ai", "finance"],
        "PDF invoice -> text -> AI extraction -> arithmetic, VAT and vendor checks -> duplicate check in "
        "accounting -> post the bill (or approval queue) -> #finance.",
    )
    trig = "Invoice received"
    wf.note(
        "About",
        (-60, -420),
        "## Invoice intake\nA PDF (from inbox triage, an upload or an API call) is "
        "decoded and read with n8n's **Extract from File**; the **AI gateway** extracts the fields and checks "
        "them like a bookkeeper: line math, VAT, totals, dates, vendor and VAT ID against master data, the total "
        "printed in the document, the auto-posting limit.\n\nThe workflow then asks **accounting** whether the "
        "invoice is already booked. Clean invoices are posted; anything else waits for an approval.",
        520,
        260,
        4,
    )
    wf.webhook(trig, (0, 0), "invoice-intake")
    body = f"$('{trig}').first().json.body"
    fingerprint = wf.node(
        "Fingerprint PDF",
        "crypto",
        1,
        (200, 0),
        {
            "action": "hash",
            "type": "SHA256",
            "value": f"={{{{ {body}.content_base64 }}}}",
            "dataPropertyName": "fingerprint",
            "encoding": "hex",
        },
    )
    start = start_run(
        wf,
        (400, 0),
        "invoice",
        "'pdf:' + $json.fingerprint",
        f"{body}.filename || 'invoice.pdf'",
        f"{body}.source || 'webhook'",
        trig,
    )
    dup = wf.if_("Duplicate?", (600, 0), wf.condition("={{ $json.duplicate }}", "true", kind="boolean"))
    wf.respond(
        "Respond: duplicate",
        (800, -180),
        "{ status: 'duplicate', run_id: $json.run_id, previous_status: $json.status, output: $json.output }",
    )
    attachment = wf.set_json(
        "Attachment",
        (800, 0),
        f"{{ content_base64: {body}.content_base64, filename: {body}.filename || 'invoice.pdf' }}",
    )
    decode = wf.node(
        "Decode PDF",
        "convertToFile",
        1.1,
        (1000, 0),
        {
            "operation": "toBinary",
            "sourceProperty": "content_base64",
            "binaryPropertyName": "data",
            "options": {"fileName": "={{ $json.filename }}", "mimeType": "application/pdf"},
        },
    )
    extract_text = wf.node(
        "Extract PDF text",
        "extractFromFile",
        1.1,
        (1200, 0),
        {
            "operation": "pdf",
            "binaryPropertyName": "data",
            "options": {},
        },
    )
    extract = wf.api(
        "Extract invoice (AI gateway)",
        (1400, 0),
        "/ai/v1/extract-invoice",
        f"{{ text: $json.text || '', filename: $('Attachment').first().json.filename, "
        f"source: {body}.source || 'webhook', run_id: {RUN_ID} }}",
        notes="Fields + bookkeeping checks",
    )
    booked = wf.http(
        "Already booked? (accounting)",
        (1600, 0),
        method="GET",
        url=f"{MOCKS}/accounting/bills?dedupe_key={{{{ $json.dedupe_key }}}}",
        full_response=True,
        notes="Duplicate check by vendor + invoice number",
    )
    x = "$('Extract invoice (AI gateway)').first().json"
    decide = wf.set_json(
        "Invoice decision",
        (1800, 0),
        f"{{ ...{x}, run_id: {RUN_ID}, duplicate: ($json.body || []).length > 0, "
        f"issues: [...{x}.issues, ...(($json.body || []).length > 0 ? ['duplicate'] : [])], "
        f"post: {x}.decision === 'auto_post' && ($json.body || []).length === 0, reviewed: false }}",
    )
    can_post = wf.if_("Post automatically?", (2000, 0), wf.condition("={{ $json.post }}", "true", kind="boolean"))
    d = "$('Invoice decision').first().json"
    final = "($('Apply approval').isExecuted ? $('Apply approval').first().json : " + d + ")"
    post = wf.http(
        "Post bill to accounting",
        (2240, -120),
        url=f"{MOCKS}/accounting/bills",
        body=f"{{ ...{final}.invoice, dedupe_key: {final}.dedupe_key, "
        f"status: {final}.reviewed ? 'approved_by_reviewer' : 'approved', "
        f"source: {body}.source || 'webhook', run_id: {RUN_ID} }}",
        headers={"Idempotency-Key": f"={{{{ {RUN_ID} }}}}"},
    )
    notify = wf.chat(
        "Notify #finance",
        (2460, -120),
        "#finance",
        "`Bill ${$json.number} posted: ${$json.vendor} ${$json.invoice_number}, "
        "${$json.currency} ${$json.total} due ${$json.due_date}`",
        f"{RUN_ID} + '-finance'",
    )
    finish = wf.api(
        "Finish run",
        (2680, -120),
        f"/ops/runs/{{{{ {RUN_ID} }}}}/finish",
        f"{{ status: {final}.reviewed ? 'approved' : 'auto', route: 'accounting', "
        f"output: {{ invoice: {final}.invoice, issues: {final}.issues, bill: $('Post bill to accounting')"
        f".first().json.number }} }}",
    )
    reviewed = wf.if_("Reviewed?", (2900, -120), wf.condition(f"={{{{ {final}.reviewed }}}}", "true", kind="boolean"))
    wf.node("Done (after review)", "noOp", 1, (3120, -200), {})
    done = wf.respond(
        "Respond: posted",
        (3120, -40),
        f"{{ status: 'posted', run_id: {RUN_ID}, bill: $('Post bill to accounting').first().json, issues: [] }}",
    )
    queue = wf.api(
        "Queue for approval",
        (2240, 160),
        "/ops/review",
        f"{{ run_id: {RUN_ID}, kind: 'invoice', title: ({d}.invoice.vendor || 'Unknown vendor') + ' ' + "
        f"({d}.invoice.invoice_number || '(no number)'), reasons: {d}.issues, "
        f"proposal: {{ invoice: {d}.invoice, checks: {d}.checks, issues: {d}.issues, dedupe_key: {d}.dedupe_key, "
        f"filename: $('Attachment').first().json.filename }}, resume_url: $execution.resumeUrl }}",
    )
    queued = wf.respond(
        "Respond: needs approval",
        (2460, 160),
        f"{{ status: 'review', run_id: {RUN_ID}, review_id: $json.id, issues: {d}.issues, invoice: {d}.invoice }}",
        code=202,
    )
    waiting = wf.wait("Wait for approval", (2680, 160))
    approved = wf.if_("Approved?", (2900, 160), wf.condition("={{ $json.body.decision }}", "equals", "approve"))
    apply_review = wf.set_json(
        "Apply approval",
        (3120, 120),
        f"{{ ...{d}, invoice: {{ ...{d}.invoice, ...(($json.body.edits || {{}}).invoice || {{}}) }}, reviewed: true }}",
    )
    rejected = wf.api(
        "Finish run: rejected",
        (3120, 300),
        f"/ops/runs/{{{{ {RUN_ID} }}}}/finish",
        f"{{ status: 'rejected', route: 'none', output: {{ reviewer: $json.body.reviewer, issues: {d}.issues }} }}",
    )
    wf.chain(trig, fingerprint, start, dup)
    wf.connect(dup, "Respond: duplicate", 0)
    wf.connect(dup, attachment, 1)
    wf.chain(attachment, decode, extract_text, extract, booked, decide, can_post)
    wf.connect(can_post, post, 0)
    wf.connect(can_post, queue, 1)
    wf.chain(post, notify, finish, reviewed)
    wf.connect(reviewed, "Done (after review)", 0)
    wf.connect(reviewed, done, 1)
    wf.chain(queue, queued, waiting, approved)
    wf.connect(approved, apply_review, 0)
    wf.connect(approved, rejected, 1)
    wf.connect(apply_review, post)
    return wf


# ================================================================================================ 03 lead enrichment


def lead_enrichment() -> Workflow:
    wf = Workflow(
        "RelayLeadEnrichment",
        "Relay - 03 Lead enrichment and reply draft",
        ["relay", "ai", "sales"],
        "Web-form lead -> enrichment from the company's own site -> ICP score in code -> CRM -> AI reply "
        "draft -> human approval -> email.",
    )
    trig = "Web form lead"
    wf.note(
        "About",
        (-60, -420),
        "## Lead enrichment\nA web-form lead is **enriched from the company's own "
        "website** (fixture sites only; hidden text and text addressed to AI are removed first), **scored in "
        "code** against the ideal customer profile (configs/icp.yaml) and saved to the **CRM**. Hot leads ping "
        "#sales.\n\nThe AI gateway drafts a reply; **a person approves or edits it** in the dashboard before "
        "*Send reply* runs. Leads without a usable site, competitors and non-buyers go to research instead.",
        520,
        260,
        4,
    )
    wf.webhook(trig, (0, 0), "lead-enrichment")
    body = f"$('{trig}').first().json.body"
    start = start_run(
        wf,
        (220, 0),
        "lead",
        f"{body}.submission_id || ((({body}.email || '') + '|' + ({body}.message || '')).slice(0, 300))",
        f"({body}.company || {body}.name || 'Lead')",
        f"{body}.source || 'web_form'",
        trig,
    )
    dup = wf.if_("Duplicate?", (440, 0), wf.condition("={{ $json.duplicate }}", "true", kind="boolean"))
    wf.respond(
        "Respond: duplicate",
        (660, -180),
        "{ status: 'duplicate', run_id: $json.run_id, previous_status: $json.status, output: $json.output }",
    )
    enrich = wf.api(
        "Enrich and score (AI gateway)",
        (660, 0),
        "/ai/v1/enrich-lead",
        f"{{ name: {body}.name, email: {body}.email, company: {body}.company, website: {body}.website, "
        f"message: {body}.message, product: {body}.product, quantity: {body}.quantity, "
        f"run_id: {RUN_ID} }}",
        notes="Site facts by AI, score by rules",
    )
    e = "$('Enrich and score (AI gateway)').first().json"
    crm = wf.http(
        "Save lead to CRM",
        (880, 0),
        url=f"{MOCKS}/crm/leads",
        body=f"{{ name: {body}.name, email: {body}.email, company: {body}.company, website: {e}.site.url, "
        f"source: 'web_form', product: {body}.product, quantity: {body}.quantity, "
        f"message: {body}.message, industry: {e}.profile.industry, "
        f"employees_band: {e}.profile.employees_band, country: {e}.profile.country, "
        f"score: {e}.score, tier: {e}.tier, disqualified: {e}.disqualified, status: 'enriched' }}",
        headers={"Idempotency-Key": f"={{{{ {RUN_ID} }}}}"},
    )
    hot = wf.if_("Hot lead?", (1100, 0), wf.condition(f"={{{{ {e}.tier }}}}", "equals", "hot"))
    alert = wf.chat(
        "Alert #sales (hot lead)",
        (1320, -140),
        "#sales",
        f"`:fire: Hot lead: ${{{body}.name}} at ${{{body}.company}} - score ${{{e}.score}}, "
        f"${{{e}.profile.industry}}, ${{{e}.profile.employees_band}} people, ${{{e}.profile.country}}. "
        f"Reply draft waiting for approval in Relay.`",
        f"{RUN_ID} + '-hot'",
    )
    draftable = wf.if_(
        "Ready for a reply draft?", (1540, 0), wf.condition(f"={{{{ {e}.decision }}}}", "equals", "draft_reply")
    )
    draft = wf.api(
        "Draft reply (AI gateway)",
        (1760, -100),
        "/ai/v1/draft-reply",
        f"{{ lead: {{ name: {body}.name, email: {body}.email, company: {body}.company, "
        f"website: {body}.website, message: {body}.message, product: {body}.product, "
        f"quantity: {body}.quantity }}, profile: {e}.profile, tier: {e}.tier, run_id: {RUN_ID} }}",
    )
    queue_reply = wf.api(
        "Queue reply for approval",
        (1980, -100),
        "/ops/review",
        f"{{ run_id: {RUN_ID}, kind: 'lead_reply', title: 'Reply to ' + {body}.name + ' (' + "
        f"({body}.company || '-') + ')', reasons: ['every first reply is approved by a person', "
        f"...$json.checks.filter(c => !c.ok).map(c => 'check failed: ' + c.code)], "
        f"proposal: {{ to: {body}.email, subject: $json.subject, body: $json.body, checks: $json.checks, "
        f"tier: {e}.tier, score: {e}.score, profile: {e}.profile, lead: {body} }}, "
        f"resume_url: $execution.resumeUrl }}",
    )
    queue_research = wf.api(
        "Queue for research",
        (1980, 120),
        "/ops/review",
        f"{{ run_id: {RUN_ID}, kind: 'lead_research', title: 'Research lead ' + ({body}.company || {body}.name), "
        f"reasons: {e}.reasons, proposal: {{ tier: {e}.tier, score: {e}.score, profile: {e}.profile, "
        f"site: {e}.site, lead: {body} }}, resume_url: $execution.resumeUrl }}",
    )
    queued = wf.respond(
        "Respond: queued",
        (2200, 0),
        f"{{ status: 'review', run_id: {RUN_ID}, review_id: $json.id, kind: $json.kind, "
        f"tier: {e}.tier, score: {e}.score, score_breakdown: {e}.score_breakdown, "
        f"profile: {e}.profile, site: {e}.site, decision: {e}.decision, reasons: {e}.reasons }}",
        code=202,
    )
    waiting = wf.wait("Wait for reviewer", (2420, 0))
    send_it = wf.if_(
        "Approved with a reply?",
        (2640, 0),
        wf.condition("={{ $json.body.decision }}", "equals", "approve"),
        wf.condition("={{ $('Draft reply (AI gateway)').isExecuted }}", "true", kind="boolean"),
    )
    q = "$('Queue reply for approval').first().json.proposal"
    send = wf.email(
        "Send reply",
        (2860, -100),
        from_email=SALES_FROM,
        to=f"{{{{ ($json.body.edits || {{}}).to || {q}.to }}}}",
        subject=f"{{{{ ($json.body.edits || {{}}).subject || {q}.subject }}}}",
        text=f"{{{{ ($json.body.edits || {{}}).body || {q}.body }}}}",
    )
    finish = wf.api(
        "Finish run",
        (3080, 0),
        f"/ops/runs/{{{{ {RUN_ID} }}}}/finish",
        f"{{ status: $('Wait for reviewer').first().json.body.decision === 'approve' ? 'approved' : 'rejected', "
        f"route: $('Send reply').isExecuted ? 'email_sent' : 'crm', output: {{ tier: {e}.tier, score: {e}.score, "
        f"profile: {e}.profile, reply_sent: $('Send reply').isExecuted }} }}",
    )
    wf.chain(trig, start, dup)
    wf.connect(dup, "Respond: duplicate", 0)
    wf.connect(dup, enrich, 1)
    wf.chain(enrich, crm, hot)
    wf.connect(hot, alert, 0)
    wf.connect(hot, draftable, 1)
    wf.connect(alert, draftable)
    wf.connect(draftable, draft, 0)
    wf.connect(draftable, queue_research, 1)
    wf.connect(draft, queue_reply)
    wf.connect(queue_reply, queued)
    wf.connect(queue_research, queued)
    wf.chain(queued, waiting, send_it)
    wf.connect(send_it, send, 0)
    wf.connect(send_it, finish, 1)
    wf.connect(send, finish)
    return wf


# ================================================================================================ 04 support FAQ


def support_faq() -> Workflow:
    wf = Workflow(
        "RelaySupportFaq",
        "Relay - 04 Support FAQ auto-reply",
        ["relay", "ai", "support"],
        "Customer question -> retrieval over the knowledge base -> cited answer -> confidence gate -> "
        "auto-reply by email, or escalation to the helpdesk with the draft and articles.",
    )
    trig = "Customer question"
    wf.note(
        "About",
        (-60, -420),
        "## Support FAQ auto-reply\nThe **AI gateway** retrieves knowledge-base articles "
        "(BM25), answers with **citations**, and gates the answer: escalation rules (orders, refunds, legal, "
        "privacy, security, complaints), retrieval coverage, model confidence, cited articles only and **no "
        "number that is not in the cited article**.\n\nPassed: emailed to the customer and logged as a solved "
        "ticket. Otherwise: an open ticket with the reasons, the draft and the suggested articles.",
        520,
        240,
        4,
    )
    wf.webhook(trig, (0, 0), "support-faq")
    body = f"$('{trig}').first().json.body"
    start = start_run(
        wf,
        (220, 0),
        "faq",
        f"{body}.message_id || ((({body}.customer_email || '') + '|' + ({body}.question || '')).slice(0, 300))",
        f"({body}.question || '').slice(0, 120)",
        f"{body}.channel || 'email'",
        trig,
    )
    dup = wf.if_("Duplicate?", (440, 0), wf.condition("={{ $json.duplicate }}", "true", kind="boolean"))
    wf.respond(
        "Respond: duplicate",
        (660, -180),
        "{ status: 'duplicate', run_id: $json.run_id, previous_status: $json.status, output: $json.output }",
    )
    answer = wf.api(
        "Answer from knowledge base (AI gateway)",
        (660, 0),
        "/ai/v1/answer-faq",
        f"{{ question: {body}.question, customer_email: {body}.customer_email, "
        f"channel: {body}.channel || 'email', run_id: {RUN_ID} }}",
        notes="RAG + gate",
    )
    a = "$('Answer from knowledge base (AI gateway)').first().json"
    gate = wf.if_("Safe to auto-reply?", (880, 0), wf.condition(f"={{{{ {a}.decision }}}}", "equals", "auto_reply"))
    send = wf.email(
        "Email the answer",
        (1100, -120),
        from_email=SUPPORT_FROM,
        to=f"{{{{ {body}.customer_email }}}}",
        subject="Re: your question to Kestrel Print Co.",
        text=f"{{{{ {a}.answer }}}}\n\nSources: {{{{ {a}.citations.join(', ') }}}}\n\n"
        "This answer was sent automatically from our help centre. Reply to this email to reach "
        "a person.\nKestrel Print Co. support",
    )
    solved = wf.http(
        "Log solved ticket",
        (1320, -120),
        url=f"{MOCKS}/helpdesk/tickets",
        body=f"{{ subject: ({body}.question || '').slice(0, 120), requester_email: {body}.customer_email, "
        f"status: 'solved', channel: {body}.channel || 'email', answer: {a}.answer, "
        f"citations: {a}.citations, auto_replied: true }}",
        headers={"Idempotency-Key": f"={{{{ {RUN_ID} }}}}"},
    )
    escalate = wf.http(
        "Escalate to helpdesk",
        (1100, 120),
        url=f"{MOCKS}/helpdesk/tickets",
        body=f"{{ subject: ({body}.question || '').slice(0, 120), "
        f"requester_email: {body}.customer_email, status: 'open', priority: "
        f"{a}.reasons.some(r => /legal|security|refund|complaint/i.test(r)) ? 'high' : 'normal', "
        f"body: {body}.question, escalation_reasons: {a}.reasons, draft_answer: {a}.answer, "
        f"suggested_articles: {a}.retrieved.map(r => r.id) }}",
        headers={"Idempotency-Key": f"={{{{ {RUN_ID} }}}}"},
    )
    finish = wf.api(
        "Finish run",
        (1540, 0),
        f"/ops/runs/{{{{ {RUN_ID} }}}}/finish",
        f"{{ status: {a}.decision === 'auto_reply' ? 'auto' : 'escalated', route: {a}.decision, "
        f"output: {{ decision: {a}.decision, answerable: {a}.answerable, confidence: {a}.confidence, "
        f"citations: {a}.citations, reasons: {a}.reasons, ticket: $json.number }} }}",
    )
    done = wf.respond(
        "Respond",
        (1760, 0),
        f"{{ status: {a}.decision, run_id: {RUN_ID}, answer: {a}.answer, citations: {a}.citations, "
        f"confidence: {a}.confidence, reasons: {a}.reasons, retrieved: {a}.retrieved, "
        f"ticket: $('Finish run').first().json.output.ticket }}",
    )
    wf.chain(trig, start, dup)
    wf.connect(dup, "Respond: duplicate", 0)
    wf.connect(dup, answer, 1)
    wf.connect(answer, gate)
    wf.connect(gate, send, 0)
    wf.connect(gate, escalate, 1)
    wf.chain(send, solved, finish)
    wf.connect(escalate, finish)
    wf.connect(finish, done)
    return wf


# ================================================================================================ 05 KPI digest

KPI_SQL = """WITH w AS (
  SELECT * FROM runs WHERE started_at >= now() - interval '7 days'
)
SELECT
  to_char(now() - interval '7 days', 'YYYY-MM-DD') || ' to ' || to_char(now(), 'YYYY-MM-DD') AS period,
  (SELECT count(*) FROM w WHERE workflow = 'triage') AS emails_triaged,
  (SELECT count(*) FROM w WHERE workflow = 'triage' AND status = 'auto') AS emails_routed_automatically,
  (SELECT count(*) FROM w WHERE workflow = 'triage' AND route = 'crm') AS sales_leads_from_email,
  (SELECT count(*) FROM w WHERE workflow = 'invoice' AND status IN ('auto', 'approved')) AS invoices_posted,
  (SELECT coalesce(round(sum((output -> 'invoice' ->> 'total')::numeric), 2), 0) FROM w
     WHERE workflow = 'invoice' AND status IN ('auto', 'approved')
       AND output -> 'invoice' ->> 'currency' = 'EUR') AS invoice_total_posted_eur,
  (SELECT count(*) FROM w WHERE workflow = 'lead') AS web_leads,
  (SELECT count(*) FROM w WHERE workflow = 'lead' AND output ->> 'tier' = 'hot') AS hot_leads,
  (SELECT count(*) FROM w WHERE workflow = 'faq' AND status = 'auto') AS faq_answered_automatically,
  (SELECT count(*) FROM w WHERE workflow = 'faq' AND status = 'escalated') AS faq_escalated,
  (SELECT count(*) FROM review_items WHERE status = 'pending') AS reviews_pending,
  (SELECT count(*) FROM dead_letters WHERE status = 'open') AS dead_letters_open,
  (SELECT count(*) FROM w WHERE status = 'error') AS failed_runs;"""


def kpi_digest() -> Workflow:
    wf = Workflow(
        "RelayKpiDigest",
        "Relay - 05 Weekly KPI digest",
        ["relay", "ai", "reporting"],
        "Every Monday 08:00 (or on demand): KPIs from PostgreSQL -> AI-written digest with every number "
        "checked -> #ops.",
    )
    wf.note(
        "About",
        (-60, -400),
        "## Weekly KPI digest\nThe **numbers come from SQL** (the Postgres node reads "
        "Relay's run log); the **AI gateway only writes the sentences** and every number in its text is "
        "checked against the query result. A digest with an unsupported number is replaced by a plain "
        "template.",
        460,
        200,
        4,
    )
    schedule = wf.node(
        "Every Monday 08:00",
        "scheduleTrigger",
        1.2,
        (0, -100),
        {
            "rule": {"interval": [{"field": "cronExpression", "expression": "0 8 * * 1"}]},
        },
    )
    manual = wf.webhook("Run now (webhook)", (0, 100), "kpi-digest", response="lastNode")
    kpis = wf.node(
        "Weekly KPIs (PostgreSQL)",
        "postgres",
        2.6,
        (240, 0),
        {
            "operation": "executeQuery",
            "query": KPI_SQL,
            "options": {},
        },
        credentials=POSTGRES,
        alwaysOutputData=True,
    )
    start = wf.api(
        "Start run (idempotency)",
        (480, 0),
        "/ops/runs/start",
        "{ workflow: 'digest', idempotency_key: $('Run now (webhook)').isExecuted ? 'manual:' + $execution.id : "
        "'week:' + $json.period, execution_id: $execution.id, input: $json, "
        "source: $('Run now (webhook)').isExecuted ? 'webhook' : 'schedule', title: 'Digest ' + $json.period }",
    )
    write = wf.api(
        "Write digest (AI gateway)",
        (720, 0),
        "/ai/v1/write-digest",
        "{ period: $('Weekly KPIs (PostgreSQL)').first().json.period, metrics: Object.fromEntries("
        "Object.entries($('Weekly KPIs (PostgreSQL)').first().json).filter(([k]) => k !== 'period')"
        ".map(([k, v]) => [k, Number(v)])), run_id: " + RUN_ID + " }",
        notes="Numbers checked against the SQL result",
    )
    post = wf.chat(
        "Post to #ops",
        (960, 0),
        "#ops",
        "'*' + $json.headline + '*\\n' + $json.bullets.map(b => '- ' + b).join('\\n')",
        f"{RUN_ID} + '-digest'",
    )
    finish = wf.api(
        "Finish run",
        (1200, 0),
        f"/ops/runs/{{{{ {RUN_ID} }}}}/finish",
        "{ status: 'auto', route: 'chat', output: { headline: $('Write digest (AI gateway)').first().json.headline, "
        "numbers_ok: $('Write digest (AI gateway)').first().json.numbers_ok, "
        "text: $('Write digest (AI gateway)').first().json.text } }",
    )
    wf.connect(schedule, kpis)
    wf.connect(manual, kpis)
    wf.chain(kpis, start, write, post, finish)
    return wf


ALL = {
    "00-error-handler.json": error_handler,
    "01-inbox-triage.json": inbox_triage,
    "02-invoice-intake.json": invoice_intake,
    "03-lead-enrichment.json": lead_enrichment,
    "04-support-faq.json": support_faq,
    "05-kpi-digest.json": kpi_digest,
}


def build_all() -> dict[str, dict[str, Any]]:
    return {filename: factory().to_json() for filename, factory in ALL.items()}


def render(workflow: dict[str, Any]) -> str:
    return json.dumps(workflow, indent=2, ensure_ascii=False) + "\n"


def write_all(directory: Path = WORKFLOW_DIR) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for filename, workflow in build_all().items():
        path = directory / filename
        path.write_text(render(workflow), encoding="utf-8")
        paths.append(path)
    return paths
