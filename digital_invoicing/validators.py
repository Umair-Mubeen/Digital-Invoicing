"""
validators.py  —  FBR DI API validation rules (official Error Message Guide).

Implements the real FBR sandbox validations so the mock client rejects invoices
with the SAME error codes the live API would. Build & test against these locally;
what passes here should pass the real sandbox.

Each check returns {"errorCode": "00xx", "error": "..."} matching FBR's guide.
Returns a list (empty = valid). Sale-module codes covered: 0002–0302.
"""

import re
from datetime import datetime, date

# Standard-rate sale types where quantity is mandatory (0098) and rate is 18%.
STANDARD_RATE_TYPES = {"Goods at standard rate"}
REDUCED_RATE_TYPES = {"Goods at reduced rate"}
THIRD_SCHEDULE_TYPES = {"3rd Schedule Goods"}
COTTON_GINNER_TYPES = {"Cotton Ginner"}

VALID_INVOICE_TYPES = {"Sale Invoice", "Debit Note"}
VALID_REG_TYPES = {"Registered", "Unregistered"}
VALID_PROVINCES = {"Sindh", "Punjab", "KPK", "Balochistan",
                   "Islamabad", "AJK", "Gilgit-Baltistan",
                   "Khyber Pakhtunkhwa", "Capital Territory"}


def _err(code, msg):
    return {"errorCode": code, "error": msg}


def _is_ntn(v):   # 7 digits, no special chars
    return bool(re.fullmatch(r"\d{7}", v or ""))


def _is_cnic(v):  # 13 digits
    return bool(re.fullmatch(r"\d{13}", v or ""))


def _valid_reg_no(v):
    return _is_ntn(v) or _is_cnic(v)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _decimals_ok(v, places=2):
    s = str(v)
    if "." not in s:
        return True
    return len(s.split(".")[1]) <= places


