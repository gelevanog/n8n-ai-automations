"""`relay` command line: servers, datasets, workflow JSON, evaluation, demo traffic and the MCP server."""

from __future__ import annotations

import json
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from relay.config import get_settings
from relay.logging_config import configure_logging

app = typer.Typer(help="Relay: AI business automations on self-hosted n8n.", no_args_is_help=True)
workflows_app = typer.Typer(help="The n8n workflow JSON in n8n/workflows/.", no_args_is_help=True)
app.add_typer(workflows_app, name="workflows")
console = Console()


@app.callback()
def main(log_level: Annotated[str, typer.Option(help="DEBUG, INFO, WARNING")] = "WARNING") -> None:
    configure_logging(log_level)


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8100) -> None:
    """The Relay API (AI gateway + ops) on http://127.0.0.1:8100."""
    import uvicorn

    from relay.api.app import create_app

    uvicorn.run(create_app(), host=host, port=port)


@app.command()
def mocks(host: str = "127.0.0.1", port: int = 8200) -> None:
    """The mock CRM, helpdesk, accounting, chat and fixture websites on http://127.0.0.1:8200."""
    import uvicorn

    from relay.mocks.app import create_app

    uvicorn.run(create_app(), host=host, port=port)


@app.command()
def synth(data_dir: Path = Path("data")) -> None:
    """Regenerate the synthetic datasets (deterministic: the committed files are the output of this)."""
    from relay.synthetic import emails, invoices, leads

    invoices.write_dataset(data_dir / "invoices")
    emails.write_dataset(data_dir / "emails")
    leads.write_dataset(data_dir / "leads")
    console.print(f"[green]datasets written to {data_dir}[/green]")


@workflows_app.command("build")
def workflows_build(directory: Path = Path("n8n/workflows")) -> None:
    """Write the workflow JSON from relay.workflows.builder."""
    from relay.workflows.builder import write_all

    for path in write_all(directory):
        console.print(f"wrote {path}")


@workflows_app.command("check")
def workflows_check(directory: Path = Path("n8n/workflows")) -> None:
    """Validate the workflow JSON: node types and versions, connections, credentials, webhooks, error workflow."""
    from relay.workflows.validate import load_catalog, validate_workflow

    catalog = load_catalog()
    failed = False
    for path in sorted(directory.glob("*.json")):
        problems = validate_workflow(json.loads(path.read_text(encoding="utf-8")), catalog)
        status = "[green]ok[/green]" if not problems else f"[red]{len(problems)} problem(s)[/red]"
        console.print(f"{path.name}: {status}")
        for problem in problems:
            console.print(f"  - {problem}")
        failed = failed or bool(problems)
    if failed:
        raise typer.Exit(1)


@app.command("eval")
def run_eval(
    suites: Annotated[list[str], typer.Argument(help="triage invoices leads faq digest idempotency chaos | all")],
    name: Annotated[str, typer.Option(help="Result file suffix, e.g. the model")] = "run",
    limit: Annotated[int | None, typer.Option(help="Only the first N items per suite")] = None,
    out: Path = Path("results"),
    n8n_url: str = "http://localhost:5678",
    api_url: str = "http://localhost:8100",
    mocks_url: str = "http://localhost:8200",
    mailpit_url: str = "http://localhost:8025",
) -> None:
    """Run the evaluation end to end through n8n (needs `docker compose up`)."""
    from relay.eval.report import write_summary
    from relay.eval.runner import SUITES, Endpoints, Harness, write_result

    settings = get_settings()
    chosen = list(SUITES) if suites == ["all"] else suites
    unknown = set(chosen) - set(SUITES)
    if unknown:
        raise typer.BadParameter(f"unknown suites: {sorted(unknown)}")
    harness = Harness(
        settings,
        Endpoints(
            n8n=n8n_url,
            api=api_url,
            mocks=mocks_url,
            mailpit=mailpit_url,
            webhook_token=settings.n8n_webhook_token,
            api_token=settings.api_token or "relay-local-api-token",
        ),
    )
    health = harness.health()
    meta = {"model": health.get("model"), "free_only": health.get("free_only"), "transport": "n8n webhooks"}
    console.print(f"model: [bold]{meta['model']}[/bold]  free-only guard: {meta['free_only']}")
    for suite in chosen:
        if suite in {"digest", "idempotency", "chaos"}:
            result = getattr(harness, suite)()
        else:
            result = getattr(harness, suite)(limit=limit)
        path = write_result(result, out, name, meta)
        console.print(f"[green]{suite}[/green] -> {path}")
        console.print_json(
            json.dumps(
                {k: v for k, v in result["summary"].items() if not isinstance(v, dict | list) or k in {"per_issue"}},
                default=str,
            )
        )
    write_summary(out)


