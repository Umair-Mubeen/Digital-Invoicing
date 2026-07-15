"""
models.py  —  Digital Invoicing data model.

Stores the invoice, its line items, and the raw FBR response (JSON payload +
returned number) so you meet the record-retention requirement and keep a full
audit trail (source invoice + payload + FBR response).
"""

from django.db import models
from django.conf import settings


class Buyer(models.Model):
    """A saved customer, so operators don't re-key buyer details each time."""
    REG_CHOICES = [("Registered", "Registered"), ("Unregistered", "Unregistered")]
    PROVINCES = [(p, p) for p in
                 ["Sindh", "Punjab", "KPK", "Balochistan",
                  "Islamabad", "AJK", "Gilgit-Baltistan"]]

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.CASCADE, related_name="buyers")
    business_name = models.CharField(max_length=255)
    ntn_cnic = models.CharField(max_length=15, blank=True)
    strn = models.CharField("Sales Tax Reg No (STRN)", max_length=20, blank=True)
    registration_type = models.CharField(max_length=20, choices=REG_CHOICES,
                                          default="Unregistered")
    province = models.CharField(max_length=30, choices=PROVINCES, default="Sindh")
    address = models.CharField(max_length=500, blank=True)
    times_used = models.PositiveIntegerField(default=0)
    last_used = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-last_used"]
        indexes = [models.Index(fields=["owner", "ntn_cnic"])]

    def __str__(self):
        return f"{self.business_name} ({self.registration_type})"


