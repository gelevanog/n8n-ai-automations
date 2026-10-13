"use client";

import Link from "next/link";
import { useState, type ReactNode } from "react";

import { Button, Checks, Fields, Meter, Notice, Pill, Reasons, Spinner, StatusPill, type Tone } from "@/components/ui";
import { errorText, post } from "@/lib/api";
import { attachmentNames, DASH, display, label, money, num, pct, whenFull } from "@/lib/format";
import type {
  Category,
  Check,
  CompanyProfile,
  InvoiceFields,
  InvoiceProposal,
  JsonObject,
  LeadIn,
  LeadReplyProposal,
  LeadResearchProposal,
  ResumeResult,
  ReviewDetail,
  ReviewItem,
  SiteInfo,
  TriageProposal,
} from "@/lib/types";
import { CATEGORIES, industryLabel, n8nExecutionUrl } from "@/lib/workflows";

// ------------------------------------------------------------------------------------------ shared

/** The edits a decided item was approved with (shown instead of the AI proposal once decided). */
function decidedEdits(item: ReviewItem): JsonObject {
  const edits = (item.decision as { edits?: unknown } | undefined)?.edits;
  return edits && typeof edits === "object" ? (edits as JsonObject) : {};
}

export type DecisionResult = { item: ReviewDetail; resume: ResumeResult | undefined; canResume: boolean };

type Decide = (decision: "approve" | "reject", edits: JsonObject) => void;

function useDecision(item: ReviewItem, onDecided: (r: DecisionResult) => void) {
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const decide: Decide = (decision, edits) => {
    setBusy(decision);
    setError(null);
    post<ReviewDetail>(`/ops/review/${item.id}/decision`, { decision, edits, reviewer: "dashboard", note })
      .then((result) => onDecided({ item: result, resume: result.resume, canResume: item.can_resume }))
      .catch((err: unknown) => setError(errorText(err)))
      .finally(() => setBusy(null));
  };
  return { busy, error, note, setNote, decide };
}

