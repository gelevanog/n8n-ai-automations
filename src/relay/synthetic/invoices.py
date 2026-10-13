"""Synthetic supplier invoices for Kestrel Print Co. (a fictional packaging printer), rendered as text PDFs.

Six fictional vendors with four layouts (English and German labels, ISO / European / US date and number formats,
VAT at 21%, 9%, reverse charge and none). About a third of the invoices carry a planted problem a bookkeeper must
catch: totals that do not add up, a wrong VAT amount, a line that is not quantity x price, a duplicate, a missing
invoice number, a due date before the issue date, or an amount above the auto-posting limit. The gold labels hold the
values as printed (what extraction should read) and the decision a careful bookkeeper would take."""

from __future__ import annotations

import datetime as dt
import json
import random
from dataclasses import asdict, dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from fpdf import FPDF

BUYER = ("Kestrel Print Co. B.V.", "Kanaalweg 14, 3526 KL Utrecht, Netherlands", "NL004495445B01")


@dataclass(frozen=True)
class Vendor:
    key: str
    name: str
    address: str
    vat_id: str | None
    currency: str
    vat_rate: Decimal
    vat_note: str
    layout: str
    items: tuple[tuple[str, Decimal, Decimal], ...]  # description, min unit price, max unit price
    prefix: str


VENDORS = (
    Vendor(
        "veldhoven",
        "Veldhoven Paper B.V.",
        "Industrieweg 88, 5504 AB Veldhoven, Netherlands",
        "NL812334966B01",
        "EUR",
        Decimal("0.21"),
        "VAT 21%",
        "A",
        (
            ("Corrugated board E-flute, white, 1200x800 mm (sheet)", Decimal("0.42"), Decimal("0.61")),
            ("Folding boxboard GC1 350 g/m2 (sheet)", Decimal("0.28"), Decimal("0.39")),
            ("Kraft liner 140 g/m2 (roll, 500 m)", Decimal("118.00"), Decimal("146.00")),
            ("Pallet delivery", Decimal("35.00"), Decimal("55.00")),
        ),
        "VP-",
    ),
    Vendor(
        "inkwell",
        "Inkwell Supplies GmbH",
        "Druckerstrasse 7, 50667 Koeln, Germany",
        "DE298765431",
        "EUR",
        Decimal("0.00"),
        "Reverse charge - VAT to be accounted for by the recipient (Art. 196 VAT Directive)",
        "B",
        (
            ("Soy-based offset ink, cyan, 2.5 kg", Decimal("38.50"), Decimal("44.00")),
            ("Soy-based offset ink, magenta, 2.5 kg", Decimal("38.50"), Decimal("44.00")),
            ("Water-based coating, aqueous matt, 20 l", Decimal("96.00"), Decimal("120.00")),
            ("Blanket wash, low-VOC, 10 l", Decimal("54.00"), Decimal("63.00")),
        ),
        "RE-",
    ),
    Vendor(
        "swiftline",
        "Swiftline Logistics Ltd",
        "Unit 4, Riverside Park, Felixstowe IP11 3AA, United Kingdom",
        "GB123456789",
        "GBP",
        Decimal("0.00"),
        "Outside the scope of UK VAT (services to an EU business customer)",
        "C",
        (
            ("Freight Rotterdam - Felixstowe, per pallet", Decimal("84.00"), Decimal("112.00")),
            ("Customs clearance (import)", Decimal("45.00"), Decimal("65.00")),
            ("Last-mile delivery, per drop", Decimal("18.00"), Decimal("26.00")),
        ),
        "SL",
    ),
    Vendor(
        "cloudforge",
        "Cloudforge Software Inc.",
        "500 Market Street, Suite 300, San Francisco, CA 94105, USA",
        None,
        "USD",
        Decimal("0.00"),
        "No sales tax charged (export of services)",
        "D",
        (
            ("Cloudforge Prepress Suite, Team plan (monthly seat)", Decimal("49.00"), Decimal("49.00")),
            ("Cloud rendering minutes, 1,000-minute pack", Decimal("29.00"), Decimal("29.00")),
            ("Priority support add-on", Decimal("99.00"), Decimal("99.00")),
        ),
        "CF-",
    ),
    Vendor(
        "energie",
        "Hollandse Energie B.V.",
        "Stationsplein 2, 3511 ED Utrecht, Netherlands",
        "NL856223101B01",
        "EUR",
        Decimal("0.21"),
        "BTW/VAT 21%",
        "A",
        (
            ("Electricity, business tariff (kWh)", Decimal("0.24"), Decimal("0.31")),
            ("Network and metering charge (month)", Decimal("41.20"), Decimal("41.20")),
        ),
        "HE-",
    ),
    Vendor(
        "groen",
        "Atelier Groen V.O.F.",
        "Oudegracht 210, 3511 NV Utrecht, Netherlands",
        "NL003117890B44",
        "EUR",
        Decimal("0.09"),
        "BTW/VAT 9%",
        "B",
        (
            ("Office plants, monthly care service", Decimal("85.00"), Decimal("85.00")),
            ("Catering, team lunch (per person)", Decimal("14.50"), Decimal("18.00")),
        ),
        "AG",
    ),
)

