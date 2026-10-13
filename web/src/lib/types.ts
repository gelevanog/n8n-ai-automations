// Shapes of the Relay API (src/relay/api/app.py, src/relay/ops/store.py).

export type WorkflowKey = "triage" | "invoice" | "lead" | "faq" | "digest";
export type RunStatus = "running" | "auto" | "review" | "approved" | "rejected" | "escalated" | "error";
export type ReviewStatus = "pending" | "approved" | "rejected" | "failed";
export type ReviewKind = "triage" | "invoice" | "lead_reply" | "lead_research";
export type DeadLetterStatus = "open" | "replayed" | "resolved";
export type Category = "sales_lead" | "support" | "invoice" | "spam" | "other";

export interface Health {
  status: string;
  version: string;
  model: string;
  free_only: boolean;
}

export interface Totals {
  runs: number;
  auto: number;
  review: number;
  errors: number;
  duplicates: number;
  pending_review: number;
  open_dead_letters: number;
  llm_calls: number;
  cached_calls: number;
  invalid_outputs: number;
  cost_usd: number;
}

export interface WorkflowMetrics {
  runs: number;
  auto: number;
  review: number;
  pending_review: number;
  errors: number;
  duplicates: number;
  auto_rate: number | null;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  llm_calls: number;
  llm_tokens: number;
  cost_usd: number;
  cost_per_run_usd: number | null;
}

export interface DayBucket {
  runs: number;
  auto: number;
  review: number;
  errors: number;
}

export interface Metrics {
  totals: Totals;
  workflows: Partial<Record<WorkflowKey, WorkflowMetrics>>;
  models: Record<string, number>;
  days: Record<string, DayBucket>;
}

export interface Run {
  id: string;
  workflow: WorkflowKey;
  status: RunStatus;
  route: string | null;
  title: string;
  source: string;
  execution_id: string | null;
  attempts: number;
  duplicates: number;
  started_at: string | null;
  finished_at: string | null;
  latency_ms: number | null;
  error_message: string | null;
}

export interface GatewayCall {
  id: string;
  run_id?: string | null;
  task: string;
  model: string;
  served_model: string | null;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  cached: boolean;
  attempts: number;
  schema_valid: boolean;
  decision: string;
  error_message: string | null;
  created_at: string | null;
}

export interface Check {
  code: string;
  ok: boolean;
  detail?: string;
}

export type JsonObject = Record<string, unknown>;

export interface ReviewItem {
  id: string;
  run_id: string;
  workflow: WorkflowKey;
  kind: ReviewKind | string;
  title: string;
  reasons: string[];
  proposal: JsonObject;
  status: ReviewStatus;
  decision: JsonObject;
  decided_by: string | null;
  created_at: string | null;
  decided_at: string | null;
  can_resume: boolean;
}

export interface ResumeResult {
  delivered: boolean;
  status_code?: number;
  error?: string;
}

export interface ReviewDetail extends ReviewItem {
  run: RunDetail | null;
  resume?: ResumeResult;
}

export interface DeadLetter {
  id: string;
  workflow: string;
  run_id: string | null;
  execution_id: string | null;
  node: string;
  error_message: string;
  status: DeadLetterStatus;
  replays: number;
  created_at: string | null;
  updated_at: string | null;
  has_payload: boolean;
}

export interface ReplayResult {
  ok: boolean;
  status_code?: number;
  error?: string;
  response?: unknown;
  dead_letter: DeadLetter;
}

export interface RunDetail extends Run {
  input: JsonObject;
  output: JsonObject | null;
  idempotency_key: string;
  calls?: GatewayCall[];
  reviews?: ReviewItem[];
  dead_letters?: DeadLetter[];
}

// ---- review proposals

export interface Contact {
  name?: string | null;
  company?: string | null;
  email?: string | null;
  phone?: string | null;
}

export interface TriageProposal {
  category?: Category;
  confidence?: number;
  summary?: string;
  contact?: Contact;
  lead?: { product?: string | null; quantity?: number | null; deadline?: string | null };
  support?: { order_id?: string | null; issue?: string | null };
  invoice?: { vendor?: string | null; invoice_number?: string | null; amount?: number | null; currency?: string | null };
  from?: { name?: string; email?: string };
  text?: string;
  checks?: Check[];
}

export interface InvoiceLine {
  description?: string;
  quantity?: number | null;
  unit_price?: number | null;
  amount?: number | null;
}

export interface InvoiceFields {
  vendor?: string | null;
  vendor_vat_id?: string | null;
  invoice_number?: string | null;
  issue_date?: string | null;
  due_date?: string | null;
  currency?: string | null;
  subtotal?: number | null;
  vat_rate?: number | null;
  vat_amount?: number | null;
  total?: number | null;
  lines?: InvoiceLine[];
  confidence?: number;
}

export interface InvoiceProposal {
  invoice?: InvoiceFields;
  checks?: Check[];
  issues?: string[];
  dedupe_key?: string;
  filename?: string;
}

export interface CompanyProfile {
  industry?: string | null;
  employees_band?: string | null;
  country?: string | null;
  sells_online?: boolean | null;
  buyer?: boolean;
  summary?: string;
  confidence?: number;
}

export interface LeadIn {
  name?: string;
  email?: string;
  company?: string;
  website?: string;
  message?: string;
  product?: string | null;
  quantity?: number | null;
}

export interface SiteInfo {
  url?: string;
  found?: boolean;
  pages?: string[];
  removed_hidden?: number;
  removed_ai_directed?: number;
  error?: string | null;
}

export interface LeadReplyProposal {
  to?: string;
  subject?: string;
  body?: string;
  checks?: Check[];
  tier?: string;
  score?: number;
  profile?: CompanyProfile;
  lead?: LeadIn;
}

export interface LeadResearchProposal {
  tier?: string;
  score?: number;
  profile?: CompanyProfile;
  site?: SiteInfo;
  lead?: LeadIn;
}

// ---- evaluation

export interface EvalRun {
  suite: string;
  name: string | null;
  model: string | null;
  run_at: string | null;
  summary: Record<string, unknown>;
}

export interface EvalSummary {
  runs: Record<string, EvalRun>;
  headline: { suite: string; run: string | null; metric: string; value: unknown }[];
}
