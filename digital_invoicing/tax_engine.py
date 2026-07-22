"""
tax_engine.py  —  The "tax-smart" layer that sets this product apart.

Given a sale type + value + quantity + buyer registration status, it returns
the correct rate, sales tax, further tax and SRO schedule — so the operator
never has to know the rules. Every rule here is traceable to the Sales Tax
Act 1990 and the PRAL DI Scenarios document v1.11 (SN001-SN028).

Rate semantics (PRAL Scenarios doc, verified against sample JSON):
  - percent .......... ST = base x rate%           (most types)
  - fixed_per_unit ... ST = quantity x Rs.X        (SN021 Cement Rs.3,
                                                    SN023 CNG Rs.200)
  - compound ......... ST = base x rate% + qty x Rs.X
                                                   (SN022 Potassium Chlorate
                                                    "18% along with rupees 60
                                                    per kilogram")
  - exempt ........... no ST                       (SN006 Sixth Schedule)
  - 3rd Schedule ..... percent on RETAIL PRICE/MRP (SN008/SN027, Sec 3(2)(a))

Further tax (Section 3(1A), 4% to unregistered buyers):
  - Auto-applied ONLY on general goods types (standard/reduced) — sector
    types default OFF; enable per current SRO via admin (TaxSaleType row).
    ASSUMPTION stated: PRAL docs give no FT applicability matrix.
  - Never on: 3rd Schedule / exempt / zero-rated, SRO 648(I)/2013
    (+1223(I)/2021) excluded HS codes, registered buyers.

NOTE (Umair verify): rates below are PRAL sandbox-sample defaults. Reduced
rate (Eighth Schedule) is ITEM-WISE — sample uses 1%. Verify all against the
current Finance Act / SROs in admin before production.
"""

from decimal import Decimal, ROUND_HALF_UP

FURTHER_TAX_RATE = Decimal("4")   # % — Section 3(1A)

# HS-code prefixes jin par further tax NAHI lagta (SRO 648(I)/2013 + amendments).
FURTHER_TAX_EXEMPT_HS = {
    # --- SRO 648(I)/2013 ---
    "3102", "3103", "3104", "3105",   # Fertilizers
    "2710",                             # Petroleum oils (POL products)
    "2711",                             # Petroleum gases (LPG/CNG)
    "8703", "8704", "8711",            # Vehicles
    "1001", "1006", "1101",            # Wheat, rice, wheat flour
    "0401", "0402",                     # Milk & dairy
    "3004",                             # Medicaments (pharma)
    # --- SRO 1223(I)/2021 (amends 648) — steel sector ---
    "7207", "7208", "7209", "7210", "7211", "7212", "7213", "7214",
    "7215", "7216", "7217", "7218", "7219", "7220", "7221", "7222",
    "7223", "7224", "7225", "7226", "7227", "7228", "7229",
    "7301", "7302", "7303", "7304", "7305", "7306", "7307", "7308",
    # Edible oil sector
    "1507", "1508", "1509", "1510", "1511", "1512", "1513", "1514",
    "1515", "1516", "1517", "1518",
}

# Rate semantics
RT_PERCENT = "percent"
RT_FIXED = "fixed_per_unit"
RT_COMPOUND = "compound"
RT_EXEMPT = "exempt"


def _cfg(rate, rate_type=RT_PERCENT, per_unit=0, label="", charges_st=True,
         further=False, sro="", sro_item="", retail=False, scenarios=()):
    return {
        "rate": Decimal(str(rate)), "rate_type": rate_type,
        "per_unit": Decimal(str(per_unit)), "rate_label": label,
        "charges_st": charges_st, "further": further,
        "sro": sro, "sro_item": sro_item,
        "retail_price_based": retail, "scenarios": list(scenarios),
    }


