"""Weekly KPI digest: the numbers come from SQL in the workflow, the model only writes the sentences around them,
and every number in the text is checked against the metrics (rounding allowed). A digest with an unsupported number
is replaced by a plain template, so a wrong figure never reaches the team channel."""

from __future__ import annotations

import json
from decimal import Decimal

from relay.config import Settings
from relay.gateway.schemas import DigestOutput, DigestRequest, DigestResponse
from relay.gateway.structured import complete_structured
from relay.gateway.textutil import numbers_in
from relay.llm.base import ChatModel, Message

TASK = "write_digest"

SYSTEM = f"""RELAY_TASK: {TASK}
You write the weekly operations digest for Kestrel Print Co.'s team chat. You get this week's metrics as JSON
(and last week's where available). Write a one-line headline and 3-6 short bullets in plain English that point out
what changed and what needs attention. Use only numbers that appear in the metrics (you may round them); do not
compute new numbers. Reply with only a JSON object: {{"headline": "...", "bullets": ["...", "..."]}}."""


def _allowed(metrics: dict[str, float | int | str]) -> set[Decimal]:
    allowed: set[Decimal] = set()
    for value in metrics.values():
        if isinstance(value, int | float):
            exact = Decimal(str(value))
            allowed |= {exact.normalize(), Decimal(round(exact)).normalize()}
            allowed |= {
                Decimal(str(round(float(value), 1))).normalize(),
                Decimal(str(round(float(value), 2))).normalize(),
            }
            if 0 < float(value) <= 1:  # a fraction may be written as a percentage
                pct = Decimal(str(float(value) * 100))
                allowed |= {Decimal(round(pct)).normalize(), Decimal(str(round(float(pct), 1))).normalize()}
        else:
            allowed |= numbers_in(str(value))
    return allowed


def template(request: DigestRequest) -> DigestOutput:
    bullets = [f"{key.replace('_', ' ')}: {value}" for key, value in list(request.metrics.items())[:6]]
    return DigestOutput(headline=f"Weekly operations digest, {request.period}", bullets=bullets)


def write_digest(model: ChatModel, request: DigestRequest, settings: Settings) -> DigestResponse:
    messages: list[Message] = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"Period: {request.period}\nMetrics: {json.dumps(request.metrics)}"},
    ]
    output, meta = complete_structured(model, messages, DigestOutput, settings, task=TASK)
    allowed = _allowed(request.metrics) | numbers_in(request.period)
    text = output.headline + "\n" + "\n".join(f"- {b}" for b in output.bullets)
    unsupported = sorted({str(n) for n in numbers_in(text) if n not in allowed})
    if unsupported:
        fallback = template(request)
        text = fallback.headline + "\n" + "\n".join(f"- {b}" for b in fallback.bullets)
        output = fallback
    return DigestResponse(
        headline=output.headline,
        bullets=output.bullets,
        text=text,
        numbers_ok=not unsupported,
        unsupported_numbers=unsupported,
        meta=meta,
    )