class Invoice(models.Model):
    TYPE_CHOICES = [("Sale Invoice", "Sale Invoice"), ("Debit Note", "Debit Note")]
    # Lifecycle per DI User Manual v1.6 (cancellation/edit statuses ke liye
    # ready — workflow Phase 8 mein aayega, vocabulary abhi se stable).
    STATUS = [
        ("draft", "Draft"), ("valid", "Valid"), ("failed", "Failed"),
        ("pending_retry", "Pending Retry"),
        ("edited", "Edited"), ("cancelled", "Cancelled"),
        ("partially_edited", "Partially Edited"),
        ("partially_cancelled", "Partially Cancelled"),
        ("partially_edited_cancelled", "Partially Edited & Cancelled"),
    ]

    # owner (multi-tenant: each business account)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                              related_name="invoices")

    invoice_type = models.CharField(max_length=30, choices=TYPE_CHOICES,
                                    default="Sale Invoice")
    invoice_date = models.DateField()
    scenario_id = models.CharField(max_length=10, blank=True)   # sandbox only

    # seller — kaunse business se issue hui (snapshot fields neeche)
    seller_profile = models.ForeignKey("SellerProfile", null=True, blank=True,
                                       on_delete=models.SET_NULL,
                                       related_name="invoices")
    seller_ntn_cnic = models.CharField(max_length=15)
    seller_business_name = models.CharField(max_length=255)
    seller_province = models.CharField(max_length=30)
    seller_address = models.CharField(max_length=500)

    # buyer snapshot (denormalised so historical invoices stay accurate)
    buyer = models.ForeignKey(Buyer, null=True, blank=True,
                              on_delete=models.SET_NULL)
    buyer_ntn_cnic = models.CharField(max_length=15, blank=True)
    buyer_business_name = models.CharField(max_length=255)
    buyer_province = models.CharField(max_length=30)
    buyer_address = models.CharField(max_length=500, blank=True)
    buyer_registration_type = models.CharField(max_length=20,
                                               default="Unregistered")
    invoice_ref_no = models.CharField(max_length=100, blank=True)
    reason = models.CharField(max_length=100, blank=True)          # debit note reason
    reason_remarks = models.CharField(max_length=500, blank=True)  # required if reason = Others

    # computed totals
    total_value = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    total_sales_tax = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    total_further_tax = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    invoice_total = models.DecimalField(max_digits=16, decimal_places=2, default=0)

    # FBR result
    status = models.CharField(max_length=30, choices=STATUS, default="draft")
    # Milestone 5 — Queue & Retry (Manual v1.6 §4.2)
    retry_count = models.PositiveSmallIntegerField(default=0)
    next_retry_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_error = models.CharField(max_length=300, blank=True, default="")
    # NULL (not "") jab number nahi mila — MySQL unique index multiple NULLs
    # allow karta hai lekin multiple "" nahi; is liye null=True + unique=True.
    fbr_invoice_number = models.CharField(max_length=60, null=True, blank=True,
                                          unique=True)
    fbr_dated = models.CharField(max_length=30, blank=True)
    fbr_payload = models.JSONField(null=True, blank=True)     # what we sent
    fbr_response = models.JSONField(null=True, blank=True)    # what FBR returned

    created_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    # Opaque public identifier (URLs/APIs ke liye — int PK enumerable hai)
    public_id = models.UUIDField(unique=True, null=True, blank=True,
                                 editable=False, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["owner", "status"]),
            models.Index(fields=["owner", "-invoice_date"]),
            models.Index(fields=["invoice_date"]),
        ]

    def save(self, *args, **kwargs):
        if self.public_id is None:
            import uuid
            self.public_id = uuid.uuid4()
        # "" kabhi store na ho — unique constraint NULL pe hi kaam karta hai
        if self.fbr_invoice_number == "":
            self.fbr_invoice_number = None
        super().save(*args, **kwargs)

    def __str__(self):
        return self.fbr_invoice_number or f"Draft #{self.pk}"

    @property
    def is_locked(self):
        """Correction window band? Manual v1.6 p.30: invoice return mein
        move hoti hai 72 GHANTE ke baad YA MONTH-END par — jo pehle aaye.
        Uske baad cancel/edit IRIS par bhi allowed nahi."""
        from django.utils import timezone
        from datetime import timedelta
        if not self.submitted_at:
            return False
        now = timezone.now()
        if now > self.submitted_at + timedelta(hours=72):
            return True
        # month-end lock: submission ke mahine ke baad
        sub = timezone.localtime(self.submitted_at)
        return (now.year, now.month) > (sub.year, sub.month)

    @property
    def is_modifiable(self):
        """Template helper: cancel/edit actions dikhane chahiyein?"""
        return (self.status in ("valid", "edited", "partially_edited",
                                "partially_cancelled",
                                "partially_edited_cancelled")
                and not self.is_locked)

    @property
    def has_edited_items(self):
        return self.items.filter(item_status="edited").exists()

    def refresh_modification_status(self):
        """Item statuses se invoice status derive karo (Manual p.22
        vocabulary). Save karta hai. Sirf modification-statuses touch hote
        hain; failed/draft waisi hi rehti hain."""
        if self.status not in ("valid", "edited", "cancelled",
                               "partially_edited", "partially_cancelled",
                               "partially_edited_cancelled"):
            return self.status
        counts = {"active": 0, "cancelled": 0, "edited": 0}
        for s in self.items.values_list("item_status", flat=True):
            counts[s] = counts.get(s, 0) + 1
        total = sum(counts.values())
        if counts["cancelled"] == total and total:
            new = "cancelled"
        elif counts["edited"] and counts["cancelled"]:
            new = "partially_edited_cancelled"
        elif counts["edited"]:
            new = "edited" if counts["edited"] == total else "partially_edited"
        elif counts["cancelled"]:
            new = "partially_cancelled"
        else:
            new = "valid"
        if new != self.status:
            self.status = new
            self.save(update_fields=["status"])
        return self.status


class InvoiceItem(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE,
                                related_name="items")
    hs_code = models.CharField(max_length=20)
    product_description = models.CharField(max_length=500)
    sale_type = models.CharField(max_length=60)
    uom = models.CharField(max_length=60, default="Numbers, pieces, units")
    # PRAL spec: quantity 4 decimal places tak allowed (Error Guide 0302)
    quantity = models.DecimalField(max_digits=18, decimal_places=4, default=1)
    value_excl_st = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    retail_price = models.DecimalField(max_digits=16, decimal_places=2, default=0)

    # PRAL v1.12 item fields — pehle sirf fbr_payload JSON mein the; ab
    # queryable (WHT/FED/discount reports ke liye)
    sales_tax_withheld = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    extra_tax = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    fed_payable = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    total_values = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    sro_item_serial_no = models.CharField(max_length=60, blank=True, default="")

    # Milestone 3 — cancellation/edit workflow (Manual v1.6 §4.1)
    ITEM_STATUS = [("active", "Active"), ("cancelled", "Cancelled"),
                   ("edited", "Edited")]
    item_status = models.CharField(max_length=12, choices=ITEM_STATUS,
                                   default="active")
    edited_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    # Pre-edit snapshot — Manual p.30: original details baad mein bhi
    # viewable rehni chahiyein. Edit se pehle ke sab tax/value fields.
    original_snapshot = models.JSONField(null=True, blank=True)

    # computed
    # "18% along with rupees 60 per kilogram" (38 chars) fit hona chahiye
    rate = models.CharField(max_length=60, blank=True)
    sales_tax = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    further_tax = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    sro_schedule = models.CharField(max_length=60, blank=True)

    def __str__(self):
        return f"{self.product_description} × {self.quantity}"


