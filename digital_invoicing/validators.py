"""
validators.py  —  FBR DI API validation rules (official Error Message Guide).

Implements the real FBR sandbox validations so the mock client rejects invoices
with the SAME error codes the live API would. Build & test against these locally;
what passes here should pass the real sandbox.

Each check returns {"errorCode": "00xx", "error": "..."} matching FBR's guide.
Returns a list (empty = valid).

Coverage (Error Message Guide — Sales, all 58 codes accounted for):
  Local (mock = live):  0002 0003 0007 0008 0010 0012 0013 0018 0019(format)
    0020 0021 0022 0026 0027 0028 0034 0035 0042 0043 0044 0046 0050 0058
    0057 0060 0061 0062 0067 0073 0074 0077 0078 0079 0090 0091 0097 0098
    0100(reg-type) 0102 0103 0104 0105 0108 0113 0300 0302
  Duplicates in guide:  0023==0018, 0029==0035, 0008==0050 (same rule text)
  REGISTRY-ONLY (sirf real FBR API enforce karta hai — mock pass karega):
    0019(HS existence) 0052(HS<->saleType FBR mapping) 0053(buyer profile)
    0056(steel-sector buyers) 0059(notified min price) 0071(NTN registry)
    0082(active registration) 0086(EFS license) 0093(seller=manufacturer)
    0100(actual registration) 0101(toll mfg service check)
"""

import re
from datetime import datetime, date

from .tax_engine import resolve_sale_type, load_rules

# Official PRAL labels (Milestone 1). resolve_sale_type() legacy aliases
# ko in par map karta hai, isliye sets sirf official names rakhte hain.
STANDARD_RATE_TYPES = {"Goods at standard rate (default)"}
REDUCED_RATE_TYPES = {"Goods at Reduced Rate"}
THIRD_SCHEDULE_TYPES = {"3rd Schedule Goods"}
COTTON_GINNER_TYPES = {"Cotton ginners"}
STEEL_MELTING_TYPES = {"Steel melting and re-rolling"}
POTASSIUM_CHLORATE_TYPES = {"Potassium Chlorate"}

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