ISSUE_PLAN = (
    # (index, issue) - the rest are clean
    (3, "total_mismatch"),
    (7, "vat_mismatch"),
    (11, "line_math_mismatch"),
    (14, "duplicate"),
    (17, "over_limit"),
    (20, "missing_invoice_number"),
    (23, "total_mismatch"),
    (26, "vat_mismatch"),
    (29, "due_before_issue"),
    (31, "duplicate"),
    (34, "over_limit"),
    (36, "subtotal_mismatch"),
    (38, "vat_mismatch"),
)
AUTO_POST_LIMIT = Decimal("5000")
CENT = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class Line:
    description: str
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal


@dataclass
class Invoice:
    id: str
    vendor: str
    vendor_key: str
    vendor_vat_id: str | None
    invoice_number: str | None
    issue_date: str
    due_date: str
    currency: str
    vat_rate: float
    lines: list[Line]
    subtotal: Decimal
    vat_amount: Decimal
    total: Decimal
    layout: str
    expected_decision: str = "auto_post"
    issues: list[str] = field(default_factory=list)
    duplicate_of: str | None = None

    def gold(self) -> dict[str, object]:
        data = asdict(self)
        for key in ("subtotal", "vat_amount", "total"):
            data[key] = float(getattr(self, key))
        data["lines"] = [
            {
                "description": line.description,
                "quantity": float(line.quantity),
                "unit_price": float(line.unit_price),
                "amount": float(line.amount),
            }
            for line in self.lines
        ]
        return data


def _number(value: Decimal, layout: str) -> str:
    text = f"{value:,.2f}"
    if layout == "B":  # German format: 1.234,56
        return text.replace(",", "_").replace(".", ",").replace("_", ".")
    return text


def _date(value: dt.date, layout: str) -> str:
    if layout == "A":
        return value.isoformat()
    if layout == "B":
        return value.strftime("%d.%m.%Y")
    if layout == "C":
        return value.strftime("%d/%m/%Y")
    return value.strftime("%b %d, %Y")


def _quantity(value: Decimal) -> str:
    return f"{value:,.0f}" if value == value.to_integral_value() else f"{value:,.2f}"


