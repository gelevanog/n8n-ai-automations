"""Scoring helpers for the evaluation: field comparison, percentiles, gold tiers."""

from __future__ import annotations

import re
import unicodedata
from statistics import median
from typing import Any

from relay.gateway.leads import Icp
from relay.gateway.schemas import CompanyProfile


def norm_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").replace("ı", "i")).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\b(gmbh|ltd|b\.?v\.?|s\.?l\.?|s\.?r\.?l\.?|s\.?r\.?o\.?|aps|inc|co|v\.?o\.?f\.?|ab)\b\.?", "", text)
    return re.sub(r"[^a-z0-9]", "", text)


def same_text(predicted: Any, gold: Any) -> bool:
    """Names and companies: case, accents, punctuation and legal suffixes ignored; one may contain the other
    (e.g. "Fernway Tea" vs "Fernway Tea GmbH")."""
    if gold is None:
        return predicted in (None, "")
    p, g = norm_text(predicted), norm_text(gold)
    return bool(p) and (p == g or (len(p) >= 4 and (p in g or g in p)))


def same_id(predicted: Any, gold: Any) -> bool:
    if gold is None:
        return predicted in (None, "")
    return re.sub(r"[\s\-_.]", "", str(predicted or "")).upper() == re.sub(r"[\s\-_.]", "", str(gold)).upper()


def same_number(predicted: Any, gold: Any, tolerance: float = 0.011) -> bool:
    if gold is None:
        return predicted is None
    try:
        return predicted is not None and abs(float(predicted) - float(gold)) <= tolerance
    except (TypeError, ValueError):
        return False


def same_value(predicted: Any, gold: Any) -> bool:
    if gold is None:
        return predicted in (None, "")
    return str(predicted).strip().lower() == str(gold).strip().lower()


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return round(ordered[index], 2)


def p50(values: list[float]) -> float | None:
    return round(median(values), 2) if values else None


def rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def gold_tier(lead: dict[str, Any], icp: Icp, hot: int, warm: int) -> str:
    """The tier a correct enrichment would produce: the same ICP rules applied to the gold facts."""
    gold = lead["gold"]
    if gold.get("disqualified"):
        return "disqualified"
    if not gold.get("site_found"):
        profile = CompanyProfile()
    else:
        profile = CompanyProfile(industry=gold["industry"], employees_band=gold["employees_band"],
                                 country=gold["country"], sells_online=gold["sells_online"])
    score, _ = icp.score(profile, lead.get("quantity"))
    return icp.tier(score, hot, warm)


def fact_hit(answer: str, fact: str) -> bool:
    """A fact is "a|b|c": any alternative (case-insensitive) in the answer counts."""
    text = answer.lower().replace(" ", " ").replace(" ", " ")
    return any(alt.strip().lower() in text for alt in fact.split("|"))
