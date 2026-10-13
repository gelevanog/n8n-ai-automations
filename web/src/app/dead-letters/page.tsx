"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { useNow, useStatus } from "@/components/Status";
import { Button, Card, Empty, ErrorBox, ExtLink, Loading, Page, Spinner, StatusPill, Table, Tabs, WorkflowTag, td, tdNum } from "@/components/ui";
import { errorText, POLL_MS, post, useApi } from "@/lib/api";
import { ago, whenFull } from "@/lib/format";
import type { DeadLetter, ReplayResult } from "@/lib/types";
import { n8nExecutionUrl } from "@/lib/workflows";

type Outcome = { tone: "ok" | "bad"; text: string };

function DeadLettersView() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const now = useNow();
  const status = useStatus();
  const tab = params.get("status") === "all" ? "all" : "open";
  // Always fetch every status and filter here, so a row keeps its place after Replay or Resolve changes its status.
  const list = useApi<DeadLetter[]>("/ops/dead-letters?status=&limit=500", { interval: POLL_MS });
  const [busy, setBusy] = useState<Record<string, "replay" | "resolve">>({});
  const [outcome, setOutcome] = useState<Record<string, Outcome>>({});
  // rows acted on stay visible on the Open tab until the tab changes, so the outcome can be read
  const [touched, setTouched] = useState<Record<string, true>>({});

  const act = (d: DeadLetter, action: "replay" | "resolve") => {
    setBusy((b) => ({ ...b, [d.id]: action }));
    const done = () => {
      setBusy((b) => {
        const next = { ...b };
        delete next[d.id];
        return next;
      });
      list.reload();
      status.refresh();
    };
    if (action === "replay") {
      post<ReplayResult>(`/ops/dead-letters/${d.id}/replay`)
        .then((r) => {
          setTouched((t) => ({ ...t, [d.id]: true }));
          setOutcome((o) => ({
            ...o,
            [d.id]: r.ok
              ? { tone: "ok", text: `Replayed: n8n accepted the original input (HTTP ${r.status_code ?? 200}) and the run starts a new attempt.` }
              : { tone: "bad", text: `Replay failed${r.status_code ? ` (HTTP ${r.status_code})` : r.error ? `: ${r.error}` : ""}. The dead letter stays open.` },
          }));
        })
        .catch((err: unknown) => setOutcome((o) => ({ ...o, [d.id]: { tone: "bad", text: errorText(err) } })))
        .finally(done);
    } else {
      post<DeadLetter>(`/ops/dead-letters/${d.id}/resolve`)
        .then(() => {
          setTouched((t) => ({ ...t, [d.id]: true }));
          setOutcome((o) => ({ ...o, [d.id]: { tone: "ok", text: "Marked resolved." } }));
        })
        .catch((err: unknown) => setOutcome((o) => ({ ...o, [d.id]: { tone: "bad", text: errorText(err) } })))
        .finally(done);
    }
  };

  const rows = (list.data ?? []).filter((d) => tab === "all" || d.status === "open" || touched[d.id]);

  return (
    <Page
      title="Dead letters"
      subtitle="Executions that failed after n8n's retries. The error workflow stored each run's original input: Replay posts it to the workflow again (same idempotency key, a new attempt), Resolve closes it without a replay."
      actions={
        <Tabs
          value={tab}
          onChange={(v) => {
            setTouched({});
            setOutcome({});
            router.replace(v === "all" ? `${pathname}?status=all` : pathname, { scroll: false });
          }}
          options={[
            { value: "open", label: <>Open{status.counts?.openDeadLetters ? <span className="rounded-full bg-bad-soft px-1.5 text-[11px] text-bad tnum">{status.counts.openDeadLetters}</span> : null}</> },
            { value: "all", label: "All" },
          ]}
        />
      }
    >
      <Card pad={false}>
        {list.error && !list.data ? (
          <div className="p-4">
            <ErrorBox>Could not load dead letters: {list.error}</ErrorBox>
          </div>
        ) : !list.data ? (
          <Loading />
        ) : rows.length === 0 ? (
          <div className="p-4">
            <Empty title={tab === "open" ? "No open dead letters" : "No dead letters yet"}>When a workflow fails after its retries, the error handler records it here.</Empty>
          </div>
        ) : (
          <Table head={["Failed", "Workflow", "Node and error", "Status", "Replays", "Links", ""]} align={["left", "left", "left", "left", "right", "left", "right"]}>
            {rows.map((d) => {
              const exec = n8nExecutionUrl(d.workflow, d.execution_id);
              const b = busy[d.id];
              const o = outcome[d.id];
              return (
                <tr key={d.id} className="align-top">
                  <td className={`${td} whitespace-nowrap text-muted tnum`} title={whenFull(d.created_at)}>
                    {ago(d.created_at, now)}
                  </td>
                  <td className={td}>
                    <WorkflowTag workflow={d.workflow} />
                  </td>
                  <td className={`${td} min-w-[280px]`}>
                    <div className="font-medium text-ink">{d.node || "unknown node"}</div>
                    <div className="mono mt-0.5 text-[12px] break-words text-bad">{d.error_message || "no error message"}</div>
                    {o ? <div className={`mt-1.5 rounded-md px-2 py-1 text-[12px] ${o.tone === "ok" ? "bg-ok-soft text-ok" : "bg-bad-soft text-bad"}`}>{o.text}</div> : null}
                  </td>
                  <td className={td}>
                    <StatusPill status={d.status} />
                  </td>
                  <td className={tdNum}>{d.replays}</td>
                  <td className={`${td} text-[12.5px] whitespace-nowrap`}>
                    <div className="flex flex-col gap-0.5">
                      {d.run_id ? (
                        <Link href={`/runs/${d.run_id}`} className="text-brand hover:underline">
                          Run details
                        </Link>
                      ) : (
                        <span className="text-faint">no run</span>
                      )}
                      {exec ? <ExtLink href={exec}>{`n8n #${d.execution_id}`}</ExtLink> : null}
                    </div>
                  </td>
                  <td className={`${td} text-right`}>
                    {d.status === "resolved" ? null : (
                      <div className="flex justify-end gap-1.5">
                        <Button size="sm" kind="primary" disabled={Boolean(b) || !d.has_payload} onClick={() => act(d, "replay")} title={d.has_payload ? "Post the original input to the workflow again" : "No stored input to replay"}>
                          {b === "replay" ? <Spinner /> : null}
                          Replay
                        </Button>
                        <Button size="sm" disabled={Boolean(b)} onClick={() => act(d, "resolve")}>
                          {b === "resolve" ? <Spinner /> : null}
                          Resolve
                        </Button>
                      </div>
                    )}
                    {!d.has_payload && d.status !== "resolved" ? <div className="mt-1 text-[11px] text-muted">no stored input</div> : null}
                  </td>
                </tr>
              );
            })}
          </Table>
        )}
      </Card>
    </Page>
  );
}

export default function DeadLettersPage() {
  return (
    <Suspense fallback={<Loading />}>
      <DeadLettersView />
    </Suspense>
  );
}