# saleType label -> config. Labels are EXACTLY the official PRAL DI strings
# (Scenarios doc v1.11 / Tech Spec v1.12 §4.1.2) — mismatch = FBR rejection.
SALE_TYPES = {
    "Goods at standard rate (default)":
        _cfg(18, further=True, scenarios=["SN001", "SN002", "SN026"]),
    "Goods at Reduced Rate":
        _cfg(1, further=True, sro="EIGHTH SCHEDULE Table 1",
             scenarios=["SN005", "SN028"]),        # item-wise — verify
    "3rd Schedule Goods":
        _cfg(18, retail=True, scenarios=["SN008", "SN027"]),
    "Exempt goods":
        _cfg(0, rate_type=RT_EXEMPT, label="Exempt", charges_st=False,
             sro="6th Schd Table I", scenarios=["SN006"]),
    "Goods at zero-rate":
        _cfg(0, label="0%", scenarios=["SN007"]),
    "Steel melting and re-rolling":
        _cfg(18, scenarios=["SN003"]),
    "Ship breaking":
        _cfg(18, scenarios=["SN004"]),
    "Cotton ginners":
        _cfg(18, scenarios=["SN009"]),
    "Telecommunication services":
        _cfg(17, scenarios=["SN010"]),
    "Toll Manufacturing":
        _cfg(18, scenarios=["SN011"]),
    "Petroleum Products":
        _cfg(1.43, sro="1450(I)/2021", sro_item="4", scenarios=["SN012"]),
    "Electricity Supply to Retailers":
        _cfg(5, sro="1450(I)/2021", sro_item="4", scenarios=["SN013"]),
    "Gas to CNG stations":
        _cfg(18, scenarios=["SN014"]),
    "Mobile Phones":
        _cfg(18, sro="NINTH SCHEDULE", sro_item="1(A)", scenarios=["SN015"]),
    "Processing/Conversion of Goods":
        _cfg(5, scenarios=["SN016"]),
    "Goods (FED in ST Mode)":
        _cfg(8, scenarios=["SN017"]),
    "Services (FED in ST Mode)":
        _cfg(8, scenarios=["SN018"]),
    "Services":
        _cfg(5, sro="ICTO TABLE I", scenarios=["SN019"]),  # ICT Ordinance
    "Electric Vehicle":
        _cfg(1, sro="6th Schd Table III", sro_item="20", scenarios=["SN020"]),
    "Cement /Concrete Block":
        _cfg(0, rate_type=RT_FIXED, per_unit=3, label="Rs.3",
             scenarios=["SN021"]),
    "Potassium Chlorate":
        _cfg(18, rate_type=RT_COMPOUND, per_unit=60,
             label="18% along with rupees 60 per kilogram",
             sro="EIGHTH SCHEDULE Table 1", sro_item="56",
             scenarios=["SN022"]),
    "CNG Sales":
        _cfg(0, rate_type=RT_FIXED, per_unit=200, label="Rs.200",
             sro="581(1)/2024", sro_item="Region-I", scenarios=["SN023"]),
    "Goods as per SRO.297(|)/2023":
        _cfg(25, sro="297(I)/2023-Table-I", scenarios=["SN024"]),
    "Non-Adjustable Supplies":
        _cfg(0, label="0%", sro="EIGHTH SCHEDULE Table 1", sro_item="81",
             scenarios=["SN025"]),
}

# Purane (pre-Milestone-1) labels — DB/SavedItems mein stored data ke liye.
LEGACY_ALIASES = {
    "Goods at standard rate": "Goods at standard rate (default)",
    "Goods at reduced rate": "Goods at Reduced Rate",
    "Exempt Goods": "Exempt goods",
    "Zero-rated Goods": "Goods at zero-rate",
}

