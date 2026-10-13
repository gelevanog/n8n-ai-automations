"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { RunsTable } from "@/components/RunsTable";
import { Button, Card, Empty, ErrorBox, Loading, Page, Select } from "@/components/ui";
import { POLL_MS, useApi } from "@/lib/api";
import type { Run } from "@/lib/types";
import { WORKFLOW_KEYS, WORKFLOWS } from "@/lib/workflows";

const LIMIT = 50;

const STATUSES = [
  { value: "", label: "All statuses" },
  { value: "running", label: "Running" },
  { value: "auto", label: "Auto" },
  { value: "review", label: "In review" },
  { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" },
  { value: "escalated", label: "Escalated" },
  { value: "error", label: "Error" },
];

function RunsView() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const workflow = params.get("workflow") ?? "";
  const status = params.get("status") ?? "";
  const offset = Math.max(0, Number(params.get("offset") ?? 0) || 0);

  const set = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params.toString());
    for (const [k, v] of Object.entries(patch)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    if (!("offset" in patch)) next.delete("offset");
    const qs = next.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  };

  const query = new URLSearchParams({ limit: String(LIMIT), offset: String(offset) });
  if (workflow) query.set("workflow", workflow);
  if (status) query.set("status", status);
  const runs = useApi<Run[]>(`/ops/runs?${query.toString()}`, { interval: POLL_MS });

  return (
    <Page title="Runs" subtitle="Every workflow execution Relay has seen, newest first. Click a run for its gateway calls, output and the n8n execution.">
      <Card
        pad={false}
        title={
          <div className="flex flex-wrap items-center gap-2">
            <Select
              ariaLabel="Workflow"
              value={workflow}
              onChange={(v) => set({ workflow: v })}
              options={[{ value: "", label: "All workflows" }, ...WORKFLOW_KEYS.map((k) => ({ value: k, label: WORKFLOWS[k].name }))]}
            />
            <Select ariaLabel="Status" value={status} onChange={(v) => set({ status: v })} options={STATUSES} />
            {workflow || status ? (
              <Button kind="ghost" size="sm" onClick={() => set({ workflow: "", status: "" })}>
                Clear filters
              </Button>
            ) : null}
          </div>
        }
        aside={
          runs.data ? (
            <>
              <span className="tnum">
                {runs.data.length ? `${offset + 1}-${offset + runs.data.length}` : "0"}
              </span>
              <Button size="sm" disabled={offset === 0} onClick={() => set({ offset: String(Math.max(0, offset - LIMIT)) })}>
                Previous
              </Button>
              <Button size="sm" disabled={runs.data.length < LIMIT} onClick={() => set({ offset: String(offset + LIMIT) })}>
                Next
              </Button>
            </>
          ) : null
        }
      >
        {runs.error && !runs.data ? (
          <div className="p-4">
            <ErrorBox>Could not load runs: {runs.error}</ErrorBox>
          </div>
        ) : runs.data ? (
          runs.data.length ? (
            <RunsTable runs={runs.data} />
          ) : (
            <div className="p-4">
              <Empty title="No runs match these filters" />
            </div>
          )
        ) : (
          <Loading />
        )}
      </Card>
    </Page>
  );
}

export default function RunsPage() {
  return (
    <Suspense fallback={<Loading />}>
      <RunsView />
    </Suspense>
  );
}