class SellerProfile(models.Model):
    """User ke businesses/suppliers — EK user ke MULTIPLE ho sakte hain
    (practitioner clients ke liye, ya apne 2-3 registered businesses).
    Invoice banate waqt dropdown se select hota hai."""
    PROVINCES = Buyer.PROVINCES

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="seller_profiles")
    ntn_cnic = models.CharField("NTN / CNIC", max_length=15)
    business_name = models.CharField(max_length=255)
    province = models.CharField(max_length=30, choices=PROVINCES, default="Sindh")
    address = models.CharField(max_length=500)
    # Per-supplier FBR credentials (SaaS: har business apna token daalta hai)
    # Encrypted at rest (crypto.py) — plaintext kabhi DB mein na jaye.
    # Read: .fbr_token_plain   Write: .fbr_token = <plaintext> (save encrypts)
    fbr_token = models.CharField(max_length=512, blank=True,
                                 help_text="PRAL se mila Bearer token (5-saal)")
    use_sandbox = models.BooleanField(default=True,
                                      help_text="ON = sandbox/testing; OFF = production")
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        from .crypto import encrypt
        if self.fbr_token:
            self.fbr_token = encrypt(self.fbr_token)
        super().save(*args, **kwargs)

    @property
    def fbr_token_plain(self):
        from .crypto import decrypt
        return decrypt(self.fbr_token)

    def __str__(self):
        return f"{self.business_name} ({self.ntn_cnic})"


class AuditLog(models.Model):
    """Har ahem event ka record — kaun, kya, kab, kis IP se. Audit trail."""
    ACTIONS = [
        ("signup", "Signup"), ("login", "Login"), ("login_failed", "Login failed"),
        ("logout", "Logout"), ("profile_saved", "Profile saved"),
        ("invoice_valid", "Invoice validated"), ("invoice_failed", "Invoice rejected"),
        ("invoice_printed", "Invoice printed"),
        ("invoice_cancelled", "Invoice cancelled"),
        ("item_cancelled", "Invoice item cancelled"),
        ("item_edited", "Invoice item edited"),
    ]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="audit_logs")
    username = models.CharField(max_length=150, blank=True)   # snapshot (user delete ho to bhi rahe)
    action = models.CharField(max_length=30, choices=ACTIONS)
    detail = models.JSONField(default=dict, blank=True)
    ip = models.CharField(max_length=45, blank=True)
    path = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "-created_at"]),
                   models.Index(fields=["action"])]

    def __str__(self):
        return f"{self.username or 'anon'} · {self.action} · {self.created_at:%Y-%m-%d %H:%M}"


