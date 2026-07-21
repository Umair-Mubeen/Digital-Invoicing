"""
reference_data.py  —  FBR DI Reference APIs (Technical Doc v1.12, section
"Digital Invoicing Reference APIs").

Covers the lookups the document lists:
  - Province Code            - Document Type ID
  - Item Code (HS codes)     - UOM ID
  - Transaction/Sale Types   - SRO Schedule / SRO Item
  - Rate ID                  - HS Code with UOM
  - STATL / registration-type check (buyer verification)

Same pattern as fbr_client.py: a Mock client (works offline, realistic data)
and a Real client (hits the PRAL gateway with your Bearer token). Selection via
settings.FBR_USE_MOCK. Responses are cached — reference data changes rarely.

Why this matters: these lookups prevent sandbox rejections BEFORE submission —
  0099 (UoM not allowed for HS code), 0077 (SRO needed when rate != 18%),
  0053 (buyer registration type mismatch), 0019 (invalid HS code).
"""

from decimal import Decimal

from django.core.cache import cache

CACHE_TTL = 60 * 60 * 12  # 12 hours — reference data is slow-moving

# Real PRAL gateway endpoints (from the technical documentation; confirm the
# exact paths against your copy of the doc when the token arrives).
REAL_ENDPOINTS = {
    "provinces":   "https://gw.fbr.gov.pk/pdi/v1/provinces",
    "doctypes":    "https://gw.fbr.gov.pk/pdi/v1/doctypecode",
    "hscodes":     "https://gw.fbr.gov.pk/pdi/v1/itemdesccode",
    "sroitems":    "https://gw.fbr.gov.pk/pdi/v1/sroitemcode",
    "transtypes":  "https://gw.fbr.gov.pk/pdi/v1/transtypecode",
    "uom":         "https://gw.fbr.gov.pk/pdi/v1/uom",
    "sro_schedule":"https://gw.fbr.gov.pk/pdi/v1/SroSchedule",
    "rate":        "https://gw.fbr.gov.pk/pdi/v2/SaleTypeToRate",
    "hs_uom":      "https://gw.fbr.gov.pk/pdi/v2/HS_UOM",
    "sro_items_v2":"https://gw.fbr.gov.pk/pdi/v2/SROItem",
    "statl":       "https://gw.fbr.gov.pk/dist/v1/statl",
    "reg_type":    "https://gw.fbr.gov.pk/dist/v1/Get_Reg_Type",
}


