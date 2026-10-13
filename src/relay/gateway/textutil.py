"""Small text helpers shared by the gateway tasks: number grounding, prompt-injection screening, hidden-text removal."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_AI_DIRECTED = re.compile(
    r"(?i)\b(?:ignore (?:all |your |the )?(?:previous|prior|above) (?:instructions|rules)|"
    r"(?:note|message|notice|instructions?) (?:to|for) (?:ai|llm|language model)s?|"
    r"ai (?:assistants?|agents?|scrapers?|systems?|tools?)\b|"
    r"(?:system|developer) (?:prompt|notice|message)|"
    r"you are (?:now )?(?:an?|the) (?:ai|assistant|language model)|"
    r"(?:classify|label|rate|mark|route) (?:this|the) (?:email|message|lead|invoice) as)"
)
_NUMBER = re.compile(r"(?<![\w.,])(\d{1,3}(?:[.,\s']\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)(?![\w])")


def ai_directed_sentences(text: str) -> list[str]:
    """Sentences that address an AI system (prompt injection in data). Matching is deliberately broad: a hit sends
    the item to a human, it never changes what the automation does."""
    sentences = re.split(r"(?<=[.!?:])\s+|\n+", text)
    return [s.strip() for s in sentences if s.strip() and _AI_DIRECTED.search(s)]


def parse_number(token: str) -> Decimal | None:
    """'1,500' / '1.500' / '1 500' / '2.205,18' / '2,205.18' / '852.54' -> Decimal."""
    token = token.strip().replace(" ", " ").replace("'", "").replace(" ", "")
    if not token or not any(ch.isdigit() for ch in token):
        return None
    if "," in token and "." in token:
        if token.rfind(",") > token.rfind("."):
            token = token.replace(".", "").replace(",", ".")
        else:
            token = token.replace(",", "")
    elif "," in token:
        head, _, tail = token.rpartition(",")
        token = token.replace(",", "") if len(tail) == 3 else head.replace(",", "") + "." + tail
    elif "." in token:
        head, _, tail = token.rpartition(".")
        if len(tail) == 3 and head.replace(".", "").isdigit():
            token = token.replace(".", "")
    try:
        return Decimal(token)
    except InvalidOperation:
        return None


def numbers_in(text: str) -> set[Decimal]:
    values: set[Decimal] = set()
    for match in _NUMBER.finditer(text):
        value = parse_number(match.group(1))
        if value is not None:
            values.add(value.normalize())
    return values


def grounded_number(value: float | int | None, text: str) -> bool:
    if value is None:
        return True
    try:
        target = Decimal(str(value)).normalize()
    except InvalidOperation:
        return False
    found = numbers_in(text)
    if target in found:
        return True
    # "10k", "20 thousand" are rare in the data; accept a k-suffix explicitly.
    return bool(target % 1000 == 0 and re.search(rf"(?i)\b{int(target // 1000)}\s?k\b", text))


def grounded_text(value: str | None, text: str) -> bool:
    if not value:
        return True
    normalize = lambda s: re.sub(r"[\s\-_.]", "", s).lower()  # noqa: E731
    return normalize(value) in normalize(text)
