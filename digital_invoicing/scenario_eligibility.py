"""Scenario eligibility — Tech Doc v1.12 pages 47-51 ka OFFICIAL table.

IRIS enrollment mein business jo Activity (business nature) + Sector select
karta hai, ussi se decide hota hai kaunse sandbox scenarios "Eligible" honge
aur certification unhi ki hoti hai. Ye module wohi mapping verbatim rakhta
hai taake system khud client ko bata sake ke kya certify karna hai.

Source of truth: Technical Documentation for DI API V1.12 (rows 1-118).
Multiple selections => union of rows (IRIS bhi yehi karta hai).
"""

ACTIVITIES = ["Manufacturer", "Importer", "Distributor", "Wholesaler",
              "Exporter", "Retailer", "Service Provider", "Other"]

SECTORS = ["All Other Sectors", "Steel", "FMCG", "Textile", "Telecom",
           "Petroleum", "Electricity Distribution", "Gas Distribution",
           "Services", "Automobile", "CNG Stations", "Pharmaceuticals",
           "Wholesale / Retails"]

_BASE11 = ["SN001", "SN002", "SN005", "SN006", "SN007", "SN015", "SN016",
           "SN017", "SN021", "SN022", "SN024"]
_DIST4 = ["SN026", "SN027", "SN028", "SN008"]