def validate_invoice(p: dict) -> list:
    """Return a list of FBR-style errors for the given v1.12 payload."""
    errors = []
    inv_type = p.get("invoiceType", "")
    is_debit = inv_type == "Debit Note"

    # ---- Header-level ----
    if inv_type not in VALID_INVOICE_TYPES:
        errors.append(_err("0003", "Provided invoice type is not valid"))

    seller_reg = p.get("sellerNTNCNIC", "")
    if not seller_reg:
        errors.append(_err("0082", "Provided seller registration no. does not belong to registered person"))
    elif not _valid_reg_no(seller_reg):
        errors.append(_err("0108", "Seller Registration No. is not in proper format"))

    buyer_reg = p.get("buyerNTNCNIC", "")
    if buyer_reg and not _valid_reg_no(buyer_reg):
        errors.append(_err("0002", "Buyer Registration No. is not in proper format"))

    if not p.get("buyerBusinessName"):
        errors.append(_err("0010", "Buyer Name is mandatory"))

    reg_type = p.get("buyerRegistrationType", "")
    if not reg_type:
        errors.append(_err("0012", "Provided buyer registration type is not valid"))
    elif reg_type not in VALID_REG_TYPES:
        errors.append(_err("0012", "Provided buyer registration type is not valid"))

    if seller_reg and buyer_reg and seller_reg == buyer_reg:
        errors.append(_err("0058", "Buyer and Seller Registration number are same"))

    # province
    if p.get("sellerProvince") not in VALID_PROVINCES:
        errors.append(_err("0073", "Seller province is either not provided or invalid"))
    if p.get("buyerProvince") not in VALID_PROVINCES:
        errors.append(_err("0074", "Buyer province is either not provided or invalid"))

    # invoice date
    inv_date = p.get("invoiceDate", "")
    if not inv_date:
        errors.append(_err("0042", "Invoice date cannot be empty"))
    else:
        try:
            d = datetime.strptime(inv_date, "%Y-%m-%d").date()
            if d > date.today():
                errors.append(_err("0043", "Invoice date is greater than current date"))
        except ValueError:
            errors.append(_err("0113", 'Invoice date is not in proper format (YYYY-MM-DD)'))

    # debit note specifics
    if is_debit:
        if not p.get("invoiceRefNo"):
            errors.append(_err("0026", "Invoice Reference No. is mandatory requirement for debit note"))

    # ---- Item-level ----
    items = p.get("items", [])
    for i, it in enumerate(items, start=1):
        pfx = f"Item {i}: "
        sale_type = it.get("saleType", "")
        hs = it.get("hsCode", "")
        rate = it.get("rate", "")
        value = _num(it.get("valueSalesExcludingST"))
        qty = _num(it.get("quantity"))
        st = _num(it.get("salesTaxApplicable"))

        if not sale_type:
            errors.append(_err("0013", pfx + "Sale type is not provided"))

        if not hs:
            errors.append(_err("0044", pfx + "HS Code cannot be empty"))

        if rate in ("", None):
            errors.append(_err("0020", pfx + "Rate field cannot be empty or null"))

        if value is None:
            errors.append(_err("0021", pfx + "Value of Sales Excl. ST cannot be empty"))
        elif value < 0:
            errors.append(_err("0300", pfx + "Provided numeric values are invalid"))
        elif not _decimals_ok(it.get("valueSalesExcludingST"), 2):
            errors.append(_err("0302", pfx + "Decimal places exceed allowed limits"))

        if st is None:
            errors.append(_err("0018", pfx + "Sales Tax cannot be empty"))

        # quantity mandatory for standard-rate goods (0098)
        if sale_type in STANDARD_RATE_TYPES and (qty is None or qty == 0):
            errors.append(_err("0098", pfx + "Quantity is empty"))
        if qty is not None and not _decimals_ok(it.get("quantity"), 4):
            errors.append(_err("0302", pfx + "Decimal places exceed allowed limits"))

        # SRO/schedule mandatory where rate is not 18% (0077)
        rate_num = _num(str(rate).replace("%", "")) if rate else None
        if rate_num is not None and rate_num != 18 and not it.get("sroScheduleNo"):
            errors.append(_err("0077", pfx + "Valid SRO/Schedule No. is mandatory where rate is not 18%"))

        # 3rd Schedule → retail price mandatory (0090) + ST must match retail×rate (0102)
        if sale_type in THIRD_SCHEDULE_TYPES:
            retail = _num(it.get("fixedNotifiedValueOrRetailPrice"))
            if not retail:
                errors.append(_err("0090", pfx + "Fixed/Notified Value or Retail Price is mandatory"))
            elif rate_num is not None and st is not None:
                expected = round(retail * rate_num / 100, 2)
                if abs(expected - st) > 0.5:
                    errors.append(_err("0102", pfx + "Provided sales tax amount does not match the calculated sales tax amount in case of 3rd schedule goods"))

        # standard/reduced → ST must match value×rate (0104)
        elif rate_num not in (None, 0) and value is not None and st is not None:
            expected = round(value * rate_num / 100, 2)
            if abs(expected - st) > 0.5:
                errors.append(_err("0104", pfx + "Provided sales tax amount does not match the calculated sales tax amount"))

        # reduced-rate → no extra tax (0091)
        if sale_type in REDUCED_RATE_TYPES and it.get("extraTax") not in ("", None, 0):
            errors.append(_err("0091", pfx + "Extra tax provided where sale is of reduced rate goods"))

        # 5% rate not allowed if value > 20,000 per unit (0079)
        if rate_num == 5 and value is not None and qty:
            per_unit = value / qty if qty else value
            if per_unit > 20000:
                errors.append(_err("0079", pfx + "For value greater than 20,000 (per unit), 5% rate is not allowed"))

        # cotton ginner → buyer must be registered (0100)
        if sale_type in COTTON_GINNER_TYPES and reg_type != "Registered":
            errors.append(_err("0100", pfx + "Provided buyer is not registered"))

    if not items:
        errors.append(_err("0021", "At least one item is required"))

    return errors
