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
    "sro_schedule":"https://gw.fbr.gov.pk/pdi/v2/SroSchedule",
    "rate":        "https://gw.fbr.gov.pk/pdi/v2/SaleTypeToRate",
    "hs_uom":      "https://gw.fbr.gov.pk/pdi/v2/HS_UOM",
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

    # Small HS sample with allowed UOMs — mirrors HS_UOM behaviour (error 0099)
    HS_CODES = [
        {"hS_CODE": "8523.4990", "description": "Software / recorded media", "uoms": ["Numbers, pieces, units"]},
        {"hS_CODE": "2106.9090", "description": "Food preparations nes", "uoms": ["KG"]},
        {"hS_CODE": "3004.9099", "description": "Medicaments nes", "uoms": ["Numbers, pieces, units", "KG"]},
        {"hS_CODE": "7214.9990", "description": "Steel bars (re-rolled)", "uoms": ["MT", "KG"]},
        {"hS_CODE": "5205.1100", "description": "Cotton yarn", "uoms": ["KG"]},
        {"hS_CODE": "8471.3010", "description": "Laptops", "uoms": ["Numbers, pieces, units"]},
        {"hS_CODE": "2202.1010", "description": "Aerated beverages", "uoms": ["Liter", "Numbers, pieces, units"]},
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

    def hs_codes(self, q=""):
        q = (q or "").strip().lower()
        rows = self.HS_CODES
        if q:
            rows = [r for r in rows
                    if q in r["hS_CODE"].lower() or q in r["description"].lower()]
        return [{"hS_CODE": r["hS_CODE"], "description": r["description"]} for r in rows[:25]]

    def hs_uom(self, hs_code):
        for r in self.HS_CODES:
            if r["hS_CODE"] == hs_code:
                return [{"uoM_ID": None, "description": u} for u in r["uoms"]]
        return []

    def statl_check(self, reg_no, date=None):
        """Mock STATL: 13-digit CNICs ending in even digit = Active (arbitrary
        but deterministic, so tests are repeatable)."""
        active = bool(reg_no) and reg_no[-1].isdigit() and int(reg_no[-1]) % 2 == 0
        return {"regno": reg_no, "statl_status": "Active" if active else "In-Active"}

    def reg_type(self, reg_no):
        """Mock registration-type check (powers error 0053 prevention):
        7-digit NTN -> Registered; 13-digit CNIC -> Unregistered."""
        if reg_no and len(reg_no) == 7 and reg_no.isdigit():
            return {"REGISTRATION_NO": reg_no, "REG_TYPE": "Registered"}
        return {"REGISTRATION_NO": reg_no, "REG_TYPE": "Unregistered"}


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

    def hs_codes(self, q=""):
        rows = self._get(REAL_ENDPOINTS["hscodes"])
        q = (q or "").strip().lower()
        if q:
            rows = [r for r in rows
                    if q in str(r.get("hS_CODE", "")).lower()
                    or q in str(r.get("description", "")).lower()]
        return rows[:25]

    def hs_uom(self, hs_code):
        return self._get(REAL_ENDPOINTS["hs_uom"],
                         {"hs_code": hs_code, "annexure_id": 3})

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