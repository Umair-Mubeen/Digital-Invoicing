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
from .models import SellerProfile, Buyer, Invoice, InvoiceItem


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
        # Milestone 1: 24 official PRAL sale types (Scenarios doc v1.11)
        self.assertEqual(
            TaxSaleType.objects.filter(is_active=True).values("name")
            .distinct().count(), 24)
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
        row = TaxSaleType.objects.get(name="Goods at standard rate (default)")
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
            name="Goods at standard rate (default)", rate=Decimal("20"),
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


class PRALScenarioTaxTests(TestCase):
    """Milestone 1 — SN001-SN028 math, PRAL Scenarios doc v1.11 ke sample
    JSON se directly verify (page refs comments mein)."""

    def test_sn001_standard_rate(self):                     # p.3-4: 18% of 1000 = 180
        r = compute_item("Goods at standard rate (default)", 1000)
        self.assertEqual(r["sales_tax"], Decimal("180.00"))

    def test_sn005_reduced_rate_1pct(self):                 # p.11-12: 1% of 1000 = 10
        r = compute_item("Goods at Reduced Rate", 1000)
        self.assertEqual(r["sales_tax"], Decimal("10.00"))
        self.assertEqual(r["sro_schedule"], "EIGHTH SCHEDULE Table 1")

    def test_sn006_exempt(self):                            # p.13: rate "Exempt"
        r = compute_item("Exempt goods", 1000)
        self.assertEqual(r["sales_tax"], Decimal("0.00"))
        self.assertEqual(r["rate"], "Exempt")

    def test_sn008_third_schedule_mrp(self):                # p.17-18: 18% of MRP 1000 = 180
        r = compute_item("3rd Schedule Goods", 0, retail_price=1000)
        self.assertEqual(r["sales_tax"], Decimal("180.00"))

    def test_sn010_telecom_17pct(self):                     # p.21: 17%
        r = compute_item("Telecommunication services", 1000)
        self.assertEqual(r["sales_tax"], Decimal("170.00"))

    def test_sn012_petroleum(self):                         # p.25-26: 1.43% of 100 = 1.43
        r = compute_item("Petroleum Products", 100)
        self.assertEqual(r["sales_tax"], Decimal("1.43"))
        self.assertEqual(r["sro_schedule"], "1450(I)/2021")
        self.assertEqual(r["sro_item"], "4")

    def test_sn013_electricity_5pct(self):                  # p.27-28: 5% of 1000 = 50
        r = compute_item("Electricity Supply to Retailers", 1000)
        self.assertEqual(r["sales_tax"], Decimal("50.00"))

    def test_sn017_fed_in_st_mode(self):                    # p.35-36: 8% of 100 = 8
        r = compute_item("Goods (FED in ST Mode)", 100)
        self.assertEqual(r["sales_tax"], Decimal("8.00"))

    def test_sn021_cement_fixed_per_unit(self):             # p.43-44: 12 x Rs.3 = 36
        r = compute_item("Cement /Concrete Block", 123, quantity=12)
        self.assertEqual(r["sales_tax"], Decimal("36.00"))
        self.assertEqual(r["rate"], "Rs.3")

    def test_sn022_potassium_chlorate_compound(self):       # p.45-46: 100x18% + 1x60 = 78
        r = compute_item("Potassium Chlorate", 100, quantity=1)
        self.assertEqual(r["sales_tax"], Decimal("78.00"))
        self.assertEqual(r["rate"], "18% along with rupees 60 per kilogram")

    def test_sn023_cng_fixed_per_unit(self):                # p.47-48: 123 x Rs.200 = 24600
        r = compute_item("CNG Sales", 234, quantity=123)
        self.assertEqual(r["sales_tax"], Decimal("24600.00"))
        self.assertEqual(r["rate"], "Rs.200")

    def test_sn024_sro297_25pct(self):                      # p.49: 25%
        r = compute_item("Goods as per SRO.297(|)/2023", 1000)
        self.assertEqual(r["sales_tax"], Decimal("250.00"))

    def test_sn025_non_adjustable_0pct(self):               # p.51-52: 0%
        r = compute_item("Non-Adjustable Supplies", 100)
        self.assertEqual(r["sales_tax"], Decimal("0.00"))

    def test_legacy_aliases_still_work(self):
        # Purane stored labels (SavedItems/Products) na tootein
        for old, new in [("Goods at standard rate", "Goods at standard rate (default)"),
                         ("Exempt Goods", "Exempt goods"),
                         ("Zero-rated Goods", "Goods at zero-rate"),
                         ("Goods at reduced rate", "Goods at Reduced Rate")]:
            r = compute_item(old, 1000)
            self.assertEqual(r["sale_type"], new)

    def test_all_28_scenarios_seeded(self):
        from .models import TaxScenario
        self.assertEqual(TaxScenario.objects.count(), 28)
        self.assertEqual(TaxScenario.objects.get(code="SN002").description,
                         "Sale of Standard Rate Goods to Unregistered Buyers")

    def test_all_24_sale_types_seeded_and_computable(self):
        from .models import TaxSaleType
        from .tax_engine import SALE_TYPES as ENGINE_TYPES
        db_names = set(TaxSaleType.objects.values_list("name", flat=True))
        for name in ENGINE_TYPES:
            self.assertIn(name, db_names)
            compute_item(name, 100, quantity=2)   # koi crash na ho

    def test_further_tax_only_on_general_goods(self):
        # Sector types pe FT auto nahi (stated assumption — admin enable kare)
        r = compute_item("Petroleum Products", 1000, buyer_unregistered=True,
                         hs_code="9999.0000")
        self.assertEqual(r["further_tax"], Decimal("0.00"))
        r = compute_item("Goods at standard rate (default)", 1000,
                         buyer_unregistered=True, hs_code="9999.0000")
        self.assertEqual(r["further_tax"], Decimal("40.00"))


