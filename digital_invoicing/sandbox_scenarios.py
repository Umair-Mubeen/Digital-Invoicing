"""
Milestone 7 — PRAL Sandbox Scenario Runner.

Sab 28 official scenarios (DI Scenarios doc v1.11) ke payloads generate
karta hai — item values PRAL ke sample JSONs ke pattern par, taxes hamare
Tax Engine se (server-authoritative). `run_sandbox_scenarios` command inhe
FBR sandbox ke validateinvoicedata endpoint par chala kar per-scenario
result deta hai — invoice CREATE kiye baghair (Tech Spec §4.2).
"""
from datetime import date
from decimal import Decimal

from .tax_engine import SCENARIOS, compute_item

# Scenario-specific item inputs (PRAL doc ke sample JSONs se):
# (hs, uom, value, qty, mrp, buyer_reg, stwh)
_D = {
    "SN001": ("0101.2100", "Numbers, pieces, units", 1000, 1, 0, "Registered", 0),
    "SN002": ("0101.2100", "Numbers, pieces, units", 1000, 1, 0, "Unregistered", 0),
    "SN003": ("7214.1010", "MT", 1000, 1, 0, "Unregistered", 0),
    "SN004": ("7204.3000", "MT", 1000, 1, 0, "Unregistered", 0),
    "SN005": ("0101.2100", "Numbers, pieces, units", 1000, 1, 0, "Registered", 0),
    "SN006": ("0101.2100", "Numbers, pieces, units", 1000, 1, 0, "Registered", 0),
    "SN007": ("0101.2100", "Numbers, pieces, units", 1000, 1, 0, "Registered", 0),
    "SN008": ("0101.2100", "Numbers, pieces, units", 1000, 1, 1000, "Registered", 0),
    "SN009": ("5201.0000", "KG", 1000, 1, 0, "Registered", 180),
    "SN010": ("9915.0000", "Numbers, pieces, units", 1000, 1, 0, "Unregistered", 0),
    "SN011": ("7214.9990", "MT", 1000, 1, 0, "Registered", 0),
    "SN012": ("2710.1210", "Liter", 100, 1, 0, "Registered", 0),
    "SN013": ("2716.0000", "KWH", 1000, 1, 0, "Registered", 0),
    "SN014": ("2711.2100", "MMBTU", 1000, 1, 0, "Unregistered", 0),
    "SN015": ("8517.1219", "Numbers, pieces, units", 1000, 1, 0, "Unregistered", 0),
    "SN016": ("0101.2100", "Numbers, pieces, units", 1000, 1, 0, "Unregistered", 0),
    "SN017": ("0101.2100", "Numbers, pieces, units", 100, 1, 0, "Unregistered", 0),
    "SN018": ("9815.0000", "Numbers, pieces, units", 100, 1, 0, "Unregistered", 0),
    "SN019": ("9815.0000", "Numbers, pieces, units", 1000, 1, 0, "Unregistered", 0),
    "SN020": ("8703.8030", "Numbers, pieces, units", 1000, 1, 0, "Unregistered", 0),
    "SN021": ("6810.1100", "Numbers, pieces, units", 123, 12, 0, "Unregistered", 0),
    "SN022": ("3102.1000", "KG", 100, 1, 0, "Unregistered", 0),
    "SN023": ("2711.2100", "KG", 234, 123, 0, "Unregistered", 0),
    "SN024": ("7204.3000", "MT", 1000, 1, 0, "Registered", 0),
    "SN025": ("3004.9099", "Numbers, pieces, units", 100, 1, 0, "Registered", 0),
    "SN026": ("0101.2100", "Numbers, pieces, units", 1000, 1, 0, "Registered", 0),
    "SN027": ("0101.2100", "Numbers, pieces, units", 1000, 1, 1000, "Registered", 0),
    "SN028": ("0101.2100", "Numbers, pieces, units", 1000, 1, 0, "Registered", 0),
}

# STWH note: SN009 (cotton ginners) mein sample STWH = ST ke barabar (0050
# rule: zero ya ST ke barabar) — 180 for value 1000 @ 18%.

