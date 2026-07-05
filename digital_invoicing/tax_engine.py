"""
tax_engine.py  —  The "tax-smart" layer that sets this product apart.

Given a sale type + value + buyer registration status, it returns the correct
rate, sales tax, further tax and SRO schedule — so the operator never has to
know the rules. Every rule here is traceable to the Sales Tax Act 1990.

Rules encoded (verify current rates against the Act/SRO before production):
  - Standard rate ...... 18%            (Section 3)
  - Reduced rate ....... 5% (example)   (Section 3(2)(aa) + Eighth Schedule)
  - Third Schedule ..... 18% on retail  (Section 3(2)(a)) — NO further tax
  - Exempt ............. no tax         (Section 13 + Sixth Schedule)
  - Zero-rated ......... 0%             (Section 4 + Fifth Schedule)
  - Services ........... 15% (example)  (provincial — varies)
  - Further tax ........ 4% to UNREGISTERED buyers on taxable supplies
                                        (Section 3(1A)); excluded for Third
                                        Schedule / exempt / zero-rated.
"""

from decimal import Decimal, ROUND_HALF_UP

FURTHER_TAX_RATE = Decimal("4")   # % — Section 3(1A)

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


def compute_item(sale_type, value_excl_st, buyer_unregistered=False):
    """
    Return a dict of computed tax fields for a single line item.
    `value_excl_st` is the taxable value before sales tax.
    """
    cfg = SALE_TYPES.get(sale_type)
    if cfg is None:
        raise ValueError(f"Unknown sale type: {sale_type!r}")

    value = Decimal(str(value_excl_st or 0))
    sales_tax = _money(value * cfg["rate"] / 100) if cfg["charges_st"] else _money(0)

    further_tax = _money(0)
    if cfg["further"] and buyer_unregistered:
        further_tax = _money(value * FURTHER_TAX_RATE / 100)

    total = _money(value + sales_tax + further_tax)

    return {
        "rate": f'{cfg["rate"].normalize()}%',
        "rate_value": cfg["rate"],
        "sales_tax": sales_tax,
        "further_tax": further_tax,
        "sro_schedule": cfg["sro"],
        "retail_price_based": cfg["retail_price_based"],
        "total": total,
    }


def compute_invoice(items, buyer_unregistered=False):
    """
    items: list of dicts each with keys `sale_type` and `value_excl_st`.
    Returns per-item results + invoice totals.
    """
    results, tv, tst, tft = [], Decimal(0), Decimal(0), Decimal(0)
    for it in items:
        r = compute_item(it["sale_type"], it["value_excl_st"], buyer_unregistered)
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
