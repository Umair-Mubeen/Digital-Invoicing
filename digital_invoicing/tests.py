"""
tests.py — Step 0 baseline suite.

Covers the pure business-rule modules (tax_engine, validators) plus the two
Step-0 bug fixes as regression tests (FBR client factory, buyers create).
Run: DB_ENGINE=sqlite python manage.py test
"""

from decimal import Decimal
from django.test import TestCase, Client, override_settings
from django.contrib.auth import get_user_model

from .tax_engine import compute_item
from .validators import validate_invoice
from .fbr_client import get_fbr_client, MockFBRClient, RealFBRClient
from .models import SellerProfile, Buyer


class TaxEngineTests(TestCase):
    def test_standard_rate_registered_buyer(self):
        r = compute_item("Goods at standard rate", 1000)
        self.assertEqual(r["sales_tax"], Decimal("180.00"))
        self.assertEqual(r["further_tax"], Decimal("0.00"))
        self.assertEqual(r["rate"], "18%")

    def test_further_tax_unregistered_buyer(self):
        r = compute_item("Goods at standard rate", 1000, buyer_unregistered=True)
        self.assertEqual(r["further_tax"], Decimal("40.00"))  # 4% Sec 3(1A)

    def test_further_tax_exempt_hs_sro648(self):
        # Fertilizer (3102...) — SRO 648(I)/2013 exemption
        r = compute_item("Goods at standard rate", 1000,
                         buyer_unregistered=True, hs_code="3102.1000")
        self.assertEqual(r["further_tax"], Decimal("0.00"))

    def test_third_schedule_uses_retail_price(self):
        r = compute_item("3rd Schedule Goods", 800, retail_price=1000)
        self.assertEqual(r["sales_tax"], Decimal("180.00"))  # 18% of MRP, not value
        self.assertEqual(r["further_tax"], Decimal("0.00"))  # no FT on 3rd Sched

    def test_exempt_and_zero_rated(self):
        self.assertEqual(compute_item("Exempt Goods", 1000)["sales_tax"], Decimal("0.00"))
        self.assertEqual(compute_item("Zero-rated Goods", 1000)["sales_tax"], Decimal("0.00"))

    def test_unknown_sale_type_raises(self):
        with self.assertRaises(ValueError):
            compute_item("Nonexistent Type", 1000)


class ValidatorTests(TestCase):
    def _base(self, **over):
        p = {
            "invoiceType": "Sale Invoice", "invoiceDate": "2026-07-01",
            "sellerNTNCNIC": "1234567", "sellerProvince": "Sindh",
            "buyerNTNCNIC": "7654321", "buyerBusinessName": "Test Buyer",
            "buyerProvince": "Sindh", "buyerRegistrationType": "Registered",
            "items": [{"saleType": "Goods at standard rate", "hsCode": "0101.2100",
                       "rate": "18%", "quantity": 1,
                       "valueSalesExcludingST": 1000, "salesTaxApplicable": 180}],
        }
        p.update(over)
        return p

    def _codes(self, p):
        return {e["errorCode"] for e in validate_invoice(p)}

    def test_valid_payload_passes(self):
        self.assertEqual(validate_invoice(self._base()), [])

    def test_bad_buyer_reg_format_0002(self):
        self.assertIn("0002", self._codes(self._base(buyerNTNCNIC="12AB")))

    def test_self_invoicing_0058(self):
        self.assertIn("0058", self._codes(self._base(buyerNTNCNIC="1234567")))

    def test_debit_note_needs_ref_0026(self):
        self.assertIn("0026", self._codes(self._base(invoiceType="Debit Note")))

    def test_future_date_0043(self):
        self.assertIn("0043", self._codes(self._base(invoiceDate="2099-01-01")))

    def test_st_mismatch_0104(self):
        p = self._base()
        p["items"][0]["salesTaxApplicable"] = 50   # should be 180
        self.assertIn("0104", self._codes(p))


