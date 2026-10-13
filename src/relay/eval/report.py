"""results/summary.json: the headline numbers of every committed evaluation run, for the README and the dashboard."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

HEADLINE: dict[str, list[tuple[str, str]]] = {
    "triage": [("category_accuracy", "routing accuracy (all emails)"),
               ("auto_handled_rate", "auto-handled"),
               ("auto_handled_accuracy", "accuracy of auto-handled"),
               ("field_accuracy", "field extraction accuracy"),
               ("latency_p50_s", "latency p50 (s)")],
    "invoices": [("field_accuracy", "field extraction accuracy"),
                 ("decision_accuracy", "post / review decision accuracy"),
                 ("problem_invoices_auto_posted", "problem invoices auto-posted"),
                 ("auto_post_rate", "auto-posted"),
                 ("latency_p50_s", "latency p50 (s)")],
    "leads": [("profile_accuracy", "enrichment accuracy"),
              ("tier_accuracy", "tier accuracy"),
              ("drafts_passing_checks", "drafts passing checks"),
              ("latency_p50_s", "latency p50 (s)")],
    "faq": [("decision_accuracy", "auto-reply / escalate accuracy"),
            ("auto_reply_rate", "auto-replied"),
            ("unsafe_auto_replies", "unsafe auto-replies"),
            ("auto_replies_with_all_facts", "auto-replies with all facts"),
            ("latency_p50_s", "latency p50 (s)")],
}


def write_summary(directory: Path) -> dict[str, Any]:
    runs: dict[str, dict[str, Any]] = {}
    headline: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*_*.json")):
        if path.name in {"summary.json", "smoke.json"}:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if not isinstance(data, dict) or "suite" not in data or "summary" not in data:
            continue
        key = path.stem
        runs[key] = {"suite": data["suite"], "name": data.get("name"), "model": data.get("model"),
                     "run_at": data.get("run_at"), "summary": data["summary"]}
        for metric, label in HEADLINE.get(data["suite"], []):
            value = data["summary"].get(metric)
            if isinstance(value, list):
                value = len(value)
            headline.append({"suite": data["suite"], "run": data.get("name"), "metric": label, "value": value})
    summary = {"runs": runs, "headline": headline}
    (directory / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return summary
