"""
tax_engine.py  —  The "tax-smart" layer that sets this product apart.

Given a sale type + value + buyer registration status, it returns the correct
rate, sales tax, further tax and SRO schedule — so the operator never has to
know the rules. Every rule here is traceable to the Sales Tax Act 1990.

Rules encoded (verify current rates against the Act/SRO before production):
  - Standard rate ...... 18%            (Section 3)
  - Reduced rate ....... 5% (example)   (Section 3(2)(aa) + Eighth Schedule)
  - Third Schedule ..... 18% on RETAIL PRICE/MRP (Sec 3(2)(a)) — NO further tax, NO value-addition tax
  - Exempt ............. no tax         (Section 13 + Sixth Schedule)
  - Zero-rated ......... 0%             (Section 4 + Fifth Schedule)
  - Services ........... 15% (example)  (provincial — varies)
  - Further tax ........ 4% to UNREGISTERED buyers on taxable supplies
                                        (Section 3(1A)). NAHI lagta:
       * Third Schedule / exempt / zero-rated (sale-type level)
       * SRO 648(I)/2013 (+ SRO 1223(I)/2021) excluded items — fertilizers,
         petroleum products (POL), vehicles, essential food, dairy, pharma,
         + STEEL SECTOR supplies + EDIBLE OIL SECTOR supplies (HS-code level)
       * Registered buyer (hamesha)

NOTE (Umair verify): FURTHER_TAX_EXEMPT_HS list neeche hai — is mein wahi HS
prefixes daalein jo current SRO 648(I)/2013 (+ amendments) mein further-tax se
mustasna hain. Ye practitioner-curated list hai; system suggest karta hai.
"""

from decimal import Decimal, ROUND_HALF_UP

FURTHER_TAX_RATE = Decimal("4")   # % — Section 3(1A)

# HS-code prefixes jin par further tax NAHI lagta (SRO 648(I)/2013 + amendments).
# Prefix match hota hai (e.g. "3102" saare urea/fertilizer codes cover karta hai).
# Umair: ye list current SRO se verify/update karein.
FURTHER_TAX_EXEMPT_HS = {
    # --- SRO 648(I)/2013 ---
    "3102", "3103", "3104", "3105",   # Fertilizers
    "2710",                             # Petroleum oils (POL products)
    "2711",                             # Petroleum gases (LPG/CNG)
    "8703", "8704", "8711",            # Vehicles (cars, goods vehicles, motorcycles)
    "1001", "1006", "1101",            # Wheat, rice, wheat flour (essential food)
    "0401", "0402",                     # Milk & dairy
    "3004",                             # Medicaments (pharma)
    # --- SRO 1223(I)/2021 (amends 648) ---
    # Steel sector supplies
    "7207", "7208", "7209", "7210", "7211", "7212", "7213", "7214",
    "7215", "7216", "7217", "7218", "7219", "7220", "7221", "7222",
    "7223", "7224", "7225", "7226", "7227", "7228", "7229",
    "7301", "7302", "7303", "7304", "7305", "7306", "7307", "7308",
    "7213.9990", "7214.9990",          # (already covered by 4-digit above)
    # Edible oil sector supplies
    "1507", "1508", "1509", "1510", "1511", "1512", "1513", "1514",
    "1515", "1516", "1517", "1518",
}


def _further_tax_exempt_hs(hs_code):
    """True agar HS code further-tax se mustasna hai (SRO 648(I)/2013)."""
    if not hs_code:
        return False
    digits = str(hs_code).replace(".", "").strip()
    return any(digits.startswith(p) for p in FURTHER_TAX_EXEMPT_HS)

# saleType label -> config.  These labels match what FBR's DI API expects.
SALE_TYPES = {
    "Goods at standard rate": {
        "rate": Decimal("18"), "charges_st": True,  "further": True,
        "sro": "", "retail_price_based": False,
    },
    "Goods at reduced rate": {
        "rate": Decimal("5"),  "charges_st": True,  "further": True,
        "sro": "Eighth Schedule", "retail_price_based": False,
    },
    "3rd Schedule Goods": {
        "rate": Decimal("18"), "charges_st": True,  "further": False,
        "sro": "Third Schedule", "retail_price_based": True,
    },
    "Exempt Goods": {
        "rate": Decimal("0"),  "charges_st": False, "further": False,
        "sro": "Sixth Schedule", "retail_price_based": False,
    },
    "Zero-rated Goods": {
        "rate": Decimal("0"),  "charges_st": True,  "further": False,
        "sro": "Fifth Schedule", "retail_price_based": False,
    },
    "Services": {
        "rate": Decimal("15"), "charges_st": True,  "further": False,
        "sro": "", "retail_price_based": False,
    },
}