def build_invoices(seed: int = 11, count: int = 40) -> list[Invoice]:
    rng = random.Random(seed)
    issues = dict(ISSUE_PLAN)
    invoices: list[Invoice] = []
    counters = {vendor.key: rng.randint(1000, 4000) for vendor in VENDORS}
    start = dt.date(2026, 6, 1)
    for index in range(count):
        issue = issues.get(index)
        if issue == "duplicate":
            original = invoices[index - rng.randint(3, 6)]
            copy = Invoice(**{**original.__dict__, "lines": list(original.lines)})
            copy.id = f"inv-{index + 1:03d}"
            copy.issues = ["duplicate"]
            copy.expected_decision = "review"
            copy.duplicate_of = original.id
            invoices.append(copy)
            continue
        vendor = VENDORS[index % len(VENDORS)] if index % 7 else VENDORS[rng.randrange(len(VENDORS))]
        counters[vendor.key] += rng.randint(1, 9)
        number = f"{vendor.prefix}{2026}-{counters[vendor.key]:05d}"
        issue_date = start + dt.timedelta(days=index * 3 + rng.randint(0, 2))
        due_date = issue_date + dt.timedelta(days=rng.choice((14, 30, 30, 30)))
        lines: list[Line] = []
        picks = rng.sample(vendor.items, k=rng.randint(1, len(vendor.items)))
        for description, low, high in picks:
            unit = money(low + (high - low) * Decimal(rng.random())) if high > low else low
            if "sheet" in description or "kWh" in description:
                qty = Decimal(rng.randrange(500, 6000, 50))
            elif "per person" in description:
                qty = Decimal(rng.randint(8, 24))
            else:
                qty = Decimal(rng.randint(1, 12))
            lines.append(Line(description, qty, unit, money(qty * unit)))
        if issue == "over_limit":
            description, low, high = vendor.items[0]
            unit = high if high > Decimal("1") else Decimal("118.00")
            qty = Decimal(60) if unit > Decimal("50") else Decimal(14000)
            lines.append(Line(description, qty, unit, money(qty * unit)))
        subtotal = money(sum((line.amount for line in lines), Decimal(0)))
        vat = money(subtotal * vendor.vat_rate)
        total = subtotal + vat
        invoice = Invoice(
            id=f"inv-{index + 1:03d}",
            vendor=vendor.name,
            vendor_key=vendor.key,
            vendor_vat_id=vendor.vat_id,
            invoice_number=number,
            issue_date=issue_date.isoformat(),
            due_date=due_date.isoformat(),
            currency=vendor.currency,
            vat_rate=float(vendor.vat_rate),
            lines=lines,
            subtotal=subtotal,
            vat_amount=vat,
            total=total,
            layout=vendor.layout,
        )
        # Over-limit invoices are correct but need an approval; a high plain total would otherwise also count.
        if issue is None and total > AUTO_POST_LIMIT:
            lines.pop()
            invoice.subtotal = money(sum((line.amount for line in lines), Decimal(0)))
            invoice.vat_amount = money(invoice.subtotal * vendor.vat_rate)
            invoice.total = invoice.subtotal + invoice.vat_amount
        if issue == "total_mismatch":
            invoice.total = invoice.total + Decimal(rng.choice((10, 100, 90))) + Decimal("0.00")
        elif issue == "vat_mismatch":
            if vendor.vat_rate == 0:
                invoice.vat_amount = money(invoice.subtotal * Decimal("0.19"))
            else:
                invoice.vat_amount = money(invoice.subtotal * (vendor.vat_rate + Decimal("0.02")))
            invoice.total = invoice.subtotal + invoice.vat_amount  # consistent total, wrong VAT
        elif issue == "line_math_mismatch":
            line = invoice.lines[0]
            line.amount = money(line.amount + Decimal(rng.choice((12, 25, 40))))
            invoice.subtotal = money(sum((x.amount for x in invoice.lines), Decimal(0)))
            invoice.vat_amount = money(invoice.subtotal * vendor.vat_rate)
            invoice.total = invoice.subtotal + invoice.vat_amount
        elif issue == "subtotal_mismatch":
            invoice.subtotal = invoice.subtotal + Decimal(50)
            invoice.vat_amount = money(invoice.subtotal * vendor.vat_rate)
            invoice.total = invoice.subtotal + invoice.vat_amount
        elif issue == "missing_invoice_number":
            invoice.invoice_number = None
        elif issue == "due_before_issue":
            invoice.due_date = (issue_date - dt.timedelta(days=16)).isoformat()
        if issue is not None:
            invoice.issues = [issue]
            invoice.expected_decision = "review"
        invoices.append(invoice)
    return invoices


class _PDF(FPDF):
    pass