class HSCode(models.Model):
    """Tax-intelligent HS directory — har code ke saath default schedule,
    sale type aur practitioner note. Ye TaxBuddy ki curated tax knowledge hai
    (admin se edit hoti hai). System SUGGEST karta hai; final classification
    hamesha user/practitioner ki hai."""
    # Official PRAL DI labels (Scenarios doc v1.11). Legacy labels neeche
    # rakhe hain taake purani rows validate hoti rahein — engine alias se
    # resolve karta hai (tax_engine.LEGACY_ALIASES).
    SALE_TYPES = [(n, n) for n in [
        "Goods at standard rate (default)", "Goods at Reduced Rate",
        "3rd Schedule Goods", "Exempt goods", "Goods at zero-rate",
        "Steel melting and re-rolling", "Ship breaking", "Cotton ginners",
        "Telecommunication services", "Toll Manufacturing",
        "Petroleum Products", "Electricity Supply to Retailers",
        "Gas to CNG stations", "Mobile Phones",
        "Processing/Conversion of Goods", "Goods (FED in ST Mode)",
        "Services (FED in ST Mode)", "Services", "Electric Vehicle",
        "Cement /Concrete Block", "Potassium Chlorate", "CNG Sales",
        "Goods as per SRO.297(|)/2023", "Non-Adjustable Supplies",
        # legacy
        "Goods at standard rate", "Goods at reduced rate",
        "Exempt Goods", "Zero-rated Goods",
    ]]
    hs_code = models.CharField(max_length=12, unique=True)   # XXXX.XXXX
    description = models.CharField(max_length=255)
    uoms = models.CharField(max_length=255, blank=True,
                            help_text="Pipe-separated, pehla default (e.g. KG|Bag)")
    default_sale_type = models.CharField(max_length=40, choices=SALE_TYPES,
                                         default="Goods at standard rate")
    schedule_hint = models.CharField(max_length=120, blank=True,
                                     help_text="e.g. '3rd Schedule — retail price pe ST'")
    note = models.CharField(max_length=255, blank=True,
                            help_text="Practitioner note / VERIFY flag")
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["hs_code"]

    def __str__(self):
        return f"{self.hs_code} — {self.description[:40]}"

    def uom_list(self):
        return [u for u in self.uoms.split("|") if u]



class SavedItem(models.Model):
    """Frequent products — valid invoice se KHUD save hote hain.
    Create page pe chips ban ke dikhte hain: ek click = poori row."""
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                              related_name="saved_items")
    hs_code = models.CharField(max_length=12)
    description = models.CharField(max_length=255)
    sale_type = models.CharField(max_length=40, default="Goods at standard rate")
    uom = models.CharField(max_length=50, default="Numbers, pieces, units")
    last_value = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    times_used = models.PositiveIntegerField(default=0)
    last_used = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-times_used", "-last_used"]
        unique_together = [("owner", "hs_code", "description")]

    def __str__(self):
        return f"{self.description} ({self.hs_code})"



class ATLStatus(models.Model):
    """FBR Sales Tax Active Taxpayer List — har buyer ka MAHINA-wise status.
    Practitioner FBR ki ATL file (CSV) upload karta hai; system reg-no ke
    against Active/Inactive save karta hai (period = YYYY-MM)."""
    STATUS = [("Active", "Active"), ("Inactive", "Inactive")]
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                              related_name="atl_records")
    reg_no = models.CharField(max_length=20, db_index=True)   # NTN/STRN/CNIC
    period = models.CharField(max_length=7)                   # "2026-07"
    status = models.CharField(max_length=10, choices=STATUS, default="Active")
    # FBR/IRIS se download ki hui official ATL PDF — per party per month proof
    evidence_pdf = models.FileField(upload_to="atl_evidence/%Y/%m/",
                                    null=True, blank=True)
    # True = status PDF ke text se khud parh kar confirm hua (NTN match ke saath)
    verified = models.BooleanField(default=False)
    uploaded_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-period"]
        unique_together = [("owner", "reg_no", "period")]
        indexes = [models.Index(fields=["owner", "reg_no", "period"])]

    def __str__(self):
        return f"{self.reg_no} · {self.period} · {self.status}"

