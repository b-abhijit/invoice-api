"""
Fixed Schema Invoice Extraction API
------------------------------------
POST /extract  {"invoice_text": "..."}  ->  6-key JSON (nulls if not found)

Run locally:
    pip install -r requirements.txt
    uvicorn main:app --reload --port 8000
"""

import re
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dateutil import parser as dateparser

app = FastAPI(title="Invoice Extraction API")

# --- Rule 4: CORS must be enabled so a Cloudflare Worker (or any browser) can call us ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       # allow any website to call this API
    allow_credentials=True,
    allow_methods=["*"],       # allow GET, POST, OPTIONS, etc.
    allow_headers=["*"],
)


class InvoiceRequest(BaseModel):
    invoice_text: str


def clean_number(raw: str):
    """Turn '1,40,000.00' or '2,199.00' into a plain float."""
    if not raw:
        return None
    raw = raw.replace(",", "").strip()
    try:
        return float(raw)
    except ValueError:
        return None


def extract_fields(text: str) -> dict:
    result = {
        "invoice_no": None,
        "date": None,
        "vendor": None,
        "amount": None,
        "tax": None,
        "currency": None,
    }

    # ---- invoice_no: look for "Invoice No", "Invoice Number", or "Ref" ----
    m = re.search(
        r"(?:Invoice\s*No\.?|Invoice\s*Number|Ref)\s*[:#]?\s*([A-Za-z0-9\-/]+)",
        text, re.IGNORECASE,
    )
    if m:
        result["invoice_no"] = m.group(1).strip()

    # ---- date: look for "Date", "Issued", "Invoice Date" then parse whatever follows ----
    m = re.search(
        r"(?:Invoice\s*Date|Date|Issued)\s*[:#]?\s*"
        r"([0-9]{1,2}\s+\w+\s+[0-9]{4}|[0-9]{4}-[0-9]{2}-[0-9]{2}|\w+\s+[0-9]{1,2},?\s+[0-9]{4})",
        text, re.IGNORECASE,
    )
    if m:
        try:
            result["date"] = dateparser.parse(m.group(1)).strftime("%Y-%m-%d")
        except (ValueError, OverflowError):
            pass

    # ---- vendor: "Vendor:" label first, else the company name in a title line ----
    m = re.search(r"Vendor\s*[:#]?\s*(.+)", text, re.IGNORECASE)
    if m:
        result["vendor"] = m.group(1).strip()
    else:
        m2 = re.search(r"^([A-Za-z0-9&.,'\s]+?)\s*[—-]\s*Tax Invoice", text, re.MULTILINE)
        if m2:
            result["vendor"] = m2.group(1).strip()

    # ---- amount: the Subtotal line (before tax) ----
    m = re.search(
        r"Sub\s*[- ]?\s*total[^\d]*?([0-9][0-9,]*\.?[0-9]*)",
        text, re.IGNORECASE,
    )
    if m:
        result["amount"] = clean_number(m.group(1))

    # ---- tax: GST/IGST/CGST/SGST/VAT/Tax line, skipping any "(18%)" style rate first ----
    # Anchored to the START of a line so we don't accidentally match the word
    # "Tax" inside an unrelated phrase like "Tax Invoice".
    m = re.search(
        r"^[ \t]*(?:IGST|CGST|SGST|GST|VAT|Tax)\b\s*(?:\(\d{1,3}%\))?[^\d\n]*?([0-9][0-9,]*\.?[0-9]*)",
        text, re.IGNORECASE | re.MULTILINE,
    )
    if m:
        result["tax"] = clean_number(m.group(1))

    # ---- currency: explicit "Currency:" label wins, else guess from symbols ----
    if re.search(r"\bINR\b|Rs\.|₹", text):
        result["currency"] = "INR"
    elif re.search(r"\bUSD\b|\$", text):
        result["currency"] = "USD"
    elif re.search(r"\bEUR\b|€", text):
        result["currency"] = "EUR"

    m = re.search(r"Currency\s*[:#]?\s*([A-Za-z]{3})", text, re.IGNORECASE)
    if m:
        result["currency"] = m.group(1).upper()

    return result


@app.get("/")
def health_check():
    return {"status": "ok", "message": "Invoice extraction API is running"}


@app.post("/extract")
def extract(req: InvoiceRequest):
    return extract_fields(req.invoice_text)