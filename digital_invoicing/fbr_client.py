"""
fbr_client.py  —  The FBR integration layer.

Two interchangeable clients with the SAME method signature:

    MockFBRClient  -> builds locally, returns a realistic DI-API-v1.12 response
                      (unique FBR invoice number + validationResponse). Use this
                      to develop the entire product with no PRAL token.

    RealFBRClient  -> posts to the live PRAL gateway. Drop in when your token
                      arrives; nothing else in the app changes.

Selection is driven by settings:
    FBR_USE_MOCK = True            # flip to False for production
    FBR_API_TOKEN = "..."          # PRAL security token (5-year validity)
    FBR_POST_URL  = "https://gw.fbr.gov.pk/di_data/v1/di/postinvoicedata_sb"
    FBR_VALIDATE_URL = "https://gw.fbr.gov.pk/di_data/v1/di/validateinvoicedata_sb"

Response shape (both clients), matching the real API:
    {
      "invoiceNumber": "0788762DI20260703123456",
      "dated": "2026-07-03 12:34:56",
      "validationResponse": {
         "statusCode": "00", "status": "Valid", "error": "",
         "invoiceStatuses": [
             {"itemSNo":"1","statusCode":"00","status":"Valid",
              "invoiceNo":"...","errorCode":"","error":""}
         ]
      }
    }
"""

import random
from datetime import datetime


# ---------------------------------------------------------------- Mock client
class MockFBRClient:
    """Simulates the FBR DI API for development. No network, no token."""

    def validate_invoice(self, payload: dict) -> dict:
        """validateinvoicedata mirror — post jaisa hi, magar invoice number
        issue NAHI hota (spec 4.2)."""
        r = self.post_invoice(payload)
        r.pop("invoiceNumber", None)
        vr = r.get("validationResponse", {})
        for st in vr.get("invoiceStatuses") or []:
            st.pop("invoiceNo", None)
        return r

    def post_invoice(self, payload: dict) -> dict:
        errors = self._validate(payload)
        if errors:
            return {
                "invoiceNumber": "",
                "dated": self._now(),
                "validationResponse": {
                    "statusCode": "01",
                    "status": "Invalid",
                    "error": errors[0]["error"],
                    "invoiceStatuses": [
                        {
                            "itemSNo": str(i + 1),
                            "statusCode": "01",
                            "status": "Invalid",
                            "invoiceNo": "",
                            "errorCode": e["errorCode"],
                            "error": e["error"],
                        }
                        for i, e in enumerate(errors)
                    ],
                },
            }

        number = self._gen_number(payload.get("sellerNTNCNIC", ""))
        return {
            "invoiceNumber": number,
            "dated": self._now(),
            "validationResponse": {
                "statusCode": "00",
                "status": "Valid",
                "error": "",
                "invoiceStatuses": [
                    {
                        "itemSNo": str(i + 1),
                        "statusCode": "00",
                        "status": "Valid",
                        "invoiceNo": number,
                        "errorCode": "",
                        "error": "",
                    }
                    for i, _ in enumerate(payload.get("items", []))
                ],
            },
        }

    # -- full FBR validation from the official Error Message Guide --
    def _validate(self, p):
        from .validators import validate_invoice
        return validate_invoice(p)

    def _gen_number(self, ntn):
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        seq = str(random.randint(100000, 999999))
        return f"{(ntn or '0000000')[:7]}DI{stamp}{seq}"

    def _now(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- Real client
class RealFBRClient:
    """Posts to the live PRAL gateway. Requires a valid security token.

    is_sandbox=False (production) par payload se scenarioId strip hota hai —
    PRAL Technical Spec v1.12: scenarioId "Required for Sandbox only".

    Network/HTTP failures NEVER raise: hamesha ek FBR-shaped Invalid response
    return hota hai taa-ke view invoice ko `failed` status ke saath persist
    kare (audit trail kabhi loss na ho).
    """

    def __init__(self, token: str, post_url: str, is_sandbox: bool = True,
                 timeout: int = 30):
        self.token = token
        self.post_url = post_url
        self.is_sandbox = is_sandbox
        self.timeout = timeout

    def validate_invoice(self, payload: dict) -> dict:
        """PRAL validateinvoicedata (spec 4.2) — pre-submission check."""
        url = self.post_url.replace("postinvoicedata", "validateinvoicedata")
        return self._call(url, payload)

    def post_invoice(self, payload: dict) -> dict:
        return self._call(self.post_url, payload)

    def _call(self, url: str, payload: dict) -> dict:
        import requests  # local import so mock path needs no dependency

        payload = dict(payload)
        if not self.is_sandbox:
            payload.pop("scenarioId", None)   # sandbox-only field (spec v1.12)

        try:
            resp = requests.post(
                url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.token}",
                },
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            return self._failure("NET", f"FBR gateway unreachable: {e.__class__.__name__}")

        if resp.status_code == 401:
            return self._failure("401", "Unauthorized — FBR token invalid ya expired")
        if resp.status_code >= 500:
            return self._failure("500", "FBR Internal Server Error (Contact Administrator)")

        try:
            return resp.json()
        except ValueError:
            return self._failure("BAD", "FBR returned non-JSON response")

    @staticmethod
    def _failure(code, msg):
        return {
            "invoiceNumber": "",
            "dated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "validationResponse": {
                "statusCode": "01", "status": "Invalid", "error": msg,
                "invoiceStatuses": [{
                    "itemSNo": "1", "statusCode": "01", "status": "Invalid",
                    "invoiceNo": "", "errorCode": code, "error": msg}],
            },
        }


# ---------------------------------------------------------------- Factory
def get_fbr_client(profile=None):
    """Return the configured client.

    Priority:
      1. FBR_USE_MOCK=True (settings)              -> MockFBRClient
      2. profile.fbr_token set (per-business SaaS) -> RealFBRClient with
         profile.use_sandbox routing (sandbox vs production URL)
      3. Global settings token (legacy fallback)   -> RealFBRClient
      4. Nothing configured                        -> MockFBRClient
    """
    from django.conf import settings
    if getattr(settings, "FBR_USE_MOCK", True):
        return MockFBRClient()

    sandbox_url = getattr(settings, "FBR_POST_URL_SANDBOX",
                          "https://gw.fbr.gov.pk/di_data/v1/di/postinvoicedata_sb")
    prod_url = getattr(settings, "FBR_POST_URL_PRODUCTION",
                       "https://gw.fbr.gov.pk/di_data/v1/di/postinvoicedata")

    if profile is not None and getattr(profile, "fbr_token", ""):
        is_sb = bool(getattr(profile, "use_sandbox", True))
        _tok = getattr(profile, "fbr_token_plain", None) or profile.fbr_token
        return RealFBRClient(
            token=_tok,
            post_url=sandbox_url if is_sb else prod_url,
            is_sandbox=is_sb,
        )

    token = getattr(settings, "FBR_API_TOKEN", "")
    if token:
        legacy_url = getattr(settings, "FBR_POST_URL", sandbox_url)
        return RealFBRClient(token=token, post_url=legacy_url,
                             is_sandbox=("_sb" in legacy_url))

    return MockFBRClient()
