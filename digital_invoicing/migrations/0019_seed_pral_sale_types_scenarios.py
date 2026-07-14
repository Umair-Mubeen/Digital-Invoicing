# Milestone 1 — PRAL DI Scenarios doc v1.11 (SN001-SN028) seed.
# get_or_create only: existing rows kabhi overwrite nahi hote. Reversible.
from django.db import migrations
from datetime import date
from decimal import Decimal

EFFECTIVE = date(2024, 7, 1)   # FY25 start — verify rates in admin


LEGACY_RENAMES = {
    "Goods at standard rate": "Goods at standard rate (default)",
    "Goods at reduced rate": "Goods at Reduced Rate",
    "Exempt Goods": "Exempt goods",
    "Zero-rated Goods": "Goods at zero-rate",
}


def seed(apps, schema_editor):
    # tax_engine se import nahi karte — migration frozen rehni chahiye.
    TaxSaleType = apps.get_model("digital_invoicing", "TaxSaleType")
    TaxScenario = apps.get_model("digital_invoicing", "TaxScenario")

    # 0012 ke legacy names -> official PRAL labels (in-place rename: admin
    # ki rate edits PRESERVE hoti hain; koi row delete nahi hoti). Agar
    # official-name row pehle se hai to legacy ko deactivate karo.
    for old, new in LEGACY_RENAMES.items():
        for row in TaxSaleType.objects.filter(name=old):
            clash = TaxSaleType.objects.filter(
                name=new, effective_from=row.effective_from).exists()
            if clash:
                row.is_active = False
                row.save(update_fields=["is_active"])
            else:
                row.name = new
                row.save(update_fields=["name"])

    # Renamed/legacy rows jo 0012 ke UNTOUCHED seed values rakhti hain unhe
    # PRAL v1.11 pe upgrade karo (admin-modified rows preserve). Tuple:
    # (name, old_rate, old_legal_ref_prefix, new_values_dict)
    UPGRADES = [
        ("Goods at Reduced Rate", Decimal("5"), "Section 3(2)(aa)",
         dict(rate=Decimal("1"), sro_schedule="EIGHTH SCHEDULE Table 1",
              legal_reference="PRAL DI Scenarios doc v1.11 SN005 — Eighth "
                              "Schedule item-wise, verify")),
        ("Services", Decimal("15"), "Provincial",
         dict(rate=Decimal("5"), sro_schedule="ICTO TABLE I",
              legal_reference="PRAL DI Scenarios doc v1.11 SN019 — ICT "
                              "Ordinance, verify per province")),
    ]
    for name, old_rate, ref_prefix, new_vals in UPGRADES:
        TaxSaleType.objects.filter(
            name=name, rate=old_rate,
            legal_reference__startswith=ref_prefix).update(**new_vals)
    # Metadata (tax value nahi) — hamesha set karo:
    TaxSaleType.objects.filter(name="Exempt goods").update(
        rate_type="exempt", rate_label="Exempt")
    TaxSaleType.objects.filter(name="Goods at zero-rate").update(
        rate_label="0%")

    # (name, rate%, rate_type, per_unit, label, charges_st, further,
    #  sro, sro_item, retail)  — PRAL Scenarios doc v1.11 sample values.
    TYPES = [
        ("Goods at standard rate (default)", "18", "percent", "0", "", True, True, "", "", False),
        ("Goods at Reduced Rate", "1", "percent", "0", "", True, True, "EIGHTH SCHEDULE Table 1", "", False),
        ("3rd Schedule Goods", "18", "percent", "0", "", True, False, "", "", True),
        ("Exempt goods", "0", "exempt", "0", "Exempt", False, False, "6th Schd Table I", "", False),
        ("Goods at zero-rate", "0", "percent", "0", "0%", True, False, "", "", False),
        ("Steel melting and re-rolling", "18", "percent", "0", "", True, False, "", "", False),
        ("Ship breaking", "18", "percent", "0", "", True, False, "", "", False),
        ("Cotton ginners", "18", "percent", "0", "", True, False, "", "", False),
        ("Telecommunication services", "17", "percent", "0", "", True, False, "", "", False),
        ("Toll Manufacturing", "18", "percent", "0", "", True, False, "", "", False),
        ("Petroleum Products", "1.43", "percent", "0", "", True, False, "1450(I)/2021", "4", False),
        ("Electricity Supply to Retailers", "5", "percent", "0", "", True, False, "1450(I)/2021", "4", False),
        ("Gas to CNG stations", "18", "percent", "0", "", True, False, "", "", False),
        ("Mobile Phones", "18", "percent", "0", "", True, False, "NINTH SCHEDULE", "1(A)", False),
        ("Processing/Conversion of Goods", "5", "percent", "0", "", True, False, "", "", False),
        ("Goods (FED in ST Mode)", "8", "percent", "0", "", True, False, "", "", False),
        ("Services (FED in ST Mode)", "8", "percent", "0", "", True, False, "", "", False),
        ("Services", "5", "percent", "0", "", True, False, "ICTO TABLE I", "", False),
        ("Electric Vehicle", "1", "percent", "0", "", True, False, "6th Schd Table III", "20", False),
        ("Cement /Concrete Block", "0", "fixed_per_unit", "3", "Rs.3", True, False, "", "", False),
        ("Potassium Chlorate", "18", "compound", "60", "18% along with rupees 60 per kilogram", True, False, "EIGHTH SCHEDULE Table 1", "56", False),
        ("CNG Sales", "0", "fixed_per_unit", "200", "Rs.200", True, False, "581(1)/2024", "Region-I", False),
        ("Goods as per SRO.297(|)/2023", "25", "percent", "0", "", True, False, "297(I)/2023-Table-I", "", False),
        ("Non-Adjustable Supplies", "0", "percent", "0", "0%", True, False, "EIGHTH SCHEDULE Table 1", "81", False),
    ]
    for (name, rate, rtype, per_unit, label, st, further,
         sro, sro_item, retail) in TYPES:
        TaxSaleType.objects.get_or_create(
            name=name, effective_from=EFFECTIVE,
            defaults=dict(
                rate=Decimal(rate), rate_type=rtype,
                rate_per_unit=Decimal(per_unit), rate_label=label,
                charges_st=st, further_tax_applies=further,
                sro_schedule=sro, sro_item_serial=sro_item,
                retail_price_based=retail, is_active=True,
                legal_reference="PRAL DI Scenarios doc v1.11 (sandbox "
                                "sample) — verify against current SRO",
            ))

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
    for code, desc, stype in SCENARIOS:
        TaxScenario.objects.update_or_create(
            code=code, defaults=dict(description=desc, sale_type=stype,
                                     is_active=True))


def unseed(apps, schema_editor):
    TaxSaleType = apps.get_model("digital_invoicing", "TaxSaleType")
    TaxSaleType.objects.filter(
        effective_from=EFFECTIVE,
        legal_reference__startswith="PRAL DI Scenarios doc v1.11").delete()
    # TaxScenario rows harmless — chhor dete hain.


class Migration(migrations.Migration):
    dependencies = [
        ("digital_invoicing",
         "0018_taxsaletype_rate_label_taxsaletype_rate_per_unit_and_more"),
    ]
    operations = [migrations.RunPython(seed, unseed)]
