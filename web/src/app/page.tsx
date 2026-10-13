"use client";

import Link from "next/link";
import { useState } from "react";

import { DayChart } from "@/components/DayChart";
import { RunsTable } from "@/components/RunsTable";
import { useNow, useStatus } from "@/components/Status";
import { Card, Empty, ErrorBox, Loading, Meter, Page, Stat, Table, Tabs, WorkflowTag, td, tdNum } from "@/components/ui";
import { POLL_MS, useApi } from "@/lib/api";
import { compact, ms, num, pct, usd } from "@/lib/format";
import type { Metrics, Run, WorkflowKey } from "@/lib/types";
import { WORKFLOW_KEYS } from "@/lib/workflows";

const RANGES = [
  { value: "7", label: "7 days" },
  { value: "14", label: "14 days" },
  { value: "30", label: "30 days" },
  { value: "all", label: "All time" },
] as const;
type Range = (typeof RANGES)[number]["value"];

export default function OverviewPage() {
  const [range, setRange] = useState<Range>("14");
  const now = useNow();
  const metrics = useApi<Metrics>(range === "all" ? "/ops/metrics" : `/ops/metrics?days=${range}`, { interval: POLL_MS });
  const runs = useApi<Run[]>("/ops/runs?limit=8", { interval: POLL_MS });
  const { health } = useStatus();
  const m = metrics.data;
  const t = m?.totals;
  const autoRate = t && t.runs ? t.auto / t.runs : null;
  const free = health?.free_only ?? true;

  return (
    <Page
      title="Overview"
      subtitle="Five n8n workflows for Kestrel Print Co.: what ran, what the AI handled on its own and what is waiting for a person."
      actions={<Tabs value={range} onChange={setRange} options={RANGES.map((r) => ({ value: r.value, label: r.label }))} />}
    >
      {metrics.error && !m ? <ErrorBox>Could not load metrics: {metrics.error}</ErrorBox> : null}
      {!m && !metrics.error ? <Loading>Loading metrics</Loading> : null}
      {m && t ? (
        <div className="space-y-5">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            <Stat label="Runs" value={compact(t.runs)} hint={`${num(t.duplicates)} duplicate deliveries absorbed`} />
            <Stat label="Auto-handled" value={pct(autoRate)} tone="green" hint={`${num(t.auto)} of ${num(t.runs)} without a person`} />
            <Stat label="Waiting for review" value={num(t.pending_review)} tone={t.pending_review ? "amber" : undefined} hint="Open the review queue" href="/review" />
            <Stat label="Open dead letters" value={num(t.open_dead_letters)} tone={t.open_dead_letters ? "red" : undefined} hint={`${num(t.errors)} failed runs in range`} href="/dead-letters" />
            <Stat label="Model calls" value={compact(t.llm_calls)} hint={`${num(t.cached_calls)} cached, ${num(t.invalid_outputs)} invalid outputs`} />
            <Stat label="Model cost" value={usd(t.cost_usd)} hint={t.cost_usd === 0 && free ? "free models" : free ? "free-only guard on" : "paid models allowed"} />
          </div>

          <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
            <Card title="Runs per day" aside={<span>UTC days</span>}>
              <DayChart days={m.days} span={range === "all" ? null : Number(range)} now={now} />
            </Card>
            <Card title="AI gateway">
              <div className="space-y-4">
                <div>
                  <div className="text-[11.5px] text-muted">Active model</div>
                  <div className="mono mt-0.5 text-[13px] break-all text-ink">{health?.model ?? "—"}</div>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {health ? (
                      health.free_only ? (
                        <span className="inline-flex items-center gap-1 rounded-md bg-ok-soft px-1.5 py-0.5 text-[11.5px] font-medium text-ok ring-1 ring-ok/15 ring-inset">
                          <span className="h-1.5 w-1.5 rounded-full bg-ok-bar" />
                          Free-only guard on
                        </span>
                      ) : (
                        <span className="rounded-md bg-warn-soft px-1.5 py-0.5 text-[11.5px] font-medium text-warn ring-1 ring-warn/15 ring-inset">Paid models allowed</span>
                      )
                    ) : null}
                  </div>
                </div>
                <div>
                  <div className="mb-1.5 text-[11.5px] text-muted">Calls by model (served)</div>
                  <ModelList models={m.models} />
                </div>
              </div>
            </Card>
          </div>

          <Card title="Workflows" pad={false} aside={<Link href="/workflows" className="text-brand hover:underline">How they work</Link>}>
            <Table
              head={["Workflow", "Runs", "Auto", "Review", "Errors", "Duplicates", "Latency p50", "Latency p95", "LLM calls", "Cost / run"]}
              align={["left", "right", "left", "right", "right", "right", "right", "right", "right", "right"]}
            >
              {WORKFLOW_KEYS.map((key: WorkflowKey) => {
                const w = m.workflows[key];
                if (!w) return null;
                return (
                  <tr key={key} className="hover:bg-zinc-50">
                    <td className={td}>
                      <Link href={`/runs?workflow=${key}`} className="hover:text-brand">
                        <WorkflowTag workflow={key} />
                      </Link>
                    </td>
                    <td className={`${tdNum} font-medium`}>{num(w.runs)}</td>
                    <td className={`${td} min-w-[150px]`}>
                      <div className="flex items-center gap-2">
                        <Meter value={w.auto_rate} tone="green" className="w-20" />
                        <span className="w-10 text-right text-[12.5px] tnum text-ink-soft">{pct(w.auto_rate)}</span>
                      </div>
                    </td>
                    <td className={tdNum}>
                      {num(w.review)}
                      {w.pending_review ? <span className="ml-1 text-[11.5px] text-warn">({w.pending_review} open)</span> : null}
                    </td>
                    <td className={`${tdNum} ${w.errors ? "font-medium text-bad" : "text-faint"}`}>{num(w.errors)}</td>
                    <td className={`${tdNum} ${w.duplicates ? "" : "text-faint"}`}>{num(w.duplicates)}</td>
                    <td className={tdNum}>{ms(w.latency_p50_ms)}</td>
                    <td className={tdNum}>{ms(w.latency_p95_ms)}</td>
                    <td className={tdNum}>{num(w.llm_calls)}</td>
                    <td className={`${tdNum} text-ink-soft`}>{usd(w.cost_per_run_usd)}</td>
                  </tr>
                );
              })}
            </Table>
            <div className="border-t border-line-soft px-4 py-2 text-[11.5px] text-muted">
              Latency is end to end for runs finished without a person (auto and escalated). Review counts runs that needed a person: pending, approved, rejected or escalated.
            </div>
          </Card>

          <Card title="Latest runs" pad={false} aside={<Link href="/runs" className="text-brand hover:underline">All runs</Link>}>
            {runs.data ? runs.data.length ? <RunsTable runs={runs.data} compact /> : <div className="p-4"><Empty title="No runs yet">Send an email to hello@kestrelprint.example or run the demo traffic to see runs here.</Empty></div> : runs.error ? <div className="p-4"><ErrorBox>{runs.error}</ErrorBox></div> : <Loading />}
          </Card>
        </div>
      ) : null}
    </Page>
  );
}

function ModelList({ models }: { models: Record<string, number> }) {
  const entries = Object.entries(models).sort((a, b) => b[1] - a[1]);
  const total = entries.reduce((s, [, n]) => s + n, 0);
  if (!entries.length) return <div className="text-[13px] text-muted">No model calls in this range.</div>;
  return (
    <div className="space-y-2">
      {entries.slice(0, 6).map(([model, n]) => (
        <div key={model}>
          <div className="flex items-baseline justify-between gap-2 text-[12.5px]">
            <span className="mono truncate text-ink-soft" title={model}>
              {model}
            </span>
            <span className="shrink-0 tnum text-muted">{num(n)}</span>
          </div>
          <Meter value={n / total} tone="brand" className="mt-1" />
        </div>
      ))}
    </div>
  );
}
