"""The deterministic offline model. Every Relay prompt starts with a ``RELAY_TASK: <name>`` line; the fake model
dispatches on it to a rule-based implementation of the same task (relay.gateway.fake_rules). It makes CI, the tests
and the zero-key Docker demo work without any API key, and it is the "rules only" baseline in the evaluation."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence

from relay.llm.base import Completion, LLMError, Message

TASK_MARKER = "RELAY_TASK:"
_TASK = re.compile(r"^RELAY_TASK:\s*([a-z_]+)", re.MULTILINE)

FakeHandler = Callable[[Sequence[Message]], str]


def task_of(messages: Sequence[Message]) -> str:
    for message in messages:
        if message["role"] == "system" and (match := _TASK.search(message["content"])):
            return match.group(1)
    return ""


class FakeModel:
    def __init__(self, handlers: Mapping[str, FakeHandler]) -> None:
        self.handlers = dict(handlers)
        self.calls = 0

    @property
    def label(self) -> str:
        return "fake/rules"

    @property
    def is_local(self) -> bool:
        return True

    def complete(self, messages: Sequence[Message], *, max_tokens: int, temperature: float = 0.0) -> Completion:
        self.calls += 1
        task = task_of(messages)
        handler = self.handlers.get(task)
        if handler is None:
            raise LLMError(f"the offline model has no handler for task {task!r}")
        text = handler(messages)
        words = sum(len(m["content"].split()) for m in messages)
        return Completion(text=text, model=self.label, input_tokens=int(words * 1.3), output_tokens=len(text) // 4)