def render_pdf(invoice: Invoice, path: Path) -> None:
    vendor = next(v for v in VENDORS if v.key == invoice.vendor_key)
    layout = invoice.layout
    labels = {
        "A": (
            "INVOICE",
            "Invoice number",
            "Invoice date",
            "Due date",
            "Description",
            "Qty",
            "Unit price",
            "Amount",
            "Subtotal",
            "VAT",
            "Total due",
        ),
        "B": (
            "RECHNUNG / INVOICE",
            "Rechnungsnummer / Invoice no.",
            "Rechnungsdatum / Date",
            "Faellig am / Due",
            "Beschreibung / Description",
            "Menge",
            "Einzelpreis",
            "Betrag",
            "Zwischensumme / Net",
            "MwSt. / VAT",
            "Gesamtbetrag / Total",
        ),
        "C": (
            "TAX INVOICE",
            "Invoice #",
            "Date",
            "Payment due",
            "Service",
            "Qty",
            "Rate",
            "Line total",
            "Net amount",
            "VAT",
            "Amount payable",
        ),
        "D": (
            "Invoice",
            "Invoice ID",
            "Issued",
            "Due",
            "Item",
            "Qty",
            "Price",
            "Amount",
            "Subtotal",
            "Tax",
            "Amount due",
        ),
    }[layout]
    pdf = _PDF(format="A4")
    pdf.set_creation_date(dt.datetime(2026, 10, 1, tzinfo=dt.UTC))  # reproducible bytes
    pdf.set_producer("Relay synthetic data")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    if layout in {"A", "D"}:
        pdf.cell(0, 9, vendor.name, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(0, 5, vendor.address, new_x="LMARGIN", new_y="NEXT")
        if vendor.vat_id:
            pdf.cell(0, 5, f"VAT ID: {vendor.vat_id}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)
        pdf.set_font("Helvetica", "B", 14)
        pdf.cell(0, 8, labels[0], new_x="LMARGIN", new_y="NEXT")
    else:
        pdf.cell(0, 9, labels[0], new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(0, 5, f"{vendor.name} | {vendor.address}", new_x="LMARGIN", new_y="NEXT")
        if vendor.vat_id:
            pdf.cell(
                0,
                5,
                ("USt-IdNr. / VAT no.: " if layout == "B" else "VAT Reg No: ") + vendor.vat_id,
                new_x="LMARGIN",
                new_y="NEXT",
            )
        pdf.ln(4)
    if invoice.duplicate_of:
        # A vendor re-sending an invoice ("copy" stamp): same number and amounts, different file bytes, so only the
        # accounting duplicate check (vendor + number) can catch it, not the file fingerprint.
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 6, "COPY - resent at your request", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 5, "Bill to: " + BUYER[0], new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, BUYER[1], new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, "Customer VAT ID: " + BUYER[2], new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    if invoice.invoice_number:
        pdf.cell(0, 5, f"{labels[1]}: {invoice.invoice_number}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(
        0, 5, f"{labels[2]}: {_date(dt.date.fromisoformat(invoice.issue_date), layout)}", new_x="LMARGIN", new_y="NEXT"
    )
    pdf.cell(
        0, 5, f"{labels[3]}: {_date(dt.date.fromisoformat(invoice.due_date), layout)}", new_x="LMARGIN", new_y="NEXT"
    )
    pdf.cell(0, 5, f"Currency: {invoice.currency}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    widths = (95, 20, 30, 35)
    pdf.set_font("Helvetica", "B", 9)
    for width, label in zip(widths, labels[4:8], strict=True):
        pdf.cell(width, 6, label, border="B")
    pdf.ln()
    pdf.set_font("Helvetica", "", 9)
    for line in invoice.lines:
        pdf.cell(widths[0], 6, line.description[:58])
        pdf.cell(widths[1], 6, _quantity(line.quantity), align="R")
        pdf.cell(widths[2], 6, _number(line.unit_price, layout), align="R")
        pdf.cell(widths[3], 6, _number(line.amount, layout), align="R")
        pdf.ln()
    pdf.ln(3)
    rate_label = f"{labels[9]} {int(invoice.vat_rate * 100)}%"
    for label, value in ((labels[8], invoice.subtotal), (rate_label, invoice.vat_amount), (labels[10], invoice.total)):
        pdf.set_font("Helvetica", "B" if value is invoice.total else "", 10)
        pdf.cell(sum(widths[:3]), 6, label, align="R")
        pdf.cell(widths[3], 6, f"{invoice.currency} {_number(value, layout)}", align="R")
        pdf.ln()
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 8)
    pdf.multi_cell(0, 4, vendor.vat_note, new_x="LMARGIN", new_y="NEXT")
    terms = {
        "A": "Please pay within the term stated above to IBAN NL00 TEST 0000 0000 00 quoting the invoice number.",
        "B": "Zahlbar ohne Abzug bis zum Faelligkeitsdatum. Bank: DE00 0000 0000 0000 0000 00 (fictional).",
        "C": "Payment by BACS to sort code 00-00-00, account 00000000 (fictional). Late payments incur interest.",
        "D": "Charged to the card on file is NOT enabled for this account; pay by wire. Thank you for your business!",
    }[layout]
    pdf.multi_cell(0, 4, terms, new_x="LMARGIN", new_y="NEXT")
    pdf.multi_cell(
        0,
        4,
        "Synthetic document generated for the Relay demo. All companies are fictional.",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(path))


def write_dataset(directory: Path, seed: int = 11) -> list[Invoice]:
    invoices = build_invoices(seed)
    for invoice in invoices:
        render_pdf(invoice, directory / "pdf" / f"{invoice.id}.pdf")
    with (directory / "gold.jsonl").open("w", encoding="utf-8") as handle:
        for invoice in invoices:
            handle.write(json.dumps(invoice.gold(), ensure_ascii=False) + "\n")
    return invoices
