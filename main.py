import re
from datetime import datetime
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


app = FastAPI(title="Invoice Extraction API")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ExtractRequest(BaseModel):
    invoice_text: str


class ExtractResponse(BaseModel):
    invoice_no: Optional[str]
    date: Optional[str]
    vendor: Optional[str]
    amount: Optional[float]
    tax: Optional[float]
    currency: Optional[str]


def clean_value(value: str) -> str:
    return value.strip().strip(":").strip()


def parse_number(value: str) -> Optional[float]:
    if not value:
        return None
    value = value.replace(",", "").strip()
    try:
        return float(value)
    except ValueError:
        return None


def extract_first_match(text: str, patterns: list[str], group: int = 1) -> Optional[str]:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            return clean_value(match.group(group))
    return None


def get_non_empty_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def normalize_date(date_str: Optional[str]) -> Optional[str]:
    if not date_str:
        return None

    date_str = date_str.strip()

    formats = [
        "%d %B %Y",   # 15 March 2026
        "%d %b %Y",   # 15 Mar 2026
        "%Y-%m-%d",   # 2026-01-22
        "%d/%m/%Y",   # 15/03/2026
        "%d-%m-%Y",   # 15-03-2026
        "%d.%m.%Y",   # 15.03.2026
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue

    return None


def extract_invoice_no(text: str) -> Optional[str]:
    patterns = [
        r"Invoice\s*(?:No\.?|Number)\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9\/\-_]*)",
        r"Inv\s*(?:No\.?)\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9\/\-_]*)",
        r"Bill\s*No\.?\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9\/\-_]*)",
        r"Receipt\s*No\.?\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9\/\-_]*)",
        r"Reference\s*No\.?\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9\/\-_]*)",
        r"Ref\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9\/\-_]*)",
        r"No\.?\s*[:\-]?\s*([A-Za-z0-9]{1,10}[-\/_][A-Za-z0-9\/\-_]+)",
        r"Invoice\s*[:\-]?\s*([A-Za-z0-9]{1,10}[-\/_][A-Za-z0-9\/\-_]+)",
    ]

    found = extract_first_match(text, patterns)
    if found:
        return found

    lines = get_non_empty_lines(text)

    # fallback: look in first few lines for something that looks like KW-330 / INV-2026-0041
    for line in lines[:5]:
        if re.search(r"\b(invoice|inv|bill|receipt|ref|reference)\b", line, flags=re.IGNORECASE):
            m = re.search(r"\b([A-Za-z]{1,10}[-\/_][A-Za-z0-9\/\-_]+)\b", line)
            if m:
                return m.group(1)

    return None


def extract_date(text: str) -> Optional[str]:
    patterns = [
        r"Date\s*[:\-]?\s*([^\n]+)",
        r"Issued\s*[:\-]?\s*([^\n]+)",
        r"Invoice\s*Date\s*[:\-]?\s*([^\n]+)",
        r"Dated\s*[:\-]?\s*([^\n]+)",
    ]

    raw_date = extract_first_match(text, patterns)
    if raw_date:
        raw_date = raw_date.split("Vendor:")[0].split("Client:")[0].strip()
        normalized = normalize_date(raw_date)
        if normalized:
            return normalized

    lines = get_non_empty_lines(text)
    for line in lines[:8]:
        date_candidates = re.findall(
            r"(\d{1,2}\s+[A-Za-z]+\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}[\/.-]\d{1,2}[\/.-]\d{4})",
            line,
        )
        for candidate in date_candidates:
            normalized = normalize_date(candidate)
            if normalized:
                return normalized

    return None


def extract_vendor(text: str) -> Optional[str]:
    lines = get_non_empty_lines(text)

    labeled_patterns = [
        r"Vendor\s*[:\-]?\s*([^\n]+)",
        r"Supplier\s*[:\-]?\s*([^\n]+)",
        r"From\s*[:\-]?\s*([^\n]+)",
        r"Seller\s*[:\-]?\s*([^\n]+)",
    ]
    found = extract_first_match(text, labeled_patterns)
    if found:
        return found

    for line in lines[:3]:
        m = re.match(r"^(.*?)\s*[—\-]\s*(Tax Invoice|Invoice)\b", line, flags=re.IGNORECASE)
        if m:
            candidate = m.group(1).strip()
            if candidate:
                return candidate

    for line in lines[:3]:
        m = re.match(r"^(.*?)\s+(Tax Invoice|Invoice)\b", line, flags=re.IGNORECASE)
        if m:
            candidate = m.group(1).strip(" -—:")
            if candidate:
                return candidate

    skip_prefixes = (
        "invoice no", "invoice number", "inv no", "ref", "reference", "date", "issued",
        "invoice date", "dated", "bill to", "ship to", "client", "customer",
        "subtotal", "sub total", "sub-total", "grand total", "total due", "total",
        "gst", "igst", "cgst", "sgst", "currency", "amount", "tax", "vat"
    )

    for line in lines[:5]:
        low = line.lower()
        if low.startswith(skip_prefixes):
            continue
        if re.search(r"[A-Za-z]", line):
            cleaned = re.sub(r"\b(Tax Invoice|Invoice)\b", "", line, flags=re.IGNORECASE).strip(" -—:")
            if cleaned and len(cleaned) > 1:
                return cleaned

    return None


def extract_labeled_number(text: str, labels: list[str]) -> Optional[float]:
    lines = get_non_empty_lines(text)

    for line in lines:
        low = line.lower()

        for label in labels:
            if label.lower() in low:
                nums = re.findall(r"(\d[\d,]*(?:\.\d+)?)", line)
                if nums:
                    value = parse_number(nums[-1])
                    if value is not None:
                        return value

    return None


def extract_amount(text: str) -> Optional[float]:
    amount = extract_labeled_number(
        text,
        labels=[
            "subtotal",
            "sub total",
            "sub-total",
            "taxable amount",
            "net amount",
            "amount before tax",
            "base amount",
        ],
    )
    if amount is not None:
        return amount

    total = extract_labeled_number(
        text,
        labels=[
            "grand total",
            "total due",
            "invoice total",
            "total",
        ],
    )

    tax = extract_labeled_number(
        text,
        labels=[
            "igst",
            "cgst",
            "sgst",
            "gst",
            "vat",
            "tax",
        ],
    )

    if total is not None and tax is not None:
        base = round(total - tax, 2)
        if base >= 0:
            return base

    return None


def extract_tax(text: str) -> Optional[float]:
    return extract_labeled_number(
        text,
        labels=[
            "igst",
            "cgst",
            "sgst",
            "gst",
            "vat",
            "tax",
        ],
    )


def extract_currency(text: str) -> Optional[str]:
    patterns = [
        r"Currency\s*[:\-]?\s*([A-Z]{3})",
        r"\b(INR|USD|EUR|GBP)\b",
    ]
    found = extract_first_match(text, patterns)
    if found:
        return found.upper()

    if "Rs" in text or "₹" in text:
        return "INR"

    return None


@app.get("/")
def root():
    return {"message": "Invoice Extraction API is running"}


@app.post("/extract", response_model=ExtractResponse)
def extract_invoice_fields(req: ExtractRequest):
    text = req.invoice_text

    invoice_no = extract_invoice_no(text)
    date = extract_date(text)
    vendor = extract_vendor(text)
    amount = extract_amount(text)
    tax = extract_tax(text)
    currency = extract_currency(text)

    return ExtractResponse(
        invoice_no=invoice_no,
        date=date,
        vendor=vendor,
        amount=amount,
        tax=tax,
        currency=currency,
    )