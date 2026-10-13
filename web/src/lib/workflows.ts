import { N8N_URL } from "@/lib/api";
import type { Category, WorkflowKey } from "@/lib/types";

export interface WorkflowInfo {
  key: WorkflowKey | "errors";
  name: string;
  n8nId: string;
  /** Tailwind classes for the small square that identifies the workflow. */
  dot: string;
  tagline: string;
  trigger: string;
  steps: string[];
  gate: string;
  writes: { system: string; what: string }[];
  ai: string;
}

export const WORKFLOWS: Record<WorkflowKey, WorkflowInfo> = {
  triage: {
    key: "triage",
    name: "Inbox triage",
    n8nId: "RelayInboxTriage",
    dot: "bg-indigo-500",
    tagline: "Classifies every email to hello@kestrelprint.example and routes it to the queue that handles it.",
    trigger: "Webhook inbox-triage (Postmark-style inbound JSON). The Mailpit bridge posts every message sent to the shared inbox.",
    steps: [
      "Start run: idempotency check on the message id; a repeat delivery is answered from the first run",
      "AI gateway classifies the email: sales lead, support, invoice, spam or other, with the fields each queue needs",
      "Every extracted value is checked against the email text; invoices are routed only from known vendors",
      "Route: CRM lead plus #sales alert, helpdesk ticket, invoice PDF forwarded to invoice intake, or archive",
    ],
    gate: "Low confidence, unknown vendors, spam below 0.90 and text addressed to an AI wait at Wait for reviewer. The reviewer confirms or corrects the category and the execution resumes on the chosen route.",
    writes: [
      { system: "CRM", what: "lead with contact, product, quantity, deadline" },
      { system: "Helpdesk", what: "ticket with order id and issue type" },
      { system: "Invoice intake", what: "the attached PDF" },
      { system: "Chat", what: "#sales and #finance notifications" },
    ],
    ai: "classify-email",
  },
  invoice: {
    key: "invoice",
    name: "Invoice intake",
    n8nId: "RelayInvoiceIntake",
    dot: "bg-teal-500",
    tagline: "Reads supplier invoice PDFs, checks them like a bookkeeper and posts clean bills to accounting.",
    trigger: "Webhook invoice-intake: a PDF from inbox triage, an upload or an API call (base64).",
    steps: [
      "Fingerprint the PDF (SHA-256) and start the run; the same file twice is a duplicate",
      "Decode the PDF and read its text with n8n Extract from File",
      "AI gateway extracts vendor, numbers, dates, VAT and line items",
      "Bookkeeping checks: line math, VAT, totals, dates, vendor and VAT id against master data, auto-post limit",
      "Ask accounting whether the invoice is already booked, then post the bill and notify #finance",
    ],
    gate: "Any failed check, a duplicate or a total above 5,000 waits at Wait for approval. The approver can correct fields before the bill is posted.",
    writes: [
      { system: "Accounting", what: "bill with vendor, number, dates, totals, lines" },
      { system: "Chat", what: "#finance notification" },
    ],
    ai: "extract-invoice",
  },
  lead: {
    key: "lead",
    name: "Lead enrichment",
    n8nId: "RelayLeadEnrichment",
    dot: "bg-pink-500",
    tagline: "Researches web-form leads on their own website, scores them against the ideal customer profile and drafts a reply.",
    trigger: "Webhook lead-enrichment: the quote form on the website.",
    steps: [
      "Fetch the company website (fixture sites only); hidden text and text addressed to AI are removed first",
      "AI gateway builds a company profile; the ICP score and tier are computed in code (configs/icp.yaml)",
      "Save the lead to the CRM; hot leads alert #sales",
      "AI gateway drafts a first reply, checked for placeholders, invented numbers and length",
    ],
    gate: "Every first reply is approved by a person: the reviewer edits subject and body, then Approve and send resumes the execution and Send reply emails it. Leads without a usable site, competitors and non-buyers go to research instead.",
    writes: [
      { system: "CRM", what: "lead with profile, score and tier" },
      { system: "Email", what: "the approved reply from sales@kestrelprint.example" },
      { system: "Chat", what: "#sales alert for hot leads" },
    ],
    ai: "enrich-lead, draft-reply",
  },
  faq: {
    key: "faq",
    name: "Support FAQ",
    n8nId: "RelaySupportFaq",
    dot: "bg-orange-500",
    tagline: "Answers customer questions from the knowledge base with citations, and escalates anything it should not answer.",
    trigger: "Webhook support-faq: a customer question from email or the help widget.",
    steps: [
      "AI gateway retrieves knowledge-base articles (BM25) and answers with citations",
      "Gate: escalation rules (orders, refunds, legal, privacy, security, complaints), retrieval coverage, model confidence",
      "Only cited articles are allowed, and no number may appear that is not in the cited article",
      "Passed: email the answer and log a solved ticket",
    ],
    gate: "No reviewer pause: an answer that fails the gate becomes an open helpdesk ticket with the reasons, the draft answer and the suggested articles (status escalated).",
    writes: [
      { system: "Email", what: "the answer to the customer" },
      { system: "Helpdesk", what: "solved ticket, or open ticket with draft and reasons" },
    ],
    ai: "answer-faq",
  },
  digest: {
    key: "digest",
    name: "KPI digest",
    n8nId: "RelayKpiDigest",
    dot: "bg-slate-500",
    tagline: "A weekly summary of what the automations did, posted to #ops.",
    trigger: "Schedule: every Monday 08:00, or the run-now webhook kpi-digest.",
    steps: [
      "The numbers come from SQL: the Postgres node reads the run log",
      "AI gateway writes the headline and bullets only",
      "Every number in the text is checked against the query result; a digest with an unsupported number is replaced by a plain template",
    ],
    gate: "None: the numbers are verified in code before anything is posted.",
    writes: [{ system: "Chat", what: "#ops digest message" }],
    ai: "write-digest",
  },
};

