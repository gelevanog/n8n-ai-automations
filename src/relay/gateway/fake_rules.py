"""The offline `fake` provider: deterministic rules that play the model for every gateway task.

It makes the tests, CI and the zero-key Docker demo work without an API key, and it is the "rules only" baseline in
the evaluation: keyword classification, regular expressions over the invoice layouts, keyword enrichment, extractive
FAQ answers and template drafts. It reads exactly the prompt the real model gets (the RELAY_TASK line picks the
handler), so the gateway's validation, gates and checks run unchanged on its output."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

from relay.gateway.kb import tokens
from relay.gateway.textutil import parse_number
from relay.llm.base import Message

# ------------------------------------------------------------------------------------------------ helpers


def _user(messages: Sequence[Message]) -> str:
    return "\n".join(m["content"] for m in messages if m["role"] == "user")


def _block(text: str, tag: str) -> str:
    match = re.search(rf"<{tag}[^>]*>\n?(.*?)\n?</{tag}>", text, re.S)
    return match.group(1) if match else ""


def _first(pattern: str, text: str, flags: int = re.I) -> str | None:
    match = re.search(pattern, text, flags)
    return match.group(1).strip() if match else None


# ------------------------------------------------------------------------------------------------ triage

KEYWORDS: dict[str, tuple[str, ...]] = {
    "spam": ("click", "claim", "verify your password", "btc", "guarantee", "pre-approved", "offshore", "gift card",
             "survey to claim", "bank details", "per hour", "/hour", "seo", "email lists", "discounts on toner",
             "suspension", "customs fee", "keep your current password", "redesign", "directory", "loan",
             "legal action", "http://"),
    "invoice": ("invoice", "rechnung", "factuur", "facture", "statement", "kontoauszug", "payment reminder",
                "unpaid", "zahlbar", "amount payable", "receipt"),
    "support": ("order kp-", "tracking", "damaged", "crushed", "log in", "password reset", "proof", "cancel",
                "refund", "wrong", "missing", "doesn't scan", "stuck", "error", "access", "pedido", "beschädigt",
                "colour", "delivery address", "copy of the invoice", "corrected invoice", "got 1,000"),
    "sales_lead": ("quote", "pricing", "price", "offerte", "angebot", "looking for", "interested", "reorder",
                   "we need", "we'd like", "would like", "need about", "partner", "samples", "call me", "budget",
                   "order again", "go ahead"),
    "other": ("newsletter", "out of the office", "out of office", "apply for", "position", "invited", "invitation",
              "undeliverable", "delivery has failed", "survey", "fire drill", "road works", "unsubscribe", "thesis"),
}
PRODUCTS = (
    ("tissue_paper", ("tissue",)),
    ("labels", ("label",)),
    ("stickers", ("sticker",)),
    ("paper_bags", ("bag", "tassen", "tüten")),
    ("folding_cartons", ("carton", "faltschachtel", "gift box", "pasta boxes", "candle")),
    ("mailer_boxes", ("mailer", "box", "boxes", "kisten")),
)
ISSUES = (
    ("billing", ("invoice", "vat", "paid", "refund", "charge")),
    ("damage", ("damaged", "crushed", "beschädigt", "faltan", "got 1,000", "missing", "wet")),
    ("shipping", ("tracking", "shipped", "customs", "arrive")),
    ("artwork", ("artwork", "proof", "cmyk", "bleed", "pdf")),
    ("account", ("log in", "password", "access", "colleague")),
    ("order_change", ("cancel", "change the delivery", "address")),
    ("quality", ("colour", "color", "barcode", "darker")),
)
_COMPANY = re.compile(
    r"^(?:[A-Z][\w&.'-]*(?:\s+|$)){1,5}(?:GmbH|Ltd|B\.V\.|S\.L\.|S\.r\.l\.|ApS|Co\.|s\.r\.o\.|Inc\.|AB)\.?$", re.M
)


def fake_triage(messages: Sequence[Message]) -> str:
    raw = _block(_user(messages), "email")
    header, _, body = raw.partition("\n\n")
    sender = _first(r"From: (.*?) <", header) or ""
    subject = _first(r"Subject: (.*)", header) or ""
    attachments = _first(r"Attachments: (.*)", header) or "none"
    text = f"{subject}\n{body}"
    low = text.lower()
    scores = {cat: sum(low.count(word) for word in words) for cat, words in KEYWORDS.items()}
    if ".pdf" in attachments.lower():
        scores["invoice"] += 3
    if re.search(r"\bKP-\d{5}\b", text):
        scores["support"] += 1
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    (category, top), (_, second) = ranked[0], ranked[1]
    if top == 0:
        category, confidence = "other", 0.4
    else:
        confidence = round(min(0.97, 0.55 + 0.12 * (top - second)), 2)
    quantity = None
    match = re.search(r"(\d{1,3}(?:[.,]\d{3})+|\d{3,6})\s*(?:\w+\s){0,4}?(?:boxes|cartons|bags|labels|stickers|"
                      r"sheets|units|pieces|mailers|tassen|faltschachteln|cajas|boxes)", text, re.I)
    if match:
        value = parse_number(match.group(1))
        quantity = int(value) if value is not None else None
    product = next((name for name, words in PRODUCTS if any(w in low for w in words)), None)
    issue = next((name for name, words in ISSUES if any(w in low for w in words)), "other")
    company = None
    if (found := _COMPANY.search(body)) is not None:
        company = found.group(0).strip()
    invoice_number = _first(r"\b((?:RE|VP|HE|CF)-\d{4}-\d{5}|(?:SL|AG)\d{4}-\d{5})\b", text, 0)
    amount = None
    if (amount_match := re.search(r"(?:EUR|GBP|USD)\s?([\d.,]+\d)", text)) is not None:
        parsed = parse_number(amount_match.group(1))
        amount = float(parsed) if parsed is not None else None
    reply = {
        "category": category,
        "confidence": confidence,
        "summary": subject[:200] or "email",
        "language": "en",
        "urgency": "high" if "urgent" in low else "normal",
        "contact": {"name": sender or None, "company": company, "email": None,
                    "phone": _first(r"(\+?\d[\d ()-]{8,}\d)", body)},
        "lead": {"product": product, "quantity": quantity, "deadline": None} if category == "sales_lead" else {},
        "support": {"order_id": _first(r"\b(KP-\d{5})\b", text), "issue": issue} if category == "support" else {},
        "invoice": {"vendor": None, "invoice_number": invoice_number, "amount": amount,
                    "currency": _first(r"\b(EUR|GBP|USD)\b", text, 0)} if category == "invoice" else {},
    }
    return json.dumps(reply)


# ------------------------------------------------------------------------------------------------ invoices

_LABELS = {
    "invoice_number": r"(?:Invoice number|Invoice no\.|Invoice #|Invoice ID)\s*:\s*(\S+)",
    "issue_date": r"(?:Invoice date|Rechnungsdatum / Date|^Date|Issued)\s*:\s*(.+)",
    "due_date": r"(?:Due date|Faellig am / Due|Payment due|^Due)\s*:\s*(.+)",
    "subtotal": r"(?:Subtotal|Zwischensumme / Net|Net amount)\s+[A-Z]{3}\s+([\d.,]+)",
    "vat_amount": r"(?:VAT|MwSt\. / VAT|Tax)\s+\d+%\s+[A-Z]{3}\s+([\d.,]+)",
    "vat_rate": r"(?:VAT|MwSt\. / VAT|Tax)\s+(\d+)%",
    "total": r"(?:Total due|Gesamtbetrag / Total|Amount payable|Amount due)\s+[A-Z]{3}\s+([\d.,]+)",
    "currency": r"Currency:\s*([A-Z]{3})",
    "vendor_vat_id": r"(?:VAT ID|USt-IdNr\. / VAT no\.|VAT Reg No):\s*([A-Z]{2}[A-Z0-9]+)",
}
_LINE = re.compile(r"^(.+?)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)$", re.M)


def _iso(value: str | None) -> str | None:
    from relay.gateway.invoice import parse_date

    parsed = parse_date(value)
    return parsed.isoformat() if parsed else value


def fake_invoice(messages: Sequence[Message]) -> str:
    text = _block(_user(messages), "invoice")
    found = {key: _first(pattern, text, re.I | re.M) for key, pattern in _LABELS.items()}

    def num(key: str) -> float | None:
        raw = found.get(key)
        value = parse_number(raw) if raw else None
        return float(value) if value is not None else None

    lines_text = text.split("\n")
    vendor = None
    for line in lines_text[:3]:
        if re.search(r"(B\.V\.|GmbH|Ltd|Inc\.|V\.O\.F\.)", line):
            vendor = line.split("|")[0].strip()
            break
    lines = []
    for match in _LINE.finditer(text):
        description = match.group(1).strip()
        if re.match(r"(?i)(description|beschreibung|service|item)\b.*\b(qty|menge)\b", match.group(0)):
            continue
        quantity, unit, amount = (parse_number(match.group(i)) for i in (2, 3, 4))
        lines.append({"description": description, "quantity": float(quantity) if quantity is not None else None,
                      "unit_price": float(unit) if unit is not None else None,
                      "amount": float(amount) if amount is not None else None})
    rate = num("vat_rate")
    reply = {
        "vendor": vendor,
        "vendor_vat_id": found["vendor_vat_id"],
        "invoice_number": found["invoice_number"],
        "issue_date": _iso(found["issue_date"]),
        "due_date": _iso(found["due_date"]),
        "currency": found["currency"],
        "subtotal": num("subtotal"),
        "vat_rate": rate / 100 if rate is not None else None,
        "vat_amount": num("vat_amount"),
        "total": num("total"),
        "lines": lines,
        "confidence": 0.9 if found["total"] and found["invoice_number"] else 0.5,
    }
    return json.dumps(reply)


# ------------------------------------------------------------------------------------------------ leads

INDUSTRY_WORDS = (
    ("printing_packaging", ("print shop", "prints folding cartons", "printing")),
    ("agency", ("agency", "branding")),
    ("pets", ("dog", "cat ", "pet ", "grooming")),
    ("cosmetics_beauty", ("skincare", "skin care", "make-up", "cosmetic", "hair care", "creams")),
    ("food_beverage", ("coffee", "tea", "granola", "gin", "kombucha", "bakery", "bakeries", "sourdough", "food")),
    ("fashion_apparel", ("clothing", "fashion", "swimwear", "merino", "apparel", "outdoor gear")),
    ("health_wellness", ("vitamin", "physiotherapy", "supplement", "clinic")),
    ("education", ("courses", "teach", "school")),
    ("technology", ("software", "saas", "headphones", "engineers")),
    ("home_lifestyle", ("furniture", "candle", "toys", "flowers", "tableware", "ceramic", "home")),
)
NUMBER_WORDS = {"four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12}
COUNTRIES = {
    "austria": "AT", "ireland": "IE", "denmark": "DK", "united kingdom": "GB", "uk": "GB", "scotland": "GB",
    "netherlands": "NL", "germany": "DE", "belgium": "BE", "usa": "US", "united states": "US", "france": "FR",
    "sweden": "SE", "italy": "IT", "poland": "PL", "spain": "ES", "czech": "CZ", "portugal": "PT", "finland": "FI",
    "eindhoven": "NL", "munich": "DE", "hamburg": "DE", "berlin": "DE", "rotterdam": "NL", "utrecht": "NL",
}


def _band(count: int) -> str:
    return "1-10" if count <= 10 else "11-50" if count <= 50 else "51-200" if count <= 200 else (
        "201-500" if count <= 500 else "500+")


def fake_enrich(messages: Sequence[Message]) -> str:
    user = _user(messages)
    site = _block(user, "website").lower()
    form = _block(user, "form").lower()
    industry = next((name for name, words in INDUSTRY_WORDS if any(w in site for w in words)), "other")
    count = None
    match = re.search(r"(?:team of|employs|around|roughly|about|more than|over)?\s*(\d[\d,]*)\s*(?:-strong team|"
                      r"people|employees|colleagues|makers|potters|florists|therapists|teachers|engineers|bakers|"
                      r"friends|groomers)", site)
    if match:
        count = int(match.group(1).replace(",", ""))
    else:
        for word, value in NUMBER_WORDS.items():
            if re.search(rf"\b{word}\b", site):
                count = value
                break
    country = next((code for name, code in COUNTRIES.items() if re.search(rf"\b{name}\b", site)), None)
    reply = {
        "industry": industry,
        "employees_band": _band(count) if count else None,
        "country": country,
        "sells_online": bool(re.search(r"webshop|online shop|online store|e-shop|web store|ordered online", site)),
        "buyer": "student" not in form and "school project" not in form,
        "summary": (_first(r"\[index\.html\]\s*(.{0,160})", _block(user, "website")) or "")[:200],
        "confidence": 0.8 if count and country else 0.55,
    }
    return json.dumps(reply)


def fake_draft(messages: Sequence[Message]) -> str:
    form = _block(_user(messages), "form")
    name = (_first(r"Name: (.*)", form) or "there").split(" ")[0]
    company = _first(r"Company: (.*)", form) or "your company"
    product = (_first(r"Product: (.*)", form) or "-").replace("_", " ")
    product = "packaging" if product == "-" else product
    body = (
        f"Hi {name},\n\nThank you for contacting Kestrel Print Co. about {product} for {company}. "
        "Standard production takes 10 business days after you approve the digital proof, and volume discounts "
        "start at 1,000 units. We can send you a free sample pack of our unprinted products so you can check the "
        "materials first.\n\nWould a short call this week suit you to go through sizes, quantities and artwork? "
        "\n\nBest regards,\nThe Kestrel Print Co. sales team"
    )
    return json.dumps({"subject": f"Your packaging enquiry - {company}", "body": body})


# ------------------------------------------------------------------------------------------------ FAQ


def fake_faq(messages: Sequence[Message]) -> str:
    user = _user(messages)
    question = _block(user, "question")
    articles = re.findall(r'<article id="([^"]+)">\n# (.*?)\n(.*?)\n</article>', user, re.S)
    if not articles:
        return json.dumps({"answerable": False, "answer": "", "citations": [], "confidence": 0.0,
                           "escalation_reason": "no articles"})
    q_terms = set(tokens(question))
    article_id, _title, text = articles[0]
    sentences = [s.strip(" -*") for s in re.split(r"(?<=[.!?])\s+|\n+", text) if len(s.strip()) > 12]
    scored = sorted(sentences, key=lambda s: len(q_terms & set(tokens(s))), reverse=True)
    best = [s for s in scored[:2] if q_terms & set(tokens(s))]
    coverage = len(q_terms & set(tokens(text))) / max(1, len(q_terms))
    if not best or coverage < 0.5:
        return json.dumps({"answerable": False, "answer": "", "citations": [], "confidence": round(coverage, 2),
                           "escalation_reason": "the articles do not cover the question"})
    answer = "Thanks for your question! " + " ".join(s.replace("**", "") for s in best)
    return json.dumps({"answerable": True, "answer": answer, "citations": [article_id],
                       "confidence": round(min(0.95, 0.5 + coverage / 2), 2), "escalation_reason": None})


def fake_digest(messages: Sequence[Message]) -> str:
    user = _user(messages)
    period = _first(r"Period: (.*)", user) or "this week"
    metrics = json.loads(_first(r"Metrics: (\{.*\})", user, re.S) or "{}")
    bullets = [f"{key.replace('_', ' ').capitalize()}: {value}" for key, value in list(metrics.items())[:6]]
    return json.dumps({"headline": f"Operations digest for {period}", "bullets": bullets})


HANDLERS = {
    "classify_email": fake_triage,
    "extract_invoice": fake_invoice,
    "enrich_lead": fake_enrich,
    "draft_reply": fake_draft,
    "answer_faq": fake_faq,
    "write_digest": fake_digest,
}
