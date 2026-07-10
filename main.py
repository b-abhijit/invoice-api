import re
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dateutil import parser as dateparser

app = FastAPI(title="Invoice Extraction API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class InvoiceRequest(BaseModel):
    invoice_text: str


def clean_number(raw: str):
    if not raw:
        return None

    raw = raw.replace(",", "").replace("₹", "").replace("Rs.", "").replace("Rs", "").strip()

    m = re.search(r"\d+(?:\.\d+)?", raw)
    if not m:
        return None

    try:
        return float(m.group())
    except ValueError:
        return None


def extract_fields(text: str):

    result = {
        "invoice_no": None,
        "date": None,
        "vendor": None,
        "amount": None,
        "tax": None,
        "currency": None,
    }

    # ------------------------------------------------------------------
    # Invoice Number
    # ------------------------------------------------------------------

    invoice_patterns = [
        r"Invoice\s*No\.?\s*[:#]?\s*([A-Za-z0-9._/-]+)",
        r"Invoice\s*Number\s*[:#]?\s*([A-Za-z0-9._/-]+)",
        r"Invoice\s*#\s*([A-Za-z0-9._/-]+)",
        r"Invoice\s*ID\s*[:#]?\s*([A-Za-z0-9._/-]+)",
        r"Inv\.?\s*No\.?\s*[:#]?\s*([A-Za-z0-9._/-]+)",
        r"Inv\.?\s*#\s*([A-Za-z0-9._/-]+)",
        r"Bill\s*No\.?\s*[:#]?\s*([A-Za-z0-9._/-]+)",
        r"Bill\s*Number\s*[:#]?\s*([A-Za-z0-9._/-]+)",
        r"Reference\s*No\.?\s*[:#]?\s*([A-Za-z0-9._/-]+)",
        r"Ref(?:erence)?\.?\s*[:#]?\s*([A-Za-z0-9._/-]+)",
    ]

    for pattern in invoice_patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            result["invoice_no"] = m.group(1).strip()
            break

    # Fallback: invoice numbers like VP-3355
    if result["invoice_no"] is None:
        candidates = re.findall(
            r"\b[A-Z]{1,6}[-/][A-Z0-9-]{2,}\b",
            text
        )
        if candidates:
            result["invoice_no"] = candidates[0]

    # ------------------------------------------------------------------
    # Date
    # ------------------------------------------------------------------

    DATE_VALUE = (
        r"[0-9]{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\.?,?\s+[0-9]{4}"
        r"|[A-Za-z]+\.?\s+[0-9]{1,2}(?:st|nd|rd|th)?,?\s+[0-9]{4}"
        r"|[0-9]{4}[/\-.][0-9]{1,2}[/\-.][0-9]{1,2}"
        r"|[0-9]{1,2}[/\-.][0-9]{1,2}[/\-.][0-9]{4}"
    )

    m = re.search(
        r"(?:Invoice\s*Date|Bill(?:ing)?\s*Date|Date\s*of\s*Issue|Issued(?:\s*On)?|Dated|Date)"
        r"\s*[:#]?\s*(" + DATE_VALUE + ")",
        text,
        re.IGNORECASE,
    )

    if not m:
        m = re.search(DATE_VALUE, text, re.IGNORECASE)

    if m:
        candidate = m.group(1) if m.lastindex else m.group(0)
        try:
            result["date"] = dateparser.parse(
                candidate,
                dayfirst=True
            ).strftime("%Y-%m-%d")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Vendor
    # ------------------------------------------------------------------

    vendor_patterns = [
        r"Vendor\s*[:#]?\s*(.+)",
        r"Supplier\s*[:#]?\s*(.+)",
        r"Seller\s*[:#]?\s*(.+)",
        r"Merchant\s*[:#]?\s*(.+)",
        r"Sold\s*By\s*[:#]?\s*(.+)",
        r"Billed\s*By\s*[:#]?\s*(.+)",
        r"Company\s*Name\s*[:#]?\s*(.+)",
        r"Business\s*Name\s*[:#]?\s*(.+)",
    ]

    for pattern in vendor_patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            result["vendor"] = m.group(1).strip()
            break

    # ------------------------------------------------------------------
    # Tax
    # ------------------------------------------------------------------

    tax_patterns = [
        r"GST.*?([0-9][0-9,]*\.?[0-9]*)",
        r"IGST.*?([0-9][0-9,]*\.?[0-9]*)",
        r"CGST.*?([0-9][0-9,]*\.?[0-9]*)",
        r"SGST.*?([0-9][0-9,]*\.?[0-9]*)",
        r"VAT.*?([0-9][0-9,]*\.?[0-9]*)",
        r"Tax\s*[: ]+.*?([0-9][0-9,]*\.?[0-9]*)",
    ]

    for pattern in tax_patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            result["tax"] = clean_number(m.group(1))
            break

    # ------------------------------------------------------------------
    # Amount (Subtotal BEFORE tax)
    # ------------------------------------------------------------------

    amount = None

    for line in text.splitlines():
        l = line.lower()

        # Ignore total-like lines
        if any(x in l for x in [
            "grand total",
            "total due",
            "amount due",
            "net payable",
            "payable",
            "total"
        ]):
            continue

        # Prefer subtotal-like lines
        if any(x in l for x in [
            "subtotal",
            "sub total",
            "taxable amount",
            "taxable value",
            "basic amount",
            "base amount",
            "amount before tax",
            "amount excluding",
            "item total",
            "goods value",
            "assessable value",
        ]):
            m = re.search(r"([0-9][0-9,]*\.?[0-9]*)", line)
            if m:
                amount = clean_number(m.group(1))
                break

    result["amount"] = amount

    # ------------------------------------------------------------------
    # Currency
    # ------------------------------------------------------------------

    m = re.search(
        r"Currency\s*[:#]?\s*([A-Za-z]{3})",
        text,
        re.IGNORECASE,
    )

    if m:
        result["currency"] = m.group(1).upper()

    elif re.search(r"₹|Rs\.?|INR", text, re.IGNORECASE):
        result["currency"] = "INR"

    elif re.search(r"\$", text):
        result["currency"] = "USD"

    elif re.search(r"€", text):
        result["currency"] = "EUR"

    return result


@app.get("/")
def health():
    return {
        "status": "ok"
    }


@app.post("/extract")
def extract(req: InvoiceRequest):
    return extract_fields(req.invoice_text)