class ErrorCodeMilestone2Tests(TestCase):
    """Milestone 2 — naye official error codes (Error Message Guide, Sales)."""

    def _base_item(self, **kw):
        d = {"hsCode": "0101.2100", "productDescription": "t", "rate": "18%",
             "uoM": "Numbers, pieces, units", "quantity": 1, "totalValues": 0,
             "valueSalesExcludingST": 100, "fixedNotifiedValueOrRetailPrice": 0,
             "salesTaxApplicable": 18, "salesTaxWithheldAtSource": 0,
             "extraTax": "", "furtherTax": 0, "sroScheduleNo": "",
             "fedPayable": 0, "discount": 0,
             "saleType": "Goods at standard rate (default)",
             "sroItemSerialNo": ""}
        d.update(kw)
        return d

    def _base(self, **item_kw):
        return {"invoiceType": "Sale Invoice", "invoiceDate": "2026-07-01",
                "sellerNTNCNIC": "1234567", "sellerBusinessName": "S",
                "sellerProvince": "Sindh", "sellerAddress": "K",
                "buyerNTNCNIC": "7654321", "buyerBusinessName": "B",
                "buyerProvince": "Sindh", "buyerAddress": "K",
                "buyerRegistrationType": "Registered", "invoiceRefNo": "",
                "scenarioId": "SN001", "items": [self._base_item(**item_kw)]}

    def _codes(self, payload):
        from .validators import validate_invoice
        return {e["errorCode"] for e in validate_invoice(payload)}

    def test_0007_invalid_sale_type(self):
        self.assertIn("0007", self._codes(self._base(saleType="Bogus Type")))

    def test_0019_hs_format(self):
        self.assertIn("0019", self._codes(self._base(hsCode="ABCD")))
        self.assertNotIn("0019", self._codes(self._base(hsCode="0101.2100")))

    def test_0022_0050_cotton_ginners_stwh(self):
        p = self._base(saleType="Cotton ginners",
                       salesTaxWithheldAtSource=None)
        self.assertIn("0022", self._codes(p))
        p = self._base(saleType="Cotton ginners", salesTaxApplicable=18,
                       salesTaxWithheldAtSource=5)   # na zero na ST ke barabar
        self.assertIn("0050", self._codes(p))
        p = self._base(saleType="Cotton ginners", salesTaxApplicable=18,
                       salesTaxWithheldAtSource=18)  # ST ke barabar — theek
        self.assertNotIn("0050", self._codes(p))

    def test_0046_rate_mismatch(self):
        self.assertIn("0046", self._codes(self._base(rate="17%")))
        self.assertNotIn("0046", self._codes(self._base(rate="18%")))

    def test_0062_steel_uom_mt(self):
        p = self._base(saleType="Steel melting and re-rolling", uoM="KG")
        self.assertIn("0062", self._codes(p))
        p = self._base(saleType="Steel melting and re-rolling", uoM="MT")
        self.assertNotIn("0062", self._codes(p))

    def test_0097_potassium_chlorate_uom_kg(self):
        p = self._base(saleType="Potassium Chlorate", uoM="MT",
                       rate="18% along with rupees 60 per kilogram",
                       salesTaxApplicable=78, quantity=1,
                       sroScheduleNo="EIGHTH SCHEDULE Table 1",
                       sroItemSerialNo="56")
        self.assertIn("0097", self._codes(p))

    def test_0103_potassium_chlorate_st_math(self):
        p = self._base(saleType="Potassium Chlorate", uoM="KG",
                       rate="18% along with rupees 60 per kilogram",
                       valueSalesExcludingST=100, quantity=1,
                       salesTaxApplicable=50,      # sahi: 78
                       sroScheduleNo="EIGHTH SCHEDULE Table 1",
                       sroItemSerialNo="56")
        self.assertIn("0103", self._codes(p))
        p["items"][0]["salesTaxApplicable"] = 78
        self.assertNotIn("0103", self._codes(p))

    def test_0105_fixed_per_unit_st_math(self):
        p = self._base(saleType="Cement /Concrete Block", rate="Rs.3",
                       quantity=12, salesTaxApplicable=30)   # sahi: 36
        self.assertIn("0105", self._codes(p))
        p["items"][0]["salesTaxApplicable"] = 36
        self.assertNotIn("0105", self._codes(p))

    def test_0078_sro_item_serial_required(self):
        p = self._base(rate="1%", saleType="Goods at Reduced Rate",
                       salesTaxApplicable=1,
                       sroScheduleNo="EIGHTH SCHEDULE Table 1",
                       sroItemSerialNo="")
        self.assertIn("0078", self._codes(p))

    def test_official_labels_trigger_quantity_check(self):
        # Milestone 1 label fix ke baad 0098 official label pe fire ho
        p = self._base(quantity=0)
        self.assertIn("0098", self._codes(p))

    def test_legacy_label_still_validates(self):
        p = self._base(saleType="Goods at standard rate")   # alias
        self.assertNotIn("0007", self._codes(p))


class CancellationWorkflowTests(TestCase):
    """Milestone 3 — Manual v1.6 §4.1 cancellation/edit rules."""

    def setUp(self):
        from django.contrib.auth.models import User
        from django.utils import timezone
        self.user = User.objects.create_user("m3user", password="x")
        self.profile = SellerProfile.objects.create(
            user=self.user, ntn_cnic="1234567", business_name="S",
            province="Sindh", address="K")
        # last month sales = 100,000 -> 10% limit = 10,000
        from datetime import date, timedelta
        today = date.today()
        prev = (today.replace(day=1) - timedelta(days=1))
        self.old_inv = Invoice.objects.create(
            owner=self.user, seller_profile=self.profile,
            invoice_type="Sale Invoice", invoice_date=prev,
            buyer_business_name="B", buyer_registration_type="Registered",
            total_value=100000, invoice_total=100000, status="valid",
            submitted_at=timezone.now() - timedelta(days=35))

    def _mk_invoice(self, items=2, value=1000):
        from django.utils import timezone
        from datetime import date
        inv = Invoice.objects.create(
            owner=self.user, seller_profile=self.profile,
            invoice_type="Sale Invoice", invoice_date=date.today(),
            buyer_business_name="B", buyer_registration_type="Registered",
            total_value=value * items, invoice_total=value * items,
            status="valid", submitted_at=timezone.now())
        for _ in range(items):
            InvoiceItem.objects.create(
                invoice=inv, hs_code="0101.2100", product_description="t",
                sale_type="Goods at standard rate (default)",
                quantity=1, value_excl_st=value,
                sales_tax=Decimal(value) * Decimal("0.18"), rate="18%")
        return inv

    def _svc(self):
        from .services import InvoiceCancellationService
        return InvoiceCancellationService(self.user)

    def test_full_cancel_marks_items_and_status(self):
        inv = self._mk_invoice()
        r = self._svc().mark_cancelled(inv.pk)
        inv.refresh_from_db()
        self.assertEqual(inv.status, "cancelled")
        self.assertEqual(r["status"], "cancelled")
        self.assertEqual(inv.items.filter(item_status="cancelled").count(), 2)

    def test_item_cancel_sets_partially_cancelled(self):
        inv = self._mk_invoice()
        it = inv.items.first()
        self._svc().cancel_item(inv.pk, it.pk)
        inv.refresh_from_db()
        self.assertEqual(inv.status, "partially_cancelled")

    def test_edit_item_snapshot_and_recompute(self):
        inv = self._mk_invoice()
        it = inv.items.first()
        self._svc().edit_item(inv.pk, it.pk, {"value_excl_st": 500})
        it.refresh_from_db()
        self.assertEqual(it.item_status, "edited")
        self.assertEqual(it.sales_tax, Decimal("90.00"))     # engine recompute
        self.assertEqual(it.original_snapshot["value_excl_st"], "1000.00")
        inv.refresh_from_db()
        self.assertEqual(inv.status, "partially_edited")

    def test_edit_only_once(self):
        from .services import SubmissionError
        inv = self._mk_invoice()
        it = inv.items.first()
        self._svc().edit_item(inv.pk, it.pk, {"value_excl_st": 500})
        with self.assertRaises(SubmissionError):
            self._svc().edit_item(inv.pk, it.pk, {"value_excl_st": 700})

    def test_edited_item_cannot_be_cancelled(self):
        from .services import SubmissionError
        inv = self._mk_invoice()
        it = inv.items.first()
        self._svc().edit_item(inv.pk, it.pk, {"value_excl_st": 500})
        with self.assertRaises(SubmissionError):
            self._svc().cancel_item(inv.pk, it.pk)

    def test_invoice_with_edited_item_cannot_full_cancel(self):
        from .services import SubmissionError
        inv = self._mk_invoice()
        self._svc().edit_item(inv.pk, inv.items.first().pk,
                              {"value_excl_st": 500})
        with self.assertRaises(SubmissionError):
            self._svc().mark_cancelled(inv.pk)

    def test_10pct_limit_enforced(self):
        from .services import SubmissionError
        # limit = 10,000; invoice items total = 2 x (11000+1980) > limit
        inv = self._mk_invoice(items=2, value=11000)
        with self.assertRaises(SubmissionError) as cm:
            self._svc().mark_cancelled(inv.pk)
        self.assertIn("10%", cm.exception.message)

    def test_window_locked_after_72h(self):
        from django.utils import timezone
        from datetime import timedelta
        from .services import SubmissionError
        inv = self._mk_invoice()
        inv.submitted_at = timezone.now() - timedelta(hours=73)
        inv.save()
        with self.assertRaises(SubmissionError):
            self._svc().mark_cancelled(inv.pk)

    def test_month_end_lock(self):
        from django.utils import timezone
        from datetime import timedelta
        inv = self._mk_invoice()
        # 10 ghante purani lekin pichhle mahine ki — month-end lock
        inv.submitted_at = (timezone.now().replace(day=1)
                            - timedelta(hours=10))
        inv.save()
        if timezone.now().day == 1:      # edge: aaj 1 tareekh
            self.skipTest("month boundary edge")
        self.assertTrue(inv.is_locked)

    def test_partially_edited_and_cancelled_status(self):
        inv = self._mk_invoice(items=3)
        ids = list(inv.items.values_list("pk", flat=True))
        self._svc().edit_item(inv.pk, ids[0], {"value_excl_st": 500})
        self._svc().cancel_item(inv.pk, ids[1])
        inv.refresh_from_db()
        self.assertEqual(inv.status, "partially_edited_cancelled")

    def test_eligibility_endpoint_shape(self):
        inv = self._mk_invoice()
        e = self._svc().eligibility(inv.pk)
        self.assertTrue(e["windowOpen"])
        self.assertTrue(e["fullCancelAllowed"])
        self.assertEqual(len(e["items"]), 2)
        self.assertEqual(e["limit"], "10000.00")


