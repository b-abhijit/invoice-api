import re
from datetime import datetime
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware


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
        r"Invoice\s*(?:No\.?|Number)\s*[:\-]?\s*([A-Za-z0-9\/\-_]+)",
        r"Ref\s*[:\-]?\s*([A-Za-z0-9\/\-_]+)",
    ]
    return extract_first_match(text, patterns)


def extract_date(text: str) -> Optional[str]:
    patterns = [
        r"Date\s*[:\-]?\s*([^\n]+)",
        r"Issued\s*[:\-]?\s*([^\n]+)",
    ]
    raw_date = extract_first_match(text, patterns)
    if raw_date:
        raw_date = raw_date.split("Vendor:")[0].split("Client:")[0].strip()
    return normalize_date(raw_date)


def extract_vendor(text: str) -> Optional[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    # 1. Normal labeled patterns
    labeled_patterns = [
        r"Vendor\s*[:\-]?\s*([^\n]+)",
        r"Supplier\s*[:\-]?\s*([^\n]+)",
        r"From\s*[:\-]?\s*([^\n]+)",
        r"Seller\s*[:\-]?\s*([^\n]+)",
    ]
    found = extract_first_match(text, labeled_patterns)
    if found:
        return found

    # 2. First line like "VortexPrint - Tax Invoice"
    for line in lines[:3]:
        m = re.match(r"^(.*?)\s*[—\-]\s*(Tax Invoice|Invoice)\b", line, flags=re.IGNORECASE)
        if m:
            candidate = m.group(1).strip()
            if candidate:
                return candidate

    # 3. First line ending with Invoice, e.g. "VortexPrint Tax Invoice"
    for line in lines[:3]:
        m = re.match(r"^(.*?)\s+(Tax Invoice|Invoice)\b", line, flags=re.IGNORECASE)
        if m:
            candidate = m.group(1).strip(" -—:")
            if candidate:
                return candidate

    # 4. Fallback: first non-generic line that looks like a company name
    skip_prefixes = (
        "invoice no", "invoice number", "ref", "date", "issued",
        "bill to", "ship to", "subtotal", "total", "gst", "igst",
        "cgst", "sgst", "currency", "amount", "tax"
    )

    for line in lines[:5]:
        low = line.lower()
        if low.startswith(skip_prefixes):
            continue
        if "invoice" in low and len(line.split()) <= 2:
            continue
        if re.search(r"[A-Za-z]", line):
            cleaned = re.sub(r"\b(Tax Invoice|Invoice)\b", "", line, flags=re.IGNORECASE).strip(" -—:")
            if cleaned:
                return cleaned

    return None


def extract_amount_by_labels(text: str, labels: list[str]) -> Optional[float]:
    for label in labels:
        pattern = rf"{label}[^\n]*?Rs\.?\s*([\d,]+(?:\.\d+)?)"
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return parse_number(match.group(1))
    return None


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


@app.post("/extract", response_model=ExtractResponse)
def extract_invoice_fields(req: ExtractRequest):
    text = req.invoice_text

    invoice_no = extract_invoice_no(text)
    date = extract_date(text)
    vendor = extract_vendor(text)

    amount = extract_amount_by_labels(
        text,
        labels=[
            "Subtotal",
            "Sub-total",
            "Sub total",
        ],
    )

    tax = extract_amount_by_labels(
        text,
        labels=[
            "GST",
            "IGST",
            "CGST",
            "SGST",
            "Tax",
            "VAT",
        ],
    )

    currency = extract_currency(text)

    return ExtractResponse(
        invoice_no=invoice_no,
        date=date,
        vendor=vendor,
        amount=amount,
        tax=tax,
        currency=currency,
    )