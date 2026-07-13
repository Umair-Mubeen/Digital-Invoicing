"""
services.py — Business logic layer (Phase 5 extraction).

Rules yahan hain; views sirf HTTP handle karti hain. Ye code views.py ke
submit_invoice se MOVE hua hai (rewrite nahi) — behaviour byte-for-byte same,
tests.py guard karta hai.

Public API:
    InvoiceSubmissionService(user).submit(payload) -> dict (JSON response body)
    SubmissionError  — validation failures (simple ya FBR-shaped)
"""

from datetime import datetime, timedelta
from decimal import Decimal

from django.utils import timezone

from .models import Invoice, InvoiceItem, SellerProfile, Buyer, SavedItem
from .tax_engine import compute_item, get_sale_type_config
from .fbr_client import get_fbr_client


# --------------------------------------------------------------------------
class SubmissionError(Exception):
    """Validation failure.

    simple=True  -> {"ok": False, "error": msg} with HTTP 400
    simple=False -> full FBR-shaped Invalid response (HTTP 200, jaise FBR
                    khud statusCode 01 return karta hai)
    """

    def __init__(self, message, code="", simple=False, totals=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.simple = simple
        self.totals = totals or {"value": 0, "salesTax": 0,
                                 "furtherTax": 0, "total": 0}

    def fbr_shaped(self):
        return {
            "ok": False, "invoiceId": None, "invoiceNumber": "", "dated": "",
            "validationResponse": {
                "statusCode": "01", "status": "Invalid", "error": self.message,
                "invoiceStatuses": [{
                    "itemSNo": "1", "statusCode": "01", "status": "Invalid",
                    "invoiceNo": "", "errorCode": self.code,
                    "error": self.message}],
            },
            "totals": self.totals,
        }


# --------------------------------------------------------------------------
class SellerResolutionService:
    """Seller = SELECTED business (owner-checked). Browser ke seller fields
    ignore — SellerProfile hi server-side truth hai."""

    @staticmethod
    def resolve(user, payload):
        profile = SellerProfile.objects.filter(
            user=user, pk=payload.get("sellerProfileId")).first() \
            or SellerProfile.objects.filter(user=user).first()
        if not profile:
            raise SubmissionError("Please add a Business first", simple=True)
        payload["sellerNTNCNIC"] = profile.ntn_cnic
        payload["sellerBusinessName"] = profile.business_name
        payload["sellerProvince"] = profile.province
        payload["sellerAddress"] = profile.address
        return profile


# --------------------------------------------------------------------------
class DebitNoteValidationService:
    """FBR server-side debit-note checks ka local mirror:
    0026, 0027, 0028, 0057, 0035, 0034 (0067 totals ke baad — submit() mein)."""

    @staticmethod
    def validate(user, payload):
        if payload.get("invoiceType") != "Debit Note":
            return None

        ref_no = (payload.get("invoiceRefNo") or "").strip()
        if not ref_no:
            raise SubmissionError(
                "Invoice Reference No. is mandatory requirement for debit note",
                code="0026")

        reason = (payload.get("reason") or "").strip()
        if not reason:
            raise SubmissionError(
                "Reason is mandatory requirement for debit note", code="0027")
        if reason == "Others" and not (payload.get("reasonRemarks") or "").strip():
            raise SubmissionError(
                "Remarks are required where reason is 'Others'", code="0028")

        ref_invoice = Invoice.objects.filter(
            owner=user, fbr_invoice_number=ref_no, status="valid").first()
        if not ref_invoice:
            raise SubmissionError(
                "Reference invoice for debit note does not exist", code="0057")

        try:
            dn_date = datetime.strptime(
                payload.get("invoiceDate", ""), "%Y-%m-%d").date()
        except ValueError:
            raise SubmissionError(
                "Invoice date is not in proper format (YYYY-MM-DD)", code="0113")

        if dn_date < ref_invoice.invoice_date:
            raise SubmissionError(
                "Debit Note date must be greater or same as reference invoice date",
                code="0035")
        if dn_date > ref_invoice.invoice_date + timedelta(days=180):
            raise SubmissionError(
                "Debit note can only be added within 180 days of reference invoice date",
                code="0034")
        return ref_invoice


# --------------------------------------------------------------------------
class TaxCalculationService:
    """Server-authoritative tax pass — client ke numbers overwrite hote hain."""

    @staticmethod
    def check_retail_price(raw_items):
        for it in raw_items:
            st = it.get("saleType", "Goods at standard rate")
            if (get_sale_type_config(st) or {}).get("retail_price_based") and not (
                    float(it.get("fixedNotifiedValueOrRetailPrice", 0) or 0) > 0):
                raise SubmissionError(
                    "Retail Price (MRP) is required for 3rd Schedule items",
                    simple=True)

    @staticmethod
    def compute(raw_items, buyer_unregistered):
        total_value = total_st = total_ft = Decimal("0")
        clean_items = []
        for it in raw_items:
            sale_type = it.get("saleType", "Goods at standard rate")
            value = it.get("valueSalesExcludingST", 0) or 0
            try:
                calc = compute_item(
                    sale_type, value, buyer_unregistered=buyer_unregistered,
                    hs_code=it.get("hsCode", ""),
                    retail_price=it.get("fixedNotifiedValueOrRetailPrice", 0))
            except ValueError as e:
                raise SubmissionError(str(e), simple=True)

            it = dict(it)
            it["rate"] = calc["rate"]
            it["salesTaxApplicable"] = float(calc["sales_tax"])
            it["furtherTax"] = float(calc["further_tax"])
            it["sroScheduleNo"] = calc["sro_schedule"]
            _mrp = it.get("fixedNotifiedValueOrRetailPrice", 0) or 0
            it["fixedNotifiedValueOrRetailPrice"] = (
                float(_mrp if _mrp else value) if calc["retail_price_based"] else 0
            )
            clean_items.append(it)

            total_value += Decimal(str(value))
            total_st += calc["sales_tax"]
            total_ft += calc["further_tax"]
        return clean_items, total_value, total_st, total_ft


# --------------------------------------------------------------------------
class InvoicePersistenceService:
    @staticmethod
    def persist(user, profile, payload, clean_items, totals, result):
        total_value, total_st, total_ft, invoice_total = totals
        vr = result.get("validationResponse", {})
        valid = vr.get("status") == "Valid"

        inv = Invoice.objects.create(
            owner=user,
            seller_profile=profile,
            invoice_type=payload.get("invoiceType", "Sale Invoice"),
            invoice_date=payload.get("invoiceDate"),
            scenario_id=payload.get("scenarioId", ""),
            seller_ntn_cnic=payload.get("sellerNTNCNIC", ""),
            seller_business_name=payload.get("sellerBusinessName", ""),
            seller_province=payload.get("sellerProvince", ""),
            seller_address=payload.get("sellerAddress", ""),
            buyer_ntn_cnic=payload.get("buyerNTNCNIC", ""),
            buyer_business_name=payload.get("buyerBusinessName", ""),
            buyer_province=payload.get("buyerProvince", ""),
            buyer_address=payload.get("buyerAddress", ""),
            buyer_registration_type=payload.get("buyerRegistrationType", "Unregistered"),
            invoice_ref_no=payload.get("invoiceRefNo", ""),
            reason=payload.get("reason", ""),
            reason_remarks=payload.get("reasonRemarks", ""),
            total_value=total_value,
            total_sales_tax=total_st,
            total_further_tax=total_ft,
            invoice_total=invoice_total,
            status="valid" if valid else "failed",
            fbr_invoice_number=result.get("invoiceNumber") or None,
            fbr_dated=result.get("dated", ""),
            fbr_payload=payload,
            fbr_response=result,
            submitted_at=timezone.now() if valid else None,
        )
        for it in clean_items:
            InvoiceItem.objects.create(
                invoice=inv,
                hs_code=it.get("hsCode", ""),
                product_description=it.get("productDescription", ""),
                sale_type=it.get("saleType", ""),
                uom=it.get("uoM", "Numbers, pieces, units"),
                quantity=it.get("quantity", 0) or 0,
                value_excl_st=it.get("valueSalesExcludingST", 0) or 0,
                retail_price=it.get("fixedNotifiedValueOrRetailPrice", 0) or 0,
                rate=it.get("rate", ""),
                sales_tax=it.get("salesTaxApplicable", 0) or 0,
                further_tax=it.get("furtherTax", 0) or 0,
                sro_schedule=it.get("sroScheduleNo", ""),
                sales_tax_withheld=it.get("salesTaxWithheldAtSource", 0) or 0,
                extra_tax=it.get("extraTax", 0) or 0,
                fed_payable=it.get("fedPayable", 0) or 0,
                discount=it.get("discount", 0) or 0,
                total_values=it.get("totalValues", 0) or 0,
                sro_item_serial_no=it.get("sroItemSerialNo", "") or "",
            )
        return inv, valid


# --------------------------------------------------------------------------
class AutoLearnService:
    """Valid invoice se Buyer Book + Saved Products khud update."""

    @staticmethod
    def learn(user, payload, clean_items):
        try:
            bkey = {"owner": user}
            if payload.get("buyerNTNCNIC"):
                bkey["ntn_cnic"] = payload["buyerNTNCNIC"]
            else:
                bkey["business_name"] = payload.get("buyerBusinessName", "")
            b, _ = Buyer.objects.update_or_create(**bkey, defaults={
                "business_name": payload.get("buyerBusinessName", ""),
                "ntn_cnic": payload.get("buyerNTNCNIC", ""),
                "registration_type": payload.get("buyerRegistrationType", "Unregistered"),
                "province": payload.get("buyerProvince", "Sindh"),
                "address": payload.get("buyerAddress", ""),
            })
            Buyer.objects.filter(pk=b.pk).update(
                times_used=b.times_used + 1, last_used=timezone.now())
            for it in clean_items:
                si, _ = SavedItem.objects.update_or_create(
                    owner=user, hs_code=it.get("hsCode", ""),
                    description=it.get("productDescription", ""),
                    defaults={"sale_type": it.get("saleType", ""),
                              "uom": it.get("uoM", ""),
                              "last_value": it.get("valueSalesExcludingST", 0)})
                SavedItem.objects.filter(pk=si.pk).update(
                    times_used=si.times_used + 1)
        except Exception:
            pass  # convenience feature — kabhi submission fail na kare


# --------------------------------------------------------------------------
class InvoiceSubmissionService:
    """Orchestrator: validate -> tax -> FBR -> persist -> learn."""

    def __init__(self, user):
        self.user = user

    def submit(self, payload):
        """Returns the JSON-serialisable response body.
        Raises SubmissionError on validation failure."""
        p = dict(payload)
        raw_items = p.get("items", [])
        if not raw_items:
            raise SubmissionError("Add at least one item", simple=True)

        # Date format (0113) — sab se pehle
        try:
            datetime.strptime(p.get("invoiceDate", ""), "%Y-%m-%d")
        except (ValueError, TypeError):
            raise SubmissionError(
                "Invoice date is not in proper format (YYYY-MM-DD)", code="0113")

        # Sale Invoice: stale debit-note fields saaf (UI toggle bug guard)
        if p.get("invoiceType") != "Debit Note":
            p["invoiceRefNo"] = ""
            p["reason"] = ""
            p["reasonRemarks"] = ""

        profile = SellerResolutionService.resolve(self.user, p)
        ref_invoice = DebitNoteValidationService.validate(self.user, p)

        unreg = p.get("buyerRegistrationType") == "Unregistered"
        TaxCalculationService.check_retail_price(raw_items)
        clean_items, total_value, total_st, total_ft = \
            TaxCalculationService.compute(raw_items, unreg)

        payload_out = dict(p)
        payload_out["items"] = clean_items
        invoice_total = total_value + total_st + total_ft

        # Debit note amounts <= referenced invoice (0067)
        if ref_invoice is not None:
            if (total_value > ref_invoice.total_value
                    or total_st > ref_invoice.total_sales_tax
                    or invoice_total > ref_invoice.invoice_total):
                raise SubmissionError(
                    "Quantity, sale value or tax amounts of the debit note are "
                    "greater than those of the referenced invoice",
                    code="0067",
                    totals={"value": float(total_value),
                            "salesTax": float(total_st),
                            "furtherTax": float(total_ft),
                            "total": float(invoice_total)})

        # FBR call (mock/real per settings + per-profile token/sandbox)
        result = get_fbr_client(profile).post_invoice(payload_out)
        vr = result.get("validationResponse", {})

        inv, valid = InvoicePersistenceService.persist(
            self.user, profile, payload_out, clean_items,
            (total_value, total_st, total_ft, invoice_total), result)

        if valid:
            AutoLearnService.learn(self.user, payload_out, clean_items)
            try:
                InventoryService(self.user).record_sale_items(
                    clean_items, reference=f"INV-{inv.pk}")
            except Exception:
                pass  # inventory optional — submission kabhi fail na ho

        return {
            "ok": valid,
            "invoiceId": inv.pk,
            "invoiceNumber": result.get("invoiceNumber", ""),
            "dated": result.get("dated", ""),
            "validationResponse": vr,
            "totals": {
                "value": float(total_value),
                "salesTax": float(total_st),
                "furtherTax": float(total_ft),
                "total": float(invoice_total),
            },
            # audit metadata (view log karti hai)
            "_audit": {
                "invoice_id": inv.pk,
                "invoice_type": payload_out.get("invoiceType", ""),
                "total": float(invoice_total),
                "errors": [] if valid else [
                    st.get("errorCode")
                    for st in vr.get("invoiceStatuses", []) or []
                    if st.get("errorCode")],
            },
        }


# --------------------------------------------------------------------------
# Phase 8 — FBR Integration Improvements
# --------------------------------------------------------------------------
class InvoiceValidationService:
    """PRAL validateinvoicedata (spec 4.2) — submit se PEHLE FBR-verified
    check. Invoice number issue nahi hota, DB mein kuch save nahi hota."""

    def __init__(self, user):
        self.user = user

    def validate(self, payload):
        p = dict(payload)
        raw_items = p.get("items", [])
        if not raw_items:
            raise SubmissionError("Add at least one item", simple=True)
        try:
            datetime.strptime(p.get("invoiceDate", ""), "%Y-%m-%d")
        except (ValueError, TypeError):
            raise SubmissionError(
                "Invoice date is not in proper format (YYYY-MM-DD)", code="0113")
        if p.get("invoiceType") != "Debit Note":
            p["invoiceRefNo"] = ""; p["reason"] = ""; p["reasonRemarks"] = ""

        profile = SellerResolutionService.resolve(self.user, p)
        DebitNoteValidationService.validate(self.user, p)
        unreg = p.get("buyerRegistrationType") == "Unregistered"
        TaxCalculationService.check_retail_price(raw_items)
        clean_items, tv, tst, tft = TaxCalculationService.compute(raw_items, unreg)
        p["items"] = clean_items

        result = get_fbr_client(profile).validate_invoice(p)
        vr = result.get("validationResponse", {})
        return {
            "ok": vr.get("status") == "Valid",
            "dated": result.get("dated", ""),
            "validationResponse": vr,
            "totals": {"value": float(tv), "salesTax": float(tst),
                       "furtherTax": float(tft),
                       "total": float(tv + tst + tft)},
        }


class InvoiceResubmissionService:
    """Manual v1.6 §4.2: connection loss/failure par resubmission lazmi —
    system automatic retry nahi karta, user one-click resubmit karta hai.
    Wahi record update hota hai (naya row nahi) — audit AuditLog mein."""

    def __init__(self, user):
        self.user = user

    def resubmit(self, invoice_pk):
        inv = Invoice.objects.filter(owner=self.user, pk=invoice_pk).first()
        if not inv:
            raise SubmissionError("Invoice not found", simple=True)
        if inv.status != "failed":
            raise SubmissionError(
                "Only failed invoices can be resubmitted", simple=True)
        if not inv.fbr_payload:
            raise SubmissionError("Original payload was not saved", simple=True)

        profile = inv.seller_profile or SellerProfile.objects.filter(
            user=self.user).first()
        result = get_fbr_client(profile).post_invoice(dict(inv.fbr_payload))
        vr = result.get("validationResponse", {})
        valid = vr.get("status") == "Valid"

        inv.status = "valid" if valid else "failed"
        inv.fbr_invoice_number = result.get("invoiceNumber") or None
        inv.fbr_dated = result.get("dated", "")
        inv.fbr_response = result
        if valid:
            inv.submitted_at = timezone.now()
        inv.save()

        if valid:
            AutoLearnService.learn(self.user, inv.fbr_payload,
                                   inv.fbr_payload.get("items", []))
        return {
            "ok": valid, "invoiceId": inv.pk,
            "invoiceNumber": result.get("invoiceNumber", ""),
            "dated": result.get("dated", ""),
            "validationResponse": vr,
        }


class InvoiceCancellationService:
    """Local cancellation TRACKING — PRAL v1.12 mein cancellation ka koi API
    NAHI; cancel IRIS portal se hota hai (Manual v1.6). Ye service system ki
    books ko IRIS ke saath sync rakhti hai. Manual ke rules mirror:
      - sirf valid invoices
      - 72-hour window (insertion/submission se)
      - edited invoice cancel nahi ho sakti (edit workflow abhi nahi — guard
        future-proof hai)
    """

    def __init__(self, user):
        self.user = user

    def mark_cancelled(self, invoice_pk, remarks=""):
        inv = Invoice.objects.filter(owner=self.user, pk=invoice_pk).first()
        if not inv:
            raise SubmissionError("Invoice not found", simple=True)
        if inv.status != "valid":
            raise SubmissionError(
                "Only valid invoices can be marked cancelled", simple=True)
        if inv.is_locked:
            raise SubmissionError(
                "72-hour window has passed — cancellation is no longer "
                "allowed on IRIS either (Manual v1.6)", simple=True)
        inv.status = "cancelled"
        inv.reason_remarks = (remarks or inv.reason_remarks or
                              "Cancelled on IRIS portal")
        inv.save()
        return {"ok": True, "invoiceId": inv.pk, "status": "cancelled"}


# --------------------------------------------------------------------------
# Phase 14 — Reports (return-filing ready)
# --------------------------------------------------------------------------
class ReportService:
    """Owner-filtered reporting queries. Sirf VALID invoices tax summary mein
    ginte hain (failed/cancelled return mein nahi jaate); status breakdown
    alag section hai."""

    def __init__(self, user):
        self.user = user

    def _base(self, business_id=None, period=None):
        qs = Invoice.objects.filter(owner=self.user)
        if business_id:
            qs = qs.filter(seller_profile_id=business_id)
        if period:  # "YYYY-MM" (tax period)
            try:
                y, m = int(period[:4]), int(period[5:7])
                qs = qs.filter(invoice_date__year=y, invoice_date__month=m)
            except (ValueError, IndexError):
                pass
        return qs

    def tax_summary(self, business_id=None, period=None):
        """Return-filing totals: value / ST / FT / counts, type-wise split."""
        from django.db.models import Sum, Count
        qs = self._base(business_id, period).filter(status="valid")
        agg = qs.aggregate(
            value=Sum("total_value"), st=Sum("total_sales_tax"),
            ft=Sum("total_further_tax"), total=Sum("invoice_total"),
            n=Count("id"))
        by_type = list(qs.values("invoice_type").annotate(
            n=Count("id"), value=Sum("total_value"),
            st=Sum("total_sales_tax"), ft=Sum("total_further_tax"))
            .order_by("invoice_type"))
        return {"totals": {k: (v or 0) for k, v in agg.items()},
                "by_type": by_type}

    def buyer_report(self, business_id=None, period=None, limit=100):
        from django.db.models import Sum, Count
        qs = self._base(business_id, period).filter(status="valid")
        return list(qs.values("buyer_business_name", "buyer_ntn_cnic",
                              "buyer_registration_type")
                    .annotate(n=Count("id"), value=Sum("total_value"),
                              st=Sum("total_sales_tax"),
                              ft=Sum("total_further_tax"))
                    .order_by("-value")[:limit])

    def status_report(self, business_id=None, period=None):
        from django.db.models import Sum, Count
        return list(self._base(business_id, period)
                    .values("status")
                    .annotate(n=Count("id"), value=Sum("total_value"))
                    .order_by("status"))

    def sales_register(self, business_id=None, period=None):
        """Annexure-C style rows — IRIS sales tax return ke liye.
        Item-level (Annex-C item-wise hota hai)."""
        qs = (InvoiceItem.objects
              .filter(invoice__owner=self.user, invoice__status="valid")
              .select_related("invoice")
              .order_by("invoice__invoice_date", "invoice_id", "id"))
        if business_id:
            qs = qs.filter(invoice__seller_profile_id=business_id)
        if period:
            try:
                y, m = int(period[:4]), int(period[5:7])
                qs = qs.filter(invoice__invoice_date__year=y,
                               invoice__invoice_date__month=m)
            except (ValueError, IndexError):
                pass
        return qs


# --------------------------------------------------------------------------
# Phases 9–12 — Products / Inventory / Purchases
# --------------------------------------------------------------------------
class InventoryService:
    """Signed stock movements. Sale par auto stock-out (product match ho to);
    purchase par stock-in; manual adjustment bhi."""

    def __init__(self, user):
        self.user = user

    def move(self, product, qty, kind, reference="", note=""):
        from .models import StockMovement
        return StockMovement.objects.create(
            owner=self.user, product=product, quantity=Decimal(str(qty)),
            kind=kind, reference=reference, note=note)

    def record_sale_items(self, clean_items, reference=""):
        """Valid sale invoice ke items par stock-out — product match:
        (hs_code + name==productDescription) ya sirf name. Match na ho to
        chup-chaap skip (inventory optional feature hai)."""
        from .models import Product
        for it in clean_items:
            desc = (it.get("productDescription") or "").strip()
            if not desc:
                continue
            q = Product.objects.filter(owner=self.user, is_active=True,
                                       track_stock=True, name=desc)
            hs = (it.get("hsCode") or "").strip()
            prod = (q.filter(hs_code=hs).first() if hs else None) or q.first()
            if prod:
                qty = Decimal(str(it.get("quantity", 0) or 0))
                if qty > 0:
                    self.move(prod, -qty, "sale", reference=reference)


class PurchaseService:
    """Purchase entry + input tax + stock-in."""

    def __init__(self, user):
        self.user = user

    def create(self, data, items):
        from .models import PurchaseInvoice, PurchaseItem, Supplier, Product
        if not items:
            raise SubmissionError("Add at least one item", simple=True)
        try:
            datetime.strptime(data.get("invoice_date", ""), "%Y-%m-%d")
        except (ValueError, TypeError):
            raise SubmissionError("Date must be in YYYY-MM-DD format", simple=True)

        tv = tst = Decimal("0")
        clean = []
        for it in items:
            v = Decimal(str(it.get("value_excl_st", 0) or 0))
            st = Decimal(str(it.get("sales_tax", 0) or 0))
            if v < 0 or st < 0:
                raise SubmissionError("Negative values are not allowed", simple=True)
            tv += v; tst += st
            clean.append(it)

        if not (data.get("supplier_name") or "").strip() and not data.get("supplier_id"):
            raise SubmissionError("Supplier name is required", simple=True)
        supplier = None
        if data.get("supplier_id"):
            supplier = Supplier.objects.filter(
                owner=self.user, pk=data["supplier_id"]).first()

        pi = PurchaseInvoice.objects.create(
            owner=self.user,
            seller_profile_id=data.get("seller_profile_id") or None,
            supplier=supplier,
            supplier_name=data.get("supplier_name", "") or
                          (supplier.business_name if supplier else ""),
            supplier_ntn_cnic=data.get("supplier_ntn_cnic", "") or
                              (supplier.ntn_cnic if supplier else ""),
            supplier_invoice_no=data.get("supplier_invoice_no", ""),
            invoice_date=data["invoice_date"],
            total_value=tv, total_sales_tax=tst,
            invoice_total=tv + tst,
            notes=data.get("notes", ""))

        inv_svc = InventoryService(self.user)
        for it in clean:
            product = None
            if it.get("product_id"):
                product = Product.objects.filter(
                    owner=self.user, pk=it["product_id"]).first()
            PurchaseItem.objects.create(
                purchase=pi, product=product,
                description=it.get("description", "") or
                            (product.name if product else ""),
                hs_code=it.get("hs_code", "") or
                        (product.hs_code if product else ""),
                uom=it.get("uom", "Numbers, pieces, units"),
                quantity=it.get("quantity", 1) or 1,
                value_excl_st=it.get("value_excl_st", 0) or 0,
                sales_tax=it.get("sales_tax", 0) or 0)
            if product and product.track_stock:
                inv_svc.move(product, Decimal(str(it.get("quantity", 0) or 0)),
                             "purchase",
                             reference=f"PI-{pi.pk}")
        return pi

    def input_tax_summary(self, business_id=None, period=None):
        from django.db.models import Sum, Count
        from .models import PurchaseInvoice
        qs = PurchaseInvoice.objects.filter(owner=self.user)
        if business_id:
            qs = qs.filter(seller_profile_id=business_id)
        if period:
            try:
                y, m = int(period[:4]), int(period[5:7])
                qs = qs.filter(invoice_date__year=y, invoice_date__month=m)
            except (ValueError, IndexError):
                pass
        agg = qs.aggregate(value=Sum("total_value"),
                           input_tax=Sum("total_sales_tax"), n=Count("id"))
        return {k: (v or 0) for k, v in agg.items()}


# --------------------------------------------------------------------------
# Monthly ATL Evidence (buyers + suppliers, per tax period)
# Further tax / input tax admissibility ke audit-proof ke liye.
# --------------------------------------------------------------------------
class ATLReportService:
    """Ek tax period ke SAB counterparties:
      - Buyers: us month ki VALID sale invoices se (grouped by NTN/CNIC)
      - Suppliers: us month ki purchase invoices se
    Har party ka ATL status (saved ATLStatus record) + FBR STATL API se
    on-demand check."""

    def __init__(self, user):
        self.user = user

    @staticmethod
    def _ym(period):
        return int(period[:4]), int(period[5:7])

    def month_report(self, period):
        from django.db.models import Count
        from .models import PurchaseInvoice, ATLStatus
        y, m = self._ym(period)

        buyers = list(
            Invoice.objects.filter(owner=self.user, status="valid",
                                   invoice_date__year=y, invoice_date__month=m)
            .values("buyer_ntn_cnic", "buyer_business_name",
                    "buyer_registration_type")
            .annotate(tx=Count("id")).order_by("-tx"))
        suppliers = list(
            PurchaseInvoice.objects.filter(owner=self.user,
                                           invoice_date__year=y,
                                           invoice_date__month=m)
            .values("supplier_ntn_cnic", "supplier_name")
            .annotate(tx=Count("id")).order_by("-tx"))

        regs = ({b["buyer_ntn_cnic"] for b in buyers if b["buyer_ntn_cnic"]} |
                {s["supplier_ntn_cnic"] for s in suppliers
                 if s["supplier_ntn_cnic"]})
        atl = {r.reg_no: r for r in ATLStatus.objects.filter(
            owner=self.user, period=period, reg_no__in=regs)}

        rows = []
        for b in buyers:
            rec = atl.get(b["buyer_ntn_cnic"])
            rows.append({
                "party_type": "Buyer",
                "name": b["buyer_business_name"] or "—",
                "reg_no": b["buyer_ntn_cnic"] or "",
                "reg_type": b["buyer_registration_type"],
                "tx": b["tx"], "tx_label": f'{b["tx"]} sale invoice(s)',
                "atl": rec.status if rec else None,
                "checked_at": rec.uploaded_at if rec else None,
                "pdf_pk": rec.pk if (rec and rec.evidence_pdf) else None,
                "verified": rec.verified if rec else False,
            })
        for s in suppliers:
            rec = atl.get(s["supplier_ntn_cnic"])
            rows.append({
                "party_type": "Supplier",
                "name": s["supplier_name"] or "—",
                "reg_no": s["supplier_ntn_cnic"] or "",
                "reg_type": "",
                "tx": s["tx"], "tx_label": f'{s["tx"]} purchase(s)',
                "atl": rec.status if rec else None,
                "checked_at": rec.uploaded_at if rec else None,
                "pdf_pk": rec.pk if (rec and rec.evidence_pdf) else None,
                "verified": rec.verified if rec else False,
            })
        return rows

    def check_party(self, reg_no, period):
        """FBR STATL API se status le kar us period ke against save karo."""
        from .reference_data import get_reference_client
        from .models import ATLStatus
        reg_no = (reg_no or "").strip()
        if not reg_no:
            raise SubmissionError("Registration number is empty", simple=True)
        y, m = self._ym(period)
        result = get_reference_client().statl_check(
            reg_no, date=f"{y:04d}-{m:02d}-01")
        raw = (result.get("statl_status") or result.get("status") or "")
        status = "Active" if "in" not in raw.lower().replace("-", "") else "Inactive"
        # "In-Active"/"Inactive" -> Inactive; "Active" -> Active
        if raw.lower().replace("-", "").startswith("inactive"):
            status = "Inactive"
        elif raw.lower().startswith("active"):
            status = "Active"
        rec, _ = ATLStatus.objects.update_or_create(
            owner=self.user, reg_no=reg_no, period=period,
            defaults={"status": status})
        return rec

    def check_all_missing(self, period):
        done, failed = 0, 0
        for row in self.month_report(period):
            if row["reg_no"] and not row["atl"]:
                try:
                    self.check_party(row["reg_no"], period)
                    done += 1
                except Exception:
                    failed += 1
        return done, failed