export const WORKFLOW_KEYS = Object.keys(WORKFLOWS) as WorkflowKey[];

export const ERROR_HANDLER = {
  name: "Error handler",
  n8nId: "RelayErrorHandler",
  description:
    "Set as the error workflow of every Relay workflow. Records the failed execution in the dead-letter queue with the run's original input, so it can be replayed from this dashboard or over MCP, and posts an alert to #ops-alerts.",
};

export function workflowName(key: string | null | undefined): string {
  if (!key) return "Unknown";
  return WORKFLOWS[key as WorkflowKey]?.name ?? key;
}

export function n8nWorkflowUrl(key: string): string | null {
  const id = WORKFLOWS[key as WorkflowKey]?.n8nId;
  return id ? `${N8N_URL}/workflow/${id}` : null;
}

export function n8nExecutionUrl(key: string, executionId: string | null | undefined): string | null {
  const id = WORKFLOWS[key as WorkflowKey]?.n8nId;
  return id && executionId ? `${N8N_URL}/workflow/${id}/executions/${executionId}` : null;
}

export const CATEGORIES: { key: Category; label: string; route: string }[] = [
  { key: "sales_lead", label: "Sales lead", route: "CRM" },
  { key: "support", label: "Support", route: "Helpdesk" },
  { key: "invoice", label: "Invoice", route: "Accounting" },
  { key: "spam", label: "Spam", route: "Archive" },
  { key: "other", label: "Other", route: "Archive" },
];

const INDUSTRIES: Record<string, string> = {
  food_beverage: "Food and beverage",
  cosmetics_beauty: "Cosmetics and beauty",
  fashion_apparel: "Fashion and apparel",
  home_lifestyle: "Home and lifestyle",
  health_wellness: "Health and wellness",
  printing_packaging: "Printing and packaging",
};

export function industryLabel(value: string | null | undefined): string {
  if (!value) return "\u2014";
  return INDUSTRIES[value] ?? value.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

export const REVIEW_KINDS: Record<string, string> = {
  triage: "Email triage",
  invoice: "Invoice approval",
  lead_reply: "Reply approval",
  lead_research: "Lead research",
};

export const ROUTES: Record<string, string> = {
  crm: "CRM",
  helpdesk: "Helpdesk",
  accounting: "Accounting",
  archive: "Archive",
  review: "Review",
  email_sent: "Email sent",
  auto_reply: "Auto-reply",
  escalate: "Escalated",
  chat: "Chat",
};

export function routeLabel(route: string | null | undefined): string {
  if (!route) return "—";
  return ROUTES[route] ?? route.replace(/_/g, " ");
}