class ReferenceSyncTests(TestCase):
    """Milestone 4 — transtypecode + SaleTypeToRate sync (mock-driven)."""

    def _svc(self):
        from .reference_data import ReferenceSyncService, MockReferenceClient
        return ReferenceSyncService(client=MockReferenceClient())

    def test_trans_type_ids_synced(self):
        from .models import TaxSaleType
        r = self._svc().sync_trans_type_ids()
        self.assertIn(("Goods at standard rate (default)", 75), r["matched"])
        row = TaxSaleType.objects.filter(
            name="Goods at standard rate (default)").first()
        self.assertEqual(row.fbr_trans_type_id, 75)
        # Mock mein sirf 7 types — baqi unmatched report hon, crash na ho
        self.assertIn("Petroleum Products", r["unmatched"])

    def test_no_drift_when_rates_match(self):
        svc = self._svc()
        svc.sync_trans_type_ids()
        report = svc.check_rate_drift()
        std = next(d for d in report
                   if d["sale_type"] == "Goods at standard rate (default)")
        self.assertFalse(std["drift"])          # ours 18% == FBR 18%

    def test_drift_detected_and_applied_single_rate(self):
        from decimal import Decimal as D
        from .models import TaxSaleType
        from .tax_engine import invalidate_rules_cache, compute_item
        svc = self._svc()
        svc.sync_trans_type_ids()
        # Standard rate ko jaan boojh kar 17% kar do -> FBR 18% se drift
        TaxSaleType.objects.filter(
            name="Goods at standard rate (default)").update(rate=D("17"))
        invalidate_rules_cache()
        report = svc.check_rate_drift()
        std = next(d for d in report
                   if d["sale_type"] == "Goods at standard rate (default)")
        self.assertTrue(std["drift"])
        self.assertTrue(std["auto_applicable"])   # FBR single rate
        applied = svc.apply_rate_updates(report)
        self.assertIn("Goods at standard rate (default)", applied)
        # Nayi date-effective row + engine ab 18% compute kare
        rows = TaxSaleType.objects.filter(
            name="Goods at standard rate (default)").order_by("effective_from")
        self.assertEqual(rows.count(), 2)
        self.assertIsNotNone(rows.first().effective_to)   # purani closed
        invalidate_rules_cache()
        r = compute_item("Goods at standard rate (default)", 1000)
        self.assertEqual(r["sales_tax"], Decimal("180.00"))

    def test_multi_rate_never_auto_applied(self):
        from decimal import Decimal as D
        from .models import TaxSaleType
        from .tax_engine import invalidate_rules_cache
        svc = self._svc()
        svc.sync_trans_type_ids()
        # Reduced rate ko 3% kar do -> FBR ["1%","5%"] se drift, multi-rate
        TaxSaleType.objects.filter(
            name="Goods at Reduced Rate").update(rate=D("3"), rate_label="")
        invalidate_rules_cache()
        report = svc.check_rate_drift()
        red = next(d for d in report
                   if d["sale_type"] == "Goods at Reduced Rate")
        self.assertTrue(red["drift"])
        self.assertFalse(red["auto_applicable"])
        applied = svc.apply_rate_updates(report)
        self.assertNotIn("Goods at Reduced Rate", applied)

    def test_management_command_runs(self):
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        call_command("sync_fbr_reference", stdout=out)
        self.assertIn("Trans type IDs:", out.getvalue())