# PRAL DI Scenarios doc v1.11 — official descriptions.
SCENARIOS = [
    ("SN001", "Sale of Standard Rate Goods to Registered Buyers", "Goods at standard rate (default)"),
    ("SN002", "Sale of Standard Rate Goods to Unregistered Buyers", "Goods at standard rate (default)"),
    ("SN003", "Sale of Steel (Melted and Re-Rolled)", "Steel melting and re-rolling"),
    ("SN004", "Sale of Steel Scrap by Ship Breakers", "Ship breaking"),
    ("SN005", "Sales of Reduced Rate Goods (Eighth Schedule)", "Goods at Reduced Rate"),
    ("SN006", "Sale of Exempt Goods (Sixth Schedule)", "Exempt goods"),
    ("SN007", "Sale of Zero-Rated Goods (Fifth Schedule)", "Goods at zero-rate"),
    ("SN008", "Sale of 3rd Schedule Goods", "3rd Schedule Goods"),
    ("SN009", "Purchase From Registered Cotton Ginners", "Cotton ginners"),
    ("SN010", "Sale of Telecom Services by Mobile Operators", "Telecommunication services"),
    ("SN011", "Sale of Steel through Toll Manufacturing", "Toll Manufacturing"),
    ("SN012", "Sale of Petroleum Products", "Petroleum Products"),
    ("SN013", "Sale of Electricity to Retailers", "Electricity Supply to Retailers"),
    ("SN014", "Sale of Gas to CNG Stations", "Gas to CNG stations"),
    ("SN015", "Sale of Mobile Phones", "Mobile Phones"),
    ("SN016", "Processing / Conversion of Goods", "Processing/Conversion of Goods"),
    ("SN017", "Sale of Goods Where FED Is Charged in ST Mode", "Goods (FED in ST Mode)"),
    ("SN018", "Sale of Services Where FED Is Charged in ST Mode", "Services (FED in ST Mode)"),
    ("SN019", "Sale of Services (as per ICT Ordinance)", "Services"),
    ("SN020", "Sale of Electric Vehicles", "Electric Vehicle"),
    ("SN021", "Sale of Cement / Concrete Block", "Cement /Concrete Block"),
    ("SN022", "Sale of Potassium Chlorate", "Potassium Chlorate"),
    ("SN023", "Sale of CNG", "CNG Sales"),
    ("SN024", "Sale of Goods Listed in SRO 297(1)/2023", "Goods as per SRO.297(|)/2023"),
    ("SN025", "Drugs Sold at Fixed ST Rate (Eighth Sch. Serial 81)", "Non-Adjustable Supplies"),
    ("SN026", "Sale at Standard Rate to End Consumers by Retailers", "Goods at standard rate (default)"),
    ("SN027", "Sale of 3rd Schedule Goods to End Consumers by Retailers", "3rd Schedule Goods"),
    ("SN028", "Sale at Reduced Rate to End Consumers by Retailers", "Goods at Reduced Rate"),
]


def resolve_sale_type(name):
    """Official PRAL label return karta hai (legacy alias -> naya name)."""
    return LEGACY_ALIASES.get(name, name)


def _money(x):
    return Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# DB-driven rule resolution (date-effective, cached, safe fallback)
# Tables khali/unavailable hon to upar wale hardcoded dicts use hote hain.
# ---------------------------------------------------------------------------
_CACHE_TTL = 300  # 5 min


def _cache():
    from django.core.cache import cache
    return cache


