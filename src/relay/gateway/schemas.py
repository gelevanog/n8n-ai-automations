"""Request, model-output and response schemas of the AI gateway.

Two layers on purpose: `*Output` models are what the LLM must return (validated strictly; a reply that does not fit
is sent back once with the validation errors), `*Response` models are what the workflow gets: the validated output
plus the deterministic checks, the decision and the call metadata. Workflows branch only on `decision`, never on free
text, so the same gateway can sit behind n8n, Make or Zapier."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Category = Literal["sales_lead", "support", "invoice", "spam", "other"]
Product = Literal["mailer_boxes", "folding_cartons", "paper_bags", "labels", "stickers", "tissue_paper", "other"]
Issue = Literal["shipping", "damage", "artwork", "billing", "account", "order_change", "quality", "other"]
Industry = Literal[
    "food_beverage",
    "cosmetics_beauty",
    "fashion_apparel",
    "home_lifestyle",
    "pets",
    "health_wellness",
    "agency",
    "printing_packaging",
    "education",
    "technology",
    "other",
]
EmployeesBand = Literal["1-10", "11-50", "51-200", "201-500", "500+"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="ignore")


# ---------------------------------------------------------------------------------------------------- common


class CallMeta(Strict):
    call_id: str = ""
    task: str = ""
    model: str = ""
    served_model: str | None = None
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cached: bool = False
    attempts: int = 1
    schema_valid: bool = True


class Check(Strict):
    code: str
    ok: bool
    detail: str = ""


# ---------------------------------------------------------------------------------------------------- triage


class Sender(Strict):
    name: str = ""
    email: str = ""


class AttachmentRef(Strict):
    filename: str
    content_type: str = "application/octet-stream"


class TriageRequest(Strict):
    message_id: str = ""
    sender: Sender = Field(default_factory=Sender, validation_alias="from")
    subject: str = ""
    text: str = Field(default="", max_length=60_000)
    attachments: list[AttachmentRef] = Field(default_factory=list)
    run_id: str | None = None

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class Contact(Strict):
    name: str | None = None
    company: str | None = None
    email: str | None = None
    phone: str | None = None


class LeadFields(Strict):
    product: Product | None = None
    quantity: int | None = Field(default=None, ge=0, le=10_000_000)
    deadline: str | None = None


class SupportFields(Strict):
    order_id: str | None = None
    issue: Issue | None = None


class InvoiceMention(Strict):
    vendor: str | None = None
    invoice_number: str | None = None
    amount: float | None = Field(default=None, ge=0)
    currency: str | None = None


class TriageOutput(Strict):
    """What the model must return for an email."""

    category: Category
    confidence: float = Field(ge=0, le=1)
    summary: str = Field(max_length=400)
    language: str = Field(default="en", max_length=8)
    urgency: Literal["low", "normal", "high"] = "normal"
    contact: Contact = Field(default_factory=Contact)
    lead: LeadFields = Field(default_factory=LeadFields)
    support: SupportFields = Field(default_factory=SupportFields)
    invoice: InvoiceMention = Field(default_factory=InvoiceMention)

    @field_validator("language")
    @classmethod
    def _lower(cls, value: str) -> str:
        return value.lower()[:2] or "en"


Route = Literal["crm", "helpdesk", "accounting", "archive", "review"]


class TriageResponse(TriageOutput):
    decision: Literal["route", "review"]
    route: Route
    reasons: list[str] = Field(default_factory=list)
    checks: list[Check] = Field(default_factory=list)
    has_invoice_attachment: bool = False
    injection_suspected: bool = False
    meta: CallMeta = Field(default_factory=CallMeta)


# ---------------------------------------------------------------------------------------------------- invoices


class InvoiceRequest(Strict):
    text: str = Field(max_length=100_000)
    filename: str = ""
    source: str = "upload"
    run_id: str | None = None


class InvoiceLine(Strict):
    description: str = ""
    quantity: float | None = None
    unit_price: float | None = None
    amount: float | None = None


class VatLine(Strict):
    rate: float = Field(ge=0, le=1, description="fraction, 0.09 for 9%")
    base: float
    amount: float

    @field_validator("rate", mode="before")
    @classmethod
    def _fraction(cls, value: object) -> object:
        return _as_fraction(value)


def _as_fraction(value: object) -> object:
    """21 or "21%" -> 0.21 (models often answer in percent)."""
    if isinstance(value, str):
        value = value.strip().rstrip("%").strip() or None
        if value is None:
            return None
        value = float(value)
    if isinstance(value, int | float) and value > 1:
        return round(float(value) / 100, 4)
    return value


class InvoiceOutput(Strict):
    document_type: Literal["invoice", "credit_note", "statement", "reminder", "other"] = "invoice"
    vendor: str | None = None
    vendor_vat_id: str | None = None
    invoice_number: str | None = None
    issue_date: str | None = Field(default=None, description="ISO 8601 date YYYY-MM-DD")
    due_date: str | None = None
    currency: str | None = None
    subtotal: float | None = None
    vat_rate: float | None = Field(default=None, ge=0, le=1, description="fraction, 0.21 for 21%")
    vat_amount: float | None = None
    total: float | None = None
    vat_breakdown: list[VatLine] = Field(default_factory=list)
    amount_paid: float | None = None
    balance_due: float | None = None
    lines: list[InvoiceLine] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)

    @field_validator("vat_rate", mode="before")
    @classmethod
    def _fraction(cls, value: object) -> object:
        return _as_fraction(value)


class InvoiceResponse(Strict):
    invoice: InvoiceOutput
    confidence: float
    decision: Literal["auto_post", "review"]
    issues: list[str] = Field(default_factory=list)
    checks: list[Check] = Field(default_factory=list)
    dedupe_key: str = ""
    meta: CallMeta = Field(default_factory=CallMeta)


# ---------------------------------------------------------------------------------------------------- leads


class LeadRequest(Strict):
    name: str = ""
    email: str = ""
    company: str = ""
    website: str = ""
    message: str = Field(default="", max_length=10_000)
    product: str | None = None
    quantity: int | None = None
    run_id: str | None = None


class CompanyProfile(Strict):
    industry: Industry | None = None
    employees_band: EmployeesBand | None = None
    country: str | None = Field(default=None, description="ISO 3166-1 alpha-2")
    sells_online: bool | None = None
    buyer: bool = Field(default=True, description="false for students, job seekers, researchers, vendors")
    summary: str = Field(default="", max_length=400)
    confidence: float = Field(default=0.5, ge=0, le=1)

    @field_validator("country")
    @classmethod
    def _upper(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().upper()
        return {"UK": "GB"}.get(value, value) or None


class SiteInfo(Strict):
    url: str = ""
    found: bool = False
    pages: list[str] = Field(default_factory=list)
    removed_hidden: int = 0
    removed_ai_directed: int = 0
    error: str | None = None


class LeadResponse(Strict):
    site: SiteInfo
    profile: CompanyProfile
    score: int
    tier: Literal["hot", "warm", "cold", "disqualified"]
    disqualified: str | None = None
    score_breakdown: dict[str, int] = Field(default_factory=dict)
    decision: Literal["draft_reply", "review"]
    reasons: list[str] = Field(default_factory=list)
    meta: CallMeta = Field(default_factory=CallMeta)


class DraftRequest(Strict):
    lead: LeadRequest
    profile: CompanyProfile = Field(default_factory=CompanyProfile)
    tier: str = "warm"
    run_id: str | None = None


class DraftOutput(Strict):
    subject: str = Field(max_length=160)
    body: str = Field(max_length=4000)


class DraftResponse(DraftOutput):
    checks: list[Check] = Field(default_factory=list)
    ok: bool = True
    meta: CallMeta = Field(default_factory=CallMeta)


# ---------------------------------------------------------------------------------------------------- FAQ


class FaqRequest(Strict):
    question: str = Field(max_length=8000)
    customer_email: str = ""
    channel: str = "email"
    run_id: str | None = None


class FaqOutput(Strict):
    answerable: bool
    answer: str = Field(default="", max_length=3000)
    citations: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)
    escalation_reason: str | None = None


class RetrievedArticle(Strict):
    id: str
    title: str
    score: float


class FaqResponse(FaqOutput):
    decision: Literal["auto_reply", "escalate"]
    reasons: list[str] = Field(default_factory=list)
    retrieved: list[RetrievedArticle] = Field(default_factory=list)
    checks: list[Check] = Field(default_factory=list)
    meta: CallMeta = Field(default_factory=CallMeta)


# ---------------------------------------------------------------------------------------------------- digest


class DigestRequest(Strict):
    period: str
    metrics: dict[str, float | int | str]
    run_id: str | None = None


class DigestOutput(Strict):
    headline: str = Field(max_length=200)
    bullets: list[str] = Field(max_length=8)


class DigestResponse(DigestOutput):
    text: str
    numbers_ok: bool
    unsupported_numbers: list[str] = Field(default_factory=list)
    meta: CallMeta = Field(default_factory=CallMeta)