class RetryQueueTests(TestCase):
    """Milestone 5 — Queue & Retry (Manual v1.6 §4.2)."""

    def setUp(self):
        from django.contrib.auth.models import User
        self.user = User.objects.create_user("m5user", password="x")
        self.profile = SellerProfile.objects.create(
            user=self.user, ntn_cnic="1234567", business_name="S",
            province="Sindh", address="K", use_sandbox=True)

    def _payload(self):
        return {"invoiceType": "Sale Invoice", "invoiceDate": "2026-07-01",
                "sellerNTNCNIC": "1234567", "sellerBusinessName": "S",
                "sellerProvince": "Sindh", "sellerAddress": "K",
                "buyerNTNCNIC": "7654321", "buyerBusinessName": "B",
                "buyerProvince": "Sindh", "buyerAddress": "K",
                "buyerRegistrationType": "Registered", "scenarioId": "SN001",
                "items": [{"hsCode": "0101.2100", "productDescription": "t",
                           "rate": "18%", "uoM": "Numbers, pieces, units",
                           "quantity": 1, "valueSalesExcludingST": 100,
                           "salesTaxApplicable": 18, "furtherTax": 0,
                           "saleType": "Goods at standard rate (default)",
                           "sroScheduleNo": "", "sroItemSerialNo": "",
                           "salesTaxWithheldAtSource": 0, "extraTax": "",
                           "fedPayable": 0, "discount": 0,
                           "fixedNotifiedValueOrRetailPrice": 0,
                           "totalValues": 0}]}

    def _mk_pending(self):
        from django.utils import timezone
        return Invoice.objects.create(
            owner=self.user, seller_profile=self.profile,
            invoice_type="Sale Invoice", invoice_date="2026-07-01",
            buyer_business_name="B", buyer_registration_type="Registered",
            status="pending_retry", fbr_payload=self._payload(),
            next_retry_at=timezone.now(), retry_count=0)

    def test_classify(self):
        from .services import RetryQueueService as R
        self.assertEqual(R.classify(
            {"validationResponse": {"status": "Valid"}}), "valid")
        self.assertEqual(R.classify(
            {"_transient": True,
             "validationResponse": {"status": "Invalid"}}), "pending_retry")
        self.assertEqual(R.classify(
            {"_transient": False,
             "validationResponse": {"status": "Invalid"}}), "failed")

    def test_backoff_schedule(self):
        from django.utils import timezone
        from .services import RetryQueueService as R
        inv = self._mk_pending()
        t = R.schedule(inv)
        mins = (t - timezone.now()).total_seconds() / 60
        self.assertAlmostEqual(mins, 5, delta=1)      # pehla backoff 5 min
        inv.retry_count = 3
        t = R.schedule(inv)
        mins = (t - timezone.now()).total_seconds() / 60
        self.assertAlmostEqual(mins, 120, delta=1)    # 4th -> 2h

    def test_exhausted_attempts_fail(self):
        from .services import RetryQueueService as R
        inv = self._mk_pending()
        inv.retry_count = R.MAX_ATTEMPTS
        self.assertIsNone(R.schedule(inv))
        inv.refresh_from_db()
        self.assertEqual(inv.status, "failed")
        self.assertIn("exhausted", inv.last_error)

    def test_process_due_success(self):
        # Mock client valid return karta hai -> invoice valid ho jaye
        from .services import RetryQueueService as R
        inv = self._mk_pending()
        s = R.process_due()
        self.assertEqual(s["processed"], 1)
        self.assertEqual(s["valid"], 1)
        inv.refresh_from_db()
        self.assertEqual(inv.status, "valid")
        self.assertTrue(inv.fbr_invoice_number)
        self.assertIsNone(inv.next_retry_at)

    def test_process_due_skips_future(self):
        from django.utils import timezone
        from datetime import timedelta
        from .services import RetryQueueService as R
        inv = self._mk_pending()
        inv.next_retry_at = timezone.now() + timedelta(hours=1)
        inv.save()
        s = R.process_due()
        self.assertEqual(s["processed"], 0)

    def test_ambiguous_readtimeout_not_queued(self):
        # NET_AMBIGUOUS failure pending_retry NAHI banta (duplicate risk)
        from .services import RetryQueueService as R
        result = {"_transient": False, "_failure_code": "NET_AMBIGUOUS",
                  "validationResponse": {"status": "Invalid",
                                         "error": "verify on IRIS"}}
        self.assertEqual(R.classify(result), "failed")

    def test_management_command_runs(self):
        from django.core.management import call_command
        from io import StringIO
        self._mk_pending()
        out = StringIO()
        call_command("retry_pending_invoices", stdout=out)
        self.assertIn("processed=1", out.getvalue())


