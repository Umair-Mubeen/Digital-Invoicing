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
    """Posts to the live PRAL gateway. Requires a valid security token."""

    def __init__(self, token: str, post_url: str, timeout: int = 30):
        self.token = token
        self.post_url = post_url
        self.timeout = timeout

    def post_invoice(self, payload: dict) -> dict:
        import requests  # local import so mock path needs no dependency
        resp = requests.post(
            self.post_url,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------- Factory
def get_fbr_client():
    """Return the configured client. Flip FBR_USE_MOCK in settings to go live."""
    from django.conf import settings
    if getattr(settings, "FBR_USE_MOCK", True):
        return MockFBRClient()
    return RealFBRClient(
        token=settings.FBR_API_TOKEN,
        post_url=settings.FBR_POST_URL,
    )