def load_rules(on_date=None):
    """Return (sale_types_dict, further_rate, exempt_prefixes) for a date.
    DB first; empty/error -> hardcoded fallback. DB rows are MERGED over the
    hardcoded set so a partially-seeded table never hides official types."""
    from datetime import date as _date
    on_date = on_date or _date.today()
    key = f"tax_rules:{on_date.isoformat()}"
    cached_val = _cache().get(key)
    if cached_val is not None:
        return cached_val

    try:
        from .models import TaxSaleType, FurtherTaxConfig, FurtherTaxExemptHS
        from django.db.models import Q
        date_q = (Q(effective_from__lte=on_date) &
                  (Q(effective_to__isnull=True) | Q(effective_to__gte=on_date)))

        st_rows = (TaxSaleType.objects.filter(date_q, is_active=True)
                   .order_by("name", "-effective_from"))
        sale_types = dict(SALE_TYPES)          # base: full official set
        seen = set()
        for r in st_rows:
            if r.name in seen:                 # latest effective_from wins
                continue
            seen.add(r.name)
            sale_types[r.name] = {
                "rate": r.rate, "charges_st": r.charges_st,
                "further": r.further_tax_applies, "sro": r.sro_schedule,
                "retail_price_based": r.retail_price_based,
                "rate_type": getattr(r, "rate_type", RT_PERCENT) or RT_PERCENT,
                "per_unit": getattr(r, "rate_per_unit", 0) or Decimal("0"),
                "rate_label": getattr(r, "rate_label", "") or "",
                "sro_item": getattr(r, "sro_item_serial", "") or "",
                "scenarios": SALE_TYPES.get(r.name, {}).get("scenarios", []),
            }

        ft_row = (FurtherTaxConfig.objects.filter(date_q, is_active=True)
                  .order_by("-effective_from").first())
        further_rate = ft_row.rate if ft_row else FURTHER_TAX_RATE

        prefixes = set(FurtherTaxExemptHS.objects.filter(date_q, is_active=True)
                       .values_list("hs_prefix", flat=True))
        result = (sale_types, further_rate,
                  prefixes or FURTHER_TAX_EXEMPT_HS)
    except Exception:
        result = (SALE_TYPES, FURTHER_TAX_RATE, FURTHER_TAX_EXEMPT_HS)

    _cache().set(key, result, _CACHE_TTL)
    return result


def get_sale_type_config(sale_type, on_date=None):
    """Ek sale type ki effective config (services/UI ke liye). Alias-aware."""
    sale_types, _, _ = load_rules(on_date)
    return sale_types.get(resolve_sale_type(sale_type))


def get_scenarios():
    """(code, description, sale_type) list — DB seeded ho to wahan se."""
    try:
        from .models import TaxScenario
        rows = list(TaxScenario.objects.filter(is_active=True)
                    .values_list("code", "description", "sale_type"))
        if rows:
            return rows
    except Exception:
        pass
    return SCENARIOS


def invalidate_rules_cache():
    """Admin save ke baad turant reflect karne ke liye (signals se call)."""
    from datetime import date as _date
    _cache().delete(f"tax_rules:{_date.today().isoformat()}")


def compute_item(sale_type, value_excl_st, buyer_unregistered=False,
                 hs_code="", retail_price=0, on_date=None, quantity=1):
    """
    Return a dict of computed tax fields for a single line item.
    `value_excl_st` — taxable value before sales tax.
    `quantity` — fixed_per_unit / compound rates ke liye (Rs.X per unit/kg).
    `on_date` — rules is date par effective (default: aaj).
    """
    sale_types, further_rate, exempt_prefixes = load_rules(on_date)
    official = resolve_sale_type(sale_type)
    cfg = sale_types.get(official)
    if cfg is None:
        raise ValueError(f"Unknown sale type: {sale_type!r}")

    def _ft_exempt(hs):
        if not hs:
            return False
        digits = str(hs).replace(".", "").strip()
        return any(digits.startswith(p) for p in exempt_prefixes)

    value = Decimal(str(value_excl_st or 0))
    qty = Decimal(str(quantity or 1))
    retail = Decimal(str(retail_price or 0))
    # 3rd Schedule: ST RETAIL PRICE (MRP) pe (Sec 3(2)(a)); baqi sale value pe.
    st_base = (retail if retail > 0 else value) if cfg["retail_price_based"] else value

    rate_type = cfg.get("rate_type", RT_PERCENT)
    per_unit = Decimal(str(cfg.get("per_unit", 0) or 0))
    if not cfg["charges_st"] or rate_type == RT_EXEMPT:
        sales_tax = _money(0)
    elif rate_type == RT_FIXED:           # SN021/SN023: qty x Rs.X
        sales_tax = _money(qty * per_unit)
    elif rate_type == RT_COMPOUND:        # SN022: base x % + qty x Rs.X
        sales_tax = _money(st_base * cfg["rate"] / 100 + qty * per_unit)
    else:
        sales_tax = _money(st_base * cfg["rate"] / 100)

    further_tax = _money(0)
    if cfg["further"] and buyer_unregistered and not _ft_exempt(hs_code):
        further_tax = _money(value * further_rate / 100)

    total = _money(value + sales_tax + further_tax)

    rate_label = cfg.get("rate_label") or f'{Decimal(cfg["rate"]).normalize()}%'

    return {
        "rate": rate_label,
        "rate_value": cfg["rate"],
        "sales_tax": sales_tax,
        "further_tax": further_tax,
        "sro_schedule": cfg["sro"],
        "sro_item": cfg.get("sro_item", ""),
        "sale_type": official,
        "retail_price_based": cfg["retail_price_based"],
        "further_tax_hs_exempt": _ft_exempt(hs_code),
        "st_base": st_base,
        "total": total,
    }