def _money(x):
    return Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Phase 7 — DB-driven rule resolution (date-effective, cached, safe fallback)
# Tables khali/unavailable hon to upar wale hardcoded dicts use hote hain —
# parity guaranteed, deploy risk zero.
# ---------------------------------------------------------------------------
_CACHE_TTL = 300  # 5 min — admin change jaldi reflect ho


def _cache():
    from django.core.cache import cache
    return cache


def load_rules(on_date=None):
    """Return (sale_types_dict, further_rate, exempt_prefixes) for a date.
    DB first; empty/error -> hardcoded fallback."""
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
        sale_types = {}
        for r in st_rows:
            if r.name in sale_types:      # latest effective_from jeet gaya
                continue
            sale_types[r.name] = {
                "rate": r.rate, "charges_st": r.charges_st,
                "further": r.further_tax_applies, "sro": r.sro_schedule,
                "retail_price_based": r.retail_price_based,
            }

        ft_row = (FurtherTaxConfig.objects.filter(date_q, is_active=True)
                  .order_by("-effective_from").first())
        further_rate = ft_row.rate if ft_row else FURTHER_TAX_RATE

        prefixes = set(FurtherTaxExemptHS.objects.filter(date_q, is_active=True)
                       .values_list("hs_prefix", flat=True))

        if not sale_types:                # tables khali — fallback
            raise LookupError
        result = (sale_types, further_rate,
                  prefixes or FURTHER_TAX_EXEMPT_HS)
    except Exception:
        result = (SALE_TYPES, FURTHER_TAX_RATE, FURTHER_TAX_EXEMPT_HS)

    _cache().set(key, result, _CACHE_TTL)
    return result


def get_sale_type_config(sale_type, on_date=None):
    """Ek sale type ki effective config (services/UI ke liye)."""
    sale_types, _, _ = load_rules(on_date)
    return sale_types.get(sale_type)


def invalidate_rules_cache():
    """Admin save ke baad turant reflect karne ke liye (signals se call)."""
    from datetime import date as _date
    _cache().delete(f"tax_rules:{_date.today().isoformat()}")


def compute_item(sale_type, value_excl_st, buyer_unregistered=False,
                 hs_code="", retail_price=0, on_date=None):
    """
    Return a dict of computed tax fields for a single line item.
    `value_excl_st` is the taxable value before sales tax.
    `on_date`: rules is date par effective (default: aaj) — historical
    recomputation ke liye invoice_date pass karein.
    """
    sale_types, further_rate, exempt_prefixes = load_rules(on_date)
    cfg = sale_types.get(sale_type)
    if cfg is None:
        raise ValueError(f"Unknown sale type: {sale_type!r}")

    def _ft_exempt(hs):
        if not hs:
            return False
        digits = str(hs).replace(".", "").strip()
        return any(digits.startswith(p) for p in exempt_prefixes)

    value = Decimal(str(value_excl_st or 0))
    # 3rd Schedule: sales tax RETAIL PRICE (MRP) pe lagta hai, sale value pe nahi
    # (Sec 3(2)(a)). Baqi sab pe normal sale value.
    retail = Decimal(str(retail_price or 0))
    if cfg["retail_price_based"]:
        st_base = retail if retail > 0 else value   # MRP na ho to fallback value
    else:
        st_base = value
    sales_tax = _money(st_base * cfg["rate"] / 100) if cfg["charges_st"] else _money(0)

    further_tax = _money(0)
    if cfg["further"] and buyer_unregistered and not _ft_exempt(hs_code):
        further_tax = _money(value * further_rate / 100)

    total = _money(value + sales_tax + further_tax)

    return {
        "rate": f'{Decimal(cfg["rate"]).normalize()}%',
        "rate_value": cfg["rate"],
        "sales_tax": sales_tax,
        "further_tax": further_tax,
        "sro_schedule": cfg["sro"],
        "retail_price_based": cfg["retail_price_based"],
        "further_tax_hs_exempt": _ft_exempt(hs_code),
        "st_base": st_base,
        "total": total,
    }


def compute_invoice(items, buyer_unregistered=False):
    """
    items: list of dicts each with keys `sale_type` and `value_excl_st`.
    Returns per-item results + invoice totals.
    """
    results, tv, tst, tft = [], Decimal(0), Decimal(0), Decimal(0)
    for it in items:
        r = compute_item(it["sale_type"], it["value_excl_st"], buyer_unregistered, it.get("hs_code", ""))
        results.append(r)
        tv  += Decimal(str(it["value_excl_st"] or 0))
        tst += r["sales_tax"]
        tft += r["further_tax"]
    return {
        "items": results,
        "total_value": _money(tv),
        "total_sales_tax": _money(tst),
        "total_further_tax": _money(tft),
        "invoice_total": _money(tv + tst + tft),
    }