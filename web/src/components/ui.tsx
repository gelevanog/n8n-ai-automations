import Link from "next/link";
import type { ReactNode } from "react";

import { DASH, display, label } from "@/lib/format";
import type { Check } from "@/lib/types";
import { WORKFLOWS, workflowName } from "@/lib/workflows";

export function Page({ title, subtitle, actions, children }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <div className="mx-auto max-w-[1440px] px-4 py-5 sm:px-7 sm:py-7">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3 sm:mb-6">
        <div className="min-w-0">
          <h1 className="text-[22px] font-semibold tracking-tight text-ink sm:text-[24px]">{title}</h1>
          {subtitle ? <div className="mt-1 max-w-3xl text-[13.5px] text-muted">{subtitle}</div> : null}
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
      {children}
    </div>
  );
}

export function Card({ title, aside, children, className = "", pad = true }: { title?: ReactNode; aside?: ReactNode; children: ReactNode; className?: string; pad?: boolean }) {
  return (
    <section className={`min-w-0 rounded-xl border border-line bg-card shadow-[0_1px_2px_rgba(16,24,40,0.04)] ${className}`}>
      {title ? (
        <div className="flex min-h-11 flex-wrap items-center justify-between gap-x-3 gap-y-1 border-b border-line px-4 py-2">
          <h2 className="text-[13px] font-semibold text-ink">{title}</h2>
          {aside ? <div className="flex items-center gap-2 text-xs text-muted">{aside}</div> : null}
        </div>
      ) : null}
      <div className={pad ? "p-4" : ""}>{children}</div>
    </section>
  );
}

const TONES = {
  green: "bg-ok-soft text-ok ring-ok/15",
  amber: "bg-warn-soft text-warn ring-warn/15",
  blue: "bg-info-soft text-info ring-info/15",
  gray: "bg-zinc-100 text-zinc-600 ring-zinc-500/10",
  violet: "bg-violet-soft text-violet ring-violet/15",
  red: "bg-bad-soft text-bad ring-bad/15",
  sky: "bg-sky-soft text-sky ring-sky/15",
  brand: "bg-brand-soft text-brand-ink ring-brand/15",
} as const;
export type Tone = keyof typeof TONES;

const DOTS: Record<Tone, string> = {
  green: "bg-ok-bar",
  amber: "bg-warn-bar",
  blue: "bg-info",
  gray: "bg-zinc-400",
  violet: "bg-violet",
  red: "bg-bad-bar",
  sky: "bg-sky",
  brand: "bg-brand",
};

export function Pill({ tone = "gray", children, title, dot = false, pulse = false }: { tone?: Tone; children: ReactNode; title?: string; dot?: boolean; pulse?: boolean }) {
  return (
    <span title={title} className={`inline-flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-[11.5px] leading-4 font-medium whitespace-nowrap ring-1 ring-inset ${TONES[tone]}`}>
      {dot ? <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${DOTS[tone]} ${pulse ? "animate-pulse" : ""}`} /> : null}
      {children}
    </span>
  );
}

const STATUS_TONE: Record<string, Tone> = {
  auto: "green",
  review: "amber",
  pending: "amber",
  approved: "blue",
  rejected: "gray",
  escalated: "violet",
  error: "red",
  failed: "red",
  running: "sky",
  open: "red",
  replayed: "sky",
  resolved: "gray",
};

const STATUS_LABEL: Record<string, string> = {
  auto: "Auto",
  review: "In review",
  pending: "Pending",
  approved: "Approved",
  rejected: "Rejected",
  escalated: "Escalated",
  error: "Error",
  failed: "Resume failed",
  running: "Running",
  open: "Open",
  replayed: "Replayed",
  resolved: "Resolved",
};

export function statusTone(status: string): Tone {
  return STATUS_TONE[status] ?? "gray";
}

export function StatusPill({ status }: { status: string }) {
  return (
    <Pill tone={statusTone(status)} dot pulse={status === "running"}>
      {STATUS_LABEL[status] ?? label(status)}
    </Pill>
  );
}

export function WorkflowTag({ workflow, muted = false }: { workflow: string; muted?: boolean }) {
  const info = WORKFLOWS[workflow as keyof typeof WORKFLOWS];
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap ${muted ? "text-muted" : "text-ink-soft"}`}>
      <span className={`h-2 w-2 shrink-0 rounded-[3px] ${info?.dot ?? "bg-zinc-400"}`} />
      {workflowName(workflow)}
    </span>
  );
}