def compute_invoice(items, buyer_unregistered=False):
    """
    items: list of dicts each with keys `sale_type` and `value_excl_st`
    (optional: `hs_code`, `quantity`, `retail_price`).
    """
    results, tv, tst, tft = [], Decimal(0), Decimal(0), Decimal(0)
    for it in items:
        r = compute_item(it["sale_type"], it["value_excl_st"],
                         buyer_unregistered, it.get("hs_code", ""),
                         retail_price=it.get("retail_price", 0),
                         quantity=it.get("quantity", 1))
        results.append(r)
        tv += Decimal(str(it["value_excl_st"] or 0))
        tst += r["sales_tax"]
        tft += r["further_tax"]
    return {
        "items": results,
        "total_value": _money(tv),
        "total_sales_tax": _money(tst),
        "total_further_tax": _money(tft),
        "invoice_total": _money(tv + tst + tft),
    }


# ---------------------------------------------------------------------------
# Eleventh Schedule (Sales Tax Act 1990) — ST withholding rules.
# basis: "st" = fraction OF SALES TAX; "gross" = % OF (value + ST);
#        "conv" = multiple of tax on conversion charges (toll mfg).
# rate: st/gross ke liye fraction (0-1+), conv ke liye multiplier.
# [VERIFY against current Finance Act — budget mein badalte hain]
# ---------------------------------------------------------------------------
WHT_RULES = {
    "S1":  {"basis": "st",    "rate": 0.20},   # 1/5 of ST
    "S2":  {"basis": "st",    "rate": 0.10},   # 1/10 of ST
    "S3":  {"basis": "st",    "rate": 1.00},   # whole of ST
    "S4":  {"basis": "gross", "rate": 0.05},   # 5% of gross value
    "S5":  {"basis": "st",    "rate": 1.00},   # whole of ST (advertisement)
    "S6":  {"basis": "st",    "rate": 1.00},   # whole of ST (cane molasses)
    "S7":  {"basis": "st",    "rate": 0.80},   # 80% of ST (lead/batteries)
    "S8":  {"basis": "gross", "rate": 0.02},   # 2% of gross (digital goods)
    "S9":  {"basis": "st",    "rate": 0.80},   # 80% (gypsum->cement)
    "S10": {"basis": "st",    "rate": 0.80},   # 80% (coal)
    "S11": {"basis": "st",    "rate": 0.80},   # 80% (waste paper)
    "S12": {"basis": "st",    "rate": 0.80},   # 80% (plastic waste)
    "S13": {"basis": "st",    "rate": 0.80},   # 80% (crush stone/silica)
    "S14": {"basis": "conv",  "rate": 4.00},   # 4x tax on conversion charges
}


def compute_withheld(serial, sales_tax, value_excl_st,
                     conversion_tax=0.0):
    """Selected Eleventh-Schedule serial se STWH amount. Classification
    caller (practitioner) deta hai; ye sirf arithmetic karta hai."""
    rule = WHT_RULES.get(serial or "")
    if not rule:
        return 0.0
    st = float(sales_tax or 0)
    if rule["basis"] == "st":
        return round(st * rule["rate"], 2)
    if rule["basis"] == "gross":
        gross = float(value_excl_st or 0) + st
        return round(gross * rule["rate"], 2)
    if rule["basis"] == "conv":
        return round(float(conversion_tax or 0) * rule["rate"], 2)
    return 0.0
