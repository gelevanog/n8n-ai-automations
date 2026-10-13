"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import { Card, Empty, ErrorBox, ExtLink, Fields, JsonBlock, Loading, Page, Pill, StatusPill, Table, WorkflowTag, td, tdNum } from "@/components/ui";
import { POLL_MS, useApi } from "@/lib/api";
import { attachmentNames, clock, DASH, ms, num, stripBinary, usd, whenFull } from "@/lib/format";
import type { RunDetail } from "@/lib/types";
import { n8nExecutionUrl, REVIEW_KINDS, routeLabel } from "@/lib/workflows";

export default function RunPage() {
  const { id } = useParams<{ id: string }>();
  const run = useApi<RunDetail>(`/ops/runs/${id}`, { interval: POLL_MS });
  const r = run.data;

  if (!r) {
    return (
      <Page title="Run" subtitle={<Link href="/runs" className="text-brand hover:underline">Back to runs</Link>}>
        {run.error ? <ErrorBox>{run.status === 404 ? `Run ${id} was not found.` : `Could not load the run: ${run.error}`}</ErrorBox> : <Loading />}
      </Page>
    );
  }

  const files = attachmentNames(r.input);
  const calls = r.calls ?? [];
  const execUrl = n8nExecutionUrl(r.workflow, r.execution_id);
  const tokens = calls.reduce((s, c) => s + c.input_tokens + c.output_tokens, 0);
  const llmMs = calls.reduce((s, c) => s + c.latency_ms, 0);

  return (
    <Page
      title={<span className="break-words">{r.title || `Run ${r.id}`}</span>}
      subtitle={
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <Link href="/runs" className="text-brand hover:underline">
            Runs
          </Link>
          <span className="text-faint">/</span>
          <WorkflowTag workflow={r.workflow} />
          <StatusPill status={r.status} />
          <span className="text-ink-soft">Route: {routeLabel(r.route)}</span>
          <span className="mono text-[12px] text-faint">{r.id}</span>
        </div>
      }
      actions={
        execUrl ? (
          <a href={execUrl} target="_blank" rel="noreferrer" className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-line bg-white px-3.5 text-[13px] font-medium text-ink shadow-sm hover:bg-zinc-50">
            Open in n8n
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
            </svg>
          </a>
        ) : null
      }
    >
      <div className="space-y-5">
        {r.error_message ? (
          <ErrorBox>
            <div className="font-medium">The run failed</div>
            <div className="mt-0.5 break-words text-red-900/80">{r.error_message}</div>
          </ErrorBox>
        ) : null}

        <Card>
          <Fields
            cols={3}
            items={[
              ["Started", whenFull(r.started_at)],
              ["Finished", r.finished_at ? whenFull(r.finished_at) : r.status === "running" ? "still running" : DASH],
              ["End-to-end latency", ms(r.latency_ms)],
              ["Attempts", num(r.attempts)],
              ["Duplicate deliveries", num(r.duplicates)],
              ["Source", r.source || DASH],
              ["n8n execution", r.execution_id ? <ExtLink href={execUrl}>{`#${r.execution_id}`}</ExtLink> : DASH],
              ["Model calls", `${calls.length} call${calls.length === 1 ? "" : "s"}, ${num(tokens)} tokens, ${ms(llmMs)} in the model`],
              ["Idempotency key", <span key="k" className="mono text-[12px] break-all">{r.idempotency_key}</span>],
            ]}
          />
        </Card>

        {(r.reviews?.length ?? 0) > 0 || (r.dead_letters?.length ?? 0) > 0 ? (
          <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
            {r.reviews?.length ? (
              <Card title="Human review" pad={false}>
                <ul className="divide-y divide-line-soft">
                  {r.reviews.map((rev) => (
                    <li key={rev.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5 text-[13px]">
                      <StatusPill status={rev.status} />
                      <span className="text-ink">{REVIEW_KINDS[rev.kind] ?? rev.kind}</span>
                      <span className="text-muted">{rev.decided_by ? `by ${rev.decided_by}, ${whenFull(rev.decided_at)}` : `queued ${whenFull(rev.created_at)}`}</span>
                      <Link href={`/review?id=${rev.id}${rev.status === "pending" ? "" : "&tab=decided"}`} className="ml-auto text-brand hover:underline">
                        Open in review queue
                      </Link>
                    </li>
                  ))}
                </ul>
              </Card>
            ) : null}
            {r.dead_letters?.length ? (
              <Card title="Dead letters" pad={false}>
                <ul className="divide-y divide-line-soft">
                  {r.dead_letters.map((d) => (
                    <li key={d.id} className="px-4 py-2.5 text-[13px]">
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                        <StatusPill status={d.status} />
                        <span className="font-medium text-ink">{d.node || "unknown node"}</span>
                        <span className="text-muted">{whenFull(d.created_at)}</span>
                        <span className="text-muted">{d.replays} replays</span>
                        <Link href="/dead-letters?status=all" className="ml-auto text-brand hover:underline">
                          Dead-letter queue
                        </Link>
                      </div>
                      <div className="mt-1 break-words text-[12.5px] text-bad">{d.error_message}</div>
                    </li>
                  ))}
                </ul>
              </Card>
            ) : null}
          </div>
        ) : null}

        <Card title="AI gateway calls" pad={false} aside={<span>{calls.length} call{calls.length === 1 ? "" : "s"}</span>}>
          {calls.length ? (
            <Table
              dense
              head={["Time", "Task", "Model", "Latency", "Tokens in / out", "Cost", "Cached", "Attempts", "Schema", "Decision"]}
              align={["left", "left", "left", "right", "right", "right", "left", "right", "left", "left"]}
            >
              {calls.map((c) => (
                <tr key={c.id} className="align-top">
                  <td className={`${td} whitespace-nowrap text-muted tnum`}>{clock(c.created_at)}</td>
                  <td className={`${td} mono whitespace-nowrap`}>{c.task}</td>
                  <td className={`${td} max-w-[280px]`}>
                    <div className="mono truncate text-[12px]" title={c.served_model ?? c.model}>
                      {c.served_model ?? c.model}
                    </div>
                    {c.served_model && c.served_model !== c.model ? <div className="mono truncate text-[11px] text-muted">asked {c.model}</div> : null}
                  </td>
                  <td className={tdNum}>{ms(c.latency_ms)}</td>
                  <td className={tdNum}>
                    {num(c.input_tokens)} / {num(c.output_tokens)}
                  </td>
                  <td className={tdNum}>{usd(c.cost_usd)}</td>
                  <td className={td}>{c.cached ? <Pill tone="sky">cached</Pill> : <span className="text-faint">no</span>}</td>
                  <td className={`${tdNum} ${c.attempts > 1 ? "font-medium text-warn" : ""}`}>{c.attempts}</td>
                  <td className={td}>{c.schema_valid ? <Pill tone="green">valid</Pill> : <Pill tone="red">invalid</Pill>}</td>
                  <td className={td}>
                    <span className="mono text-[12px]">{c.decision}</span>
                    {c.error_message ? <div className="max-w-[320px] text-[12px] break-words text-bad">{c.error_message}</div> : null}
                  </td>
                </tr>
              ))}
            </Table>
          ) : (
            <div className="p-4">
              <Empty>No model calls recorded for this run.</Empty>
            </div>
          )}
        </Card>

        <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
          <Card title="Output">
            {r.output && Object.keys(r.output).length ? <JsonBlock value={stripBinary(r.output)} maxHeight="max-h-[560px]" /> : <Empty>No output yet.</Empty>}
          </Card>
          <Card title="Input" aside={files.length ? <span>{files.length} file{files.length > 1 ? "s" : ""}</span> : null}>
            {files.length ? (
              <div className="mb-3 flex flex-wrap gap-1.5">
                {files.map((f) => (
                  <span key={f} className="inline-flex items-center gap-1.5 rounded-md border border-line bg-zinc-50 px-2 py-1 text-[12px] text-ink-soft">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z M14 3v5h5" />
                    </svg>
                    {f}
                  </span>
                ))}
              </div>
            ) : null}
            {r.input && Object.keys(r.input).length ? <JsonBlock value={stripBinary(r.input)} maxHeight="max-h-[560px]" /> : <Empty>No input stored.</Empty>}
          </Card>
        </div>
      </div>
    </Page>
  );
}
