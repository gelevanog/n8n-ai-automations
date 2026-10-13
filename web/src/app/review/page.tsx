"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useRef, useState } from "react";

import { GenericPanel, InvoicePanel, LeadReplyPanel, LeadResearchPanel, ResumeNotice, TriagePanel, type DecisionResult } from "@/components/review";
import { useNow, useStatus } from "@/components/Status";
import { Button, Empty, ErrorBox, ExtLink, Loading, Page, Select, StatusPill, Tabs, WorkflowTag } from "@/components/ui";
import { POLL_MS, useApi } from "@/lib/api";
import { ago, label, whenFull } from "@/lib/format";
import type { ReviewDetail, ReviewItem } from "@/lib/types";
import { n8nExecutionUrl, REVIEW_KINDS, WORKFLOW_KEYS, WORKFLOWS } from "@/lib/workflows";

type Tab = "pending" | "decided";


function reasonText(reason: string | undefined): string {
  if (!reason) return "";
  return /^[a-z_]+$/.test(reason) ? label(reason) : reason;
}

function KindTag({ item }: { item: ReviewItem }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md bg-zinc-100 px-1.5 py-0.5 text-[11.5px] leading-4 font-medium whitespace-nowrap text-ink-soft">
      <span className={`h-2 w-2 rounded-[3px] ${WORKFLOWS[item.workflow]?.dot ?? "bg-zinc-400"}`} />
      {REVIEW_KINDS[item.kind] ?? item.kind}
    </span>
  );
}

