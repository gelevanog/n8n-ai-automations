"use client";

import { Card, Empty, ErrorBox, Loading, Page, Pill, Table, td, tdNum } from "@/components/ui";
import { useApi } from "@/lib/api";
import { DASH, label, num, pct, seconds, usd, whenFull } from "@/lib/format";
import type { EvalRun, EvalSummary } from "@/lib/types";

type Kind = "rate" | "count" | "seconds" | "usd" | "text" | "list" | "bool" | "zeroGood";
type Metric = { key: string; label: string; kind: Kind; of?: string };

const COMMON: Metric[] = [
  { key: "items", label: "Items", kind: "count" },
  { key: "error_rate", label: "Error rate", kind: "rate" },
  { key: "latency_p50_s", label: "Latency p50", kind: "seconds" },
  { key: "latency_p95_s", label: "Latency p95", kind: "seconds" },
  { key: "llm_calls_per_item", label: "Model calls per item", kind: "count" },
  { key: "invalid_model_outputs", label: "Invalid model outputs", kind: "zeroGood" },
  { key: "tokens", label: "Tokens", kind: "count" },
  { key: "cost_usd", label: "Cost", kind: "usd" },
];

const SUITES: Record<string, { title: string; blurb: string; metrics: Metric[] }> = {
  triage: {
    title: "Inbox triage",
    blurb: "80 emails with known categories, including hard cases (mixed intent, forwarded threads, text addressed to an AI).",
    metrics: [
      { key: "category_accuracy", label: "Routing accuracy (all emails)", kind: "rate" },
      { key: "auto_handled_rate", label: "Auto-handled", kind: "rate" },
      { key: "auto_handled_accuracy", label: "Accuracy of auto-handled", kind: "rate" },
      { key: "misrouted_automatically", label: "Misrouted without review", kind: "zeroGood" },
      { key: "wrong_caught_by_review", label: "Wrong categories caught by review", kind: "count", of: "wrong_category" },
      { key: "review_rate", label: "Sent to review", kind: "rate" },
      { key: "field_accuracy", label: "Field extraction accuracy", kind: "rate" },
      { key: "hard_accuracy", label: "Accuracy on hard items", kind: "rate" },
      ...COMMON,
    ],
  },
  invoices: {
    title: "Invoice intake",
    blurb: "40 generated PDF invoices; 13 carry a planted problem (wrong totals or VAT, duplicates, over the limit, bad dates).",
    metrics: [
      { key: "field_accuracy", label: "Field extraction accuracy", kind: "rate" },
      { key: "all_fields_right", label: "Invoices with every field right", kind: "text" },
      { key: "decision_accuracy", label: "Post / review decision accuracy", kind: "rate" },
      { key: "problem_invoices_auto_posted", label: "Problem invoices auto-posted", kind: "zeroGood" },
      { key: "clean_invoices_sent_to_review", label: "Clean invoices sent to review", kind: "count" },
      { key: "auto_post_rate", label: "Auto-posted", kind: "rate" },
      { key: "double_posted", label: "Double-posted bills", kind: "list" },
      ...COMMON,
    ],
  },
  leads: {
    title: "Lead enrichment",
    blurb: "30 web-form leads with fixture company sites, including competitors, non-buyers and a site with text aimed at AI models.",
    metrics: [
      { key: "profile_accuracy", label: "Enrichment accuracy", kind: "rate" },
      { key: "tier_accuracy", label: "Tier accuracy", kind: "rate" },
      { key: "disqualified_caught", label: "Disqualified leads caught", kind: "text" },
      { key: "drafts_passing_checks", label: "Drafts passing checks", kind: "count", of: "drafts" },
      { key: "research_queue", label: "Sent to research", kind: "count" },
      { key: "replies_sent_after_approval", label: "Replies sent after approval", kind: "count" },
      ...COMMON,
    ],
  },
  faq: {
    title: "Support FAQ",
    blurb: "40 customer questions: answerable from the knowledge base, or ones a person must handle (refunds, legal, account-specific, manipulation).",
    metrics: [
      { key: "decision_accuracy", label: "Auto-reply / escalate accuracy", kind: "rate" },
      { key: "auto_reply_rate", label: "Auto-replied", kind: "rate" },
      { key: "unsafe_auto_replies", label: "Unsafe auto-replies", kind: "list" },
      { key: "over_escalated", label: "Over-escalated", kind: "list" },
      { key: "auto_replies_with_all_facts", label: "Auto-replies with all facts", kind: "text" },
      { key: "auto_replies_citing_gold_article", label: "Auto-replies citing the right article", kind: "text" },
      { key: "retrieval_recall_at_4", label: "Retrieval recall at 4", kind: "rate" },
      ...COMMON,
    ],
  },
  idempotency: {
    title: "Idempotency",
    blurb: "25 deliveries across four workflows, then every one sent again: the second delivery must not create a second record.",
    metrics: [
      { key: "deliveries", label: "Deliveries", kind: "count" },
      { key: "second_delivery_answered_as_duplicate", label: "Second delivery answered as duplicate", kind: "count", of: "deliveries" },
      { key: "same_run_id", label: "Same run id returned", kind: "count", of: "deliveries" },
      { key: "new_records_from_resend", label: "New records from the resend", kind: "zeroGood" },
      { key: "duplicate_p50_seconds", label: "Duplicate answer p50", kind: "seconds" },
    ],
  },
  chaos: {
    title: "Failure handling",
    blurb: "Accounting fails at random, then goes down; failed runs must reach the dead-letter queue and replay exactly once.",
    metrics: [
      { key: "phase1_posted", label: "Posted despite injected 503s", kind: "count", of: "phase1_invoices" },
      { key: "phase1_injected_503s", label: "Injected 503s (phase 1)", kind: "count" },
      { key: "phase2_failed_runs", label: "Failed runs while accounting was down", kind: "count", of: "phase2_invoices" },
      { key: "dead_letters", label: "Dead letters recorded", kind: "count" },
      { key: "ops_alerts", label: "#ops-alerts messages", kind: "count" },
      { key: "replayed_ok", label: "Replayed successfully", kind: "count", of: "dead_letters" },
      { key: "bills_posted", label: "Bills posted", kind: "count", of: "expected_bills" },
      { key: "missing_bills", label: "Missing bills", kind: "list" },
      { key: "double_posted", label: "Double-posted bills", kind: "list" },
    ],
  },
  digest: {
    title: "KPI digest",
    blurb: "One digest over the run log; every number in the text must come from the SQL result.",
    metrics: [
      { key: "ok", label: "Digest written", kind: "bool" },
      { key: "numbers_ok", label: "All numbers supported", kind: "bool" },
      { key: "posted_to_chat", label: "Posted to #ops", kind: "bool" },
      { key: "seconds", label: "Duration", kind: "seconds" },
    ],
  },
};