function DecisionBar({
  state,
  approveLabel,
  rejectLabel = "Reject",
  onApprove,
  onReject,
  hint,
  approveDisabled,
}: {
  state: ReturnType<typeof useDecision>;
  approveLabel: string;
  rejectLabel?: string;
  onApprove: () => void;
  onReject: () => void;
  hint?: ReactNode;
  approveDisabled?: string | null;
}) {
  return (
    <div className="sticky bottom-0 z-10 -mx-4 -mb-4 mt-5 rounded-b-xl border-t border-line bg-white/95 px-4 py-3 backdrop-blur sm:-mx-5 sm:-mb-5 sm:px-5">
      {state.error ? <div className="mb-2 rounded-md bg-bad-soft px-3 py-1.5 text-[12.5px] text-bad">{state.error}</div> : null}
      <div className="flex flex-col gap-2.5 md:flex-row md:items-center">
        <input className="field h-9 md:max-w-[340px]" placeholder="Note for the audit log (optional)" value={state.note} onChange={(e) => state.setNote(e.target.value)} aria-label="Reviewer note" />
        {hint ? <div className="text-[12px] text-muted md:flex-1">{hint}</div> : <div className="md:flex-1" />}
        <div className="flex gap-2">
          <Button kind="danger" onClick={onReject} disabled={state.busy !== null}>
            {state.busy === "reject" ? <Spinner /> : null}
            {rejectLabel}
          </Button>
          <Button kind="approve" onClick={onApprove} disabled={state.busy !== null || Boolean(approveDisabled)} title={approveDisabled ?? undefined}>
            {state.busy === "approve" ? <Spinner /> : null}
            {approveLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}

export function ResumeNotice({ result }: { result: DecisionResult }) {
  const { item, resume, canResume } = result;
  const verb = item.status === "rejected" || item.decision?.decision === "reject" ? "Rejected" : "Approved";
  const exec = item.run ? n8nExecutionUrl(item.workflow, item.run.execution_id) : null;
  if (!canResume) {
    return (
      <Notice tone="blue">
        <span className="font-medium">{verb}.</span> No n8n execution was waiting for this item, so the decision was recorded and the run was updated directly.
      </Notice>
    );
  }
  if (resume?.delivered) {
    return (
      <Notice tone="green">
        <span className="font-medium">{verb} and delivered.</span> n8n accepted the decision (HTTP {resume.status_code ?? 200}) and the waiting execution
        {item.run?.execution_id ? ` #${item.run.execution_id}` : ""} carries on.{" "}
        {exec ? (
          <a href={exec} target="_blank" rel="noreferrer" className="font-medium underline">
            Open in n8n
          </a>
        ) : null}
      </Notice>
    );
  }
  return (
    <Notice tone="red">
      <span className="font-medium">Decision saved, but n8n did not resume.</span>{" "}
      {resume?.status_code ? `The resume URL answered HTTP ${resume.status_code}.` : resume?.error ? `Error: ${resume.error}.` : "The resume URL could not be reached."} The item is marked
      &quot;resume failed&quot;; the n8n execution may have expired or been deleted.
    </Notice>
  );
}

function describeEdits(edits: JsonObject): string {
  const show = (v: unknown): string => {
    if (v && typeof v === "object") return Object.entries(v as JsonObject).map(([k, x]) => `${k} ${show(x)}`).join(", ");
    const text = display(v);
    return text.length > 60 ? "(edited text)" : text;
  };
  return Object.entries(edits)
    .map(([k, v]) => `${k}: ${show(v)}`)
    .join("; ");
}

function DecidedSummary({ item }: { item: ReviewItem }) {
  const d = item.decision ?? {};
  const edits = (d.edits as JsonObject | undefined) ?? {};
  const delivery = d.delivery as ResumeResult | undefined;
  return (
    <div className="mt-5 rounded-lg border border-line bg-zinc-50 px-4 py-3">
      <div className="flex flex-wrap items-center gap-2 text-[13px]">
        <StatusPill status={item.status} />
        <span className="text-ink-soft">
          {d.decision === "reject" ? "Rejected" : "Approved"} by <span className="font-medium text-ink">{item.decided_by ?? "unknown"}</span> on {whenFull(item.decided_at)}
        </span>
      </div>
      {typeof d.note === "string" && d.note ? <div className="mt-2 text-[13px] text-ink-soft">Note: {d.note}</div> : null}
      {Object.keys(edits).length ? (
        <div className="mt-2 text-[12.5px] text-ink-soft">
          Edits sent: <span className="break-words">{describeEdits(edits)}</span>
        </div>
      ) : null}
      {delivery && !delivery.delivered ? (
        <div className="mt-2 text-[12.5px] text-bad">Resume not delivered{delivery.status_code ? ` (HTTP ${delivery.status_code})` : delivery.error ? `: ${delivery.error}` : ""}.</div>
      ) : null}
    </div>
  );
}

function Section({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="min-w-0">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 className="text-[11.5px] font-semibold tracking-wide text-muted uppercase">{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  );
}

function Confidence({ value }: { value: number | undefined }) {
  const tone: Tone = value === undefined ? "gray" : value >= 0.85 ? "green" : value >= 0.7 ? "amber" : "red";
  return (
    <div className="flex items-center gap-2">
      <Meter value={value} tone={tone} className="w-24" />
      <span className="text-[12.5px] whitespace-nowrap tnum text-ink-soft">{value === undefined ? DASH : pct(value)}</span>
    </div>
  );
}

const TIER_TONE: Record<string, Tone> = { hot: "brand", warm: "amber", cold: "sky", disqualified: "gray" };

function Tier({ tier, score }: { tier?: string; score?: number }) {
  return (
    <div className="flex items-center gap-2.5">
      <Pill tone={TIER_TONE[tier ?? ""] ?? "gray"} dot>
        {label(tier ?? "unscored")}
      </Pill>
      <div className="flex items-center gap-2">
        <Meter value={(score ?? 0) / 100} tone={TIER_TONE[tier ?? ""] ?? "gray"} className="w-20" />
        <span className="text-[12.5px] whitespace-nowrap tnum text-ink-soft">{score ?? DASH} / 100</span>
      </div>
    </div>
  );
}

function compactFields(obj: Record<string, unknown> | undefined, labels: Record<string, string>): [string, unknown][] {
  if (!obj) return [];
  return Object.entries(labels)
    .filter(([k]) => obj[k] !== null && obj[k] !== undefined && obj[k] !== "")
    .map(([k, l]) => [l, k === "product" || k === "issue" ? label(String(obj[k])) : obj[k]]);
}

function LeadCard({ lead }: { lead: LeadIn | undefined }) {
  const l = lead ?? {};
  return (
    <div className="space-y-3">
      <Fields
        items={[
          ["Name", l.name],
          ["Email", l.email],
          ["Company", l.company],
          ["Website", l.website ? <span className="mono text-[12px] break-all">{l.website}</span> : DASH],
          ["Product", l.product ? label(l.product) : DASH],
          ["Quantity", l.quantity ?? DASH],
        ]}
      />
      {l.message ? <blockquote className="rounded-lg border-l-2 border-brand/40 bg-zinc-50 px-3 py-2 text-[13px] whitespace-pre-wrap text-ink-soft">{l.message}</blockquote> : null}
    </div>
  );
}

function ProfileCard({ profile }: { profile: CompanyProfile | undefined }) {
  const p = profile ?? {};
  return (
    <div className="space-y-3">
      <Fields
        cols={3}
        items={[
          ["Industry", industryLabel(p.industry)],
          ["Employees", p.employees_band],
          ["Country", p.country],
          ["Sells online", p.sells_online],
          ["Buyer", p.buyer === undefined ? DASH : p.buyer],
          ["Confidence", <Confidence key="c" value={p.confidence} />],
        ]}
      />
      {p.summary ? <p className="text-[12.5px] leading-relaxed text-muted">{p.summary}</p> : null}
    </div>
  );
}

// ------------------------------------------------------------------------------------------ triage

export function TriagePanel({ item, detail, onDecided }: { item: ReviewItem; detail: ReviewDetail | null; onDecided: (r: DecisionResult) => void }) {
  const p = item.proposal as TriageProposal;
  const input = (detail?.run?.input ?? {}) as JsonObject;
  const subject = (typeof input.subject === "string" && input.subject) || item.title;
  const files = attachmentNames(input);
  const [category, setCategory] = useState<Category>((decidedEdits(item).category as Category | undefined) ?? p.category ?? "other");
  const state = useDecision(item, onDecided);
  const pending = item.status === "pending";
  const target = CATEGORIES.find((c) => c.key === category);

  return (
    <div>
      <div className="grid grid-cols-1 gap-5 2xl:grid-cols-2">
        <Section title="Email">
          <div className="rounded-lg border border-line">
            <div className="space-y-1 border-b border-line px-3.5 py-2.5 text-[13px]">
              <div className="flex gap-2">
                <span className="w-14 shrink-0 text-muted">From</span>
                <span className="min-w-0 break-words text-ink">
                  {p.from?.name ? <span className="font-medium">{p.from.name} </span> : null}
                  <span className="text-ink-soft">&lt;{p.from?.email ?? "unknown"}&gt;</span>
                </span>
              </div>
              <div className="flex gap-2">
                <span className="w-14 shrink-0 text-muted">Subject</span>
                <span className="min-w-0 font-medium break-words text-ink">{subject || DASH}</span>
              </div>
              {files.length ? (
                <div className="flex gap-2">
                  <span className="w-14 shrink-0 text-muted">Files</span>
                  <span className="flex flex-wrap gap-1.5">
                    {files.map((f) => (
                      <span key={f} className="rounded border border-line bg-zinc-50 px-1.5 text-[12px] text-ink-soft">
                        {f}
                      </span>
                    ))}
                  </span>
                </div>
              ) : null}
            </div>
            <div className="scroll-thin max-h-[340px] overflow-y-auto px-3.5 py-3 text-[13px] leading-relaxed whitespace-pre-wrap text-ink">{p.text || <span className="text-muted">No body text.</span>}</div>
          </div>
        </Section>

        <div className="space-y-5">
          <Section title="AI classification">
            <div className="flex flex-wrap items-center gap-3">
              <Pill tone="brand">{CATEGORIES.find((c) => c.key === p.category)?.label ?? label(p.category)}</Pill>
              <Confidence value={p.confidence} />
            </div>
            {p.summary ? <p className="mt-2 text-[13px] text-ink-soft">{p.summary}</p> : null}
          </Section>
          <Section title="Extracted fields">
            {(() => {
              const items: [string, unknown][] = [
                ...compactFields(p.contact as Record<string, unknown>, { name: "Contact name", company: "Company", email: "Email", phone: "Phone" }),
                ...compactFields(p.lead as Record<string, unknown>, { product: "Product", quantity: "Quantity", deadline: "Deadline" }),
                ...compactFields(p.support as Record<string, unknown>, { order_id: "Order", issue: "Issue" }),
                ...compactFields(p.invoice as Record<string, unknown>, { vendor: "Vendor", invoice_number: "Invoice number", amount: "Amount", currency: "Currency" }),
              ];
              return items.length ? <Fields items={items} /> : <div className="text-[13px] text-muted">Nothing extracted.</div>;
            })()}
          </Section>
          <Section title="Why it needs a person">
            <Reasons reasons={item.reasons} />
          </Section>
          <Section title="Checks">
            <Checks checks={p.checks} />
          </Section>
        </div>
      </div>

      <div className="mt-5">
        <Section title={pending ? "Confirm or correct the category" : "Category"}>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
            {CATEGORIES.map((c) => {
              const active = category === c.key;
              return (
                <button
                  key={c.key}
                  type="button"
                  disabled={!pending}
                  onClick={() => setCategory(c.key)}
                  className={`rounded-lg border px-3 py-2 text-left transition disabled:cursor-default ${active ? "border-brand bg-brand-soft ring-1 ring-brand" : "border-line bg-white hover:border-zinc-300"} ${!pending && !active ? "opacity-50" : ""}`}
                >
                  <div className={`text-[13px] font-medium ${active ? "text-brand-ink" : "text-ink"}`}>
                    {c.label}
                    {c.key === p.category ? <span className="ml-1.5 text-[11px] font-normal text-muted">AI</span> : null}
                  </div>
                  <div className="text-[11.5px] text-muted">to {c.route}</div>
                </button>
              );
            })}
          </div>
        </Section>
      </div>

      {pending ? (
        <DecisionBar
          state={state}
          approveLabel={category === p.category ? "Approve" : "Approve with correction"}
          onApprove={() => state.decide("approve", { category })}
          onReject={() => state.decide("reject", {})}
          hint={
            <>
              Approve routes the email to <span className="font-medium text-ink">{target?.route}</span>
              {category !== p.category ? <> as {target?.label.toLowerCase()} (corrected from {label(p.category)})</> : null}. Reject closes it without routing.
            </>
          }
        />
      ) : (
        <DecidedSummary item={item} />
      )}
    </div>
  );
}

// ------------------------------------------------------------------------------------------ invoice

type FieldKind = "text" | "date" | "money" | "rate";
const INVOICE_FIELDS: { key: keyof InvoiceFields; label: string; kind: FieldKind; checks: string[] }[] = [
  { key: "vendor", label: "Vendor", kind: "text", checks: ["known_vendor", "required_fields"] },
  { key: "vendor_vat_id", label: "Vendor VAT ID", kind: "text", checks: ["vendor_vat_id"] },
  { key: "invoice_number", label: "Invoice number", kind: "text", checks: ["has_invoice_number", "number_in_document", "duplicate"] },
  { key: "issue_date", label: "Issue date", kind: "date", checks: [] },
  { key: "due_date", label: "Due date", kind: "date", checks: ["due_after_issue"] },
  { key: "currency", label: "Currency", kind: "text", checks: [] },
  { key: "subtotal", label: "Subtotal", kind: "money", checks: ["lines_sum_to_subtotal"] },
  { key: "vat_rate", label: "VAT rate (%)", kind: "rate", checks: [] },
  { key: "vat_amount", label: "VAT amount", kind: "money", checks: ["vat_amount"] },
  { key: "total", label: "Total", kind: "money", checks: ["total", "total_in_document", "below_auto_post_limit"] },
];

function initialValue(inv: InvoiceFields, key: keyof InvoiceFields, kind: FieldKind): string {
  const v = inv[key];
  if (v === null || v === undefined) return "";
  if (kind === "rate" && typeof v === "number") return String(Math.round(v * 10000) / 100);
  return String(v);
}

function parseValue(raw: string, kind: FieldKind): string | number | null {
  const s = raw.trim();
  if (!s) return null;
  if (kind === "money" || kind === "rate") {
    const n = Number(s.replace(/,/g, ""));
    if (Number.isNaN(n)) return s;
    return kind === "rate" ? Math.round((n / 100) * 10000) / 10000 : n;
  }
  return s;
}

export function InvoicePanel({ item, onDecided }: { item: ReviewItem; onDecided: (r: DecisionResult) => void }) {
  const p = item.proposal as InvoiceProposal;
  const inv = p.invoice ?? {};
  const checks = p.checks ?? [];
  const byCode = new Map<string, Check>(checks.map((c) => [c.code, c]));
  const issues = p.issues ?? [];
  const [values, setValues] = useState<Record<string, string>>(() => {
    const applied = { ...inv, ...((decidedEdits(item).invoice as InvoiceFields | undefined) ?? {}) };
    return Object.fromEntries(INVOICE_FIELDS.map((f) => [f.key, initialValue(applied, f.key, f.kind)]));
  });
  const state = useDecision(item, onDecided);
  const pending = item.status === "pending";

  const edits: Record<string, unknown> = {};
  for (const f of INVOICE_FIELDS) {
    if (values[f.key] !== initialValue(inv, f.key, f.kind)) edits[f.key] = parseValue(values[f.key], f.kind);
  }
  const changed = Object.keys(edits).length;
  const invalid = INVOICE_FIELDS.filter((f) => (f.kind === "money" || f.kind === "rate") && values[f.key].trim() && Number.isNaN(Number(values[f.key].replace(/,/g, ""))));
  const lines = inv.lines ?? [];
  const lineSum = lines.reduce((s, l) => s + (l.amount ?? 0), 0);

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-[13px]">
        {p.filename ? (
          <span className="inline-flex items-center gap-1.5 rounded-md border border-line bg-zinc-50 px-2 py-1 text-[12.5px] text-ink-soft">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z M14 3v5h5" />
            </svg>
            {p.filename}
          </span>
        ) : null}
        <span className="flex items-center gap-2 text-muted">
          Extraction confidence <Confidence value={inv.confidence} />
        </span>
        {p.dedupe_key ? <span className="mono text-[11.5px] text-faint">dedupe {p.dedupe_key}</span> : null}
      </div>

      {issues.length ? (
        <div className="mb-5 flex flex-wrap items-center gap-1.5">
          <span className="text-[12.5px] text-muted">Issues:</span>
          {issues.map((i) => (
            <Pill key={i} tone="red">
              {label(i)}
            </Pill>
          ))}
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-5 2xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <Section title="Extracted fields" aside={changed ? <span className="text-[12px] text-brand">{changed} corrected</span> : null}>
          <div className="overflow-hidden rounded-lg border border-line">
            <table className="w-full text-[13px]">
              <tbody className="divide-y divide-line-soft">
                {INVOICE_FIELDS.map((f) => {
                  const related = f.checks.map((c) => byCode.get(c)).filter((c): c is Check => Boolean(c));
                  const failed = related.filter((c) => !c.ok);
                  const isChanged = values[f.key] !== initialValue(inv, f.key, f.kind);
                  return (
                    <tr key={f.key} className={failed.length ? "bg-bad-soft/50" : ""}>
                      <td className="w-[34%] px-3 py-1.5 align-top text-[12.5px] text-muted">
                        <div className="pt-1.5">{f.label}</div>
                      </td>
                      <td className="px-3 py-1.5">
                        <input
                          className={`field h-8 ${f.kind !== "text" ? "tnum" : ""} ${isChanged ? "field-changed" : failed.length ? "field-bad" : ""}`}
                          value={values[f.key]}
                          disabled={!pending}
                          placeholder="missing"
                          inputMode={f.kind === "money" || f.kind === "rate" ? "decimal" : undefined}
                          onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
                          aria-label={f.label}
                        />
                        {failed.map((c) => (
                          <div key={c.code} className="mt-1 text-[12px] text-bad">
                            <span className="mono font-medium">{c.code}</span>
                            {c.detail ? `: ${c.detail}` : ""}
                          </div>
                        ))}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Section>

        <div className="space-y-5">
          <Section title="Bookkeeping checks" aside={<span className="text-[12px] text-muted">{checks.filter((c) => !c.ok).length} failed of {checks.length}</span>}>
            <Checks checks={checks} />
          </Section>
          <Section title="Why it needs approval">
            <Reasons reasons={item.reasons.length ? item.reasons.map(label) : issues.map(label)} />
          </Section>
        </div>
      </div>

      <div className="mt-5">
        <Section title={`Line items (${lines.length})`}>
          <div className="scroll-thin overflow-x-auto rounded-lg border border-line">
            <table className="w-full text-[12.5px]">
              <thead>
                <tr className="border-b border-line bg-zinc-50 text-left text-[11.5px] text-muted">
                  <th className="px-3 py-1.5 font-medium">Description</th>
                  <th className="px-3 py-1.5 text-right font-medium">Qty</th>
                  <th className="px-3 py-1.5 text-right font-medium">Unit price</th>
                  <th className="px-3 py-1.5 text-right font-medium">Amount</th>
                  <th className="px-3 py-1.5 text-right font-medium">Qty x price</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line-soft">
                {lines.map((l, i) => {
                  const calc = l.quantity != null && l.unit_price != null ? Math.round(l.quantity * l.unit_price * 100) / 100 : null;
                  const off = calc !== null && l.amount != null && Math.abs(calc - l.amount) > 0.02;
                  return (
                    <tr key={i} className={off ? "bg-bad-soft/60" : ""}>
                      <td className="px-3 py-1.5 text-ink">{l.description || DASH}</td>
                      <td className="px-3 py-1.5 text-right tnum">{display(l.quantity)}</td>
                      <td className="px-3 py-1.5 text-right tnum">{money(l.unit_price)}</td>
                      <td className={`px-3 py-1.5 text-right tnum ${off ? "font-medium text-bad" : ""}`}>{money(l.amount)}</td>
                      <td className={`px-3 py-1.5 text-right tnum ${off ? "text-bad" : "text-faint"}`}>{money(calc)}</td>
                    </tr>
                  );
                })}
                {!lines.length ? (
                  <tr>
                    <td colSpan={5} className="px-3 py-3 text-center text-muted">
                      No line items extracted.
                    </td>
                  </tr>
                ) : null}
              </tbody>
              {lines.length ? (
                <tfoot>
                  <tr className="border-t border-line bg-zinc-50 text-[12.5px]">
                    <td colSpan={3} className="px-3 py-1.5 text-right text-muted">
                      Sum of lines
                    </td>
                    <td className="px-3 py-1.5 text-right font-medium tnum">{money(lineSum, inv.currency)}</td>
                    <td />
                  </tr>
                </tfoot>
              ) : null}
            </table>
          </div>
        </Section>
      </div>

      {pending ? (
        <DecisionBar
          state={state}
          approveLabel={changed ? "Approve with corrections (post bill)" : "Approve (post bill)"}
          rejectLabel="Reject"
          onApprove={() => state.decide("approve", changed ? { invoice: edits } : {})}
          onReject={() => state.decide("reject", {})}
          approveDisabled={invalid.length ? `Not a number: ${invalid.map((f) => f.label).join(", ")}` : null}
          hint={
            invalid.length ? (
              <span className="text-bad">Fix the number format in {invalid.map((f) => f.label).join(", ")}.</span>
            ) : (
              <>
                Approve posts the bill to accounting{changed ? " with your corrections" : ""}
                {inv.total != null ? (
                  <>
                    {" "}
                    for <span className="font-medium text-ink">{money(typeof edits.total === "number" ? edits.total : inv.total, (edits.currency as string | undefined) ?? inv.currency)}</span>
                  </>
                ) : null}
                .
              </>
            )
          }
        />
      ) : (
        <DecidedSummary item={item} />
      )}
    </div>
  );
}

// ------------------------------------------------------------------------------------------ lead reply

export function LeadReplyPanel({ item, onDecided }: { item: ReviewItem; onDecided: (r: DecisionResult) => void }) {
  const p = item.proposal as LeadReplyProposal;
  const sent = decidedEdits(item);
  const [to, setTo] = useState(typeof sent.to === "string" ? sent.to : (p.to ?? p.lead?.email ?? ""));
  const [subject, setSubject] = useState(typeof sent.subject === "string" ? sent.subject : (p.subject ?? ""));
  const [body, setBody] = useState(typeof sent.body === "string" ? sent.body : (p.body ?? ""));
  const state = useDecision(item, onDecided);
  const pending = item.status === "pending";
  const edited = to !== (p.to ?? p.lead?.email ?? "") || subject !== (p.subject ?? "") || body !== (p.body ?? "");
  const words = body.trim() ? body.trim().split(/\s+/).length : 0;
  const empty = !to.trim() || !subject.trim() || !body.trim();

  return (
    <div>
      <div className="grid grid-cols-1 gap-5 2xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <div className="space-y-5">
          <Section title="Lead score">
            <Tier tier={p.tier} score={p.score} />
          </Section>
          <Section title="Lead (web form)">
            <LeadCard lead={p.lead} />
          </Section>
          <Section title="Company profile (from their website)">
            <ProfileCard profile={p.profile} />
          </Section>
        </div>

        <div className="space-y-5">
          <Section title="Reply draft" aside={edited ? <span className="text-[12px] text-brand">edited</span> : <span className="text-[12px] text-muted">AI draft</span>}>
            <div className="space-y-2.5">
              <label className="block">
                <span className="mb-1 block text-[11.5px] text-muted">To</span>
                <input className="field h-9" value={to} onChange={(e) => setTo(e.target.value)} disabled={!pending} />
              </label>
              <label className="block">
                <span className="mb-1 block text-[11.5px] text-muted">Subject</span>
                <input className="field h-9" value={subject} onChange={(e) => setSubject(e.target.value)} disabled={!pending} />
              </label>
              <label className="block">
                <span className="mb-1 flex justify-between text-[11.5px] text-muted">
                  <span>Body</span>
                  <span className="tnum">{words} words</span>
                </span>
                <textarea className="field min-h-[280px] resize-y leading-relaxed" value={body} onChange={(e) => setBody(e.target.value)} disabled={!pending} rows={13} />
              </label>
            </div>
          </Section>
          <Section title="Draft checks">
            <Checks checks={p.checks} />
          </Section>
          <Section title="Why it needs a person">
            <Reasons reasons={item.reasons} />
          </Section>
        </div>
      </div>

      {pending ? (
        <DecisionBar
          state={state}
          approveLabel={edited ? "Approve edits and send" : "Approve and send"}
          rejectLabel="Reject"
          onApprove={() => state.decide("approve", { to: to.trim(), subject, body })}
          onReject={() => state.decide("reject", {})}
          approveDisabled={empty ? "To, subject and body are required" : null}
          hint={
            <>
              Sends from sales@kestrelprint.example to <span className="font-medium text-ink">{to || "nobody"}</span>. Reject keeps the lead in the CRM without a reply.
            </>
          }
        />
      ) : (
        <DecidedSummary item={item} />
      )}
    </div>
  );
}

// ------------------------------------------------------------------------------------------ lead research

export function LeadResearchPanel({ item, onDecided }: { item: ReviewItem; onDecided: (r: DecisionResult) => void }) {
  const p = item.proposal as LeadResearchProposal;
  const site: SiteInfo = p.site ?? {};
  const state = useDecision(item, onDecided);
  const pending = item.status === "pending";
  return (
    <div>
      <div className="grid grid-cols-1 gap-5 2xl:grid-cols-2">
        <div className="space-y-5">
          <Section title="Why it went to research">
            <Reasons reasons={item.reasons} />
          </Section>
          <Section title="Lead score">
            <Tier tier={p.tier} score={p.score} />
          </Section>
          <Section title="Lead (web form)">
            <LeadCard lead={p.lead} />
          </Section>
        </div>
        <div className="space-y-5">
          <Section title="Website">
            <Fields
              items={[
                ["URL", site.url ? <span className="mono text-[12px] break-all">{site.url}</span> : DASH],
                ["Found", site.found === undefined ? DASH : site.found ? <Pill tone="green">found</Pill> : <Pill tone="red">not found</Pill>],
                ["Pages read", site.pages?.length ? site.pages.join(", ") : DASH],
                ["Hidden text removed", num(site.removed_hidden ?? 0)],
                [
                  "AI-directed text removed",
                  site.removed_ai_directed ? (
                    <Pill tone="red" title="Text addressed to an AI model was stripped before the model saw the page">
                      {site.removed_ai_directed} removed
                    </Pill>
                  ) : (
                    "0"
                  ),
                ],
                ["Fetch error", site.error ? <span className="text-bad">{site.error}</span> : DASH],
              ]}
            />
          </Section>
          <Section title="Company profile">
            <ProfileCard profile={p.profile} />
          </Section>
        </div>
      </div>
      {pending ? (
        <DecisionBar
          state={state}
          approveLabel="Approve (keep in CRM)"
          rejectLabel="Reject lead"
          onApprove={() => state.decide("approve", {})}
          onReject={() => state.decide("reject", {})}
          hint="No reply is drafted for research leads; approving closes the research task with the lead kept in the CRM."
        />
      ) : (
        <DecidedSummary item={item} />
      )}
    </div>
  );
}

// ------------------------------------------------------------------------------------------ unknown kinds

export function GenericPanel({ item, onDecided }: { item: ReviewItem; onDecided: (r: DecisionResult) => void }) {
  const state = useDecision(item, onDecided);
  const pending = item.status === "pending";
  return (
    <div className="space-y-5">
      <Section title="Reasons">
        <Reasons reasons={item.reasons} />
      </Section>
      <Section title="Proposal">
        <pre className="mono scroll-thin max-h-[420px] overflow-auto rounded-lg border border-line bg-zinc-50 p-3 text-[11.5px]">{JSON.stringify(item.proposal, null, 2)}</pre>
      </Section>
      {pending ? <DecisionBar state={state} approveLabel="Approve" onApprove={() => state.decide("approve", {})} onReject={() => state.decide("reject", {})} /> : <DecidedSummary item={item} />}
    </div>
  );
}

export function RunLink({ item }: { item: ReviewItem }) {
  return (
    <Link href={`/runs/${item.run_id}`} className="text-brand hover:underline">
      Run details
    </Link>
  );
}

