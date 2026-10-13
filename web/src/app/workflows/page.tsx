"use client";

import Link from "next/link";
import type { ReactNode } from "react";

import { Card, ExtLink, Page, StatusPill } from "@/components/ui";
import { N8N_URL, useApi } from "@/lib/api";
import { ms, num, pct } from "@/lib/format";
import type { Metrics } from "@/lib/types";
import { ERROR_HANDLER, n8nWorkflowUrl, WORKFLOW_KEYS, WORKFLOWS } from "@/lib/workflows";

function Row({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-1 gap-1 py-3 sm:grid-cols-[120px_minmax(0,1fr)] sm:gap-4">
      <div className="text-[11.5px] font-semibold tracking-wide text-muted uppercase sm:pt-0.5">{title}</div>
      <div className="min-w-0 text-[13px] leading-relaxed text-ink-soft">{children}</div>
    </div>
  );
}

export default function WorkflowsPage() {
  const metrics = useApi<Metrics>("/ops/metrics");
  return (
    <Page
      title="Workflows"
      subtitle={
        <>
          Five n8n workflows plus an error handler, imported from <span className="mono text-[12.5px]">n8n/workflows/</span>. n8n owns the triggers, branching, retries and waits; the AI gateway behind each step returns validated JSON and a decision the workflow branches on.
        </>
      }
      actions={
        <a href={N8N_URL} target="_blank" rel="noreferrer" className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-line bg-white px-3.5 text-[13px] font-medium text-ink shadow-sm hover:bg-zinc-50">
          Open the n8n editor
        </a>
      }
    >
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        {WORKFLOW_KEYS.map((key) => {
          const w = WORKFLOWS[key];
          const m = metrics.data?.workflows[key];
          return (
            <Card
              key={key}
              title={
                <span className="flex items-center gap-2">
                  <span className={`h-2.5 w-2.5 rounded-[3px] ${w.dot}`} />
                  <span className="text-[14px]">{w.name}</span>
                  <span className="mono text-[11.5px] font-normal text-faint">{w.n8nId}</span>
                </span>
              }
              aside={<ExtLink href={n8nWorkflowUrl(key)}>Open in n8n</ExtLink>}
            >
              <p className="text-[13.5px] text-ink">{w.tagline}</p>
              {m ? (
                <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-[12.5px] text-muted">
                  <Link href={`/runs?workflow=${key}`} className="hover:text-brand">
                    <span className="font-medium text-ink tnum">{num(m.runs)}</span> runs
                  </Link>
                  <span>
                    <span className="font-medium text-ink tnum">{pct(m.auto_rate)}</span> auto-handled
                  </span>
                  <span>
                    p50 <span className="font-medium text-ink tnum">{ms(m.latency_p50_ms)}</span>
                  </span>
                  {m.pending_review ? (
                    <Link href={`/review?workflow=${key}`} className="inline-flex items-center gap-1.5 hover:text-brand">
                      <StatusPill status="review" /> {m.pending_review}
                    </Link>
                  ) : null}
                </div>
              ) : null}
              <div className="mt-2 divide-y divide-line-soft">
                <Row title="Trigger">{w.trigger}</Row>
                <Row title="Steps">
                  <ol className="space-y-1">
                    {w.steps.map((s, i) => (
                      <li key={i} className="flex gap-2.5">
                        <span className="mt-[1px] inline-flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full bg-zinc-100 text-[10.5px] font-semibold text-ink-soft tnum">{i + 1}</span>
                        <span>{s}</span>
                      </li>
                    ))}
                  </ol>
                </Row>
                <Row title="Human gate">{w.gate}</Row>
                <Row title="Writes to">
                  <ul className="space-y-0.5">
                    {w.writes.map((x) => (
                      <li key={x.system}>
                        <span className="font-medium text-ink">{x.system}</span>: {x.what}
                      </li>
                    ))}
                  </ul>
                </Row>
                <Row title="AI gateway">
                  <span className="mono text-[12px]">/ai/v1/{w.ai.split(", ").join(", /ai/v1/")}</span>
                </Row>
              </div>
            </Card>
          );
        })}
        <Card
          title={
            <span className="flex items-center gap-2">
              <span className="h-2.5 w-2.5 rounded-[3px] bg-rose-500" />
              <span className="text-[14px]">{ERROR_HANDLER.name}</span>
              <span className="mono text-[11.5px] font-normal text-faint">{ERROR_HANDLER.n8nId}</span>
            </span>
          }
          aside={<ExtLink href={`${N8N_URL}/workflow/${ERROR_HANDLER.n8nId}`}>Open in n8n</ExtLink>}
        >
          <p className="text-[13.5px] text-ink">{ERROR_HANDLER.description}</p>
          <div className="mt-2 divide-y divide-line-soft">
            <Row title="Trigger">Error Trigger: any Relay workflow execution that fails after its node retries.</Row>
            <Row title="Writes to">
              <span className="font-medium text-ink">Relay</span>: dead letter with node, error and the run&apos;s input.{" "}
              <span className="font-medium text-ink">Chat</span>: #ops-alerts message.
            </Row>
            <Row title="Recovery">
              <Link href="/dead-letters" className="text-brand hover:underline">
                Dead letters
              </Link>{" "}
              replays the stored input with the same idempotency key, so a retried run never double-posts.
            </Row>
          </div>
        </Card>
      </div>
    </Page>
  );
}