export function Stat({ label, value, hint, tone, href }: { label: string; value: ReactNode; hint?: ReactNode; tone?: Tone; href?: string }) {
  const color = tone ? { green: "text-ok", amber: "text-warn", blue: "text-info", gray: "text-ink", violet: "text-violet", red: "text-bad", sky: "text-sky", brand: "text-brand" }[tone] : "text-ink";
  const body = (
    <>
      <div className="text-[12px] font-medium text-muted">{label}</div>
      <div className={`mt-1.5 text-[26px] leading-none font-semibold tracking-tight tnum ${color}`}>{value}</div>
      {hint ? <div className="mt-2 truncate text-[12px] text-muted">{hint}</div> : null}
    </>
  );
  const cls = "block min-w-0 rounded-xl border border-line bg-card px-4 py-3.5 shadow-[0_1px_2px_rgba(16,24,40,0.04)]";
  return href ? (
    <Link href={href} className={`${cls} transition hover:border-zinc-300`}>
      {body}
    </Link>
  ) : (
    <div className={cls}>{body}</div>
  );
}

export function Empty({ children, title }: { children?: ReactNode; title?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-line px-4 py-10 text-center">
      {title ? <div className="text-sm font-medium text-ink-soft">{title}</div> : null}
      {children ? <div className={`text-[13px] text-muted ${title ? "mt-1" : ""}`}>{children}</div> : null}
    </div>
  );
}