# --------------------------------------------------------------- Mock client
class MockReferenceClient:
    """Offline reference data — realistic shapes so the UI and validators can
    be built now and swapped to the real gateway later."""

    PROVINCES = [
        {"stateProvinceCode": 1, "stateProvinceDesc": "Balochistan"},
        {"stateProvinceCode": 2, "stateProvinceDesc": "AJK"},
        {"stateProvinceCode": 4, "stateProvinceDesc": "Capital Territory"},
        {"stateProvinceCode": 5, "stateProvinceDesc": "KPK"},
        {"stateProvinceCode": 6, "stateProvinceDesc": "Gilgit-Baltistan"},
        {"stateProvinceCode": 7, "stateProvinceDesc": "Punjab"},
        {"stateProvinceCode": 8, "stateProvinceDesc": "Sindh"},
    ]

    DOC_TYPES = [
        {"docTypeId": 4, "docDescription": "Sale Invoice"},
        {"docTypeId": 9, "docDescription": "Debit Note"},
    ]

    UOM = [
        {"uoM_ID": 13, "description": "Numbers, pieces, units"},
        {"uoM_ID": 74, "description": "KG"},
        {"uoM_ID": 22, "description": "Liter"},
        {"uoM_ID": 77, "description": "MT"},           # steel melting/re-rolling
        {"uoM_ID": 25, "description": "SqY"},           # services 50/SqY
        {"uoM_ID": 66, "description": "Bill of lading"},# services FED-in-ST 200/bill
        {"uoM_ID": 71, "description": "Meter"},
        {"uoM_ID": 30, "description": "Dozen"},
    ]

    TRANS_TYPES = [
        {"transactioN_TYPE_ID": 75, "transactioN_DESC": "Goods at standard rate (default)"},
        {"transactioN_TYPE_ID": 24, "transactioN_DESC": "Goods at Reduced Rate"},
        {"transactioN_TYPE_ID": 129,"transactioN_DESC": "3rd Schedule Goods"},
        {"transactioN_TYPE_ID": 81, "transactioN_DESC": "Exempt goods"},
        {"transactioN_TYPE_ID": 80, "transactioN_DESC": "Goods at zero-rate"},
        {"transactioN_TYPE_ID": 18, "transactioN_DESC": "Services"},
        {"transactioN_TYPE_ID": 130,"transactioN_DESC": "Cotton Ginners"},
    ]

    # HS codes — FBR Technical Spec (PRAL) ke official annexure se verified
    # 8-digit codes, dotted format (XXXX.XXXX) — wahi jo DI API v1.12 samples mein hai.
    # Real token pe ye list FBR ke itemdesccode API se replace ho jati hai (poora tariff).
    HS_CODES = [
        # --- Food & FMCG ---
        {"hS_CODE": "1701.9910", "description": "White crystalline cane sugar", "uoms": ["KG"]},
        {"hS_CODE": "1701.9920", "description": "White crystalline beet sugar", "uoms": ["KG"]},
        {"hS_CODE": "1703.1000", "description": "Cane molasses", "uoms": ["KG"]},
        {"hS_CODE": "1006.3010", "description": "Rice - Basmati", "uoms": ["KG"]},
        {"hS_CODE": "1006.3090", "description": "Rice - other", "uoms": ["KG"]},
        {"hS_CODE": "1006.4000", "description": "Broken rice", "uoms": ["KG"]},
        {"hS_CODE": "1101.1000", "description": "Wheat flour", "uoms": ["KG", "40KG"]},
        {"hS_CODE": "1102.2000", "description": "Maize (corn) flour", "uoms": ["KG"]},
        {"hS_CODE": "1902.1920", "description": "Vermicelli", "uoms": ["KG"]},
        {"hS_CODE": "1902.3000", "description": "Other pasta", "uoms": ["KG"]},
        {"hS_CODE": "1905.3100", "description": "Sweet biscuits", "uoms": ["KG", "Numbers, pieces, units"]},
        {"hS_CODE": "1905.3200", "description": "Waffles and wafers", "uoms": ["KG"]},
        {"hS_CODE": "1905.9000", "description": "Bakery products - other", "uoms": ["KG"]},
        {"hS_CODE": "1704.1000", "description": "Chewing gum", "uoms": ["KG"]},
        {"hS_CODE": "1704.9090", "description": "Sugar confectionery - other", "uoms": ["KG"]},
        {"hS_CODE": "1806.3100", "description": "Chocolate - filled", "uoms": ["KG"]},
        {"hS_CODE": "1806.9000", "description": "Chocolate preparations - other", "uoms": ["KG"]},
        {"hS_CODE": "2103.2000", "description": "Tomato ketchup and sauces", "uoms": ["KG", "Numbers, pieces, units"]},
        {"hS_CODE": "2103.9000", "description": "Sauces and condiments - other", "uoms": ["KG"]},
        {"hS_CODE": "2105.0000", "description": "Ice cream and edible ice", "uoms": ["KG", "Liter"]},
        {"hS_CODE": "2106.9020", "description": "Syrups and squashes", "uoms": ["Liter", "Numbers, pieces, units"]},
        {"hS_CODE": "2106.9090", "description": "Food preparations nes", "uoms": ["KG"]},
        {"hS_CODE": "2201.1010", "description": "Mineral waters", "uoms": ["Liter", "Numbers, pieces, units"]},
        {"hS_CODE": "2202.1010", "description": "Aerated waters / beverages", "uoms": ["Liter", "Numbers, pieces, units"]},
        {"hS_CODE": "2009.5000", "description": "Tomato juice", "uoms": ["Liter"]},
        {"hS_CODE": "2009.9000", "description": "Mixed fruit juices", "uoms": ["Liter"]},
        {"hS_CODE": "0902.3000", "description": "Black tea (retail packs upto 3kg)", "uoms": ["KG"]},
        {"hS_CODE": "0902.4020", "description": "Black tea (bulk above 3kg)", "uoms": ["KG"]},
        {"hS_CODE": "2101.1120", "description": "Instant coffee (retail packs)", "uoms": ["KG", "Numbers, pieces, units"]},
        {"hS_CODE": "0904.2010", "description": "Red chillies (whole)", "uoms": ["KG"]},
        {"hS_CODE": "0904.2020", "description": "Red chillies (powder)", "uoms": ["KG"]},
        {"hS_CODE": "0910.1000", "description": "Ginger", "uoms": ["KG"]},
        {"hS_CODE": "0910.3000", "description": "Turmeric (curcuma)", "uoms": ["KG"]},
        {"hS_CODE": "0909.3000", "description": "Cumin seeds (zeera)", "uoms": ["KG"]},
        {"hS_CODE": "0813.4070", "description": "Raisins (kishmish)", "uoms": ["KG"]},
        {"hS_CODE": "0813.4030", "description": "Pine nuts (chilgoza)", "uoms": ["KG"]},
        {"hS_CODE": "0810.9010", "description": "Pomegranates", "uoms": ["KG"]},
        {"hS_CODE": "1601.0000", "description": "Sausages / prepared meat", "uoms": ["KG"]},
        {"hS_CODE": "1604.1400", "description": "Canned tuna", "uoms": ["KG", "Numbers, pieces, units"]},
        {"hS_CODE": "2007.9900", "description": "Jams and fruit preserves", "uoms": ["KG"]},
        {"hS_CODE": "2309.9020", "description": "Poultry/animal feed preparations", "uoms": ["KG", "40KG"]},
        # --- Edible oils ---
        {"hS_CODE": "1511.1000", "description": "Palm oil - crude", "uoms": ["KG", "MT"]},
        {"hS_CODE": "1511.9020", "description": "RBD palm oil", "uoms": ["KG", "MT"]},
        {"hS_CODE": "1511.9030", "description": "Palm olein (cooking oil)", "uoms": ["KG", "Liter"]},
        {"hS_CODE": "1512.1900", "description": "Sunflower oil - refined", "uoms": ["Liter", "KG"]},
        {"hS_CODE": "1509.1000", "description": "Olive oil - virgin", "uoms": ["Liter"]},
        {"hS_CODE": "1517.1000", "description": "Margarine", "uoms": ["KG"]},
        # --- Pharma & medical ---
        {"hS_CODE": "3004.9092", "description": "Paracetamol (medicaments)", "uoms": ["Numbers, pieces, units", "KG"]},
        {"hS_CODE": "3004.9099", "description": "Medicaments - other", "uoms": ["Numbers, pieces, units", "KG"]},
        {"hS_CODE": "3004.2000", "description": "Medicaments - antibiotics", "uoms": ["Numbers, pieces, units"]},
        {"hS_CODE": "3004.9050", "description": "Eye drops", "uoms": ["Numbers, pieces, units"]},
        {"hS_CODE": "3004.9060", "description": "Medicinal ointments", "uoms": ["Numbers, pieces, units"]},
        {"hS_CODE": "3002.2020", "description": "Hepatitis-B vaccines", "uoms": ["Numbers, pieces, units"]},
        {"hS_CODE": "3005.1090", "description": "Surgical tapes / dressings", "uoms": ["Numbers, pieces, units"]},
        {"hS_CODE": "3006.5000", "description": "First-aid boxes and kits", "uoms": ["Numbers, pieces, units"]},
        {"hS_CODE": "3822.0000", "description": "Diagnostic / lab reagents", "uoms": ["Numbers, pieces, units"]},
        # --- Soaps, cosmetics, home care ---
        {"hS_CODE": "3401.1100", "description": "Toilet soap", "uoms": ["Numbers, pieces, units", "KG"]},
        {"hS_CODE": "3401.2000", "description": "Soap - other forms", "uoms": ["KG"]},
        {"hS_CODE": "3402.2000", "description": "Detergents (retail packs)", "uoms": ["KG", "Numbers, pieces, units"]},
        {"hS_CODE": "3305.1000", "description": "Shampoos", "uoms": ["Numbers, pieces, units", "Liter"]},
        {"hS_CODE": "3305.9020", "description": "Hair dyes", "uoms": ["Numbers, pieces, units"]},
        {"hS_CODE": "3306.1010", "description": "Toothpaste", "uoms": ["Numbers, pieces, units"]},
        {"hS_CODE": "3303.2000", "description": "Perfumes", "uoms": ["Numbers, pieces, units"]},
        {"hS_CODE": "3304.3010", "description": "Nail polish", "uoms": ["Numbers, pieces, units"]},
        {"hS_CODE": "3304.9910", "description": "Face and skin creams / lotions", "uoms": ["Numbers, pieces, units"]},
        {"hS_CODE": "3307.4100", "description": "Agarbatti / incense", "uoms": ["Numbers, pieces, units", "KG"]},
        {"hS_CODE": "3808.9110", "description": "Mosquito coils and mats", "uoms": ["Numbers, pieces, units"]},
        {"hS_CODE": "3808.9400", "description": "Disinfectants", "uoms": ["Liter", "Numbers, pieces, units"]},
        {"hS_CODE": "3406.0000", "description": "Candles", "uoms": ["KG", "Numbers, pieces, units"]},
        {"hS_CODE": "3605.0000", "description": "Matches", "uoms": ["Numbers, pieces, units", "Packs"]},
        # --- Chemicals, paints, fertilizers ---
        {"hS_CODE": "3102.1000", "description": "Urea fertilizer", "uoms": ["KG", "40KG", "Bag"]},
        {"hS_CODE": "3103.1000", "description": "Superphosphate fertilizer", "uoms": ["KG", "Bag"]},
        {"hS_CODE": "3105.3000", "description": "DAP fertilizer", "uoms": ["KG", "Bag"]},
        {"hS_CODE": "3808.9170", "description": "Registered agricultural pesticides", "uoms": ["Liter", "KG"]},
        {"hS_CODE": "3208.1010", "description": "Varnishes", "uoms": ["Liter", "Gallon"]},
        {"hS_CODE": "3209.1090", "description": "Paints (water based)", "uoms": ["Liter", "Gallon"]},
        {"hS_CODE": "3215.1190", "description": "Printing ink", "uoms": ["KG", "Liter"]},
        {"hS_CODE": "3204.1600", "description": "Reactive dyes (textile)", "uoms": ["KG"]},
        {"hS_CODE": "3506.1000", "description": "Glues / adhesives (retail)", "uoms": ["KG", "Numbers, pieces, units"]},
        {"hS_CODE": "3814.0000", "description": "Thinners and solvents", "uoms": ["Liter"]},
        # --- Plastics & packaging ---
        {"hS_CODE": "3901.1000", "description": "Polyethylene (PE) resin", "uoms": ["KG", "MT"]},
        {"hS_CODE": "3902.1000", "description": "Polypropylene (PP) resin", "uoms": ["KG", "MT"]},
        {"hS_CODE": "3907.6020", "description": "PET resin (bottle grade)", "uoms": ["KG", "MT"]},
        {"hS_CODE": "3917.2100", "description": "Pipes and tubes of polyethylene", "uoms": ["KG", "Meter"]},
        {"hS_CODE": "3918.1000", "description": "PVC floor coverings", "uoms": ["Square Metre", "KG"]},
        {"hS_CODE": "3920.2010", "description": "BOPP film - plain", "uoms": ["KG"]},
        {"hS_CODE": "3923.2100", "description": "Plastic bags (polyethylene)", "uoms": ["KG"]},
        {"hS_CODE": "3923.3010", "description": "Plastic bottles", "uoms": ["Numbers, pieces, units", "KG"]},
        {"hS_CODE": "3923.5000", "description": "Caps, lids and closures", "uoms": ["KG", "Numbers, pieces, units"]},
        {"hS_CODE": "3924.1000", "description": "Plastic tableware / kitchenware", "uoms": ["KG", "Numbers, pieces, units"]},
        {"hS_CODE": "3925.2000", "description": "Plastic doors and windows", "uoms": ["Numbers, pieces, units", "KG"]},
        {"hS_CODE": "3926.9099", "description": "Plastic articles - other", "uoms": ["KG", "Numbers, pieces, units"]},
        # --- Rubber & tyres ---
        {"hS_CODE": "4011.1000", "description": "Tyres - motor cars", "uoms": ["Numbers, pieces, units"]},
        {"hS_CODE": "4011.2010", "description": "Tyres - light trucks", "uoms": ["Numbers, pieces, units"]},
        {"hS_CODE": "4011.4000", "description": "Tyres - motorcycles", "uoms": ["Numbers, pieces, units"]},
        {"hS_CODE": "4011.5000", "description": "Tyres - bicycles", "uoms": ["Numbers, pieces, units"]},
        {"hS_CODE": "4013.9020", "description": "Inner tubes - motorcycles", "uoms": ["Numbers, pieces, units"]},
        {"hS_CODE": "4016.9310", "description": "Rubber gaskets", "uoms": ["Numbers, pieces, units", "KG"]},
        # --- Leather & textiles ---
        {"hS_CODE": "4107.1100", "description": "Finished leather - full grain", "uoms": ["Square Foot", "KG"]},
        {"hS_CODE": "4203.1010", "description": "Leather jackets", "uoms": ["Numbers, pieces, units"]},
        {"hS_CODE": "4203.2920", "description": "Leather gloves - industrial", "uoms": ["Pair", "Dozen"]},
        {"hS_CODE": "4202.2100", "description": "Handbags - leather", "uoms": ["Numbers, pieces, units"]},
        {"hS_CODE": "5205.1100", "description": "Cotton yarn (single, uncombed)", "uoms": ["KG"]},
        # --- Wood ---
        {"hS_CODE": "4407.1000", "description": "Sawn wood - coniferous", "uoms": ["Cubic Metre", "Timber Logs"]},
        {"hS_CODE": "4410.1100", "description": "Particle board", "uoms": ["Square Metre", "Numbers, pieces, units"]},
        {"hS_CODE": "4418.2000", "description": "Wooden doors and frames", "uoms": ["Numbers, pieces, units"]},
        {"hS_CODE": "4419.0000", "description": "Wooden tableware / kitchenware", "uoms": ["Numbers, pieces, units"]},
        # --- Minerals, cement, fuel ---
        {"hS_CODE": "2501.1000", "description": "Table salt", "uoms": ["KG", "40KG"]},
        {"hS_CODE": "2501.2000", "description": "Rock salt", "uoms": ["KG", "MT"]},
        {"hS_CODE": "2523.1000", "description": "Cement clinker", "uoms": ["MT"]},
        {"hS_CODE": "2523.2100", "description": "White cement", "uoms": ["KG", "Bag", "MT"]},
        {"hS_CODE": "2523.2900", "description": "Portland cement (grey)", "uoms": ["Bag", "MT", "KG"]},
        {"hS_CODE": "2520.1010", "description": "Gypsum", "uoms": ["MT", "KG"]},
        {"hS_CODE": "2701.1200", "description": "Bituminous coal", "uoms": ["MT"]},
        {"hS_CODE": "2515.1200", "description": "Marble blocks / slabs", "uoms": ["MT", "Square Foot"]},
        # --- Steel, electronics, IT, services (PCT standard) ---
        {"hS_CODE": "7214.9990", "description": "Steel bars (re-rolled)", "uoms": ["MT", "KG"]},
        {"hS_CODE": "8523.4990", "description": "Software / recorded media", "uoms": ["Numbers, pieces, units"]},
        {"hS_CODE": "8471.3010", "description": "Laptops / notebooks", "uoms": ["Numbers, pieces, units"]},
        {"hS_CODE": "9983.0000", "description": "Telecom / IT services", "uoms": ["Numbers, pieces, units", "SqY"]},
    ]

    SRO_SCHEDULES = [
        {"srO_ID": 3, "srO_DESC": "Third Schedule"},
        {"srO_ID": 5, "srO_DESC": "Fifth Schedule (Zero-rated)"},
        {"srO_ID": 6, "srO_DESC": "Sixth Schedule (Exempt)"},
        {"srO_ID": 8, "srO_DESC": "Eighth Schedule (Reduced rate)"},
    ]

    def provinces(self):   return self.PROVINCES
    def doc_types(self):   return self.DOC_TYPES
    def uom(self):         return self.UOM
    def trans_types(self): return self.TRANS_TYPES
    def sro_schedules(self, rate_id=None, date=None): return self.SRO_SCHEDULES

    def hs_codes(self, q="", limit=25):
        q = (q or "").strip().lower()
        rows = self.HS_CODES
        if q:
            rows = [r for r in rows
                    if q in r["hS_CODE"].lower() or q in r["description"].lower()]
        capped = rows if limit is None else rows[:limit]
        return [{"hS_CODE": r["hS_CODE"], "description": r["description"]} for r in capped]

    def hs_uom(self, hs_code):
        for r in self.HS_CODES:
            if r["hS_CODE"] == hs_code:
                return [{"uoM_ID": None, "description": u} for u in r["uoms"]]
        return []

    # §5.8 SaleTypeToRate — rATE_ID/rATE_DESC/rATE_VALUE per transaction type.
    # Mock rates engine ke official configs se derive hote hain; reduced rate
    # par multi-rate case (Eighth Schedule item-wise) bhi included.
    RATES_BY_TRANS_DESC = {
        "Goods at standard rate (default)": [
            {"ratE_ID": 734, "ratE_DESC": "18%", "ratE_VALUE": 18}],
        "Goods at Reduced Rate": [
            {"ratE_ID": 280, "ratE_DESC": "1%", "ratE_VALUE": 1},
            {"ratE_ID": 281, "ratE_DESC": "5%", "ratE_VALUE": 5}],
        "3rd Schedule Goods": [
            {"ratE_ID": 413, "ratE_DESC": "18%", "ratE_VALUE": 18}],
        "Exempt goods": [
            {"ratE_ID": 300, "ratE_DESC": "Exempt", "ratE_VALUE": 0}],
        "Goods at zero-rate": [
            {"ratE_ID": 301, "ratE_DESC": "0%", "ratE_VALUE": 0}],
        "Services": [
            {"ratE_ID": 318, "ratE_DESC": "5%", "ratE_VALUE": 5}],
        "Cotton Ginners": [
            {"ratE_ID": 330, "ratE_DESC": "18%", "ratE_VALUE": 18}],
    }

    def sale_type_to_rate(self, date=None, trans_type_id=None,
                          province_id=None):
        desc = next((t["transactioN_DESC"] for t in self.TRANS_TYPES
                     if t["transactioN_TYPE_ID"] == trans_type_id), None)
        return list(self.RATES_BY_TRANS_DESC.get(desc, []))

    def sro_items(self, sro_id=None, date=None):
        # §5.10 SROItem v2 shape
        return [{"srO_ITEM_ID": 17853, "srO_ITEM_DESC": "50"},
                {"srO_ITEM_ID": 17854, "srO_ITEM_DESC": "51"}]

    def statl_check(self, reg_no, date=None):
        """Mock STATL: 13-digit CNICs ending in even digit = Active (arbitrary
        but deterministic, so tests are repeatable)."""
        active = bool(reg_no) and reg_no[-1].isdigit() and int(reg_no[-1]) % 2 == 0
        # Real API shape (Tech Doc §5.11): {"status code": "01", "status": ...}
        return {"status code": "01" if active else "02",
                "status": "Active" if active else "In-Active"}

    def reg_type(self, reg_no):
        """Mock registration-type check (powers error 0053 prevention):
        7-digit NTN -> Registered; 13-digit CNIC -> Unregistered."""
        if reg_no and len(reg_no) == 7 and reg_no.isdigit():
            # Real API shape (Tech Doc §5.12 Get_Reg_Type)
            return {"statuscode": "00", "REGISTRATION_NO": reg_no,
                    "REGISTRATION_TYPE": "Registered"}
        return {"statuscode": "01", "REGISTRATION_NO": reg_no,
                "REGISTRATION_TYPE": "Unregistered"}