function ReviewView() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const now = useNow();
  const status = useStatus();
  const tab: Tab = params.get("tab") === "decided" ? "decided" : "pending";
  const workflow = params.get("workflow") ?? "";
  const detailRef = useRef<HTMLDivElement>(null);
  const [result, setResult] = useState<{ id: string; result: DecisionResult } | null>(null);

  const set = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params.toString());
    for (const [k, v] of Object.entries(patch)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    const qs = next.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  };

  const query = new URLSearchParams({ status: tab === "pending" ? "pending" : "", limit: "200" });
  if (workflow) query.set("workflow", workflow);
  const list = useApi<ReviewItem[]>(`/ops/review?${query.toString()}`, { interval: POLL_MS });
  const items = (list.data ?? []).filter((i) => (tab === "pending" ? i.status === "pending" : i.status !== "pending"));
  const selectedId = params.get("id") ?? items[0]?.id ?? null;
  const detail = useApi<ReviewDetail>(selectedId ? `/ops/review/${selectedId}` : null);
  const listItem = items.find((i) => i.id === selectedId) ?? null;
  const item: ReviewItem | null = detail.data ?? listItem;
  const index = items.findIndex((i) => i.id === selectedId);
  const nextPending = items.find((i, n) => i.status === "pending" && i.id !== selectedId && n > index) ?? items.find((i) => i.status === "pending" && i.id !== selectedId);

  const select = (id: string) => {
    set({ id });
    if (typeof window !== "undefined" && window.innerWidth < 1280) {
      window.setTimeout(() => detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
    }
  };

  const onDecided = (r: DecisionResult) => {
    setResult({ id: r.item.id, result: r });
    set({ id: r.item.id });
    list.reload();
    detail.reload();
    status.refresh();
  };

  return (
    <Page
      title="Review queue"
      subtitle="Items an n8n workflow paused on. Approving or rejecting posts the decision, with your edits, to the waiting execution and the workflow carries on."
    >
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[380px_minmax(0,1fr)]">
        <section className="min-w-0 self-start rounded-xl border border-line bg-card shadow-[0_1px_2px_rgba(16,24,40,0.04)] xl:sticky xl:top-5">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-3 py-2.5">
            <Tabs
              value={tab}
              onChange={(v) => set({ tab: v === "pending" ? null : v, id: null })}
              options={[
                { value: "pending", label: <>Pending{status.counts?.pendingReview ? <span className="rounded-full bg-warn-soft px-1.5 text-[11px] text-warn tnum">{status.counts.pendingReview}</span> : null}</> },
                { value: "decided", label: "Decided" },
              ]}
            />
            <Select
              ariaLabel="Workflow"
              value={workflow}
              onChange={(v) => set({ workflow: v, id: null })}
              options={[{ value: "", label: "All workflows" }, ...WORKFLOW_KEYS.filter((k) => k !== "digest" && k !== "faq").map((k) => ({ value: k, label: WORKFLOWS[k].name }))]}
            />
          </div>
          <div className="scroll-thin max-h-[calc(100vh-190px)] overflow-y-auto">
            {list.error && !list.data ? (
              <div className="p-3">
                <ErrorBox>{list.error}</ErrorBox>
              </div>
            ) : !list.data ? (
              <Loading />
            ) : items.length === 0 ? (
              <div className="p-3">
                <Empty title={tab === "pending" ? "Nothing waiting" : "No decisions yet"}>{tab === "pending" ? "Every item has been handled. New ones appear here within a few seconds." : "Approved and rejected items show up here."}</Empty>
              </div>
            ) : (
              <ul className="divide-y divide-line-soft">
                {items.map((i) => {
                  const active = i.id === selectedId;
                  return (
                    <li key={i.id}>
                      <button
                        type="button"
                        onClick={() => select(i.id)}
                        className={`relative block w-full px-3.5 py-2.5 text-left transition ${active ? "bg-brand-soft/60" : "hover:bg-zinc-50"}`}
                      >
                        {active ? <span className="absolute inset-y-0 left-0 w-0.5 bg-brand" /> : null}
                        <div className="flex items-center gap-2">
                          <KindTag item={i} />
                          {i.status !== "pending" ? <StatusPill status={i.status} /> : null}
                          <span className="ml-auto shrink-0 text-[11.5px] text-muted tnum" title={whenFull(i.created_at)}>
                            {ago(i.status === "pending" ? i.created_at : (i.decided_at ?? i.created_at), now)}
                          </span>
                        </div>
                        <div className={`mt-1 truncate text-[13px] ${active ? "font-medium text-ink" : "text-ink"}`}>{i.title || "Untitled"}</div>
                        <div className="mt-0.5 truncate text-[12px] text-muted">{reasonText(i.reasons[0])}</div>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </section>

        <div ref={detailRef} className="min-w-0 scroll-mt-16">
          {!selectedId || !item ? (
            detail.loading ? (
              <Loading />
            ) : detail.error ? (
              <ErrorBox>{detail.error}</ErrorBox>
            ) : list.data ? (
              <div className="rounded-xl border border-line bg-card p-4">
                <Empty title="Select an item">Pick an item on the left to see what the AI proposed and decide.</Empty>
              </div>
            ) : null
          ) : (
            <section className="rounded-xl border border-line bg-card shadow-[0_1px_2px_rgba(16,24,40,0.04)]">
              <header className="border-b border-line px-4 py-3.5 sm:px-5">
                <div className="flex flex-wrap items-center gap-2">
                  <KindTag item={item} />
                  <StatusPill status={item.status} />
                  <span className="text-[12.5px]">
                    <WorkflowTag workflow={item.workflow} muted />
                  </span>
                  <span className="text-[12.5px] text-muted">queued {ago(item.created_at, now)}</span>
                  <span className="ml-auto flex items-center gap-3 text-[12.5px]">
                    <Link href={`/runs/${item.run_id}`} className="text-brand hover:underline">
                      Run details
                    </Link>
                    {detail.data?.run?.execution_id ? <ExtLink href={n8nExecutionUrl(item.workflow, detail.data.run.execution_id)}>n8n execution</ExtLink> : null}
                  </span>
                </div>
                <h2 className="mt-2 text-[17px] font-semibold tracking-tight break-words text-ink">{item.title || "Untitled"}</h2>
                {!item.can_resume && item.status === "pending" ? <div className="mt-1 text-[12px] text-muted">No waiting n8n execution is attached; a decision only updates the run.</div> : null}
              </header>
              {result && result.id === item.id ? (
                <div className="flex flex-col gap-2 border-b border-line px-4 py-3 sm:flex-row sm:items-center sm:px-5">
                  <div className="flex-1">
                    <ResumeNotice result={result.result} />
                  </div>
                  {nextPending ? (
                    <Button kind="primary" onClick={() => select(nextPending.id)}>
                      Next item
                    </Button>
                  ) : null}
                </div>
              ) : null}
              <div className="p-4 sm:p-5">
                {item.kind === "triage" ? (
                  <TriagePanel key={item.id + item.status} item={item} detail={detail.data} onDecided={onDecided} />
                ) : item.kind === "invoice" ? (
                  <InvoicePanel key={item.id + item.status} item={item} onDecided={onDecided} />
                ) : item.kind === "lead_reply" ? (
                  <LeadReplyPanel key={item.id + item.status} item={item} onDecided={onDecided} />
                ) : item.kind === "lead_research" ? (
                  <LeadResearchPanel key={item.id + item.status} item={item} onDecided={onDecided} />
                ) : (
                  <GenericPanel key={item.id + item.status} item={item} onDecided={onDecided} />
                )}
              </div>
            </section>
          )}
        </div>
      </div>
    </Page>
  );
}

export default function ReviewPage() {
  return (
    <Suspense fallback={<Loading />}>
      <ReviewView />
    </Suspense>
  );
}