@app.command()
def summary(out: Path = Path("results")) -> None:
    """Rebuild results/summary.json and print the headline table."""
    from relay.eval.report import write_summary

    data = write_summary(out)
    table = Table("suite", "run", "metric", "value")
    for row in data.get("headline", []):
        table.add_row(row["suite"], row["run"], row["metric"], str(row["value"]))
    console.print(table)


@app.command()
def mcp(
    http: Annotated[bool, typer.Option(help="Streamable HTTP instead of stdio")] = False,
    port: int = 8765,
) -> None:
    """MCP server for Claude Code / Claude Desktop (stdio by default)."""
    from relay.mcp_server import run

    run(http=http, port=port)


@app.command()
def demo(
    emails: int = 30,
    invoices: int = 12,
    leads: int = 10,
    questions: int = 16,
    reset: Annotated[bool, typer.Option(help="Empty the ops store and the mock systems first")] = True,
    n8n_url: str = "http://localhost:5678",
    api_url: str = "http://localhost:8100",
    mocks_url: str = "http://localhost:8200",
) -> None:
    """Send a mix of synthetic traffic through the n8n workflows so the dashboard has something to show."""
    import base64

    import yaml

    from relay.eval.runner import Endpoints, Harness
    from relay.synthetic.emails import build_emails, email_payload

    settings = get_settings()
    harness = Harness(
        settings,
        Endpoints(
            n8n=n8n_url,
            api=api_url,
            mocks=mocks_url,
            webhook_token=settings.n8n_webhook_token,
            api_token=settings.api_token or "relay-local-api-token",
        ),
    )
    if reset:
        harness.reset()
    every = build_emails()
    picked = every[:: max(1, len(every) // emails)][:emails]
    for email in picked:
        harness.webhook("inbox-triage", email_payload(email, Path("data/invoices")))
    gold = [json.loads(line) for line in Path("data/invoices/gold.jsonl").read_text(encoding="utf-8").splitlines()]
    for invoice in gold[-invoices:]:
        pdf = Path("data/invoices/pdf") / f"{invoice['id']}.pdf"
        harness.webhook(
            "invoice-intake",
            {"filename": pdf.name, "source": "upload", "content_base64": base64.b64encode(pdf.read_bytes()).decode()},
        )
    lead_rows = [json.loads(line) for line in Path("data/leads/leads.jsonl").read_text(encoding="utf-8").splitlines()]
    for lead in lead_rows[:leads]:
        body = {k: lead[k] for k in ("name", "email", "company", "website", "message", "product", "quantity")}
        harness.webhook("lead-enrichment", {**body, "submission_id": lead["id"]})
    faq = yaml.safe_load(Path("data/faq/questions.yaml").read_text(encoding="utf-8"))
    for q in faq[:: max(1, len(faq) // questions)][:questions]:
        harness.webhook(
            "support-faq",
            {"question": q["question"], "message_id": q["id"], "customer_email": f"{q['id']}@customer.example"},
        )
    console.print(harness.api("GET", "/ops/metrics")["totals"])


@app.command("send-email")
def send_email(
    email_id: Annotated[str, typer.Argument(help="An id from data/emails/emails.jsonl, e.g. em-001")],
    smtp_host: str = "localhost",
    smtp_port: int = 1025,
) -> None:
    """Send one synthetic email to the shared inbox through Mailpit's SMTP (it reaches n8n via the bridge)."""
    from relay.synthetic.emails import INBOX, build_emails

    email = next((e for e in build_emails() if e.id == email_id), None)
    if email is None:
        raise typer.BadParameter(f"no email {email_id}")
    message = EmailMessage()
    message["From"] = f"{email.from_name} <{email.from_email}>"
    message["To"] = INBOX
    message["Subject"] = email.subject
    message["Message-ID"] = f"<{email.id}.smtp@mail.kestrelprint.example>"
    message.set_content(email.body)
    if email.attachment:
        pdf = Path("data/invoices/pdf") / f"{email.attachment}.pdf"
        message.add_attachment(pdf.read_bytes(), maintype="application", subtype="pdf", filename=pdf.name)
    with smtplib.SMTP(smtp_host, smtp_port) as smtp:
        smtp.send_message(message)
    console.print(f"sent {email.id} to {INBOX} via {smtp_host}:{smtp_port}")


@app.command("smoke-models")
def smoke_models(
    models: Annotated[list[str], typer.Argument(help="OpenRouter model ids ending in :free")],
    out: Path = Path("results/smoke.json"),
) -> None:
    """One triage, one invoice and one FAQ call per free model (direct, no n8n) to choose the main model."""
    from relay.eval.smoke import smoke

    result = smoke(get_settings(), models)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for row in result["models"]:
        console.print(row)


if __name__ == "__main__":
    app()
