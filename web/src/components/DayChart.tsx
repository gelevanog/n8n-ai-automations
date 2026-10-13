"use client";

import { useState } from "react";

import type { DayBucket } from "@/lib/types";

const SERIES = [
  { key: "auto", label: "Auto-handled", color: "bg-ok-bar" },
  { key: "review", label: "Human review", color: "bg-warn-bar" },
  { key: "errors", label: "Errors", color: "bg-bad-bar" },
  { key: "other", label: "Running", color: "bg-sky-300" },
] as const;

function isoDay(d: Date): string {
  return d.toISOString().slice(0, 10);
}

/** Every UTC day from `from` to `to` inclusive. */
function dayRange(from: string, to: string): string[] {
  const out: string[] = [];
  const end = new Date(`${to}T00:00:00Z`);
  for (let d = new Date(`${from}T00:00:00Z`); d <= end && out.length < 400; d.setUTCDate(d.getUTCDate() + 1)) out.push(isoDay(d));
  return out;
}

function shortDate(day: string): string {
  return new Date(`${day}T00:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
}

/** Stacked bars of runs per day. `span` = number of days to show ending today, or null for the whole history. */
export function DayChart({ days, span, now }: { days: Record<string, DayBucket>; span: number | null; now: number }) {
  const [hover, setHover] = useState<string | null>(null);
  const keys = Object.keys(days).sort();
  const today = now ? isoDay(new Date(now)) : (keys[keys.length - 1] ?? isoDay(new Date(0)));
  const last = keys.length && keys[keys.length - 1] > today ? keys[keys.length - 1] : today;
  let first: string;
  if (span) {
    const d = new Date(`${last}T00:00:00Z`);
    d.setUTCDate(d.getUTCDate() - (span - 1));
    first = isoDay(d);
  } else {
    first = keys[0] ?? last;
    const minStart = new Date(`${last}T00:00:00Z`);
    minStart.setUTCDate(minStart.getUTCDate() - 6);
    if (first > isoDay(minStart)) first = isoDay(minStart);
  }
  const range = dayRange(first, last);
  const rows = range.map((day) => {
    const b = days[day] ?? { runs: 0, auto: 0, review: 0, errors: 0 };
    return { day, ...b, other: Math.max(0, b.runs - b.auto - b.review - b.errors) };
  });
  const max = Math.max(1, ...rows.map((r) => r.runs));
  const ceiling = niceCeil(max);
  const labelEvery = Math.ceil(rows.length / 8);
  const focus = rows.find((r) => r.day === hover) ?? null;
  const total = rows.reduce((s, r) => s + r.runs, 0);

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-muted">
        {SERIES.map((s) => (
          <span key={s.key} className="inline-flex items-center gap-1.5">
            <span className={`h-2.5 w-2.5 rounded-[3px] ${s.color}`} />
            {s.label}
          </span>
        ))}
        <span className="ml-auto text-ink-soft tnum">
          {focus ? (
            <>
              <span className="font-medium text-ink">{shortDate(focus.day)}</span>: {focus.runs} runs, {focus.auto} auto, {focus.review} review, {focus.errors} errors
            </>
          ) : (
            <>{total} runs in {rows.length} days</>
          )}
        </span>
      </div>
      <div className="relative flex h-[168px] gap-2">
        <div className="flex w-7 shrink-0 flex-col justify-between pb-5 text-right text-[10.5px] text-faint tnum">
          <span>{ceiling}</span>
          <span>{ceiling / 2}</span>
          <span>0</span>
        </div>
        <div className="relative min-w-0 flex-1">
          <div className="pointer-events-none absolute inset-x-0 top-0 bottom-5 flex flex-col justify-between">
            <div className="border-t border-dashed border-line" />
            <div className="border-t border-dashed border-line" />
            <div className="border-t border-line" />
          </div>
          <div className="absolute inset-x-0 top-0 bottom-5 flex items-end gap-[3px] sm:gap-1">
            {rows.map((r) => (
              <div
                key={r.day}
                onMouseEnter={() => setHover(r.day)}
                onMouseLeave={() => setHover(null)}
                className={`flex h-full min-w-0 flex-1 flex-col justify-end rounded-t-[3px] ${hover === r.day ? "bg-zinc-100/80" : ""}`}
                title={`${shortDate(r.day)}: ${r.runs} runs`}
              >
                <div className="flex w-full flex-col-reverse overflow-hidden rounded-t-[3px]" style={{ height: `${(r.runs / ceiling) * 100}%` }}>
                  {SERIES.map((s) =>
                    r[s.key] ? <div key={s.key} className={`${s.color} w-full`} style={{ height: `${(r[s.key] / Math.max(1, r.runs)) * 100}%` }} /> : null,
                  )}
                </div>
              </div>
            ))}
          </div>
          <div className="absolute inset-x-0 bottom-0 flex h-4 gap-[3px] sm:gap-1">
            {rows.map((r, i) => (
              <div key={r.day} className="min-w-0 flex-1 overflow-visible text-center text-[10.5px] whitespace-nowrap text-faint">
                {i % labelEvery === 0 || i === rows.length - 1 ? shortDate(r.day) : ""}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function niceCeil(n: number): number {
  if (n <= 4) return 4;
  const pow = 10 ** Math.floor(Math.log10(n));
  for (const m of [1, 2, 2.5, 5, 10]) {
    const c = m * pow;
    if (c >= n && (c / 2) % 1 === 0) return c;
  }
  return Math.ceil(n / pow) * pow * 2;
}
