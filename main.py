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

    # ---- date: look for a label, then parse whatever date-shaped text follows ----
    # Covers: "15 March 2026", "4th April 2026", "March 15, 2026", "2026-03-15",
    # "04/04/2026", "04-04-2026", "04.04.2026", etc.
    DATE_VALUE = (
        r"[0-9]{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\.?,?\s+[0-9]{4}"   # 15 March 2026 / 4th April 2026
        r"|[A-Za-z]+\.?\s+[0-9]{1,2}(?:st|nd|rd|th)?,?\s+[0-9]{4}"   # March 15, 2026
        r"|[0-9]{4}[/\-.][0-9]{1,2}[/\-.][0-9]{1,2}"                 # 2026-03-15 / 2026/03/15
        r"|[0-9]{1,2}[/\-.][0-9]{1,2}[/\-.][0-9]{4}"                 # 04/04/2026 / 04-04-2026
    )
    m = re.search(
        r"(?:Invoice\s*Date|Bill(?:ing)?\s*Date|Date\s*of\s*Issue|Dated|Invoice\s*Dt\.?|Issued(?:\s*On)?|Dt\.?|Date)"
        r"\s*[:#]?\s*(" + DATE_VALUE + r")",
        text, re.IGNORECASE,
    )
    if not m:
        # Fallback: no recognizable label — just grab the first date-shaped text anywhere.
        m = re.search(DATE_VALUE, text, re.IGNORECASE)

    if m:
        candidate = m.group(1) if m.lastindex else m.group(0)
        try:
            # dayfirst=True since these are Indian invoices (DD/MM/YYYY convention)
            result["date"] = dateparser.parse(candidate, dayfirst=True).strftime("%Y-%m-%d")
        except (ValueError, OverflowError):
            pass

    # ---- vendor: try many common labels first, then fall back to the first
    # "real" line of the document (skipping generic headers and other known labels) ----
    m = re.search(
        r"(?:Vendor|Seller|Supplier|Merchant|Sold\s*By|Billed\s*By|From|Company(?:\s*Name)?|Business\s*Name)"
        r"\s*[:#]?\s*(.+)",
        text, re.IGNORECASE,
    )
    if m:
        result["vendor"] = m.group(1).strip()
    else:
        m2 = re.search(r"^([A-Za-z0-9&.,'\s]+?)\s*[—-]\s*Tax Invoice", text, re.MULTILINE)
        if m2:
            result["vendor"] = m2.group(1).strip()
        else:
            # Last resort: scan line by line for the first line that isn't a
            # generic header word (INVOICE, RECEIPT...) and isn't itself
            # some other known label (Invoice No, Date, Bill To...).
            generic_headers = {"invoice", "tax invoice", "receipt", "bill",
                                "credit note", "proforma invoice"}
            labeled_line = re.compile(
                r"^\s*(?:invoice\s*no\.?|invoice\s*number|ref|date|dated|"
                r"invoice\s*date|bill(?:ing)?\s*date|issued|dt\.?|bill\s*to|"
                r"client|customer|buyer)\b",
                re.IGNORECASE,
            )
            for line in text.splitlines():
                line = line.strip()
                if not line or line.lower() in generic_headers:
                    continue
                if labeled_line.match(line):
                    continue
                line = re.sub(r"\s*[—-]\s*(?:Tax\s*)?Invoice\s*$", "", line, flags=re.IGNORECASE).strip()
                if line:
                    result["vendor"] = line
                    break

    # ---- tax: GST/IGST/CGST/SGST/VAT/Tax line, skipping any "(18%)" style rate first ----
    # Anchored to the START of a line so we don't accidentally match the word
    # "Tax" inside an unrelated phrase like "Tax Invoice".
    m = re.search(
        r"^[ \t]*(?:IGST|CGST|SGST|GST|VAT|Tax)\b\s*(?:\(\d{1,3}%\))?[^\d\n]*?([0-9][0-9,]*\.?[0-9]*)",
        text, re.IGNORECASE | re.MULTILINE,
    )
    if m:
        result["tax"] = clean_number(m.group(1))

    # ---- amount: the pre-tax subtotal, under many possible labels ----
    m = re.search(
        r"^[ \t]*(?:Sub\s*[- ]?\s*total|Net\s*Amount|Taxable\s*(?:Value|Amount)|"
        r"Basic\s*Amount|Amount\s*Before\s*Tax|Amount\s*\(?\s*excl(?:uding|\.)?\s*(?:Tax|GST)\s*\)?|"
        r"Base\s*Amount)\b[^\d\n]*?([0-9][0-9,]*\.?[0-9]*)",
        text, re.IGNORECASE | re.MULTILINE,
    )
    if m:
        result["amount"] = clean_number(m.group(1))
    else:
        # Fallback: no subtotal-style label found. If we can find a grand
        # total AND we already know the tax, amount = total - tax.
        total_match = re.search(
            r"^[ \t]*(?:Grand\s*Total|Total\s*Due|Total\s*Amount|Amount\s*Due|TOTAL|Total)\b"
            r"[^\d\n]*?([0-9][0-9,]*\.?[0-9]*)",
            text, re.IGNORECASE | re.MULTILINE,
        )
        if total_match and result["tax"] is not None:
            total_value = clean_number(total_match.group(1))
            if total_value is not None:
                result["amount"] = round(total_value - result["tax"], 2)

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