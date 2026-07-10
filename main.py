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


def extract_money(text):
    if text is None:
        return None

    text = text.replace(",", "")

    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", text)

    if match:
        return float(match.group(1))

    return None


@app.post("/extract")
def extract(req: InvoiceRequest):

    text = req.invoice_text

    invoice_no = None
    date = None
    vendor = None
    amount = None
    tax = None
    currency = "INR"

    invoice_match = re.search(r"Invoice\s*No[:\s]+(.+)", text, re.I)

    if invoice_match:
        invoice_no = invoice_match.group(1).strip()

    vendor_match = re.search(r"Vendor[:\s]+(.+)", text, re.I)

    if vendor_match:
        vendor = vendor_match.group(1).strip()

    subtotal_match = re.search(r"Subtotal[:\s]+(.+)", text, re.I)

    if subtotal_match:
        amount = extract_money(subtotal_match.group(1))

    tax_match = re.search(r"GST.*?:\s*(.+)", text, re.I)

    if tax_match:
        tax = extract_money(tax_match.group(1))

    date_match = re.search(r"Date[:\s]+(.+)", text, re.I)

    if date_match:
        try:
            dt = parser.parse(date_match.group(1))
            date = dt.strftime("%Y-%m-%d")
        except:
            pass

    return {
        "invoice_no": invoice_no,
        "date": date,
        "vendor": vendor,
        "amount": amount,
        "tax": tax,
        "currency": currency
    }