from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dateutil import parser
import re

app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class InvoiceRequest(BaseModel):
    invoice_text: str


def find_first(patterns, text):
    """Try multiple regex patterns and return the first captured value."""
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if m:
            return m.group(1).strip()
    return None


def extract_amount(value):
    """Extract a numeric amount like Rs. 2,199.00 -> 2199.00"""
    if value is None:
        return None

    value = value.replace(",", "")

    m = re.search(r"([0-9]+(?:\.[0-9]+)?)", value)

    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None

    return None


@app.post("/extract")
def extract(req: InvoiceRequest):

    text = req.invoice_text

    invoice_patterns = [
        r"Invoice\s*No\.?\s*[:#]?\s*(\S+)",
        r"Invoice\s*Number\s*[:#]?\s*(\S+)",
        r"Invoice\s*#\s*(\S+)",
        r"Invoice\s*ID\s*[:#]?\s*(\S+)",
        r"Inv\s*No\.?\s*[:#]?\s*(\S+)",
        r"Bill\s*No\.?\s*[:#]?\s*(\S+)",
    ]

    vendor_patterns = [
        r"Vendor\s*:\s*(.+)",
        r"Supplier\s*:\s*(.+)",
        r"Sold\s*By\s*:\s*(.+)",
        r"Bill\s*From\s*:\s*(.+)",
    ]

    date_patterns = [
        r"Invoice\s*Date\s*:\s*(.+)",
        r"Bill\s*Date\s*:\s*(.+)",
        r"Date\s*:\s*(.+)",
        r"Dated\s*:\s*(.+)",
    ]

    subtotal_patterns = [
        r"Subtotal\s*:\s*(.+)",
        r"Sub\s*Total\s*:\s*(.+)",
        r"Taxable\s*Amount\s*:\s*(.+)",
        r"Amount\s*Before\s*Tax\s*:\s*(.+)",
        r"Net\s*Amount\s*:\s*(.+)",
    ]

    tax_patterns = [
        r"GST.*?:\s*(.+)",
        r"IGST.*?:\s*(.+)",
        r"CGST.*?:\s*(.+)",
        r"SGST.*?:\s*(.+)",
        r"Tax\s*:\s*(.+)",
        r"VAT\s*:\s*(.+)",
    ]

    invoice_no = find_first(invoice_patterns, text)

    vendor = find_first(vendor_patterns, text)

    date_string = find_first(date_patterns, text)

    if date_string:
        try:
            date = parser.parse(date_string, dayfirst=True).strftime("%Y-%m-%d")
        except Exception:
            date = None
    else:
        date = None

    subtotal = find_first(subtotal_patterns, text)
    amount = extract_amount(subtotal)

    tax_value = find_first(tax_patterns, text)
    tax = extract_amount(tax_value)

    return {
        "invoice_no": invoice_no,
        "date": date,
        "vendor": vendor,
        "amount": amount,
        "tax": tax,
        "currency": "INR",
    }