# ---------------------------------------------------------------------------
# Phase 7 — Configurable Tax Rule Tables
# Finance Act / budget changes = Django admin mein data change, code untouched.
# Date-effective: effective_from <= date <= effective_to (NULL = open-ended).
# ---------------------------------------------------------------------------
class TaxSaleType(models.Model):
    """Ek sale type ki ek date-effective configuration row.
    Naya budget rate = NAYI row (purani ko effective_to se close karein) —
    historical invoices ki recomputation bhi sahi rahegi."""
    RATE_TYPES = [
        ("percent", "Percent of value"),
        ("fixed_per_unit", "Fixed Rs. per unit (qty x Rs.X)"),
        ("compound", "Percent + Rs. per unit"),
        ("exempt", "Exempt (no ST)"),
    ]
    name = models.CharField(max_length=80)          # exact FBR DI label
    rate = models.DecimalField(max_digits=6, decimal_places=2)   # %
    rate_type = models.CharField(max_length=20, choices=RATE_TYPES,
                                 default="percent")
    rate_per_unit = models.DecimalField(max_digits=10, decimal_places=2,
                                        default=0)  # Rs.X (fixed/compound)
    rate_label = models.CharField(max_length=60, blank=True, default="",
                                  help_text='Exact PRAL rate string, e.g. '
                                  '"Rs.3" ya "18% along with rupees 60 per '
                                  'kilogram". Khali = "<rate>%"')
    sro_item_serial = models.CharField(max_length=20, blank=True, default="")
    charges_st = models.BooleanField(default=True)
    further_tax_applies = models.BooleanField(default=False)
    sro_schedule = models.CharField(max_length=80, blank=True, default="")
    retail_price_based = models.BooleanField(default=False)
    fbr_trans_type_id = models.IntegerField(null=True, blank=True)  # ref API 5.5
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    legal_reference = models.CharField(max_length=200, blank=True, default="")
    notes = models.TextField(blank=True, default="")

    class Meta:
        indexes = [models.Index(fields=["name", "effective_from"])]
        ordering = ["name", "-effective_from"]

    def __str__(self):
        return f"{self.name} @ {self.rate}% (from {self.effective_from})"


class FurtherTaxConfig(models.Model):
    """Section 3(1A) further tax rate — date-effective."""
    rate = models.DecimalField(max_digits=6, decimal_places=2)   # %
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    legal_reference = models.CharField(max_length=200, blank=True,
                                       default="Section 3(1A), Sales Tax Act 1990")

    class Meta:
        ordering = ["-effective_from"]

    def __str__(self):
        return f"Further Tax {self.rate}% (from {self.effective_from})"


class FurtherTaxExemptHS(models.Model):
    """HS prefixes jin par further tax NAHI (SRO 648(I)/2013 + amendments).
    Prefix match: '3102' saare fertilizer sub-codes cover karta hai."""
    hs_prefix = models.CharField(max_length=12)
    sro_reference = models.CharField(max_length=100, blank=True, default="")
    description = models.CharField(max_length=200, blank=True, default="")
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [models.Index(fields=["hs_prefix"])]
        ordering = ["hs_prefix"]
        verbose_name = "Further-tax exempt HS prefix"
        verbose_name_plural = "Further-tax exempt HS prefixes"

    def __str__(self):
        return f"{self.hs_prefix} ({self.sro_reference})"


class TaxScenario(models.Model):
    """PRAL sandbox scenarios SN001–SN028 (Technical Spec v1.12 §9) —
    onboarding/certification tracking + sale-type mapping."""
    code = models.CharField(max_length=10, unique=True)   # SN001
    description = models.CharField(max_length=200)
    sale_type = models.CharField(max_length=80)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} — {self.description}"


# ---------------------------------------------------------------------------
# Phases 9–12 — Products / Inventory / Suppliers / Purchases
# ---------------------------------------------------------------------------
class Category(models.Model):
    owner = models.ForeignKey("auth.User", on_delete=models.CASCADE)
    name = models.CharField(max_length=100)

    class Meta:
        unique_together = [("owner", "name")]
        ordering = ["name"]
        verbose_name_plural = "Categories"

    def __str__(self):
        return self.name


class Brand(models.Model):
    owner = models.ForeignKey("auth.User", on_delete=models.CASCADE)
    name = models.CharField(max_length=100)

    class Meta:
        unique_together = [("owner", "name")]
        ordering = ["name"]

    def __str__(self):
        return self.name


class Product(models.Model):
    """Product master — SavedItem convenience-cache se aage: SKU, pricing,
    category/brand, stock tracking flag."""
    owner = models.ForeignKey("auth.User", on_delete=models.CASCADE)
    sku = models.CharField(max_length=60, blank=True, default="")
    name = models.CharField(max_length=200)
    hs_code = models.CharField(max_length=20, blank=True, default="")
    sale_type = models.CharField(max_length=80,
                                 default="Goods at standard rate")
    uom = models.CharField(max_length=60, default="Numbers, pieces, units")
    default_price = models.DecimalField(max_digits=16, decimal_places=2,
                                        default=0)   # excl. ST
    category = models.ForeignKey(Category, null=True, blank=True,
                                 on_delete=models.SET_NULL)
    brand = models.ForeignKey(Brand, null=True, blank=True,
                              on_delete=models.SET_NULL)
    track_stock = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["owner", "name"]),
                   models.Index(fields=["owner", "sku"])]

    def __str__(self):
        return f"{self.name} ({self.sku})" if self.sku else self.name

    @property
    def stock(self):
        from django.db.models import Sum
        s = self.movements.aggregate(q=Sum("quantity"))["q"]
        return s or 0