def _hs_format_ok(v):
    """FBR HS: 4-8 digits, dot optional (e.g. 0101.2100 / 8471.3010 / 0101)."""
    return bool(re.fullmatch(r"\d{4}(\.\d{2,4})?", str(v or "").strip()))


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
    # Effective engine rules (0007/0046/0103/0105 in ke against check hote hain)
    try:
        engine_types, _, _ = load_rules()
    except Exception:
        engine_types = {}
    items = p.get("items", [])
    for i, it in enumerate(items, start=1):
        pfx = f"Item {i}: "
        sale_type = resolve_sale_type(it.get("saleType", ""))
        cfg = engine_types.get(sale_type)
        hs = it.get("hsCode", "")
        uom = (it.get("uoM") or "").strip()
        rate = it.get("rate", "")
        value = _num(it.get("valueSalesExcludingST"))
        qty = _num(it.get("quantity"))
        st = _num(it.get("salesTaxApplicable"))
        stwh = _num(it.get("salesTaxWithheldAtSource"))

        if not sale_type:
            errors.append(_err("0013", pfx + "Sale type is not provided"))
        elif engine_types and cfg is None:
            errors.append(_err("0007", pfx + "Provided sale type is not valid"))

        if not hs:
            errors.append(_err("0044", pfx + "HS Code cannot be empty"))
        elif not _hs_format_ok(hs):
            errors.append(_err("0019", pfx + "HS Code is either not provided or invalid"))

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

        # SRO/schedule mandatory where rate is not 18% (0077); item Sr No
        # mandatory where SRO provided (0078)
        rate_num = _num(str(rate).replace("%", "")) if rate else None
        if rate_num is not None and rate_num != 18 and not it.get("sroScheduleNo"):
            errors.append(_err("0077", pfx + "Valid SRO/Schedule No. is mandatory where rate is not 18%"))
        if it.get("sroScheduleNo") and not (it.get("sroItemSerialNo") or "").strip():
            errors.append(_err("0078", pfx + "Valid Item Sr. No. is mandatory where SRO/Schedule No. is provided"))

        # Rate label must match selected sale type's effective rate (0046)
        if cfg is not None and rate not in ("", None):
            from decimal import Decimal as _D
            expected_label = (cfg.get("rate_label")
                              or f'{_D(str(cfg["rate"])).normalize()}%')
            rate_norm = str(rate).strip()
            ok = (rate_norm == expected_label or
                  (rate_num is not None and _num(str(cfg["rate"])) == rate_num
                   and cfg.get("rate_type", "percent") == "percent"))
            if not ok:
                errors.append(_err("0046", pfx + "Provided Rate is not correct for selected sales type"))

        rate_type = (cfg or {}).get("rate_type", "percent")
        per_unit = float((cfg or {}).get("per_unit", 0) or 0)

        # ST amount checks, routed by PRAL rate semantics:
        # 0102 3rd Schedule (retail x rate), 0103 Potassium Chlorate
        # (value x 18% + qty x 60), 0105 fixed per unit (qty x Rs.X),
        # 0104 default (value x rate)
        if sale_type in THIRD_SCHEDULE_TYPES:
            retail = _num(it.get("fixedNotifiedValueOrRetailPrice"))
            if not retail:
                errors.append(_err("0090", pfx + "Fixed/Notified Value or Retail Price is mandatory"))
            elif rate_num is not None and st is not None:
                expected = round(retail * rate_num / 100, 2)
                if abs(expected - st) > 0.5:
                    errors.append(_err("0102", pfx + "Provided sales tax amount does not match the calculated sales tax amount in case of 3rd schedule goods"))

        elif sale_type in POTASSIUM_CHLORATE_TYPES:
            if st is not None and value is not None and qty is not None:
                expected = round(value * float((cfg or {}).get("rate", 18)) / 100
                                 + qty * (per_unit or 60), 2)
                if abs(expected - st) > 0.5:
                    errors.append(_err("0103", pfx + 'Provided sales tax amount does not match the calculated sales tax amount in case where sale type is "Potassium Chlorate"'))

        elif rate_type == "fixed_per_unit":
            if st is not None and qty is not None:
                expected = round(qty * per_unit, 2)
                if abs(expected - st) > 0.5:
                    errors.append(_err("0105", pfx + "Provided sales tax amount does not match the calculated sales tax amount"))

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

        # cotton ginners → STWH mandatory (0022); zero ya ST ke barabar (0050)
        if sale_type in COTTON_GINNER_TYPES:
            if stwh is None:
                errors.append(_err("0022", pfx + "ST withheld at Source cannot be empty where sales type is cotton ginners"))
            elif st is not None and stwh != 0 and abs(stwh - st) > 0.01:
                errors.append(_err("0050", pfx + "For cotton ginner sale type, ST withheld at source should either be zero or same as sales tax/fed in ST mode"))

        # UoM rules (Error Guide):
        # 0062 — Steel melting and re-rolling => MT
        if sale_type in STEEL_MELTING_TYPES and uom and uom.upper() != "MT":
            errors.append(_err("0062", pfx + "MT UoM is required for Steel melting and re-rolling sale type"))
        # 0097 — Potassium Chlorate => KG
        if sale_type in POTASSIUM_CHLORATE_TYPES and uom and uom.upper() != "KG":
            errors.append(_err("0097", pfx + "Provided UoM is not KG"))
        # 0060 — Services + rate 50/SqY|100/SqY => SqY
        if sale_type == "Services" and str(rate).strip() in ("50/SqY", "100/SqY") \
                and uom.lower() != "sqy":
            errors.append(_err("0060", pfx + "SqY UoM is required for Services sale type if selected rate is 50/SqY or 100/SqY"))
        # 0061 — Services (FED in ST Mode) + rate 200/bill => Bill of lading
        if sale_type == "Services (FED in ST Mode)" and str(rate).strip() == "200/bill" \
                and uom.lower() != "bill of lading":
            errors.append(_err("0061", pfx + "Bill of lading UoM is required for Services (Fed in ST Mode) sale type if selected rate is 200/bill"))

    if not items:
        errors.append(_err("0021", "At least one item is required"))

    return errors