# (activity, sector) -> scenario list — doc rows verbatim
ELIGIBILITY = {
    # Manufacturer (rows 1-13)
    ("Manufacturer", "All Other Sectors"): _BASE11,
    ("Manufacturer", "Steel"): ["SN003", "SN004", "SN011"],
    ("Manufacturer", "FMCG"): _BASE11 + ["SN008"],
    ("Manufacturer", "Textile"): _BASE11 + ["SN009"],
    ("Manufacturer", "Telecom"): _BASE11 + ["SN010"],
    ("Manufacturer", "Petroleum"): _BASE11 + ["SN012"],
    ("Manufacturer", "Electricity Distribution"): _BASE11 + ["SN013"],
    ("Manufacturer", "Gas Distribution"): _BASE11 + ["SN014"],
    ("Manufacturer", "Services"): _BASE11 + ["SN018", "SN019"],
    ("Manufacturer", "Automobile"): _BASE11 + ["SN020"],
    ("Manufacturer", "CNG Stations"): _BASE11 + ["SN023"],
    ("Manufacturer", "Pharmaceuticals"): _BASE11,
    ("Manufacturer", "Wholesale / Retails"): _BASE11 + _DIST4,
    # Importer (rows 16-28)
    ("Importer", "All Other Sectors"): _BASE11,
    ("Importer", "Steel"): _BASE11 + ["SN003", "SN004", "SN011"],
    ("Importer", "FMCG"): _BASE11 + ["SN008"],
    ("Importer", "Textile"): _BASE11 + ["SN009"],
    ("Importer", "Telecom"): _BASE11 + ["SN010"],
    ("Importer", "Petroleum"): _BASE11 + ["SN012"],
    ("Importer", "Electricity Distribution"): _BASE11 + ["SN013"],
    ("Importer", "Gas Distribution"): _BASE11 + ["SN014"],
    ("Importer", "Services"): _BASE11 + ["SN018", "SN019"],
    ("Importer", "Automobile"): _BASE11 + ["SN020"],
    ("Importer", "CNG Stations"): _BASE11 + ["SN023"],
    ("Importer", "Pharmaceuticals"): _BASE11 + ["SN025"],
    ("Importer", "Wholesale / Retails"): _BASE11 + _DIST4,
    # Distributor (rows 31-43)
    ("Distributor", "All Other Sectors"): _BASE11 + _DIST4,
    ("Distributor", "Steel"): ["SN003", "SN004", "SN011"] + _DIST4,
    ("Distributor", "FMCG"): ["SN008"] + _DIST4,
    ("Distributor", "Textile"): ["SN009"] + _DIST4,
    ("Distributor", "Telecom"): ["SN010"] + _DIST4,
    ("Distributor", "Petroleum"): ["SN012"] + _DIST4,
    ("Distributor", "Electricity Distribution"): ["SN013"] + _DIST4,
    ("Distributor", "Gas Distribution"): ["SN014"] + _DIST4,
    ("Distributor", "Services"): ["SN018", "SN019"] + _DIST4,
    ("Distributor", "Automobile"): ["SN020"] + _DIST4,
    ("Distributor", "CNG Stations"): ["SN023"] + _DIST4,
    ("Distributor", "Pharmaceuticals"): ["SN025"] + _DIST4,
    ("Distributor", "Wholesale / Retails"): ["SN001", "SN002"] + _DIST4,
    # Wholesaler (rows 46-58)
    ("Wholesaler", "All Other Sectors"): _BASE11 + _DIST4,
    ("Wholesaler", "Steel"): ["SN003", "SN004", "SN011"] + _DIST4,
    ("Wholesaler", "FMCG"): ["SN008"] + _DIST4,
    ("Wholesaler", "Textile"): ["SN009"] + _DIST4,
    ("Wholesaler", "Telecom"): ["SN010"] + _DIST4,
    ("Wholesaler", "Petroleum"): ["SN012"] + _DIST4,
    ("Wholesaler", "Electricity Distribution"): ["SN013"] + _DIST4,
    ("Wholesaler", "Gas Distribution"): ["SN014"] + _DIST4,
    ("Wholesaler", "Services"): ["SN018", "SN019"] + _DIST4,
    ("Wholesaler", "Automobile"): ["SN020"] + _DIST4,
    ("Wholesaler", "CNG Stations"): ["SN023"] + _DIST4,
    ("Wholesaler", "Pharmaceuticals"): ["SN025"] + _DIST4,
    ("Wholesaler", "Wholesale / Retails"): ["SN001", "SN002"] + _DIST4,
    # Exporter (rows 61-73)
    ("Exporter", "All Other Sectors"): _BASE11,
    ("Exporter", "Steel"): _BASE11 + ["SN003", "SN004", "SN011"],
    ("Exporter", "FMCG"): _BASE11 + ["SN008"],
    ("Exporter", "Textile"): _BASE11 + ["SN009"],
    ("Exporter", "Telecom"): _BASE11 + ["SN010"],
    ("Exporter", "Petroleum"): _BASE11 + ["SN012"],
    ("Exporter", "Electricity Distribution"): _BASE11 + ["SN013"],
    ("Exporter", "Gas Distribution"): _BASE11 + ["SN014"],
    ("Exporter", "Services"): _BASE11 + ["SN018", "SN019"],
    ("Exporter", "Automobile"): _BASE11 + ["SN020"],
    ("Exporter", "CNG Stations"): _BASE11 + ["SN023"],
    ("Exporter", "Pharmaceuticals"): _BASE11 + ["SN025"],
    ("Exporter", "Wholesale / Retails"): _BASE11 + _DIST4,
    # Retailer (rows 76-88)
    ("Retailer", "All Other Sectors"): _BASE11 + _DIST4,
    ("Retailer", "Steel"): ["SN003", "SN004", "SN011"],
    ("Retailer", "FMCG"): _DIST4,
    ("Retailer", "Textile"): ["SN009"] + _DIST4,
    ("Retailer", "Telecom"): ["SN010"] + _DIST4,
    ("Retailer", "Petroleum"): ["SN012"] + _DIST4,
    ("Retailer", "Electricity Distribution"): ["SN013"] + _DIST4,
    ("Retailer", "Gas Distribution"): ["SN014"] + _DIST4,
    ("Retailer", "Services"): ["SN018", "SN019"] + _DIST4,
    ("Retailer", "Automobile"): ["SN020"] + _DIST4,
    ("Retailer", "CNG Stations"): ["SN023"] + _DIST4,
    ("Retailer", "Pharmaceuticals"): ["SN025"] + _DIST4,
    ("Retailer", "Wholesale / Retails"): _DIST4,
    # Service Provider (rows 91-103)
    ("Service Provider", "All Other Sectors"): _BASE11 + ["SN018", "SN019"],
    ("Service Provider", "Steel"): ["SN003", "SN004", "SN011", "SN018", "SN019"],
    ("Service Provider", "FMCG"): ["SN008", "SN018", "SN019"],
    ("Service Provider", "Textile"): ["SN009", "SN018", "SN019"],
    ("Service Provider", "Telecom"): ["SN010", "SN018", "SN019"],
    ("Service Provider", "Petroleum"): ["SN012", "SN018", "SN019"],
    ("Service Provider", "Electricity Distribution"): ["SN013", "SN018", "SN019"],
    ("Service Provider", "Gas Distribution"): ["SN014", "SN018", "SN019"],
    ("Service Provider", "Services"): ["SN018", "SN019"],
    ("Service Provider", "Automobile"): ["SN020", "SN018", "SN019"],
    ("Service Provider", "CNG Stations"): ["SN023", "SN018", "SN019"],
    ("Service Provider", "Pharmaceuticals"): ["SN025", "SN018", "SN019"],
    ("Service Provider", "Wholesale / Retails"): _DIST4 + ["SN018", "SN019"],
    # Other (rows 106-118)
    ("Other", "All Other Sectors"): _BASE11,
    ("Other", "Steel"): _BASE11 + ["SN003", "SN004", "SN011"],
    ("Other", "FMCG"): _BASE11 + ["SN008"],
    ("Other", "Textile"): _BASE11 + ["SN009"],
    ("Other", "Telecom"): _BASE11 + ["SN010"],
    ("Other", "Petroleum"): _BASE11 + ["SN012"],
    ("Other", "Electricity Distribution"): _BASE11 + ["SN013"],
    ("Other", "Gas Distribution"): _BASE11 + ["SN014"],
    ("Other", "Services"): _BASE11 + ["SN018", "SN019"],
    ("Other", "Automobile"): _BASE11 + ["SN020"],
    ("Other", "CNG Stations"): _BASE11 + ["SN023"],
    ("Other", "Pharmaceuticals"): _BASE11 + ["SN025"],
    ("Other", "Wholesale / Retails"): _BASE11 + _DIST4,
}


def eligible_scenarios(activities, sectors):
    """IRIS jaisa union: selected activities × sectors ke sab rows mila kar
    sorted unique scenario list. Khali input => khali list."""
    out = set()
    for a in activities:
        for s in sectors:
            out.update(ELIGIBILITY.get((a.strip(), s.strip()), []))
    return sorted(out)


def eligible_for_profile(profile):
    """SellerProfile ke stored CSV fields se eligible list."""
    acts = [x for x in (profile.business_activity or "").split(",") if x.strip()]
    secs = [x for x in (profile.business_sector or "").split(",") if x.strip()]
    return eligible_scenarios(acts, secs)