export function ErrorBox({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-bad/20 bg-bad-soft px-4 py-3 text-[13px] text-bad">{children}</div>;
}

export function Notice({ tone = "blue", children }: { tone?: Tone; children: ReactNode }) {
  return <div className={`rounded-lg px-3.5 py-2.5 text-[13px] ring-1 ring-inset ${TONES[tone]}`}>{children}</div>;
}

type ButtonKind = "primary" | "secondary" | "danger" | "ghost" | "approve";

export function Button({
  children,
  onClick,
  kind = "secondary",
  disabled,
  type = "button",
  size = "md",
  title,
}: {
  children: ReactNode;
  onClick?: () => void;
  kind?: ButtonKind;
  disabled?: boolean;
  type?: "button" | "submit";
  size?: "sm" | "md";
  title?: string;
}) {
  const style = {
    primary: "bg-brand text-white shadow-sm hover:bg-indigo-700 disabled:bg-indigo-300",
    approve: "bg-ok text-white shadow-sm hover:bg-green-800 disabled:bg-green-300",
    secondary: "border border-line bg-white text-ink shadow-sm hover:bg-zinc-50 disabled:text-faint disabled:hover:bg-white",
    danger: "border border-line bg-white text-bad shadow-sm hover:border-bad/30 hover:bg-bad-soft disabled:text-faint disabled:hover:bg-white",
    ghost: "text-ink-soft hover:bg-zinc-100 disabled:text-faint",
  }[kind];
  const pad = size === "sm" ? "h-7 px-2.5 text-[12.5px]" : "h-9 px-3.5 text-[13px]";
  return (
    <button type={type} onClick={onClick} disabled={disabled} title={title} className={`inline-flex shrink-0 items-center justify-center gap-1.5 rounded-lg font-medium transition disabled:cursor-not-allowed ${pad} ${style}`}>
      {children}
    </button>
  );
}

export function Spinner({ className = "" }: { className?: string }) {
  return <span className={`inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent ${className}`} aria-hidden />;
}

export function Loading({ children = "Loading" }: { children?: ReactNode }) {
  return (
    <div className="flex items-center gap-2 px-4 py-8 text-[13px] text-muted">
      <Spinner /> {children}
    </div>
  );
}

export function Meter({ value, tone = "brand", className = "" }: { value: number | null | undefined; tone?: Tone; className?: string }) {
  const v = Math.max(0, Math.min(1, value ?? 0));
  const width = /\bw-/.test(className) ? "" : "w-full";
  return (
    <div className={`h-1.5 shrink-0 overflow-hidden rounded-full bg-zinc-100 ${width} ${className}`}>
      <div className={`h-full rounded-full ${DOTS[tone]}`} style={{ width: `${v * 100}%` }} />
    </div>
  );
}

export function Table({ head, children, dense = false, align }: { head: ReactNode[]; children: ReactNode; dense?: boolean; align?: ("left" | "right")[] }) {
  return (
    <div className="scroll-thin overflow-x-auto">
      <table className={`w-full border-collapse text-left ${dense ? "text-[12.5px]" : "text-[13px]"}`}>
        <thead>
          <tr className="border-b border-line text-[11.5px] text-muted">
            {head.map((h, i) => (
              <th key={i} className={`px-3 py-2 font-medium whitespace-nowrap first:pl-4 last:pr-4 ${align?.[i] === "right" ? "text-right" : ""}`}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line-soft">{children}</tbody>
      </table>
    </div>
  );
}

export const td = "px-3 py-2.5 first:pl-4 last:pr-4 align-middle";
export const tdNum = `${td} text-right tnum whitespace-nowrap`;

export function Tabs<T extends string>({ value, onChange, options }: { value: T; onChange: (v: T) => void; options: { value: T; label: ReactNode }[] }) {
  return (
    <div className="inline-flex rounded-lg border border-line bg-zinc-50 p-0.5 text-[12.5px]">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          className={`inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 font-medium transition ${value === o.value ? "bg-white text-ink shadow-sm ring-1 ring-line" : "text-muted hover:text-ink"}`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Select({ value, onChange, options, ariaLabel, className = "" }: { value: string; onChange: (v: string) => void; options: { value: string; label: string }[]; ariaLabel: string; className?: string }) {
  return (
    <select aria-label={ariaLabel} value={value} onChange={(e) => onChange(e.target.value)} className={`field h-8 w-auto py-0 text-[12.5px] font-normal ${className}`}>
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

export function Count({ n, tone = "gray" }: { n: number | null | undefined; tone?: "gray" | "amber" | "red" }) {
  if (!n) return null;
  const cls = { gray: "bg-zinc-200/70 text-zinc-600", amber: "bg-warn-soft text-warn", red: "bg-bad-soft text-bad" }[tone];
  return <span className={`ml-auto min-w-5 rounded-full px-1.5 text-center text-[11px] leading-[18px] font-semibold tnum ${cls}`}>{n}</span>;
}

/** Label / value pairs; null values show a dash. */
export function Fields({ items, cols = 2 }: { items: [string, ReactNode | unknown][]; cols?: 1 | 2 | 3 }) {
  const grid = { 1: "grid-cols-1", 2: "grid-cols-1 sm:grid-cols-2", 3: "grid-cols-2 sm:grid-cols-3" }[cols];
  return (
    <dl className={`grid gap-x-5 gap-y-2.5 ${grid}`}>
      {items.map(([k, v]) => (
        <div key={k} className="min-w-0">
          <dt className="text-[11.5px] text-muted">{k}</dt>
          <dd className="mt-0.5 text-[13px] break-words text-ink">{isNode(v) ? v : display(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

function isNode(v: unknown): v is ReactNode {
  return typeof v === "object" && v !== null && !Array.isArray(v) && "$$typeof" in (v as Record<string, unknown>);
}

export function Checks({ checks, failedOnly = false }: { checks: Check[] | undefined; failedOnly?: boolean }) {
  const list = checks ?? [];
  if (!list.length) return <div className="text-[13px] text-muted">No checks recorded.</div>;
  const failed = list.filter((c) => !c.ok);
  const passed = list.filter((c) => c.ok);
  return (
    <div className="space-y-1.5">
      {failed.map((c) => (
        <div key={c.code} className="flex items-start gap-2 rounded-lg border border-bad/20 bg-bad-soft px-2.5 py-1.5 text-[12.5px]">
          <CrossIcon className="mt-0.5 shrink-0 text-bad" />
          <div className="min-w-0">
            <span className="mono font-medium text-bad">{c.code}</span>
            {c.detail ? <div className="break-words text-red-900/80">{c.detail}</div> : null}
          </div>
        </div>
      ))}
      {!failedOnly && passed.length ? (
        <div className="flex flex-wrap gap-1.5 pt-0.5">
          {passed.map((c) => (
            <span key={c.code} title={c.detail || undefined} className="inline-flex items-center gap-1 rounded-md bg-ok-soft px-1.5 py-0.5 text-[11.5px] text-ok">
              <CheckIcon className="shrink-0" />
              <span className="mono">{c.code}</span>
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function Reasons({ reasons }: { reasons: string[] | undefined }) {
  if (!reasons?.length) return <div className="text-[13px] text-muted">{DASH}</div>;
  return (
    <ul className="space-y-1">
      {reasons.map((r, i) => (
        <li key={i} className="flex gap-2 text-[13px] text-ink-soft">
          <span className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-warn-bar" />
          <span className="break-words">{r}</span>
        </li>
      ))}
    </ul>
  );
}

export function CheckIcon({ className = "" }: { className?: string }) {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden>
      <path d="M20 6 9 17l-5-5" />
    </svg>
  );
}

export function CrossIcon({ className = "" }: { className?: string }) {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" className={className} aria-hidden>
      <path d="M18 6 6 18M6 6l12 12" />
    </svg>
  );
}

export function ExternalIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
    </svg>
  );
}

export function ExtLink({ href, children, className = "" }: { href: string | null; children: ReactNode; className?: string }) {
  if (!href) return null;
  return (
    <a href={href} target="_blank" rel="noreferrer" className={`inline-flex items-center gap-1 text-brand hover:underline ${className}`}>
      {children}
      <ExternalIcon />
    </a>
  );
}

export function JsonBlock({ value, maxHeight = "max-h-[420px]" }: { value: unknown; maxHeight?: string }) {
  return (
    <pre className={`mono scroll-thin overflow-auto rounded-lg border border-line bg-zinc-50 p-3 text-[11.5px] leading-relaxed text-ink-soft ${maxHeight}`}>
      {value === null || value === undefined ? "null" : JSON.stringify(value, null, 2)}
    </pre>
  );
}