# --------------------------------------------------------------- Real client
class RealReferenceClient:
    """Live PRAL reference APIs — Bearer token, same method names as the mock."""

    def __init__(self, token, timeout=20):
        self.token = token
        self.timeout = timeout

    def _get(self, url, params=None):
        import requests
        r = requests.get(url, params=params or {},
                         headers={"Authorization": f"Bearer {self.token}"},
                         timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def _post(self, url, payload):
        import requests
        r = requests.post(url, json=payload,
                          headers={"Authorization": f"Bearer {self.token}",
                                   "Content-Type": "application/json"},
                          timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def provinces(self):   return self._get(REAL_ENDPOINTS["provinces"])
    def doc_types(self):   return self._get(REAL_ENDPOINTS["doctypes"])
    def uom(self):         return self._get(REAL_ENDPOINTS["uom"])
    def trans_types(self): return self._get(REAL_ENDPOINTS["transtypes"])

    def sro_schedules(self, rate_id=None, date=None):
        return self._get(REAL_ENDPOINTS["sro_schedule"],
                         {"rate_id": rate_id, "date": date})

    def hs_codes(self, q="", limit=25):
        rows = self._get(REAL_ENDPOINTS["hscodes"])
        q = (q or "").strip().lower()
        if q:
            rows = [r for r in rows
                    if q in str(r.get("hS_CODE", "")).lower()
                    or q in str(r.get("description", "")).lower()]
        return rows if limit is None else rows[:limit]

    def hs_uom(self, hs_code):
        return self._get(REAL_ENDPOINTS["hs_uom"],
                         {"hs_code": hs_code, "annexure_id": 3})

    # §5.8 SaleTypeToRate — rATE_ID/rATE_DESC/rATE_VALUE per transaction type.
    # Mock rates engine ke official configs se derive hote hain; reduced rate
    # par multi-rate case (Eighth Schedule item-wise) bhi included.
    RATES_BY_TRANS_DESC = {
        "Goods at standard rate (default)": [
            {"ratE_ID": 734, "ratE_DESC": "18%", "ratE_VALUE": 18}],
        "Goods at Reduced Rate": [
            {"ratE_ID": 280, "ratE_DESC": "1%", "ratE_VALUE": 1},
            {"ratE_ID": 281, "ratE_DESC": "5%", "ratE_VALUE": 5}],
        "3rd Schedule Goods": [
            {"ratE_ID": 413, "ratE_DESC": "18%", "ratE_VALUE": 18}],
        "Exempt goods": [
            {"ratE_ID": 300, "ratE_DESC": "Exempt", "ratE_VALUE": 0}],
        "Goods at zero-rate": [
            {"ratE_ID": 301, "ratE_DESC": "0%", "ratE_VALUE": 0}],
        "Services": [
            {"ratE_ID": 318, "ratE_DESC": "5%", "ratE_VALUE": 5}],
        "Cotton Ginners": [
            {"ratE_ID": 330, "ratE_DESC": "18%", "ratE_VALUE": 18}],
    }

    def sale_type_to_rate(self, date=None, trans_type_id=None,
                          province_id=None):
        desc = next((t["transactioN_DESC"] for t in self.TRANS_TYPES
                     if t["transactioN_TYPE_ID"] == trans_type_id), None)
        return list(self.RATES_BY_TRANS_DESC.get(desc, []))

    def sro_items(self, sro_id=None, date=None):
        # §5.10 SROItem v2 shape
        return [{"srO_ITEM_ID": 17853, "srO_ITEM_DESC": "50"},
                {"srO_ITEM_ID": 17854, "srO_ITEM_DESC": "51"}]

    def statl_check(self, reg_no, date=None):
        return self._post(REAL_ENDPOINTS["statl"],
                          {"regno": reg_no, "date": date or ""})

    def reg_type(self, reg_no):
        return self._post(REAL_ENDPOINTS["reg_type"],
                          {"Registration_No": reg_no})


# --------------------------------------------------------------- Factory
def get_reference_client():
    from django.conf import settings
    if getattr(settings, "FBR_USE_MOCK", True):
        return MockReferenceClient()
    return RealReferenceClient(token=settings.FBR_API_TOKEN)


def cached(key, fn):
    data = cache.get(key)
    if data is None:
        data = fn()
        cache.set(key, data, CACHE_TTL)
    return data

# ------------------------------------------------------- Reference sync
class ReferenceSyncService:
    """Milestone 4 — FBR Reference APIs ko TaxSaleType rows ke saath sync
    rakhta hai (Tech Spec v1.12 §5.5 transtypecode + §5.8 SaleTypeToRate).

    Design (never destroy data):
      - Trans type IDs: exact-name match par fbr_trans_type_id set hota hai.
      - Rate drift: har mapped sale type ke liye FBR ke current rates fetch;
        agar hamari effective rate/label FBR ke set mein NAHI to drift.
      - Apply SIRF tab auto hota hai jab FBR EXACTLY EK rate lauta ta hai
        (unambiguous). Multi-rate (e.g. Eighth Schedule item-wise) sirf
        report hota hai — practitioner admin se decide karta hai.
      - Apply = purani row effective_to se close + nayi date-effective row.
    """

    def __init__(self, client=None):
        self.client = client or get_reference_client()

    def sync_trans_type_ids(self):
        """Resilient: FBR endpoint down ho to error report hota hai,
        exception nahi phat'ti (cron aglay run par phir try karega)."""
        from .models import TaxSaleType
        try:
            rows = self.client.trans_types() or []
        except Exception as e:
            return {"matched": [], "unmatched": [],
                    "fbr_types": 0, "error": f"{e.__class__.__name__}: {e}"}
        by_desc = {r["transactioN_DESC"]: r["transactioN_TYPE_ID"]
                   for r in rows}
        matched, unmatched = [], []
        names = set(TaxSaleType.objects.filter(is_active=True)
                    .values_list("name", flat=True))
        for name in sorted(names):
            tid = by_desc.get(name)
            if tid is None:
                unmatched.append(name)
                continue
            TaxSaleType.objects.filter(name=name).exclude(
                fbr_trans_type_id=tid).update(fbr_trans_type_id=tid)
            matched.append((name, tid))
        return {"matched": matched, "unmatched": unmatched,
                "fbr_types": len(rows)}

    def check_rate_drift(self, on_date=None, province_id=8):
        """[{sale_type, trans_type_id, current, fbr_rates, drift,
        auto_applicable}] — sirf un types ke liye jinke paas trans ID hai."""
        from datetime import date as _date
        from .models import TaxSaleType
        from .tax_engine import load_rules
        on_date = on_date or _date.today()
        sale_types, _, _ = load_rules(on_date)
        report = []
        seen = set()
        qs = (TaxSaleType.objects.filter(is_active=True,
                                         fbr_trans_type_id__isnull=False)
              .order_by("name"))
        for row in qs:
            if row.name in seen:
                continue
            seen.add(row.name)
            cfg = sale_types.get(row.name)
            if cfg is None:
                continue
            try:
                fbr = self.client.sale_type_to_rate(
                    date=on_date.strftime("%d-%b-%Y"),
                    trans_type_id=row.fbr_trans_type_id,
                    province_id=province_id) or []
            except Exception as e:
                # Ek type ka failure baqi types ka check nahi rokta
                report.append({"sale_type": row.name,
                               "trans_type_id": row.fbr_trans_type_id,
                               "current": "", "fbr_rates": [],
                               "drift": False, "auto_applicable": False,
                               "fbr_raw": [],
                               "error": f"{e.__class__.__name__}: {e}"})
                continue
            if not fbr:
                continue
            current_label = (cfg.get("rate_label")
                             or f'{Decimal(str(cfg["rate"])).normalize()}%')
            fbr_labels = [r.get("ratE_DESC", "") for r in fbr]
            drift = current_label not in fbr_labels
            report.append({
                "sale_type": row.name,
                "trans_type_id": row.fbr_trans_type_id,
                "current": current_label,
                "fbr_rates": fbr_labels,
                "drift": drift,
                "auto_applicable": drift and len(fbr) == 1,
                "fbr_raw": fbr,
            })
        return report

    def apply_rate_updates(self, report, on_date=None):
        """Unambiguous drifts apply: purani row close, nayi date-effective
        row create. Returns applied list."""
        from datetime import date as _date, timedelta
        from .models import TaxSaleType
        from .tax_engine import invalidate_rules_cache
        on_date = on_date or _date.today()
        applied = []
        for d in report:
            if not d.get("auto_applicable"):
                continue
            fbr_rate = d["fbr_raw"][0]
            old = (TaxSaleType.objects.filter(
                name=d["sale_type"], is_active=True,
                effective_from__lte=on_date)
                .order_by("-effective_from").first())
            if old is None:
                continue
            # close old row (data preserved), naya row aaj se effective
            old.effective_to = on_date - timedelta(days=1)
            old.save(update_fields=["effective_to"])
            TaxSaleType.objects.create(
                name=old.name,
                rate=Decimal(str(fbr_rate.get("ratE_VALUE", 0))),
                rate_type=old.rate_type, rate_per_unit=old.rate_per_unit,
                rate_label=fbr_rate.get("ratE_DESC", ""),
                sro_item_serial=old.sro_item_serial,
                charges_st=old.charges_st,
                further_tax_applies=old.further_tax_applies,
                sro_schedule=old.sro_schedule,
                retail_price_based=old.retail_price_based,
                fbr_trans_type_id=old.fbr_trans_type_id,
                effective_from=on_date, is_active=True,
                legal_reference=f"FBR SaleTypeToRate sync {on_date} "
                                f"(rate_id={fbr_rate.get('ratE_ID')})")
            applied.append(d["sale_type"])
        if applied:
            invalidate_rules_cache()
        return applied