@override_settings(FBR_USE_MOCK=False, FBR_API_TOKEN="")
class FBRClientFactoryTests(TestCase):
    """Regression: get_fbr_client(profile) — per-profile token/sandbox routing."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("u1", password="x")

    def _profile(self, token="tok-123", sandbox=True):
        return SellerProfile.objects.create(
            user=self.user, ntn_cnic="1234567", business_name="Biz",
            province="Sindh", address="Karachi",
            fbr_token=token, use_sandbox=sandbox)

    def test_profile_sandbox_routing(self):
        c = get_fbr_client(self._profile(sandbox=True))
        self.assertIsInstance(c, RealFBRClient)
        self.assertIn("_sb", c.post_url)
        self.assertTrue(c.is_sandbox)

    def test_profile_production_routing(self):
        c = get_fbr_client(self._profile(sandbox=False))
        self.assertIsInstance(c, RealFBRClient)
        self.assertNotIn("_sb", c.post_url)
        self.assertFalse(c.is_sandbox)

    def test_no_token_falls_back_to_mock(self):
        c = get_fbr_client(self._profile(token=""))
        self.assertIsInstance(c, MockFBRClient)

    @override_settings(FBR_USE_MOCK=True)
    def test_mock_flag_wins(self):
        self.assertIsInstance(get_fbr_client(self._profile()), MockFBRClient)


class BuyersViewRegressionTests(TestCase):
    """Regression: buyers-page create crashed with unexpected kwargs."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("u2", password="x")
        self.c = Client()
        self.c.force_login(self.user)

    def test_create_buyer_succeeds(self):
        resp = self.c.post("/digital-invoicing/buyers/", {
            "business_name": "New Buyer", "ntn_cnic": "7654321",
            "strn": "", "registration_type": "Registered",
            "province": "Sindh", "address": "Karachi",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Buyer.objects.filter(owner=self.user,
                                             business_name="New Buyer").exists())


class SubmitInvoiceSmokeTests(TestCase):
    """End-to-end submit via MockFBRClient (default settings)."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("u3", password="x")
        SellerProfile.objects.create(
            user=self.user, ntn_cnic="1234567", business_name="Biz",
            province="Sindh", address="Karachi")
        self.c = Client()
        self.c.force_login(self.user)

    def test_valid_sale_invoice(self):
        resp = self.c.post("/digital-invoicing/submit/", data={
            "invoiceType": "Sale Invoice", "invoiceDate": "2026-07-01",
            "buyerNTNCNIC": "7654321", "buyerBusinessName": "Buyer Co",
            "buyerProvince": "Sindh", "buyerAddress": "Karachi",
            "buyerRegistrationType": "Registered",
            "items": [{"saleType": "Goods at standard rate",
                       "hsCode": "0101.2100", "productDescription": "Test",
                       "uoM": "Numbers, pieces, units", "quantity": 1,
                       "valueSalesExcludingST": 1000}],
        }, content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(body["totals"]["salesTax"], 180.0)


class Phase2SchemaTests(TestCase):
    """Phase 2 DB changes: unique FBR number, public_id, 4-decimal qty, new fields."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("u4", password="x")
        SellerProfile.objects.create(
            user=self.user, ntn_cnic="1234567", business_name="Biz",
            province="Sindh", address="Karachi")
        self.c = Client()
        self.c.force_login(self.user)

    def _submit(self, qty=1):
        return self.c.post("/digital-invoicing/submit/", data={
            "invoiceType": "Sale Invoice", "invoiceDate": "2026-07-01",
            "buyerNTNCNIC": "7654321", "buyerBusinessName": "Buyer Co",
            "buyerProvince": "Sindh", "buyerAddress": "Karachi",
            "buyerRegistrationType": "Registered",
            "items": [{"saleType": "Goods at standard rate",
                       "hsCode": "0101.2100", "productDescription": "Test",
                       "uoM": "Numbers, pieces, units", "quantity": qty,
                       "valueSalesExcludingST": 1000,
                       "salesTaxWithheldAtSource": 25, "discount": 10,
                       "extraTax": 0, "fedPayable": 0}],
        }, content_type="application/json")

    def test_public_id_and_new_item_fields_persisted(self):
        from .models import Invoice
        self.assertTrue(self._submit().json()["ok"])
        inv = Invoice.objects.latest("created_at")
        self.assertIsNotNone(inv.public_id)
        item = inv.items.first()
        self.assertEqual(item.sales_tax_withheld, Decimal("25.00"))
        self.assertEqual(item.discount, Decimal("10.00"))

    def test_quantity_four_decimals(self):
        from .models import Invoice
        self.assertTrue(self._submit(qty=1.0625).json()["ok"])
        item = Invoice.objects.latest("created_at").items.first()
        self.assertEqual(item.quantity, Decimal("1.0625"))

    def test_duplicate_fbr_number_rejected_but_null_ok(self):
        from django.db import IntegrityError, transaction
        from .models import Invoice

        def mk(num):
            return Invoice(owner=self.user, invoice_type="Sale Invoice",
                           invoice_date="2026-07-01", seller_ntn_cnic="1234567",
                           seller_business_name="B", seller_province="Sindh",
                           seller_address="K", buyer_business_name="X",
                           buyer_province="Sindh", fbr_invoice_number=num)
        mk("1234567DI111").save()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                mk("1234567DI111").save()
        mk("").save()   # "" -> None via save()
        mk(None).save() # multiple NULLs allowed
        self.assertEqual(Invoice.objects.filter(fbr_invoice_number=None).count(), 2)


class ServiceLayerTests(TestCase):
    """Phase 5: business rules directly testable — no HTTP needed."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("u5", password="x")
        self.profile = SellerProfile.objects.create(
            user=self.user, ntn_cnic="1234567", business_name="Biz",
            province="Sindh", address="Karachi")

    def _payload(self, **over):
        p = {"invoiceType": "Sale Invoice", "invoiceDate": "2026-07-01",
             "buyerNTNCNIC": "7654321", "buyerBusinessName": "B Co",
             "buyerProvince": "Sindh", "buyerAddress": "K",
             "buyerRegistrationType": "Registered",
             "items": [{"saleType": "Goods at standard rate",
                        "hsCode": "0101.2100", "quantity": 1,
                        "valueSalesExcludingST": 1000}]}
        p.update(over)
        return p

    def test_submit_success_via_service(self):
        from .services import InvoiceSubmissionService
        body = InvoiceSubmissionService(self.user).submit(self._payload())
        self.assertTrue(body["ok"])
        self.assertEqual(body["totals"]["salesTax"], 180.0)

    def test_debit_note_missing_ref_raises_0026(self):
        from .services import InvoiceSubmissionService, SubmissionError
        with self.assertRaises(SubmissionError) as cm:
            InvoiceSubmissionService(self.user).submit(
                self._payload(invoiceType="Debit Note", reason="Return"))
        self.assertEqual(cm.exception.code, "0026")

    def test_bad_date_raises_0113(self):
        from .services import InvoiceSubmissionService, SubmissionError
        with self.assertRaises(SubmissionError) as cm:
            InvoiceSubmissionService(self.user).submit(
                self._payload(invoiceDate="01-07-2026"))
        self.assertEqual(cm.exception.code, "0113")

    def test_no_business_simple_error(self):
        from .services import InvoiceSubmissionService, SubmissionError
        u2 = get_user_model().objects.create_user("u5b", password="x")
        with self.assertRaises(SubmissionError) as cm:
            InvoiceSubmissionService(u2).submit(self._payload())
        self.assertTrue(cm.exception.simple)

    def test_debit_note_0067_exceeds_reference(self):
        from .services import InvoiceSubmissionService, SubmissionError
        from .models import Invoice
        Invoice.objects.create(
            owner=self.user, invoice_type="Sale Invoice",
            invoice_date="2026-06-01", seller_ntn_cnic="1234567",
            seller_business_name="Biz", seller_province="Sindh",
            seller_address="K", buyer_business_name="B Co",
            buyer_province="Sindh", status="valid",
            fbr_invoice_number="1234567DIREF1",
            total_value=500, total_sales_tax=90, invoice_total=590)
        with self.assertRaises(SubmissionError) as cm:
            InvoiceSubmissionService(self.user).submit(self._payload(
                invoiceType="Debit Note", invoiceRefNo="1234567DIREF1",
                reason="Price increase"))
        self.assertEqual(cm.exception.code, "0067")


class TaxRuleTableTests(TestCase):
    """Phase 7: Finance Act changes = data change, code untouched."""

    def setUp(self):
        from .tax_engine import invalidate_rules_cache
        invalidate_rules_cache()

    def tearDown(self):
        from .tax_engine import invalidate_rules_cache
        invalidate_rules_cache()

    def test_seeded_rules_match_hardcoded_parity(self):
        from .models import TaxSaleType, TaxScenario, FurtherTaxExemptHS
        self.assertEqual(
            TaxSaleType.objects.filter(is_active=True).values("name")
            .distinct().count(), 6)
        self.assertEqual(TaxScenario.objects.count(), 28)
        self.assertTrue(FurtherTaxExemptHS.objects
                        .filter(hs_prefix="3102").exists())
        r = compute_item("Goods at standard rate", 1000)
        self.assertEqual(r["sales_tax"], Decimal("180.00"))  # DB path, same result

    def test_budget_rate_change_without_code(self):
        """FY27 budget: standard rate 18% -> 19% sirf admin/data se."""
        from datetime import date
        from .models import TaxSaleType
        from .tax_engine import invalidate_rules_cache
        row = TaxSaleType.objects.get(name="Goods at standard rate")
        row.rate = Decimal("19")
        row.save()
        invalidate_rules_cache()
        r = compute_item("Goods at standard rate", 1000)
        self.assertEqual(r["sales_tax"], Decimal("190.00"))
        self.assertEqual(r["rate"], "19%")

    def test_date_effective_future_rate_not_applied_today(self):
        """Naya rate future effective_from ke saath — aaj apply NA ho,
        us tareekh par ho."""
        from datetime import date, timedelta
        from .models import TaxSaleType
        from .tax_engine import invalidate_rules_cache, load_rules
        future = date.today() + timedelta(days=30)
        TaxSaleType.objects.create(
            name="Goods at standard rate", rate=Decimal("20"),
            charges_st=True, further_tax_applies=True,
            effective_from=future)
        invalidate_rules_cache()
        today_r = compute_item("Goods at standard rate", 1000)
        self.assertEqual(today_r["sales_tax"], Decimal("180.00"))
        future_r = compute_item("Goods at standard rate", 1000, on_date=future)
        self.assertEqual(future_r["sales_tax"], Decimal("200.00"))

    def test_ft_exemption_from_db(self):
        """Nayi HS exemption admin se add — further tax rukh jaye."""
        from datetime import date
        from .models import FurtherTaxExemptHS
        from .tax_engine import invalidate_rules_cache
        FurtherTaxExemptHS.objects.create(
            hs_prefix="9999", effective_from=date(2024, 7, 1),
            sro_reference="Test SRO")
        invalidate_rules_cache()
        r = compute_item("Goods at standard rate", 1000,
                         buyer_unregistered=True, hs_code="9999.0000")
        self.assertEqual(r["further_tax"], Decimal("0.00"))

    def test_empty_tables_fallback_to_hardcoded(self):
        from .models import TaxSaleType
        from .tax_engine import invalidate_rules_cache
        TaxSaleType.objects.all().delete()
        invalidate_rules_cache()
        r = compute_item("Goods at standard rate", 1000)
        self.assertEqual(r["sales_tax"], Decimal("180.00"))  # fallback parity


class Phase8FBRTests(TestCase):
    """Phase 8: validate endpoint, resubmit, cancellation tracking."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("u8", password="x")
        self.profile = SellerProfile.objects.create(
            user=self.user, ntn_cnic="1234567", business_name="Biz",
            province="Sindh", address="Karachi")
        self.c = Client()
        self.c.force_login(self.user)

    def _payload(self, **over):
        p = {"invoiceType": "Sale Invoice", "invoiceDate": "2026-07-01",
             "buyerNTNCNIC": "7654321", "buyerBusinessName": "B Co",
             "buyerProvince": "Sindh", "buyerAddress": "K",
             "buyerRegistrationType": "Registered",
             "items": [{"saleType": "Goods at standard rate",
                        "hsCode": "0101.2100", "quantity": 1,
                        "valueSalesExcludingST": 1000}]}
        p.update(over)
        return p

    def test_validate_endpoint_no_invoice_number_no_persist(self):
        from .models import Invoice
        r = self.c.post("/digital-invoicing/validate/",
                        data=self._payload(), content_type="application/json")
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertNotIn("invoiceNumber", body)          # spec 4.2: number nahi
        self.assertEqual(Invoice.objects.count(), 0)     # kuch save nahi

    def test_validate_endpoint_reports_errors(self):
        r = self.c.post("/digital-invoicing/validate/",
                        data=self._payload(buyerBusinessName=""),
                        content_type="application/json")
        self.assertFalse(r.json()["ok"])

    def _make_failed_invoice(self):
        from .models import Invoice
        payload = self._payload()
        payload["sellerNTNCNIC"] = "1234567"
        payload["sellerBusinessName"] = "Biz"
        payload["sellerProvince"] = "Sindh"
        payload["sellerAddress"] = "Karachi"
        payload["items"][0].update({"rate": "18%", "salesTaxApplicable": 180.0,
                                    "furtherTax": 0.0, "uoM": "Numbers, pieces, units",
                                    "productDescription": "Test"})
        return Invoice.objects.create(
            owner=self.user, seller_profile=self.profile,
            invoice_type="Sale Invoice", invoice_date="2026-07-01",
            seller_ntn_cnic="1234567", seller_business_name="Biz",
            seller_province="Sindh", seller_address="Karachi",
            buyer_ntn_cnic="7654321", buyer_business_name="B Co",
            buyer_province="Sindh", buyer_registration_type="Registered",
            status="failed", fbr_payload=payload,
            total_value=1000, total_sales_tax=180, invoice_total=1180)

    def test_resubmit_failed_invoice_becomes_valid(self):
        inv = self._make_failed_invoice()
        r = self.c.post(f"/digital-invoicing/invoices/{inv.pk}/resubmit/")
        body = r.json()
        self.assertTrue(body["ok"], body)
        inv.refresh_from_db()
        self.assertEqual(inv.status, "valid")
        self.assertTrue(inv.fbr_invoice_number)
        self.assertIsNotNone(inv.submitted_at)

    def test_resubmit_valid_invoice_rejected(self):
        inv = self._make_failed_invoice()
        inv.status = "valid"; inv.save()
        r = self.c.post(f"/digital-invoicing/invoices/{inv.pk}/resubmit/")
        self.assertEqual(r.status_code, 400)

    def test_cancel_within_72h(self):
        from django.utils import timezone
        inv = self._make_failed_invoice()
        inv.status = "valid"; inv.submitted_at = timezone.now(); inv.save()
        r = self.c.post(f"/digital-invoicing/invoices/{inv.pk}/cancel/")
        self.assertTrue(r.json()["ok"])
        inv.refresh_from_db()
        self.assertEqual(inv.status, "cancelled")

    def test_cancel_after_72h_blocked(self):
        from django.utils import timezone
        from datetime import timedelta
        inv = self._make_failed_invoice()
        inv.status = "valid"
        inv.submitted_at = timezone.now() - timedelta(hours=73)
        inv.save()
        r = self.c.post(f"/digital-invoicing/invoices/{inv.pk}/cancel/")
        self.assertEqual(r.status_code, 400)
        inv.refresh_from_db()
        self.assertEqual(inv.status, "valid")

    def test_other_users_invoice_protected(self):
        inv = self._make_failed_invoice()
        other = get_user_model().objects.create_user("u8b", password="x")
        c2 = Client(); c2.force_login(other)
        r = c2.post(f"/digital-invoicing/invoices/{inv.pk}/resubmit/")
        self.assertEqual(r.status_code, 400)


class ReportTests(TestCase):
    """Phase 14: tax summary, buyer report, Annex-C CSV — owner-isolated."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("u14", password="x")
        SellerProfile.objects.create(
            user=self.user, ntn_cnic="1234567", business_name="Biz",
            province="Sindh", address="Karachi")
        self.c = Client()
        self.c.force_login(self.user)
        # 2 valid + 1 failed via real submit path (mock client)
        for buyer, ok_name in (("7654321", "Alpha"), ("7654321", "Alpha"),
                               ("", "")):
            self.c.post("/digital-invoicing/submit/", data={
                "invoiceType": "Sale Invoice", "invoiceDate": "2026-07-05",
                "buyerNTNCNIC": buyer, "buyerBusinessName": ok_name,
                "buyerProvince": "Sindh", "buyerAddress": "K",
                "buyerRegistrationType": "Registered",
                "items": [{"saleType": "Goods at standard rate",
                           "hsCode": "0101.2100", "quantity": 1,
                           "productDescription": "T",
                           "uoM": "Numbers, pieces, units",
                           "valueSalesExcludingST": 1000}],
            }, content_type="application/json")

    def test_tax_summary_counts_only_valid(self):
        from .services import ReportService
        s = ReportService(self.user).tax_summary(period="2026-07")
        self.assertEqual(s["totals"]["n"], 2)
        self.assertEqual(float(s["totals"]["st"]), 360.0)

    def test_reports_page_renders(self):
        r = self.c.get("/digital-invoicing/reports/?period=2026-07")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Sales Register CSV")

    def test_sales_register_csv(self):
        r = self.c.get(
            "/digital-invoicing/reports/sales-register.csv?period=2026-07")
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn("Invoice No (FBR)", body)
        self.assertEqual(body.count("0101.2100"), 2)   # sirf valid items

    def test_reports_owner_isolated(self):
        from .services import ReportService
        other = get_user_model().objects.create_user("u14b", password="x")
        s = ReportService(other).tax_summary(period="2026-07")
        self.assertEqual(s["totals"]["n"], 0)


class ERPModuleTests(TestCase):
    """Phases 9–12: products, inventory, suppliers, purchases, input tax."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("u9", password="x")
        self.profile = SellerProfile.objects.create(
            user=self.user, ntn_cnic="1234567", business_name="Biz",
            province="Sindh", address="Karachi")
        self.c = Client()
        self.c.force_login(self.user)

    def _make_product(self, name="Urea Bag", price=100):
        from .models import Product
        return Product.objects.create(
            owner=self.user, name=name, hs_code="3102.1000",
            sale_type="Goods at standard rate", default_price=price)

    def test_product_crud_page(self):
        r = self.c.post("/digital-invoicing/products/", {
            "name": "Cement Bag", "sku": "CEM-01", "hs_code": "2523.2900",
            "sale_type": "3rd Schedule Goods", "uom": "KG",
            "default_price": "1250", "category": "Construction",
            "brand": "Lucky", "track_stock": "on"})
        self.assertEqual(r.status_code, 200)
        from .models import Product, Category, Brand
        p = Product.objects.get(owner=self.user, name="Cement Bag")
        self.assertEqual(p.category.name, "Construction")
        self.assertEqual(p.brand.name, "Lucky")

    def test_stock_adjustment_and_balance(self):
        from .services import InventoryService
        p = self._make_product()
        svc = InventoryService(self.user)
        svc.move(p, 50, "adjustment")
        svc.move(p, -20, "sale")
        self.assertEqual(float(p.stock), 30.0)

    def test_purchase_creates_input_tax_and_stock_in(self):
        from .services import PurchaseService
        p = self._make_product()
        pi = PurchaseService(self.user).create(
            {"invoice_date": "2026-07-03", "supplier_name": "ABC Traders",
             "seller_profile_id": self.profile.pk},
            [{"product_id": p.pk, "description": "Urea Bag",
              "quantity": 10, "value_excl_st": 1000, "sales_tax": 180}])
        self.assertEqual(float(pi.total_sales_tax), 180.0)
        self.assertEqual(float(pi.invoice_total), 1180.0)
        self.assertEqual(float(p.stock), 10.0)   # stock-in

    def test_purchase_negative_rejected(self):
        from .services import PurchaseService, SubmissionError
        with self.assertRaises(SubmissionError):
            PurchaseService(self.user).create(
                {"invoice_date": "2026-07-03", "supplier_name": "X"},
                [{"description": "Bad", "value_excl_st": -5, "sales_tax": 0}])

    def test_valid_sale_auto_stock_out(self):
        p = self._make_product(name="Widget")
        from .services import InventoryService
        InventoryService(self.user).move(p, 100, "adjustment")
        r = self.c.post("/digital-invoicing/submit/", data={
            "invoiceType": "Sale Invoice", "invoiceDate": "2026-07-05",
            "buyerNTNCNIC": "7654321", "buyerBusinessName": "B Co",
            "buyerProvince": "Sindh", "buyerAddress": "K",
            "buyerRegistrationType": "Registered",
            "items": [{"saleType": "Goods at standard rate",
                       "hsCode": "3102.1000", "productDescription": "Widget",
                       "uoM": "Numbers, pieces, units", "quantity": 7,
                       "valueSalesExcludingST": 700}],
        }, content_type="application/json")
        self.assertTrue(r.json()["ok"])
        self.assertEqual(float(p.stock), 93.0)

    def test_reports_net_payable_with_input_tax(self):
        from .services import PurchaseService
        # output: 1 valid sale ST=180
        self.c.post("/digital-invoicing/submit/", data={
            "invoiceType": "Sale Invoice", "invoiceDate": "2026-07-05",
            "buyerNTNCNIC": "7654321", "buyerBusinessName": "B",
            "buyerProvince": "Sindh", "buyerAddress": "K",
            "buyerRegistrationType": "Registered",
            "items": [{"saleType": "Goods at standard rate",
                       "hsCode": "0101.2100", "productDescription": "T",
                       "uoM": "Numbers, pieces, units", "quantity": 1,
                       "valueSalesExcludingST": 1000}],
        }, content_type="application/json")
        # input: purchase ST=80
        PurchaseService(self.user).create(
            {"invoice_date": "2026-07-10", "supplier_name": "S"},
            [{"description": "Raw", "quantity": 1,
              "value_excl_st": 500, "sales_tax": 80}])
        r = self.c.get("/digital-invoicing/reports/?period=2026-07")
        self.assertContains(r, "Input tax")
        self.assertEqual(r.context["net_payable"], 100.0)   # 180 − 80

    def test_supplier_page_and_purchases_page_render(self):
        self.c.post("/digital-invoicing/suppliers/", {
            "business_name": "ABC Traders", "ntn_cnic": "1111111",
            "registration_type": "Registered", "province": "Sindh"})
        from .models import Supplier
        self.assertTrue(Supplier.objects.filter(
            owner=self.user, business_name="ABC Traders").exists())
        self.assertEqual(
            self.c.get("/digital-invoicing/purchases/").status_code, 200)
        self.assertEqual(
            self.c.get("/digital-invoicing/inventory/").status_code, 200)

    def test_modules_owner_isolated(self):
        p = self._make_product()
        other = get_user_model().objects.create_user("u9b", password="x")
        c2 = Client(); c2.force_login(other)
        r = c2.get("/digital-invoicing/products/")
        self.assertNotContains(r, "Urea Bag")


class Phase16HardeningTests(TestCase):
    """Security hardening: token encryption, login throttle, login page."""

    def test_token_encrypted_at_rest_and_decrypts(self):
        from .models import SellerProfile
        from .crypto import decrypt
        u = get_user_model().objects.create_user("u16", password="x")
        sp = SellerProfile.objects.create(
            user=u, ntn_cnic="1234567", business_name="Biz",
            province="Sindh", address="K", fbr_token="secret-token-123")
        sp.refresh_from_db()
        self.assertTrue(sp.fbr_token.startswith("enc$"))       # DB: ciphertext
        self.assertNotIn("secret-token-123", sp.fbr_token)
        self.assertEqual(sp.fbr_token_plain, "secret-token-123")  # app: plain

    def test_factory_uses_decrypted_token(self):
        from django.test import override_settings
        from .models import SellerProfile
        from .fbr_client import get_fbr_client, RealFBRClient
        u = get_user_model().objects.create_user("u16b", password="x")
        sp = SellerProfile.objects.create(
            user=u, ntn_cnic="1234567", business_name="Biz",
            province="Sindh", address="K", fbr_token="tok-xyz")
        sp.refresh_from_db()
        with override_settings(FBR_USE_MOCK=False):
            c = get_fbr_client(sp)
        self.assertIsInstance(c, RealFBRClient)
        self.assertEqual(c.token, "tok-xyz")                   # decrypted

    def test_legacy_plaintext_dual_read(self):
        from .crypto import decrypt
        self.assertEqual(decrypt("old-plain-token"), "old-plain-token")

    def test_login_page_renders(self):
        r = Client().get("/digital-invoicing/login/")
        self.assertEqual(r.status_code, 200)

    def test_login_throttle_locks_after_5_fails(self):
        from django.core.cache import cache
        cache.clear()
        get_user_model().objects.create_user("victim", password="right")
        c = Client()
        for _ in range(5):
            c.post("/digital-invoicing/login/",
                   {"username": "victim", "password": "wrong"})
        r = c.post("/digital-invoicing/login/",
                   {"username": "victim", "password": "right"})
        self.assertContains(r, "10 minute")     # locked even w/ right password
        cache.clear()


class UIOverhaulTests(TestCase):
    """UI: sidebar links, branding, charts data, mode chip."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("uui", password="x")
        SellerProfile.objects.create(
            user=self.user, ntn_cnic="1234567", business_name="Biz",
            province="Sindh", address="Karachi")
        self.c = Client()
        self.c.force_login(self.user)

    def test_branding_taxbuddy_umair(self):
        r = self.c.get("/digital-invoicing/dashboard/")
        self.assertContains(r, "TaxBuddy Umair")

    def test_sidebar_has_all_module_links(self):
        r = self.c.get("/digital-invoicing/dashboard/")
        for url in ("/digital-invoicing/reports/", "/digital-invoicing/products/",
                    "/digital-invoicing/inventory/", "/digital-invoicing/purchases/",
                    "/digital-invoicing/suppliers/", "/digital-invoicing/buyers/"):
            self.assertContains(r, url)

    def test_dashboard_has_charts(self):
        r = self.c.get("/digital-invoicing/dashboard/")
        self.assertContains(r, "chTrend")
        self.assertContains(r, "chStatus")
        self.assertContains(r, "chBuyers")
        self.assertContains(r, "chart.umd.min.js")
        import json
        data = json.loads(r.context["chart"])
        self.assertEqual(len(data["labels"]), 6)

    def test_mode_chip_mock_vs_live(self):
        from django.test import override_settings
        r = self.c.get("/digital-invoicing/dashboard/")
        self.assertContains(r, "MOCK MODE")
        with override_settings(FBR_USE_MOCK=False):
            r = self.c.get("/digital-invoicing/dashboard/")
            self.assertContains(r, "LIVE — FBR")

    def test_reports_page_has_tax_chart(self):
        r = self.c.get("/digital-invoicing/reports/")
        self.assertContains(r, "chTax")


class DashboardChartPlacementTests(TestCase):
    """Regression: charts di_content mein hon, sidebar (na_dash) mein nahi."""

    def test_charts_render_after_sidebar_inside_main(self):
        u = get_user_model().objects.create_user("upl", password="x")
        SellerProfile.objects.create(
            user=u, ntn_cnic="1234567", business_name="Biz",
            province="Sindh", address="K")
        c = Client(); c.force_login(u)
        html = c.get("/digital-invoicing/dashboard/").content.decode()
        # chartgrid sidebar band hone (</aside>) ke BAAD aana chahiye
        self.assertIn("</aside>", html)
        self.assertLess(html.index("</aside>"), html.index('class="chartgrid"'))
        # aur "Recent activity" se pehle
        self.assertLess(html.index('class="chartgrid"'), html.index("Recent activity"))
        # sidebar active link clean ho — class attr mein chartgrid na ho
        aside = html[:html.index("</aside>")]
        self.assertNotIn('class="chartgrid"', aside)


class FilterTests(TestCase):
    """Card tables + filters: invoices (type/status/date), products, buyers."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("uflt", password="x")
        SellerProfile.objects.create(
            user=self.user, ntn_cnic="1234567", business_name="Biz",
            province="Sindh", address="K")
        self.c = Client()
        self.c.force_login(self.user)
        for d, ntn, buyer in (("2026-06-10", "7654321", "Alpha Traders"),
                              ("2026-07-05", "7654322", "Beta Corp")):
            self.c.post("/digital-invoicing/submit/", data={
                "invoiceType": "Sale Invoice", "invoiceDate": d,
                "buyerNTNCNIC": ntn, "buyerBusinessName": buyer,
                "buyerProvince": "Sindh", "buyerAddress": "K",
                "buyerRegistrationType": "Registered",
                "items": [{"saleType": "Goods at standard rate",
                           "hsCode": "0101.2100", "productDescription": "T",
                           "uoM": "Numbers, pieces, units", "quantity": 1,
                           "valueSalesExcludingST": 1000}],
            }, content_type="application/json")

    def test_invoice_date_range_filter(self):
        r = self.c.get("/digital-invoicing/invoices/?from=2026-07-01&to=2026-07-31")
        self.assertContains(r, "Beta Corp")
        self.assertNotContains(r, "Alpha Traders")

    def test_invoice_type_and_status_filter(self):
        r = self.c.get("/digital-invoicing/invoices/?type=Debit+Note")
        self.assertNotContains(r, "Beta Corp")
        r = self.c.get("/digital-invoicing/invoices/?status=valid&q=Alpha")
        self.assertContains(r, "Alpha Traders")

    def test_pager_preserves_filters(self):
        r = self.c.get("/digital-invoicing/invoices/?status=valid")
        self.assertEqual(r.context["qstring"], "status=valid")

    def test_products_filters(self):
        from .models import Product, Category
        cat = Category.objects.create(owner=self.user, name="Fertilizer")
        Product.objects.create(owner=self.user, name="Urea Bag",
                               sku="UR-1", category=cat)
        Product.objects.create(owner=self.user, name="Cement Bag")
        r = self.c.get(f"/digital-invoicing/products/?cat={cat.pk}")
        self.assertContains(r, "Urea Bag")
        self.assertNotContains(r, "Cement Bag")
        r = self.c.get("/digital-invoicing/products/?q=UR-1")
        self.assertContains(r, "Urea Bag")

    def test_buyers_filter(self):
        r = self.c.get("/digital-invoicing/buyers/?q=Alpha")
        self.assertContains(r, "Alpha Traders")
        self.assertNotContains(r, "Beta Corp")

    def test_purchases_filter(self):
        from .services import PurchaseService
        PurchaseService(self.user).create(
            {"invoice_date": "2026-07-03", "supplier_name": "Karachi Steel"},
            [{"description": "Rod", "quantity": 1,
              "value_excl_st": 100, "sales_tax": 18}])
        r = self.c.get("/digital-invoicing/purchases/?q=Karachi")
        self.assertContains(r, "Karachi Steel")
        r = self.c.get("/digital-invoicing/purchases/?q=Lahore")
        self.assertNotContains(r, "Karachi Steel")


class ATLMonthlyTests(TestCase):
    """Monthly ATL evidence: report rows, STATL check, PDF download."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("uatl", password="x")
        SellerProfile.objects.create(
            user=self.user, ntn_cnic="1234567", business_name="Biz",
            province="Sindh", address="K")
        self.c = Client()
        self.c.force_login(self.user)
        # July: buyer ko 2 sales, supplier se 1 purchase
        for _ in range(2):
            self.c.post("/digital-invoicing/submit/", data={
                "invoiceType": "Sale Invoice", "invoiceDate": "2026-07-05",
                "buyerNTNCNIC": "7654321", "buyerBusinessName": "Alpha",
                "buyerProvince": "Sindh", "buyerAddress": "K",
                "buyerRegistrationType": "Registered",
                "items": [{"saleType": "Goods at standard rate",
                           "hsCode": "0101.2100", "productDescription": "T",
                           "uoM": "Numbers, pieces, units", "quantity": 1,
                           "valueSalesExcludingST": 1000}],
            }, content_type="application/json")
        from .services import PurchaseService
        PurchaseService(self.user).create(
            {"invoice_date": "2026-07-10", "supplier_name": "Steel Co",
             "supplier_ntn_cnic": "1111111"},
            [{"description": "Rod", "quantity": 1,
              "value_excl_st": 500, "sales_tax": 90}])

    def test_month_report_rows(self):
        from .services import ATLReportService
        rows = ATLReportService(self.user).month_report("2026-07")
        self.assertEqual(len(rows), 2)
        buyer = next(r for r in rows if r["party_type"] == "Buyer")
        supp = next(r for r in rows if r["party_type"] == "Supplier")
        self.assertEqual(buyer["tx"], 2)
        self.assertEqual(supp["reg_no"], "1111111")
        self.assertIsNone(buyer["atl"])            # abhi check nahi hua

    def test_check_party_saves_status(self):
        from .services import ATLReportService
        from .models import ATLStatus
        rec = ATLReportService(self.user).check_party("7654321", "2026-07")
        self.assertIn(rec.status, ("Active", "Inactive"))
        self.assertTrue(ATLStatus.objects.filter(
            owner=self.user, reg_no="7654321", period="2026-07").exists())

    def test_check_all_endpoint(self):
        r = self.c.post("/digital-invoicing/atl/check/",
                        {"all": "1", "period": "2026-07"})
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["done"], 2)
        # ab report mein statuses filled
        r = self.c.get("/digital-invoicing/atl/?period=2026-07")
        self.assertNotContains(r, "Not checked")

    def test_pdf_downloads(self):
        self.c.post("/digital-invoicing/atl/check/",
                    {"all": "1", "period": "2026-07"})
        r = self.c.get("/digital-invoicing/atl/report.pdf?period=2026-07")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertIn("ATL-Evidence-2026-07.pdf", r["Content-Disposition"])
        self.assertTrue(r.content.startswith(b"%PDF"))
        self.assertGreater(len(r.content), 1500)

    def test_owner_isolated(self):
        other = get_user_model().objects.create_user("uatl2", password="x")
        c2 = Client(); c2.force_login(other)
        r = c2.get("/digital-invoicing/atl/?period=2026-07")
        self.assertNotContains(r, "Alpha")


class ATLEvidencePDFTests(TestCase):
    """Per-party per-month FBR ATL PDF upload/save/view."""

    PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<<>>\n%%EOF"

    def setUp(self):
        self.user = get_user_model().objects.create_user("uev", password="x")
        SellerProfile.objects.create(
            user=self.user, ntn_cnic="1234567", business_name="Biz",
            province="Sindh", address="K")
        self.c = Client()
        self.c.force_login(self.user)

    def _upload(self, c=None, name="atl.pdf", body=None):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return (c or self.c).post("/digital-invoicing/atl/evidence/upload/", {
            "reg_no": "7654321", "period": "2026-07",
            "status": "Active",   # stub PDF text-less — manual confirm path
            "file": SimpleUploadedFile(name, body or self.PDF,
                                       content_type="application/pdf")})

    def test_upload_saves_and_view_works(self):
        from .models import ATLStatus
        r = self._upload()
        self.assertTrue(r.json()["ok"], r.content)
        rec = ATLStatus.objects.get(owner=self.user, reg_no="7654321",
                                    period="2026-07")
        self.assertTrue(rec.evidence_pdf)
        v = self.c.get(f"/digital-invoicing/atl/evidence/{rec.pk}/")
        self.assertEqual(v.status_code, 200)
        self.assertEqual(v["Content-Type"], "application/pdf")

    def test_non_pdf_rejected(self):
        r = self._upload(name="x.pdf", body=b"not a pdf at all")
        self.assertEqual(r.status_code, 400)

    def test_other_user_cannot_view(self):
        self._upload()
        from .models import ATLStatus
        rec = ATLStatus.objects.get(owner=self.user, reg_no="7654321")
        other = get_user_model().objects.create_user("uev2", password="x")
        c2 = Client(); c2.force_login(other)
        self.assertEqual(
            c2.get(f"/digital-invoicing/atl/evidence/{rec.pk}/").status_code,
            404)

    def test_reupload_replaces(self):
        self._upload()
        self._upload(body=b"%PDF-1.4 second version %%EOF")
        from .models import ATLStatus
        self.assertEqual(ATLStatus.objects.filter(
            owner=self.user, reg_no="7654321", period="2026-07").count(), 1)


class InvoiceFlowATLTests(TestCase):
    """Invoice success modal se buyer ATL PDF upload (wahi endpoint)."""

    def test_invoice_page_has_atl_upload_hook(self):
        u = get_user_model().objects.create_user("uinv", password="x")
        SellerProfile.objects.create(
            user=u, ntn_cnic="1234567", business_name="Biz",
            province="Sindh", address="K")
        c = Client(); c.force_login(u)
        r = c.get("/digital-invoicing/create/")
        self.assertContains(r, "uploadBuyerATL")
        self.assertContains(r, "atl/evidence/upload/")

    def test_upload_from_invoice_period_matches_invoice_month(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .models import ATLStatus
        u = get_user_model().objects.create_user("uinv2", password="x")
        SellerProfile.objects.create(
            user=u, ntn_cnic="1234567", business_name="Biz",
            province="Sindh", address="K")
        c = Client(); c.force_login(u)
        # jaise modal karta hai: reg_no=buyer NTN, period=invoice month
        r = c.post("/digital-invoicing/atl/evidence/upload/", {
            "reg_no": "7654321", "period": "2026-07", "status": "Active",
            "file": SimpleUploadedFile("atl.pdf", b"%PDF-1.4 x %%EOF",
                                       content_type="application/pdf")})
        self.assertTrue(r.json()["ok"])
        self.assertTrue(ATLStatus.objects.filter(
            owner=u, reg_no="7654321", period="2026-07",
            evidence_pdf__isnull=False).exclude(evidence_pdf="").exists())


class ATLPDFParsingTests(TestCase):
    """PDF content read: NTN match, status extraction, manual fallback."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("updf", password="x")
        SellerProfile.objects.create(
            user=self.user, ntn_cnic="1234567", business_name="Biz",
            province="Sindh", address="K")
        self.c = Client()
        self.c.force_login(self.user)

    def _real_pdf(self, text):
        """reportlab se asal text-wali PDF (jaisi FBR verification PDF)."""
        import io
        from reportlab.pdfgen import canvas
        buf = io.BytesIO()
        cv = canvas.Canvas(buf)
        y = 800
        for line in text.split("\n"):
            cv.drawString(60, y, line); y -= 20
        cv.save()
        buf.seek(0)
        return buf.read()

    def _upload(self, body, reg="7654321", status=None):
        from django.core.files.uploadedfile import SimpleUploadedFile
        data = {"reg_no": reg, "period": "2026-07",
                "file": SimpleUploadedFile("atl.pdf", body,
                                           content_type="application/pdf")}
        if status:
            data["status"] = status
        return self.c.post("/digital-invoicing/atl/evidence/upload/", data)

    def test_active_pdf_auto_verified(self):
        from .models import ATLStatus
        pdf = self._real_pdf("FBR Active Taxpayer List\nRegistration No: 7654321\nStatus: Active")
        r = self._upload(pdf)
        body = r.json()
        self.assertTrue(body["ok"], body)
        self.assertEqual(body["status"], "Active")
        self.assertTrue(body["verified"])
        rec = ATLStatus.objects.get(owner=self.user, reg_no="7654321")
        self.assertTrue(rec.verified)

    def test_inactive_pdf_detected(self):
        pdf = self._real_pdf("Registration No: 7654321\nStatus: In-Active")
        body = self._upload(pdf).json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["status"], "Inactive")
        self.assertTrue(body["verified"])

    def test_wrong_party_pdf_rejected(self):
        pdf = self._real_pdf("Registration No: 9999999\nStatus: Active")
        r = self._upload(pdf, reg="7654321")
        self.assertEqual(r.status_code, 400)
        self.assertIn("was not found", r.json()["error"])

    def test_unreadable_pdf_needs_manual_then_saves(self):
        from .models import ATLStatus
        blank = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<<>>\n%%EOF"
        r = self._upload(blank)
        self.assertEqual(r.status_code, 400)
        self.assertTrue(r.json().get("needs_status"))
        r2 = self._upload(blank, status="Inactive")
        self.assertTrue(r2.json()["ok"])
        rec = ATLStatus.objects.get(owner=self.user, reg_no="7654321")
        self.assertEqual(rec.status, "Inactive")
        self.assertFalse(rec.verified)          # manual — verified nahi