class SecurityEndpointTests(TestCase):
    """Milestone 6 — IDOR/auth verification + modification endpoints HTTP layer."""

    def setUp(self):
        from django.contrib.auth.models import User
        from django.utils import timezone
        self.alice = User.objects.create_user("alice6", password="x")
        self.bob = User.objects.create_user("bob6", password="x")
        self.profile = SellerProfile.objects.create(
            user=self.alice, ntn_cnic="1234567", business_name="A",
            province="Sindh", address="K")
        self.inv = Invoice.objects.create(
            owner=self.alice, seller_profile=self.profile,
            invoice_type="Sale Invoice", invoice_date="2026-07-01",
            buyer_business_name="B", buyer_registration_type="Registered",
            status="valid", submitted_at=timezone.now())
        self.item = InvoiceItem.objects.create(
            invoice=self.inv, hs_code="0101.2100", product_description="t",
            sale_type="Goods at standard rate (default)", quantity=1,
            value_excl_st=1000, sales_tax=180, rate="18%")
        # 10% modification limit ke liye last-month sales
        from datetime import date, timedelta
        prev = date.today().replace(day=1) - timedelta(days=1)
        Invoice.objects.create(
            owner=self.alice, seller_profile=self.profile,
            invoice_type="Sale Invoice", invoice_date=prev,
            buyer_business_name="B", buyer_registration_type="Registered",
            total_value=100000, invoice_total=100000, status="valid",
            submitted_at=timezone.now() - timedelta(days=35))
        self.base = f"/digital-invoicing/invoices/{self.inv.pk}"

    def test_unauthenticated_redirects(self):
        r = self.client.get(f"{self.base}/eligibility/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("login", r["Location"])

    def test_idor_other_users_invoice_blocked(self):
        self.client.login(username="bob6", password="x")
        # Bob, Alice ki invoice par: eligibility 404; cancel/edit 400/404
        self.assertEqual(
            self.client.get(f"{self.base}/eligibility/").status_code, 404)
        self.assertEqual(
            self.client.post(f"{self.base}/cancel/").status_code, 400)
        r = self.client.post(
            f"{self.base}/items/{self.item.pk}/edit/",
            data='{"value_excl_st": 1}', content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, "valid")   # kuch nahi badla

    def test_owner_full_flow_over_http(self):
        self.client.login(username="alice6", password="x")
        e = self.client.get(f"{self.base}/eligibility/").json()
        self.assertTrue(e["windowOpen"])
        r = self.client.post(
            f"{self.base}/items/{self.item.pk}/edit/",
            data='{"value_excl_st": 500}', content_type="application/json")
        self.assertTrue(r.json()["ok"])
        self.item.refresh_from_db()
        self.assertEqual(self.item.sales_tax, Decimal("90.00"))
        # edited item cancel -> 400
        r = self.client.post(f"{self.base}/items/{self.item.pk}/cancel/")
        self.assertEqual(r.status_code, 400)

    def test_edit_rejects_bad_json(self):
        self.client.login(username="alice6", password="x")
        r = self.client.post(
            f"{self.base}/items/{self.item.pk}/edit/",
            data='not json', content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_list_page_defers_but_renders(self):
        self.client.login(username="alice6", password="x")
        r = self.client.get("/digital-invoicing/invoices/")
        self.assertEqual(r.status_code, 200)


class ValidatorBranchTests(TestCase):
    """Milestone 6 — uncovered validator branches."""

    def _codes(self, payload):
        from .validators import validate_invoice
        return {e["errorCode"] for e in validate_invoice(payload)}

    def _p(self, **item_kw):
        item = {"hsCode": "0101.2100", "productDescription": "t",
                "rate": "18%", "uoM": "Numbers, pieces, units",
                "quantity": 1, "valueSalesExcludingST": 100,
                "salesTaxApplicable": 18, "furtherTax": 0,
                "saleType": "Goods at standard rate (default)",
                "sroScheduleNo": "", "sroItemSerialNo": "",
                "salesTaxWithheldAtSource": 0, "extraTax": "",
                "fedPayable": 0, "discount": 0, "totalValues": 0,
                "fixedNotifiedValueOrRetailPrice": 0}
        item.update(item_kw)
        return {"invoiceType": "Sale Invoice", "invoiceDate": "2026-07-01",
                "sellerNTNCNIC": "1234567", "sellerBusinessName": "S",
                "sellerProvince": "Sindh", "sellerAddress": "K",
                "buyerNTNCNIC": "7654321", "buyerBusinessName": "B",
                "buyerProvince": "Sindh", "buyerAddress": "K",
                "buyerRegistrationType": "Registered",
                "invoiceRefNo": "", "scenarioId": "SN001", "items": [item]}

    def test_0060_services_sqy_uom(self):
        p = self._p(saleType="Services", rate="50/SqY", uoM="KG",
                    salesTaxApplicable=0)
        self.assertIn("0060", self._codes(p))
        p["items"][0]["uoM"] = "SqY"
        self.assertNotIn("0060", self._codes(p))

    def test_0061_fed_services_bill_uom(self):
        p = self._p(saleType="Services (FED in ST Mode)", rate="200/bill",
                    uoM="KG", salesTaxApplicable=0)
        self.assertIn("0061", self._codes(p))
        p["items"][0]["uoM"] = "Bill of lading"
        self.assertNotIn("0061", self._codes(p))

    def test_0002_0003_registration_format(self):
        p = self._p()
        p["buyerNTNCNIC"] = "12AB"            # na 7-digit NTN na 13-digit CNIC
        self.assertIn("0002", self._codes(p))
        p = self._p()
        p["sellerNTNCNIC"] = "12"
        self.assertIn("0108", self._codes(p))     # seller format = 0108
        p = self._p()
        p["invoiceType"] = "Bogus Type"
        self.assertIn("0003", self._codes(p))     # 0003 = invoice type

    def test_0077_sro_needed_for_schedule_based_types(self):
        # Schedule-based (config sro defined) -> SRO mandatory
        p = self._p(saleType="Goods at Reduced Rate", rate="1%",
                    salesTaxApplicable=1, sroScheduleNo="")
        self.assertIn("0077", self._codes(p))
        # Sector rate (telecom 17%) — PRAL sample SRO ke baghair valid
        p = self._p(saleType="Telecommunication services", rate="17%",
                    salesTaxApplicable=17, sroScheduleNo="")
        self.assertNotIn("0077", self._codes(p))


class SandboxScenarioRunnerTests(TestCase):
    """Milestone 7 — 28-scenario payload generator + runner."""

    def setUp(self):
        from django.contrib.auth.models import User
        self.user = User.objects.create_user("m7user", password="x")
        self.profile = SellerProfile.objects.create(
            user=self.user, ntn_cnic="1234567", business_name="S",
            province="Sindh", address="K", use_sandbox=True)

    def test_all_28_payloads_pass_local_validation(self):
        from .sandbox_scenarios import build_scenario_payload
        from .validators import validate_invoice
        from .tax_engine import SCENARIOS
        for code, _, _ in SCENARIOS:
            p = build_scenario_payload(code, self.profile)
            errs = validate_invoice(p)
            self.assertEqual(errs, [], f"{code}: {errs[:3]}")

    def test_pral_sample_math_in_payloads(self):
        from .sandbox_scenarios import build_scenario_payload
        # Doc-verified: SN021 36, SN022 78, SN023 24600, SN008 180 (MRP)
        for code, expected in [("SN021", 36.0), ("SN022", 78.0),
                               ("SN023", 24600.0), ("SN008", 180.0)]:
            p = build_scenario_payload(code, self.profile)
            self.assertEqual(p["items"][0]["salesTaxApplicable"], expected,
                             code)

    def test_sn009_stwh_equals_st(self):
        from .sandbox_scenarios import build_scenario_payload
        p = build_scenario_payload("SN009", self.profile)
        it = p["items"][0]
        self.assertEqual(it["salesTaxWithheldAtSource"],
                         it["salesTaxApplicable"])   # 0050 rule

    def test_unregistered_scenarios_have_no_buyer_ntn(self):
        from .sandbox_scenarios import build_scenario_payload
        p = build_scenario_payload("SN002", self.profile)
        self.assertEqual(p["buyerNTNCNIC"], "")
        self.assertEqual(p["buyerRegistrationType"], "Unregistered")

    def test_runner_all_pass_on_mock(self):
        from .sandbox_scenarios import run_scenarios
        results = run_scenarios(self.profile)
        self.assertEqual(len(results), 28)
        failed = [(c, m) for c, ok, m in results if not ok]
        self.assertEqual(failed, [])

    def test_command_runs(self):
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        call_command("run_sandbox_scenarios", user="m7user",
                     only="SN001,SN021", allow_mock=True, stdout=out)
        self.assertIn("2/2 passed", out.getvalue())

    def test_command_blocks_false_certification_on_mock(self):
        """Mock ke against 28/28 PASS = jhoota certification. Block hona chahiye."""
        from django.core.management import call_command
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError) as cm:
            call_command("run_sandbox_scenarios", user="m7user",
                         only="SN001")
        msg = str(cm.exception)
        self.assertIn("MOCK client active", msg)
        self.assertIn("FBR_USE_MOCK", msg)

    def test_command_blocks_production_target(self):
        """Scenarios production par kabhi na chalein."""
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from django.test import override_settings
        self.profile.fbr_token = "FAKE_TOKEN"
        self.profile.use_sandbox = False
        self.profile.save()
        with override_settings(FBR_USE_MOCK=False):
            with self.assertRaises(CommandError) as cm:
                call_command("run_sandbox_scenarios", user="m7user",
                             only="SN001")
        self.assertIn("PRODUCTION", str(cm.exception))


class SprintGapClosureTests(TestCase):
    """Sprint 3 UI + Sprint 4 resilience gaps."""

    def setUp(self):
        from django.contrib.auth.models import User
        from django.utils import timezone
        self.user = User.objects.create_user("sgc", password="x")
        self.profile = SellerProfile.objects.create(
            user=self.user, ntn_cnic="1234567", business_name="S",
            province="Sindh", address="K")
        self.inv = Invoice.objects.create(
            owner=self.user, seller_profile=self.profile,
            invoice_type="Sale Invoice", invoice_date="2026-07-01",
            buyer_business_name="B", buyer_registration_type="Registered",
            status="valid", submitted_at=timezone.now())
        InvoiceItem.objects.create(
            invoice=self.inv, hs_code="0101.2100", product_description="t",
            sale_type="Goods at standard rate (default)", quantity=1,
            value_excl_st=1000, sales_tax=180, rate="18%")

    def test_list_page_shows_items_button_for_modifiable(self):
        self.client.login(username="sgc", password="x")
        r = self.client.get("/digital-invoicing/invoices/")
        self.assertContains(r, "toggleItems")
        self.assertContains(r, f"items-{self.inv.pk}")

    def test_is_modifiable_property(self):
        from django.utils import timezone
        from datetime import timedelta
        self.assertTrue(self.inv.is_modifiable)
        self.inv.submitted_at = timezone.now() - timedelta(hours=73)
        self.assertFalse(self.inv.is_modifiable)

    def test_sync_survives_fbr_outage(self):
        from .reference_data import ReferenceSyncService

        class DeadClient:
            def trans_types(self):
                raise ConnectionError("gateway down")

        r = ReferenceSyncService(client=DeadClient()).sync_trans_type_ids()
        self.assertIn("error", r)
        self.assertEqual(r["matched"], [])

    def test_rate_check_skips_failing_type(self):
        from .reference_data import ReferenceSyncService, MockReferenceClient
        from .models import TaxSaleType

        class FlakyClient(MockReferenceClient):
            def sale_type_to_rate(self, **kw):
                if kw.get("trans_type_id") == 75:
                    raise TimeoutError("slow")
                return super().sale_type_to_rate(**kw)

        svc = ReferenceSyncService(client=FlakyClient())
        svc.sync_trans_type_ids()
        report = svc.check_rate_drift()
        std = next(d for d in report
                   if d["sale_type"] == "Goods at standard rate (default)")
        self.assertIn("error", std)
        # Doosre types phir bhi check hue
        self.assertTrue(any(not d.get("error") for d in report))


class DashboardV2Tests(TestCase):
    """UI sprint — dashboard control-room widgets."""

    def setUp(self):
        from django.contrib.auth.models import User
        from django.utils import timezone
        from datetime import date
        self.user = User.objects.create_user("dash2", password="x")
        self.profile = SellerProfile.objects.create(
            user=self.user, ntn_cnic="1234567", business_name="S",
            province="Sindh", address="K", use_sandbox=True)
        inv = Invoice.objects.create(
            owner=self.user, seller_profile=self.profile,
            invoice_type="Sale Invoice", invoice_date=date.today(),
            buyer_business_name="B", buyer_registration_type="Registered",
            total_value=5000, invoice_total=5900, status="valid",
            scenario_id="SN001", submitted_at=timezone.now())
        InvoiceItem.objects.create(
            invoice=inv, hs_code="0101.2100", product_description="Widget",
            sale_type="Goods at standard rate (default)", quantity=1,
            value_excl_st=5000, sales_tax=900, rate="18%")
        Invoice.objects.create(
            owner=self.user, seller_profile=self.profile,
            invoice_type="Sale Invoice", invoice_date=date.today(),
            buyer_business_name="B", buyer_registration_type="Registered",
            status="pending_retry")
        self.client.login(username="dash2", password="x")

    def test_dashboard_renders_v2_widgets(self):
        r = self.client.get("/digital-invoicing/dashboard/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Today's sales")
        self.assertContains(r, "Pending retry")            # actionable chip
        self.assertContains(r, "?status=pending_retry")    # filtered link
        self.assertContains(r, "Sandbox mode")
        self.assertContains(r, "Top HS codes")
        self.assertContains(r, "0101.2100")
        self.assertContains(r, "SN001")

    def test_chips_hidden_when_zero(self):
        Invoice.objects.filter(status="pending_retry").delete()
        r = self.client.get("/digital-invoicing/dashboard/")
        self.assertNotContains(r, "?status=pending_retry")


class LoginUITests(TestCase):
    """UI sprint — login screen polish."""

    def test_login_page_has_ux_features(self):
        r = self.client.get("/digital-invoicing/login/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "pwToggle")          # show/hide
        self.assertContains(r, "capsWarn")          # caps lock warning
        self.assertContains(r, "Logging in")        # loading state JS
        self.assertContains(r, "Tokens encrypted")  # trust footer
        self.assertContains(r, 'autocomplete="current-password"')

    def test_login_still_works(self):
        from django.contrib.auth.models import User
        User.objects.create_user("uilogin", password="x")
        r = self.client.post("/digital-invoicing/login/",
                             {"username": "uilogin", "password": "x"})
        self.assertEqual(r.status_code, 302)


class BuyersUITests(TestCase):
    """UI sprint — buyers screen."""

    def setUp(self):
        from django.contrib.auth.models import User
        self.user = User.objects.create_user("buyui", password="x")
        SellerProfile.objects.create(
            user=self.user, ntn_cnic="1234567", business_name="S",
            province="Sindh", address="K")
        Buyer.objects.create(owner=self.user, business_name="Alpha Traders",
                             ntn_cnic="7654321",
                             registration_type="Registered", province="Sindh")
        Buyer.objects.create(owner=self.user, business_name="Beta Store",
                             registration_type="Unregistered",
                             province="Sindh")
        self.client.login(username="buyui", password="x")

    def test_buyers_page_v2(self):
        r = self.client.get("/digital-invoicing/buyers/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "2 buyers")            # stats chips
        self.assertContains(r, "1 registered")
        self.assertContains(r, "exportCSV")            # export
        self.assertContains(r, "sortT(0")              # sortable
        self.assertContains(r, "ntnCheck")             # live NTN validation
        self.assertContains(r, "Invoice history")      # history link

    def test_stats_respect_owner_isolation(self):
        from django.contrib.auth.models import User
        other = User.objects.create_user("buyui2", password="x")
        Buyer.objects.create(owner=other, business_name="Ghost",
                             registration_type="Registered", province="Sindh")
        r = self.client.get("/digital-invoicing/buyers/")
        self.assertContains(r, "2 buyers")             # doosre user ka nahi gina


class ProductsUITests(TestCase):
    """UI sprint — products screen + sale-type dropdown compliance fix."""

    def setUp(self):
        from django.contrib.auth.models import User
        from .models import Product
        self.user = User.objects.create_user("produi", password="x")
        SellerProfile.objects.create(
            user=self.user, ntn_cnic="1234567", business_name="S",
            province="Sindh", address="K")
        Product.objects.create(owner=self.user, name="Cement Bag",
                               hs_code="6810.1100",
                               sale_type="Cement /Concrete Block",
                               default_price=1200, track_stock=True)
        Product.objects.create(owner=self.user, name="Old Item",
                               sale_type="Goods at standard rate",  # legacy
                               default_price=100, track_stock=False)
        self.client.login(username="produi", password="x")

    def test_dropdown_has_official_24_types(self):
        r = self.client.get("/digital-invoicing/products/")
        self.assertContains(r, "Goods at standard rate (default)")
        self.assertContains(r, "Potassium Chlorate")
        self.assertContains(r, "Petroleum Products")
        self.assertContains(r, "Steel melting and re-rolling")

    def test_legacy_value_preserved_when_editing(self):
        from .models import Product
        old = Product.objects.get(name="Old Item")
        r = self.client.get(f"/digital-invoicing/products/?edit={old.pk}")
        # Legacy stored value dropdown mein selected option ke tor par mile
        self.assertContains(r, "<option selected>Goods at standard rate</option>")

    def test_stats_and_widgets(self):
        r = self.client.get("/digital-invoicing/products/")
        self.assertContains(r, "2 products")
        self.assertContains(r, "1 stock-tracked")
        self.assertContains(r, "without HS code")     # Old Item ka HS khali
        self.assertContains(r, "exportCSV")
        self.assertContains(r, "sortT(0")
        self.assertContains(r, "FBR error 0019")      # HS live check


class InvoiceListUITests(TestCase):
    """UI sprint — invoices list screen."""

    def setUp(self):
        from django.contrib.auth.models import User
        from django.utils import timezone
        from datetime import date
        self.user = User.objects.create_user("listui", password="x")
        self.profile = SellerProfile.objects.create(
            user=self.user, ntn_cnic="1234567", business_name="S",
            province="Sindh", address="K")
        Invoice.objects.create(
            owner=self.user, seller_profile=self.profile,
            invoice_type="Sale Invoice", invoice_date=date.today(),
            buyer_business_name="Alpha", buyer_registration_type="Registered",
            total_value=1000, total_sales_tax=180, total_further_tax=40,
            invoice_total=1220, status="valid", submitted_at=timezone.now(),
            fbr_invoice_number="1234567890-1")
        Invoice.objects.create(
            owner=self.user, seller_profile=self.profile,
            invoice_type="Sale Invoice", invoice_date=date.today(),
            buyer_business_name="Beta", buyer_registration_type="Registered",
            total_value=500, total_sales_tax=90, invoice_total=590,
            status="pending_retry")
        self.client.login(username="listui", password="x")

    def test_all_statuses_in_filter(self):
        r = self.client.get("/digital-invoicing/invoices/")
        # Naye statuses (Milestone 3/5) ab filter mein hain
        self.assertContains(r, 'value="pending_retry"')
        self.assertContains(r, 'value="partially_edited"')
        self.assertContains(r, "Partially Edited &amp; Cancelled")

    def test_filtered_totals_chips(self):
        r = self.client.get("/digital-invoicing/invoices/")
        self.assertContains(r, "2 invoices")
        self.assertContains(r, "Rs 1,500")      # 1000 + 500
        self.assertContains(r, "Sales tax")
        # Filter lagne par totals badlein
        r = self.client.get("/digital-invoicing/invoices/?status=valid")
        self.assertContains(r, "1 invoice")
        self.assertContains(r, "Rs 1,000")

    def test_csv_export_respects_filters(self):
        r = self.client.get("/digital-invoicing/invoices.csv?status=valid")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/csv", r["Content-Type"])
        body = r.content.decode("utf-8")
        self.assertIn("1234567890-1", body)
        self.assertIn("Alpha", body)
        self.assertNotIn("Beta", body)          # filter respected

    def test_csv_owner_isolation(self):
        from django.contrib.auth.models import User
        other = User.objects.create_user("listui2", password="x")
        p2 = SellerProfile.objects.create(
            user=other, ntn_cnic="7777777", business_name="Other",
            province="Sindh", address="K")
        Invoice.objects.create(
            owner=other, seller_profile=p2, invoice_type="Sale Invoice",
            invoice_date="2026-07-01", buyer_business_name="GhostBuyer",
            buyer_registration_type="Registered", status="valid")
        r = self.client.get("/digital-invoicing/invoices.csv")
        self.assertNotIn("GhostBuyer", r.content.decode("utf-8"))


class InvoiceCreateUITests(TestCase):
    """UI sprint — invoice create screen (error panel, draft, shortcuts)."""

    def setUp(self):
        from django.contrib.auth.models import User
        self.user = User.objects.create_user("crui", password="x")
        SellerProfile.objects.create(
            user=self.user, ntn_cnic="1234567", business_name="S",
            province="Sindh", address="K")
        self.client.login(username="crui", password="x")

    def test_create_screen_has_new_ux(self):
        r = self.client.get("/digital-invoicing/create/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'id="errPanel"')       # error panel
        self.assertContains(r, "showErrPanel")
        self.assertContains(r, "DRAFT_KEY")            # local draft autosave
        self.assertContains(r, "restoreDraft")
        self.assertContains(r, "Alt+N")                # shortcut hint on Clear
        self.assertContains(r, "ERR_HELP")             # friendly error help

    def test_no_alert_for_validation_errors(self):
        r = self.client.get("/digital-invoicing/create/")
        body = r.content.decode()
        self.assertNotIn("alert('✗ Validation failed", body)

    def test_error_help_covers_key_codes(self):
        r = self.client.get("/digital-invoicing/create/")
        body = r.content.decode()
        for code in ["0044", "0062", "0097", "0102", "0104", "0105"]:
            self.assertIn(f'"{code}":', body)

    def test_single_script_block_no_sidebar_leak(self):
        r = self.client.get("/digital-invoicing/create/")
        body = r.content.decode()
        nav_end = body.find('class="main"')
        self.assertNotIn("DRAFT_KEY", body[:nav_end] if nav_end > 0 else "")


class ReportsUITests(TestCase):
    """UI sprint — reports screen (Annex-C sale-type breakdown, nav, print)."""

    def setUp(self):
        from django.contrib.auth.models import User
        from django.utils import timezone
        from datetime import date
        self.user = User.objects.create_user("repui", password="x")
        self.profile = SellerProfile.objects.create(
            user=self.user, ntn_cnic="1234567", business_name="S",
            province="Sindh", address="K")
        self.period = date.today().strftime("%Y-%m")
        inv = Invoice.objects.create(
            owner=self.user, seller_profile=self.profile,
            invoice_type="Sale Invoice", invoice_date=date.today(),
            buyer_business_name="Alpha", buyer_registration_type="Registered",
            total_value=1000, total_sales_tax=180, invoice_total=1180,
            status="valid", submitted_at=timezone.now())
        InvoiceItem.objects.create(
            invoice=inv, hs_code="0101.2100", product_description="A",
            sale_type="Goods at standard rate (default)", quantity=1,
            value_excl_st=1000, sales_tax=180, rate="18%")
        InvoiceItem.objects.create(
            invoice=inv, hs_code="6810.1100", product_description="Cement",
            sale_type="Cement /Concrete Block", quantity=12,
            value_excl_st=123, sales_tax=36, rate="Rs.3",
            item_status="cancelled")     # cancelled — report se bahar
        self.client.login(username="repui", password="x")

    def test_sale_type_breakdown_rendered(self):
        r = self.client.get(f"/digital-invoicing/reports/?period={self.period}")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Sale type breakdown")
        self.assertContains(r, "Goods at standard rate (default)")

    def test_cancelled_items_excluded_from_report(self):
        from .services import ReportService
        rows = ReportService(self.user).sale_type_report(period=self.period)
        types = {r["sale_type"] for r in rows}
        self.assertIn("Goods at standard rate (default)", types)
        self.assertNotIn("Cement /Concrete Block", types)   # cancelled

    def test_period_nav_and_print(self):
        r = self.client.get("/digital-invoicing/reports/?period=2026-01")
        self.assertContains(r, "period=2025-12")     # prev (year rollover)
        self.assertContains(r, "period=2026-02")     # next
        self.assertContains(r, "window.print()")
        self.assertContains(r, "@media print")

    def test_thousand_separators(self):
        r = self.client.get(f"/digital-invoicing/reports/?period={self.period}")
        self.assertContains(r, "Rs 1,000")

    def test_report_owner_isolation(self):
        from django.contrib.auth.models import User
        from datetime import date
        other = User.objects.create_user("repui2", password="x")
        p2 = SellerProfile.objects.create(
            user=other, ntn_cnic="7777777", business_name="O",
            province="Sindh", address="K")
        inv = Invoice.objects.create(
            owner=other, seller_profile=p2, invoice_type="Sale Invoice",
            invoice_date=date.today(), buyer_business_name="Ghost",
            buyer_registration_type="Registered", status="valid")
        InvoiceItem.objects.create(
            invoice=inv, hs_code="0101.2100", product_description="Ghost",
            sale_type="Petroleum Products", quantity=1, value_excl_st=9999,
            sales_tax=143, rate="1.43%")
        from .services import ReportService
        rows = ReportService(self.user).sale_type_report(period=self.period)
        self.assertNotIn("Petroleum Products",
                         {r["sale_type"] for r in rows})


class SettingsUITests(TestCase):
    """UI sprint — settings/profile screen (token handling footgun fix)."""

    def setUp(self):
        from django.contrib.auth.models import User
        self.user = User.objects.create_user("setui", password="x")
        self.p = SellerProfile.objects.create(
            user=self.user, ntn_cnic="1234567", business_name="Acme",
            province="Sindh", address="K", fbr_token="REAL_TOKEN_ABCD",
            use_sandbox=True)
        self.client.login(username="setui", password="x")

    def _post(self, **extra):
        data = {"edit_id": self.p.pk, "ntn_cnic": "1234567",
                "business_name": "Acme", "province": "Sindh",
                "address": "K", "fbr_token": "", "use_sandbox": "on"}
        data.update(extra)
        return self.client.post("/digital-invoicing/profile/", data)

    def test_token_never_rendered_in_html(self):
        r = self.client.get(f"/digital-invoicing/profile/?edit={self.p.pk}")
        body = r.content.decode()
        self.assertNotIn("REAL_TOKEN_ABCD", body)      # plaintext nahi
        self.assertNotIn("enc$", body)                  # ciphertext blob bhi nahi
        self.assertIn("✓ Token set", body)              # sirf status
        self.assertIn("••••••••ABCD", body)             # last-4 hint

    def test_blank_token_keeps_existing(self):
        self._post(fbr_token="")                        # field khali chhoda
        self.p.refresh_from_db()
        self.assertEqual(self.p.fbr_token_plain, "REAL_TOKEN_ABCD")

    def test_new_token_replaces(self):
        self._post(fbr_token="NEW_TOKEN_9999")
        self.p.refresh_from_db()
        self.assertEqual(self.p.fbr_token_plain, "NEW_TOKEN_9999")

    def test_explicit_remove_clears_token(self):
        self._post(remove_token="on")
        self.p.refresh_from_db()
        self.assertEqual(self.p.fbr_token_plain, "")

    def test_token_masked_property(self):
        self.assertEqual(self.p.token_masked, "••••••••ABCD")
        self.p.fbr_token = ""
        self.assertEqual(self.p.token_masked, "")

    def test_production_warning_present(self):
        r = self.client.get(f"/digital-invoicing/profile/?edit={self.p.pk}")
        self.assertContains(r, "Production mode")
        self.assertContains(r, "REAL FBR submissions")

    def test_other_fields_still_save(self):
        self._post(business_name="Acme Renamed", address="New Address")
        self.p.refresh_from_db()
        self.assertEqual(self.p.business_name, "Acme Renamed")
        self.assertEqual(self.p.fbr_token_plain, "REAL_TOKEN_ABCD")


class FinalThreeScreensTests(TestCase):
    """UI sprint — Audit Log, My Account, Help screens."""

    def setUp(self):
        from django.contrib.auth.models import User
        self.user = User.objects.create_user("f3ui", password="OldPass123!")
        SellerProfile.objects.create(
            user=self.user, ntn_cnic="1234567", business_name="S",
            province="Sindh", address="K")
        self.client.login(username="f3ui", password="OldPass123!")

    # ---- Audit Log ----
    def test_audit_log_filters_and_csv(self):
        from .models import AuditLog
        AuditLog.objects.create(user=self.user, action="login", ip="1.1.1.1")
        AuditLog.objects.create(user=self.user, action="invoice_valid",
                                ip="1.1.1.1", detail={"total": 500})
        r = self.client.get("/digital-invoicing/activity/?action=login")
        self.assertContains(r, "1 event")                       # filtered count
        self.assertNotContains(r, 'class="pill valid"')          # valid-invoice row nahi
        r = self.client.get("/digital-invoicing/activity/?export=csv")
        self.assertIn("text/csv", r["Content-Type"])
        self.assertIn("Login", r.content.decode("utf-8"))

    def test_audit_log_owner_isolation(self):
        from django.contrib.auth.models import User
        from .models import AuditLog
        other = User.objects.create_user("f3ui2", password="x")
        AuditLog.objects.create(user=other, action="login", ip="9.9.9.9")
        r = self.client.get("/digital-invoicing/activity/")
        self.assertNotContains(r, "9.9.9.9")

    # ---- My Account ----
    def test_email_update(self):
        self.client.post("/digital-invoicing/account/",
                         {"form": "email", "email": "u@x.pk"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "u@x.pk")

    def test_password_change_keeps_session(self):
        r = self.client.post("/digital-invoicing/account/", {
            "form": "password", "old_password": "OldPass123!",
            "new_password1": "NewPass456!", "new_password2": "NewPass456!"})
        self.assertContains(r, "Password changed")
        # session valid rahe (update_session_auth_hash)
        r = self.client.get("/digital-invoicing/account/")
        self.assertEqual(r.status_code, 200)
        # naya password kaam kare
        self.client.logout()
        self.assertTrue(self.client.login(username="f3ui",
                                          password="NewPass456!"))

    def test_password_change_wrong_old_rejected(self):
        self.client.post("/digital-invoicing/account/", {
            "form": "password", "old_password": "WRONG",
            "new_password1": "NewPass456!", "new_password2": "NewPass456!"})
        self.client.logout()
        self.assertTrue(self.client.login(username="f3ui",
                                          password="OldPass123!"))

    # ---- Help ----
    def test_help_page(self):
        r = self.client.get("/digital-invoicing/help/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "From setup to live")
        self.assertContains(r, "<kbd>Ctrl</kbd> + <kbd>Enter</kbd>")
        self.assertContains(r, "0044")
        self.assertContains(r, "Sandbox")

    def test_sidebar_has_new_links(self):
        r = self.client.get("/digital-invoicing/dashboard/")
        self.assertContains(r, "My Account")
        self.assertContains(r, "Help")
        self.assertContains(r, "Audit Log")