function headlineValue(metric: string, value: unknown): string {
  if (typeof value === "number") {
    if (/^(problem|unsafe|drafts)/i.test(metric)) return num(value);
    if (/accuracy|rate|auto-handled|auto-posted|auto-replied/i.test(metric) && value <= 1) return pct(value, 1);
    if (/\(s\)/.test(metric)) return seconds(value);
    return num(value, Number.isInteger(value) ? 0 : 2);
  }
  return value === null || value === undefined ? DASH : String(value);
}

const ORDER = ["triage", "invoices", "leads", "faq", "idempotency", "chaos", "digest"];

function cell(summary: Record<string, unknown>, m: Metric): { text: string; tone?: "good" | "bad" } {
  const v = summary[m.key];
  if (v === undefined || v === null) return { text: DASH };
  switch (m.kind) {
    case "rate":
      return { text: typeof v === "number" ? pct(v, 1) : String(v) };
    case "seconds":
      return { text: typeof v === "number" ? seconds(v) : String(v) };
    case "usd":
      return { text: typeof v === "number" ? usd(v) : String(v) };
    case "bool":
      return v ? { text: "yes", tone: "good" } : { text: "no", tone: "bad" };
    case "list": {
      const n = Array.isArray(v) ? v.length : typeof v === "number" ? v : 0;
      return { text: num(n), tone: n ? "bad" : "good" };
    }
    case "zeroGood": {
      const n = typeof v === "number" ? v : Array.isArray(v) ? v.length : Number(v);
      return { text: num(n), tone: n ? "bad" : "good" };
    }
    case "count": {
      if (typeof v !== "number") return { text: String(v) };
      const of = m.of ? summary[m.of] : undefined;
      return { text: typeof of === "number" ? `${num(v, Number.isInteger(v) ? 0 : 2)} of ${num(of)}` : num(v, Number.isInteger(v) ? 0 : 2) };
    }
    default:
      return { text: typeof v === "object" ? JSON.stringify(v) : String(v) };
  }
}

