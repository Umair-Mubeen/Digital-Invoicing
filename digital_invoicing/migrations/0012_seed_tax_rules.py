"""Seed tax rule tables from the (verified) hardcoded engine values +
PRAL Technical Spec v1.12 §9 scenarios. Idempotent (get_or_create)."""
from datetime import date
from decimal import Decimal
from django.db import migrations

EFFECTIVE = date(2024, 7, 1)   # FY2024-25 se current rates

SALE_TYPES = [
    # name, rate, charges_st, further, sro, retail_based, legal_ref
    ("Goods at standard rate", "18", True, True, "", False,
     "Section 3, Sales Tax Act 1990"),
    ("Goods at reduced rate", "5", True, True, "Eighth Schedule", False,
     "Section 3(2)(aa) + Eighth Schedule"),
    ("3rd Schedule Goods", "18", True, False, "Third Schedule", True,
     "Section 3(2)(a) — ST on Retail Price/MRP"),
    ("Exempt Goods", "0", False, False, "Sixth Schedule", False,
     "Section 13 + Sixth Schedule"),
    ("Zero-rated Goods", "0", True, False, "Fifth Schedule", False,
     "Section 4 + Fifth Schedule"),
    ("Services", "15", True, False, "", False,
     "Provincial (ICT Ordinance) — verify per province"),
]

FT_EXEMPT = {
    "SRO 648(I)/2013": [
        ("3102", "Fertilizers"), ("3103", "Fertilizers"),
        ("3104", "Fertilizers"), ("3105", "Fertilizers"),
        ("2710", "Petroleum oils (POL)"), ("2711", "Petroleum gases LPG/CNG"),
        ("8703", "Vehicles — cars"), ("8704", "Vehicles — goods"),
        ("8711", "Motorcycles"),
        ("1001", "Wheat"), ("1006", "Rice"), ("1101", "Wheat flour"),
        ("0401", "Milk"), ("0402", "Dairy"),
        ("3004", "Medicaments (pharma)"),
    ],
    "SRO 1223(I)/2021": (
        [(f"72{i:02d}", "Steel sector") for i in range(7, 30)]
        + [(f"73{i:02d}", "Steel sector") for i in range(1, 9)]
        + [(f"15{i:02d}", "Edible oil sector") for i in range(7, 19)]
    ),
}

SCENARIOS = [
    ("SN001", "Goods at standard rate to registered buyers", "Goods at Standard Rate (default)"),
    ("SN002", "Goods at standard rate to unregistered buyers", "Goods at Standard Rate (default)"),
    ("SN003", "Sale of Steel (Melted and Re-Rolled)", "Steel Melting and re-rolling"),
    ("SN004", "Sale by Ship Breakers", "Ship breaking"),
    ("SN005", "Reduced rate sale", "Goods at Reduced Rate"),
    ("SN006", "Exempt goods sale", "Exempt Goods"),
    ("SN007", "Zero rated sale", "Goods at zero-rate"),
    ("SN008", "Sale of 3rd schedule goods", "3rd Schedule Goods"),
    ("SN009", "Cotton Spinners purchase from Cotton Ginners (Textile Sector)", "Cotton Ginners"),
    ("SN010", "Telecom services rendered or provided", "Telecommunication services"),
    ("SN011", "Toll Manufacturing sale by Steel sector", "Toll Manufacturing"),
    ("SN012", "Sale of Petroleum products", "Petroleum Products"),
    ("SN013", "Electricity Supply to Retailers", "Electricity Supply to Retailers"),
    ("SN014", "Sale of Gas to CNG stations", "Gas to CNG stations"),
    ("SN015", "Sale of mobile phones", "Mobile Phones"),
    ("SN016", "Processing / Conversion of Goods", "Processing/ Conversion of Goods"),
    ("SN017", "Sale of Goods where FED is charged in ST mode", "Goods (FED in ST Mode)"),
    ("SN018", "Services rendered or provided where FED is charged in ST mode", "Services (FED in ST Mode)"),
    ("SN019", "Services rendered or provided", "Services"),
    ("SN020", "Sale of Electric Vehicles", "Electric Vehicle"),
    ("SN021", "Sale of Cement /Concrete Block", "Cement /Concrete Block"),
    ("SN022", "Sale of Potassium Chlorate", "Potassium Chlorate"),
    ("SN023", "Sale of CNG", "CNG Sales"),
    ("SN024", "Goods sold that are listed in SRO 297(1)/2023", "Goods as per SRO.297(|)/2023"),
    ("SN025", "Drugs sold at fixed ST rate under serial 81 of Eighth Schedule Table 1", "Non-Adjustable Supplies"),
    ("SN026", "Sale to End Consumer by retailers", "Goods at Standard Rate (default)"),
    ("SN027", "Sale to End Consumer by retailers", "3rd Schedule Goods"),
    ("SN028", "Sale to End Consumer by retailers", "Goods at Reduced Rate"),
]


def forwards(apps, schema_editor):
    TaxSaleType = apps.get_model("digital_invoicing", "TaxSaleType")
    FurtherTaxConfig = apps.get_model("digital_invoicing", "FurtherTaxConfig")
    FurtherTaxExemptHS = apps.get_model("digital_invoicing", "FurtherTaxExemptHS")
    TaxScenario = apps.get_model("digital_invoicing", "TaxScenario")

    for name, rate, st, ft, sro, rp, ref in SALE_TYPES:
        TaxSaleType.objects.get_or_create(
            name=name, effective_from=EFFECTIVE,
            defaults=dict(rate=Decimal(rate), charges_st=st,
                          further_tax_applies=ft, sro_schedule=sro,
                          retail_price_based=rp, legal_reference=ref))

    FurtherTaxConfig.objects.get_or_create(
        effective_from=EFFECTIVE, defaults=dict(rate=Decimal("4")))

    for sro, rows in FT_EXEMPT.items():
        for prefix, desc in rows:
            FurtherTaxExemptHS.objects.get_or_create(
                hs_prefix=prefix, effective_from=EFFECTIVE,
                defaults=dict(sro_reference=sro, description=desc))

    for code, desc, st in SCENARIOS:
        TaxScenario.objects.get_or_create(code=code,
                                          defaults=dict(description=desc,
                                                        sale_type=st))


def backwards(apps, schema_editor):
    for m in ("TaxSaleType", "FurtherTaxConfig",
              "FurtherTaxExemptHS", "TaxScenario"):
        apps.get_model("digital_invoicing", m).objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("digital_invoicing", "0011_tax_rule_tables")]
    operations = [migrations.RunPython(forwards, backwards)]
