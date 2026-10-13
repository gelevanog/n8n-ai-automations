"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { useStatus } from "@/components/Status";
import { Count } from "@/components/ui";
import { N8N_URL } from "@/lib/api";

type CountKey = "pendingReview" | "openDeadLetters";

const LINKS: { href: string; label: string; icon: string; count?: CountKey; tone?: "amber" | "red" }[] = [
  { href: "/", label: "Overview", icon: "M4 13h6V4H4zM14 20h6v-9h-6zM14 4v4h6V4zM4 20h6v-3H4z" },
  { href: "/review", label: "Review queue", icon: "M9 11l3 3 8-8M20 12v7a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h11", count: "pendingReview", tone: "amber" },
  { href: "/runs", label: "Runs", icon: "M4 6h16M4 12h16M4 18h10" },
  { href: "/dead-letters", label: "Dead letters", icon: "M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z", count: "openDeadLetters", tone: "red" },
  { href: "/evaluation", label: "Evaluation", icon: "M5 20V10M12 20V4M19 20v-7" },
  { href: "/workflows", label: "Workflows", icon: "M6 3v12M18 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 9a9 9 0 0 1-9 9" },
];

function Logo() {
  return (
    <svg width="28" height="28" viewBox="0 0 32 32" aria-hidden>
      <rect width="32" height="32" rx="8" fill="#4f46e5" />
      <circle cx="9" cy="16" r="3" fill="#fff" />
      <circle cx="23" cy="9.5" r="2.6" fill="#c7d2fe" />
      <circle cx="23" cy="22.5" r="2.6" fill="#c7d2fe" />
      <path d="M12 16h4.5M16.5 16l4.2-5.2M16.5 16l4.2 5.2" stroke="#fff" strokeWidth="2" strokeLinecap="round" fill="none" />
    </svg>
  );
}

function Brand() {
  return (
    <Link href="/" className="flex items-center gap-2.5">
      <Logo />
      <div className="leading-tight">
        <div className="text-[15px] font-semibold tracking-tight text-ink">Relay</div>
        <div className="text-[11.5px] text-muted">AI automations on n8n</div>
      </div>
    </Link>
  );
}

function Nav({ onNavigate }: { onNavigate?: () => void }) {
  const path = usePathname();
  const { counts } = useStatus();
  return (
    <nav className="flex flex-col gap-0.5 text-[13.5px]">
      {LINKS.map((link) => {
        const active = link.href === "/" ? path === "/" : path.startsWith(link.href);
        return (
          <Link
            key={link.href}
            href={link.href}
            onClick={onNavigate}
            className={`flex h-8 items-center gap-2.5 rounded-md px-2.5 transition ${active ? "bg-white font-medium text-ink shadow-sm ring-1 ring-line" : "text-ink-soft hover:bg-zinc-200/50 hover:text-ink"}`}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className={active ? "text-brand" : "text-faint"}>
              <path d={link.icon} />
            </svg>
            {link.label}
            {link.count && counts ? <Count n={counts[link.count]} tone={link.tone} /> : null}
          </Link>
        );
      })}
    </nav>
  );
}

function Footer() {
  const { health, reachable } = useStatus();
  return (
    <div className="space-y-2.5 border-t border-line px-4 py-4 text-[12px]">
      {health ? (
        <>
          <div>
            <div className="text-[11px] text-muted">Model</div>
            <div className="mono mt-0.5 text-[11.5px] break-all text-ink">{health.model}</div>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {health.free_only ? (
              <span className="inline-flex items-center gap-1 rounded-md bg-ok-soft px-1.5 py-0.5 text-[11px] font-medium text-ok ring-1 ring-ok/15 ring-inset" title="The gateway refuses any model that is not free">
                <span className="h-1.5 w-1.5 rounded-full bg-ok-bar" />
                Free-only guard
              </span>
            ) : (
              <span className="rounded-md bg-warn-soft px-1.5 py-0.5 text-[11px] font-medium text-warn ring-1 ring-warn/15 ring-inset">Paid models allowed</span>
            )}
            <span className="rounded-md bg-zinc-100 px-1.5 py-0.5 text-[11px] text-zinc-600">v{health.version}</span>
          </div>
        </>
      ) : (
        <div className="flex items-center gap-1.5 text-muted">
          <span className={`h-1.5 w-1.5 rounded-full ${reachable === false ? "bg-bad-bar" : "bg-zinc-300"}`} />
          {reachable === false ? "API not reachable" : "Connecting"}
        </div>
      )}
      <a href={N8N_URL} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[12px] text-ink-soft hover:text-brand">
        Open n8n editor
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
        </svg>
      </a>
    </div>
  );
}

export function Sidebar() {
  const [open, setOpen] = useState(false);
  const { counts } = useStatus();
  const badge = (counts?.pendingReview ?? 0) + (counts?.openDeadLetters ?? 0);
  return (
    <>
      {/* desktop */}
      <aside className="sticky top-0 hidden h-screen w-[236px] shrink-0 flex-col border-r border-line bg-zinc-50/80 lg:flex">
        <div className="px-4 pt-5 pb-6">
          <Brand />
        </div>
        <div className="px-2.5">
          <Nav />
        </div>
        <div className="mt-auto">
          <Footer />
        </div>
      </aside>

      {/* mobile */}
      <div className="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-line bg-white/95 px-4 backdrop-blur lg:hidden">
        <Brand />
        <button type="button" onClick={() => setOpen(true)} className="relative inline-flex h-9 w-9 items-center justify-center rounded-lg border border-line bg-white" aria-label="Open navigation">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
            <path d="M4 7h16M4 12h16M4 17h16" />
          </svg>
          {badge ? <span className="absolute -top-1 -right-1 h-2.5 w-2.5 rounded-full bg-warn-bar ring-2 ring-white" /> : null}
        </button>
      </div>
      {open ? (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button type="button" aria-label="Close navigation" className="absolute inset-0 bg-zinc-900/30" onClick={() => setOpen(false)} />
          <div className="absolute inset-y-0 left-0 flex w-[260px] flex-col bg-zinc-50 shadow-xl">
            <div className="px-4 pt-4 pb-5">
              <Brand />
            </div>
            <div className="px-2.5">
              <Nav onNavigate={() => setOpen(false)} />
            </div>
            <div className="mt-auto">
              <Footer />
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
