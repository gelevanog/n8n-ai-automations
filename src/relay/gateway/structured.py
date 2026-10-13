"""Structured output: ask the model for JSON, parse it leniently, validate it strictly, ask once more on failure.

Free models do not all support `response_format`, so the schema is described in the prompt and the reply is parsed
with a tolerant JSON extractor (code fences, <think> blocks, trailing commas), then validated with Pydantic. When
validation fails the model gets its own reply back with the errors, once (RELAY_LLM_STRUCTURED_RETRIES). The result
carries the call metadata the gateway logs: model, latency, tokens, cost, cache hit, attempts."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from relay.config import Settings
from relay.gateway.schemas import CallMeta
from relay.llm.base import ChatModel, LLMError, Message
from relay.llm.jsonparse import JSONReplyError, parse_json_object

T = TypeVar("T", bound=BaseModel)


class StructuredOutputError(LLMError):
    """The model did not produce a valid object, even after the corrective retry."""


@dataclass
class Prices:
    free: tuple[float, float]
    default: tuple[float, float]
    models: dict[str, tuple[float, float]]

    def cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        name = model.split("/", 1)[1] if model.startswith(("openrouter/", "compat/", "qwen/", "openai/")) else model
        if model.startswith("fake/") or name.endswith(":free"):
            rate = self.free
        else:
            rate = self.models.get(name, self.models.get(model, self.default))
        return round((input_tokens * rate[0] + output_tokens * rate[1]) / 1_000_000, 6)


@lru_cache(maxsize=4)
def load_prices(path: Path) -> Prices:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    data = data or {}

    def pair(value: dict[str, float] | None, fallback: tuple[float, float]) -> tuple[float, float]:
        if not value:
            return fallback
        return float(value.get("input", 0.0)), float(value.get("output", 0.0))

    return Prices(
        free=pair(data.get("free"), (0.0, 0.0)),
        default=pair(data.get("default"), (3.0, 15.0)),
        models={k: pair(v, (3.0, 15.0)) for k, v in (data.get("models") or {}).items()},
    )


def schema_hint(model: type[BaseModel]) -> str:
    """A compact JSON Schema of the expected reply for the prompt."""
    schema = model.model_json_schema()
    return json.dumps(schema, ensure_ascii=False, separators=(",", ":"))


def complete_structured(
    model: ChatModel,
    messages: list[Message],
    output: type[T],
    settings: Settings,
    *,
    task: str,
    max_tokens: int | None = None,
) -> tuple[T, CallMeta]:
    started = time.monotonic()
    attempts = 0
    input_tokens = output_tokens = 0
    cached = True
    served: str | None = None
    conversation = list(messages)
    last_error = ""
    for attempt in range(settings.llm_structured_retries + 1):
        attempts += 1
        completion = model.complete(
            conversation, max_tokens=max_tokens or settings.llm_max_tokens, temperature=settings.llm_temperature
        )
        input_tokens += completion.input_tokens
        output_tokens += completion.output_tokens
        cached = cached and completion.cached
        served = completion.model
        try:
            data = parse_json_object(completion.text)
            value = output.model_validate(data)
        except (JSONReplyError, ValidationError) as exc:
            last_error = _short_error(exc)
            if attempt >= settings.llm_structured_retries:
                break
            conversation = [
                *messages,
                {"role": "assistant", "content": completion.text[:6000]},
                {
                    "role": "user",
                    "content": "Your reply did not match the required JSON schema: "
                    f"{last_error}. Reply again with only the corrected JSON object.",
                },
            ]
            continue
        meta = CallMeta(
            call_id=uuid.uuid4().hex[:12],
            task=task,
            model=model.label,
            served_model=served,
            latency_ms=int((time.monotonic() - started) * 1000),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=load_prices(settings.prices_file).cost(served or model.label, input_tokens, output_tokens),
            cached=cached and attempts == 1,
            attempts=attempts,
            schema_valid=True,
        )
        return value, meta
    raise StructuredOutputError(f"{task}: no valid {output.__name__} after {attempts} attempt(s): {last_error}")


def _short_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        parts = [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()[:6]]
        return "; ".join(parts)
    return str(exc)[:300]