# Scenario-specific SRO refs (PRAL sample JSONs se verbatim) — jahan sale
# type ka default sro/item kaafi nahi (0077/0078 se bachne ke liye).
_SRO = {
    "SN005": ("EIGHTH SCHEDULE Table 1", "82"),
    "SN006": ("6th Schd Table I", "100"),
    "SN007": ("327(I)/2008", "1"),
    "SN008": ("", ""),                     # 3rd Sched — sample sro khali
    "SN027": ("", ""),
    "SN010": ("", ""),                     # 17% — sample sro khali; FBR side
    "SN016": ("", ""),
    "SN018": ("", ""),
    "SN019": ("ICTO TABLE I", "1(ii)(ii)(a)"),
    "SN024": ("297(I)/2023-Table-I", "12"),
    "SN028": ("EIGHTH SCHEDULE Table 1", "70"),
}


def build_scenario_payload(code, profile, buyer_ntn="7654321",
                           on_date=None):
    """Ek scenario ka complete FBR payload — taxes engine se computed."""
    on_date = on_date or date.today()
    scen = next((s for s in SCENARIOS if s[0] == code), None)
    if scen is None:
        raise ValueError(f"Unknown scenario: {code}")
    _, desc, sale_type = scen
    hs, uom, value, qty, mrp, buyer_reg, stwh = _D[code]

    calc = compute_item(sale_type, value,
                        buyer_unregistered=(buyer_reg == "Unregistered"),
                        hs_code=hs, retail_price=mrp, on_date=on_date,
                        quantity=qty)
    item = {
        "hsCode": hs, "productDescription": f"{desc} (sandbox test)",
        "rate": calc["rate"], "uoM": uom, "quantity": qty,
        "totalValues": 0, "valueSalesExcludingST": value,
        "fixedNotifiedValueOrRetailPrice": mrp,
        "salesTaxApplicable": float(calc["sales_tax"]),
        "salesTaxWithheldAtSource": stwh,
        "extraTax": "", "furtherTax": float(calc["further_tax"]),
        "sroScheduleNo": _SRO.get(code, (calc["sro_schedule"],))[0]
                         if code in _SRO else calc["sro_schedule"],
        "sroItemSerialNo": _SRO[code][1] if code in _SRO
                           else calc["sro_item"],
        "fedPayable": 0, "discount": 0,
        "saleType": calc["sale_type"],
    }
    buyer_unreg = buyer_reg == "Unregistered"
    return {
        "invoiceType": "Sale Invoice",
        "invoiceDate": on_date.strftime("%Y-%m-%d"),
        "sellerNTNCNIC": profile.ntn_cnic,
        "sellerBusinessName": profile.business_name,
        "sellerProvince": profile.province,
        "sellerAddress": profile.address,
        "buyerNTNCNIC": "" if buyer_unreg else buyer_ntn,
        "buyerBusinessName": "Sandbox Test Buyer",
        "buyerProvince": profile.province,
        "buyerAddress": "Sandbox Address",
        "buyerRegistrationType": buyer_reg,
        "invoiceRefNo": "",
        "scenarioId": code,
        "items": [item],
    }


def run_scenarios(profile, codes=None, validate_only=True, client=None):
    """[(code, ok, message)] — validate_only=True par invoice create nahi
    hoti (Tech Spec §4.2 validateinvoicedata)."""
    from .fbr_client import get_fbr_client
    from .validators import validate_invoice
    client = client or get_fbr_client(profile)
    codes = codes or [s[0] for s in SCENARIOS]
    results = []
    for code in codes:
        payload = build_scenario_payload(code, profile)
        local = validate_invoice(payload)
        if local:
            results.append((code, False,
                            "LOCAL: " + "; ".join(
                                f'{e["errorCode"]} {e["error"]}'
                                for e in local[:3])))
            continue
        fn = (client.validate_invoice if validate_only and
              hasattr(client, "validate_invoice") else client.post_invoice)
        resp = fn(dict(payload))
        vr = resp.get("validationResponse", {})
        ok = vr.get("status") == "Valid"
        msg = "OK" if ok else (vr.get("error") or
                               str(vr.get("invoiceStatuses"))[:200])
        results.append((code, ok, msg))
    return results
