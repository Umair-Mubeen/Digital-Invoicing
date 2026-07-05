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

    business_name = models.CharField(max_length=255)
    ntn_cnic = models.CharField(max_length=15, blank=True)
    registration_type = models.CharField(max_length=20, choices=REG_CHOICES,
                                          default="Unregistered")
    province = models.CharField(max_length=30, choices=PROVINCES, default="Sindh")
    address = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.business_name} ({self.registration_type})"


class Invoice(models.Model):
    TYPE_CHOICES = [("Sale Invoice", "Sale Invoice"), ("Debit Note", "Debit Note")]
    STATUS = [("draft", "Draft"), ("valid", "Valid"), ("failed", "Failed")]

    # owner (multi-tenant: each business account)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                              related_name="invoices")

    invoice_type = models.CharField(max_length=30, choices=TYPE_CHOICES,
                                    default="Sale Invoice")
    invoice_date = models.DateField()
    scenario_id = models.CharField(max_length=10, blank=True)   # sandbox only

    # seller (the account's own business profile — usually pre-filled)
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

    # computed totals
    total_value = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    total_sales_tax = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    total_further_tax = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    invoice_total = models.DecimalField(max_digits=16, decimal_places=2, default=0)

    # FBR result
    status = models.CharField(max_length=10, choices=STATUS, default="draft")
    fbr_invoice_number = models.CharField(max_length=60, blank=True)
    fbr_dated = models.CharField(max_length=30, blank=True)
    fbr_payload = models.JSONField(null=True, blank=True)     # what we sent
    fbr_response = models.JSONField(null=True, blank=True)    # what FBR returned

    created_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.fbr_invoice_number or f"Draft #{self.pk}"

    @property
    def is_locked(self):
        """72-hour edit window (STGO 01 of 2026)."""
        from django.utils import timezone
        from datetime import timedelta
        if not self.submitted_at:
            return False
        return timezone.now() > self.submitted_at + timedelta(hours=72)


class InvoiceItem(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE,
                                related_name="items")
    hs_code = models.CharField(max_length=20)
    product_description = models.CharField(max_length=500)
    sale_type = models.CharField(max_length=60)
    uom = models.CharField(max_length=60, default="Numbers, pieces, units")
    quantity = models.DecimalField(max_digits=14, decimal_places=2, default=1)
    value_excl_st = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    retail_price = models.DecimalField(max_digits=16, decimal_places=2, default=0)

    # computed
    rate = models.CharField(max_length=10, blank=True)
    sales_tax = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    further_tax = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    sro_schedule = models.CharField(max_length=60, blank=True)

    def __str__(self):
        return f"{self.product_description} × {self.quantity}"


class SellerProfile(models.Model):
    """Each user's own business profile — auto-fills the seller side of
    every invoice. The foundation for multi-tenant use."""
    PROVINCES = Buyer.PROVINCES

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="seller_profile")
    ntn_cnic = models.CharField("NTN / CNIC", max_length=15)
    business_name = models.CharField(max_length=255)
    province = models.CharField(max_length=30, choices=PROVINCES, default="Sindh")
    address = models.CharField(max_length=500)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.business_name} ({self.ntn_cnic})"