class StockMovement(models.Model):
    """Signed quantity: +in (purchase/adjust), −out (sale/adjust)."""
    KIND = [("purchase", "Purchase"), ("sale", "Sale"),
            ("adjustment", "Adjustment")]
    owner = models.ForeignKey("auth.User", on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE,
                                related_name="movements")
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    kind = models.CharField(max_length=12, choices=KIND)
    reference = models.CharField(max_length=120, blank=True, default="")
    note = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["owner", "product"])]

    def __str__(self):
        return f"{self.product} {self.quantity:+} ({self.kind})"


class Supplier(models.Model):
    owner = models.ForeignKey("auth.User", on_delete=models.CASCADE)
    business_name = models.CharField(max_length=200)
    ntn_cnic = models.CharField(max_length=15, blank=True, default="")
    strn = models.CharField(max_length=20, blank=True, default="")
    registration_type = models.CharField(
        max_length=15, default="Registered",
        choices=[("Registered", "Registered"),
                 ("Unregistered", "Unregistered")])
    province = models.CharField(max_length=40, default="Sindh")
    address = models.CharField(max_length=300, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["business_name"]
        indexes = [models.Index(fields=["owner", "ntn_cnic"])]

    def __str__(self):
        return self.business_name


class PurchaseInvoice(models.Model):
    """Purchase record — INPUT TAX ke liye (return: output − input = payable).
    Note: FBR DI API par purchases submit NAHI hotin (API sirf seller-side
    hai) — ye local books hain."""
    owner = models.ForeignKey("auth.User", on_delete=models.CASCADE)
    seller_profile = models.ForeignKey("SellerProfile", null=True, blank=True,
                                       on_delete=models.SET_NULL)
    supplier = models.ForeignKey(Supplier, null=True, blank=True,
                                 on_delete=models.SET_NULL)
    supplier_name = models.CharField(max_length=200)         # snapshot
    supplier_ntn_cnic = models.CharField(max_length=15, blank=True, default="")
    supplier_invoice_no = models.CharField(max_length=100, blank=True,
                                           default="")
    invoice_date = models.DateField()
    total_value = models.DecimalField(max_digits=16, decimal_places=2,
                                      default=0)             # excl. ST
    total_sales_tax = models.DecimalField(max_digits=16, decimal_places=2,
                                          default=0)         # input tax
    invoice_total = models.DecimalField(max_digits=16, decimal_places=2,
                                        default=0)
    notes = models.CharField(max_length=300, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    public_id = models.UUIDField(unique=True, null=True, blank=True,
                                 editable=False)

    class Meta:
        ordering = ["-invoice_date", "-created_at"]
        indexes = [models.Index(fields=["owner", "invoice_date"])]

    def save(self, *args, **kwargs):
        if self.public_id is None:
            import uuid
            self.public_id = uuid.uuid4()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"PI {self.supplier_invoice_no or self.pk} — {self.supplier_name}"


class PurchaseItem(models.Model):
    purchase = models.ForeignKey(PurchaseInvoice, on_delete=models.CASCADE,
                                 related_name="items")
    product = models.ForeignKey(Product, null=True, blank=True,
                                on_delete=models.SET_NULL)
    description = models.CharField(max_length=200)
    hs_code = models.CharField(max_length=20, blank=True, default="")
    uom = models.CharField(max_length=60, default="Numbers, pieces, units")
    quantity = models.DecimalField(max_digits=18, decimal_places=4, default=1)
    value_excl_st = models.DecimalField(max_digits=16, decimal_places=2,
                                        default=0)
    sales_tax = models.DecimalField(max_digits=16, decimal_places=2,
                                    default=0)    # input tax on line

    def __str__(self):
        return self.description