function FieldTable({ runs }: { runs: [string, EvalRun][] }) {
  const names = Array.from(new Set(runs.flatMap(([, r]) => Object.keys((r.summary.fields as Record<string, unknown>) ?? {}))));
  if (!names.length) return null;
  return (
    <div className="border-t border-line">
      <div className="px-4 pt-3 pb-1 text-[11.5px] font-semibold tracking-wide text-muted uppercase">Field accuracy</div>
      <Table dense head={["Field", ...runs.map(([, r]) => r.name ?? "run")]} align={["left", ...runs.map(() => "right" as const)]}>
        {names.map((f) => (
          <tr key={f}>
            <td className={`${td} mono text-[12px]`}>{f}</td>
            {runs.map(([key, r]) => {
              const field = ((r.summary.fields as Record<string, { accuracy?: number; correct?: number; scored?: number }>) ?? {})[f];
              return (
                <td key={key} className={tdNum} title={field ? `${field.correct ?? 0} of ${field.scored ?? 0}` : undefined}>
                  {field?.accuracy !== undefined ? <span className={field.accuracy < 0.8 ? "text-warn" : ""}>{pct(field.accuracy, 1)}</span> : DASH}
                </td>
              );
            })}
          </tr>
        ))}
      </Table>
    </div>
  );
}

function IssueTable({ runs }: { runs: [string, EvalRun][] }) {
  const names = Array.from(new Set(runs.flatMap(([, r]) => Object.keys((r.summary.per_issue as Record<string, unknown>) ?? {}))));
  if (!names.length) return null;
  return (
    <div className="border-t border-line">
      <div className="px-4 pt-3 pb-1 text-[11.5px] font-semibold tracking-wide text-muted uppercase">Planted problems flagged</div>
      <Table dense head={["Problem", ...runs.map(([, r]) => r.name ?? "run")]} align={["left", ...runs.map(() => "right" as const)]}>
        {names.map((issue) => (
          <tr key={issue}>
            <td className={td}>{label(issue)}</td>
            {runs.map(([key, r]) => {
              const x = ((r.summary.per_issue as Record<string, { planted?: number; flagged?: number }>) ?? {})[issue];
              const ok = x && x.flagged === x.planted;
              return (
                <td key={key} className={`${tdNum} ${x ? (ok ? "text-ok" : "text-bad") : ""}`}>
                  {x ? `${x.flagged ?? 0} of ${x.planted ?? 0}` : DASH}
                </td>
              );
            })}
          </tr>
        ))}
      </Table>
    </div>
  );
}

function SuiteCard({ suite, runs }: { suite: string; runs: [string, EvalRun][] }) {
  const spec = SUITES[suite];
  const metrics: Metric[] =
    spec?.metrics ??
    Array.from(new Set(runs.flatMap(([, r]) => Object.keys(r.summary))))
      .filter((k) => runs.some(([, r]) => ["number", "string", "boolean"].includes(typeof r.summary[k])))
      .map((k) => ({ key: k, label: label(k), kind: "text" as Kind }));
  const shown = metrics.filter((m) => runs.some(([, r]) => r.summary[m.key] !== undefined));
  return (
    <Card title={spec?.title ?? label(suite)} pad={false} aside={<span>{runs.length} run{runs.length > 1 ? "s" : ""}</span>}>
      {spec?.blurb ? <div className="border-b border-line-soft px-4 py-2.5 text-[12.5px] text-muted">{spec.blurb}</div> : null}
      <Table
        head={[
          "Metric",
          ...runs.map(([key, r]) => (
            <div key={key} className="text-right normal-case">
              <div className="font-semibold text-ink">{r.name ?? key}</div>
              <div className="mono max-w-[220px] truncate text-[11px] font-normal text-muted" title={r.model ?? ""}>
                {r.model ?? DASH}
              </div>
              <div className="text-[11px] font-normal text-faint">{whenFull(r.run_at)}</div>
            </div>
          )),
        ]}
        align={["left", ...runs.map(() => "right" as const)]}
      >
        {shown.map((m) => (
          <tr key={m.key}>
            <td className={`${td} text-ink-soft`}>{m.label}</td>
            {runs.map(([key, r]) => {
              const c = cell(r.summary, m);
              return (
                <td key={key} className={`${tdNum} ${c.tone === "bad" ? "font-medium text-bad" : c.tone === "good" ? "text-ok" : "text-ink"}`}>
                  {c.text}
                </td>
              );
            })}
          </tr>
        ))}
      </Table>
      <FieldTable runs={runs} />
      {suite === "invoices" ? <IssueTable runs={runs} /> : null}
    </Card>
  );
}

