"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import { API_URL, POLL_MS, useApi } from "@/lib/api";
import type { Health, Metrics } from "@/lib/types";

interface StatusValue {
  health: Health | null;
  counts: { pendingReview: number; openDeadLetters: number } | null;
  /** null while the first health check is in flight */
  reachable: boolean | null;
  tokenRejected: boolean;
  error: string | null;
  refresh: () => void;
}

const StatusContext = createContext<StatusValue>({ health: null, counts: null, reachable: null, tokenRejected: false, error: null, refresh: () => {} });

/** Polls /health and the queue counts once for the whole app (sidebar badges, API banner). */
export function StatusProvider({ children }: { children: ReactNode }) {
  const health = useApi<Health>("/health", { interval: POLL_MS });
  const metrics = useApi<Metrics>("/ops/metrics?days=1", { interval: POLL_MS });
  const reachable = health.status === null ? null : health.status !== 0;
  const totals = metrics.data?.totals;
  const reloadHealth = health.reload;
  const reloadMetrics = metrics.reload;
  const value: StatusValue = {
    health: health.data,
    counts: totals ? { pendingReview: totals.pending_review, openDeadLetters: totals.open_dead_letters } : null,
    reachable,
    tokenRejected: metrics.status === 401,
    error: health.error ?? metrics.error,
    refresh: () => {
      reloadHealth();
      reloadMetrics();
    },
  };
  return <StatusContext.Provider value={value}>{children}</StatusContext.Provider>;
}

export function useStatus(): StatusValue {
  return useContext(StatusContext);
}

export function ApiBanner() {
  const { reachable, tokenRejected, error } = useStatus();
  if (reachable !== false && tokenRejected) {
    return (
      <div className="border-b border-warn/20 bg-warn-soft px-4 py-2.5 text-[13px] text-warn sm:px-7">
        <span className="font-semibold">The Relay API at {API_URL} rejected the API token.</span> Set NEXT_PUBLIC_API_TOKEN to the value of RELAY_API_TOKEN and rebuild the dashboard.
      </div>
    );
  }
  if (reachable !== false) return null;
  return (
    <div className="border-b border-bad/20 bg-bad-soft px-4 py-2.5 text-[13px] text-bad sm:px-7">
      <span className="font-semibold">Relay API not reachable at {API_URL}.</span>{" "}
      <span className="text-red-900/75">Start the stack with docker compose up, or set NEXT_PUBLIC_API_URL. Retrying every 5 seconds.</span>
      {error && !error.startsWith("Relay API not reachable") ? <span className="ml-1 text-red-900/75">({error})</span> : null}
    </div>
  );
}

/** Current time, refreshed every `every` ms, so "3 min ago" labels stay pure during render. */
export function useNow(every = 30_000): number {
  const [now, setNow] = useState(0);
  useEffect(() => {
    const tick = () => setNow(Date.now());
    const first = window.setTimeout(tick, 0);
    const timer = window.setInterval(tick, every);
    return () => {
      window.clearTimeout(first);
      window.clearInterval(timer);
    };
  }, [every]);
  return now;
}
