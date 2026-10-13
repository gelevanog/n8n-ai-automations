"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

import { useNow } from "@/components/Status";
import { StatusPill, Table, WorkflowTag, td, tdNum } from "@/components/ui";
import { ago, DASH, ms, whenFull } from "@/lib/format";
import type { Run } from "@/lib/types";
import { routeLabel } from "@/lib/workflows";

export function RunsTable({ runs, compact = false }: { runs: Run[]; compact?: boolean }) {
  const router = useRouter();
  const now = useNow();
  const head = compact ? ["Started", "Workflow", "Item", "Status", "Route", "Latency"] : ["Started", "Workflow", "Item", "Status", "Route", "Source", "Attempts", "Duplicates", "Latency"];
  const align: ("left" | "right")[] = compact ? ["left", "left", "left", "left", "left", "right"] : ["left", "left", "left", "left", "left", "left", "right", "right", "right"];
  return (
    <Table head={head} align={align}>
      {runs.map((run) => (
        <tr key={run.id} onClick={() => router.push(`/runs/${run.id}`)} className="cursor-pointer transition hover:bg-zinc-50">
          <td className={`${td} whitespace-nowrap text-muted tnum`} title={whenFull(run.started_at)}>
            {ago(run.started_at, now)}
          </td>
          <td className={td}>
            <WorkflowTag workflow={run.workflow} />
          </td>
          <td className={`${td} max-w-[420px]`}>
            <Link href={`/runs/${run.id}`} onClick={(e) => e.stopPropagation()} className="block truncate text-ink hover:text-brand" title={run.title}>
              {run.title || <span className="text-faint">untitled</span>}
            </Link>
            {run.status === "error" && run.error_message ? <div className="truncate text-[12px] text-bad">{run.error_message}</div> : null}
          </td>
          <td className={td}>
            <StatusPill status={run.status} />
          </td>
          <td className={`${td} whitespace-nowrap text-ink-soft`}>{routeLabel(run.route)}</td>
          {compact ? null : (
            <>
              <td className={`${td} whitespace-nowrap text-muted`}>{run.source || DASH}</td>
              <td className={`${tdNum} ${run.attempts > 1 ? "font-medium text-warn" : "text-muted"}`}>{run.attempts}</td>
              <td className={`${tdNum} ${run.duplicates ? "text-ink" : "text-faint"}`}>{run.duplicates}</td>
            </>
          )}
          <td className={`${tdNum} text-ink-soft`}>{run.status === "running" ? <span className="text-sky">running</span> : ms(run.latency_ms)}</td>
        </tr>
      ))}
    </Table>
  );
}