export default function EvaluationPage() {
  const evals = useApi<EvalSummary>("/ops/eval");
  const data = evals.data;
  const grouped = new Map<string, [string, EvalRun][]>();
  for (const [key, run] of Object.entries(data?.runs ?? {})) {
    const list = grouped.get(run.suite) ?? [];
    list.push([key, run]);
    grouped.set(run.suite, list);
  }
  for (const list of grouped.values()) list.sort((a, b) => (a[1].name ?? "").localeCompare(b[1].name ?? ""));
  const suites = Array.from(grouped.keys()).sort((a, b) => (ORDER.indexOf(a) === -1 ? 99 : ORDER.indexOf(a)) - (ORDER.indexOf(b) === -1 ? 99 : ORDER.indexOf(b)));
  const models = Array.from(new Set(Object.values(data?.runs ?? {}).map((r) => r.model).filter(Boolean)));

  return (
    <Page
      title="Evaluation"
      subtitle={
        <>
          Every workflow is measured end to end through n8n on labelled synthetic data (<span className="mono text-[12.5px]">relay eval</span>). Results come from{" "}
          <span className="mono text-[12.5px]">results/summary.json</span>; one column per committed run.
        </>
      }
      actions={models.length ? models.map((m) => <Pill key={m}>{m}</Pill>) : null}
    >
      {evals.status === 404 ? (
        <Card>
          <Empty title="No evaluation results yet">
            Run <span className="mono">relay eval</span> (or <span className="mono">make eval</span>) against the running stack. The summary is written to results/summary.json and shows up here.
          </Empty>
        </Card>
      ) : evals.error && !data ? (
        <ErrorBox>Could not load the evaluation: {evals.error}</ErrorBox>
      ) : !data ? (
        <Loading />
      ) : suites.length === 0 ? (
        <Card>
          <Empty title="The summary has no runs" />
        </Card>
      ) : (
        <div className="space-y-5">
          {data.headline?.length ? (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {suites
                .map((suite) => {
                  const rows = data.headline.filter((h) => h.suite === suite && !/latency/i.test(h.metric));
                  const metric = rows[0]?.metric;
                  return metric ? { suite, metric, values: rows.filter((h) => h.metric === metric) } : null;
                })
                .filter((x): x is { suite: string; metric: string; values: EvalSummary["headline"] } => x !== null)
                .map((h) => (
                  <div key={h.suite} className="min-w-0 rounded-xl border border-line bg-card px-4 py-3 shadow-[0_1px_2px_rgba(16,24,40,0.04)]">
                    <div className="truncate text-[12px] font-medium text-ink-soft">{SUITES[h.suite]?.title ?? label(h.suite)}</div>
                    <div className="truncate text-[11.5px] text-muted" title={h.metric}>
                      {h.metric}
                    </div>
                    <div className="mt-2 flex flex-wrap items-baseline gap-x-4 gap-y-1">
                      {h.values.map((v, i) => (
                        <div key={i} className="flex items-baseline gap-1.5">
                          <span className="text-[22px] leading-tight font-semibold tracking-tight tnum">{headlineValue(h.metric, v.value)}</span>
                          <span className="text-[11.5px] text-muted">{v.run}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
            </div>
          ) : null}
          <div className="grid grid-cols-1 gap-5 2xl:grid-cols-2">
            {suites.map((s) => (
              <SuiteCard key={s} suite={s} runs={grouped.get(s) ?? []} />
            ))}
          </div>
        </div>
      )}
    </Page>
  );
}
