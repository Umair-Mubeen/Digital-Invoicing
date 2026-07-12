"""admin.py — surface invoices in your existing Django admin panel."""
from django.contrib import admin
from .models import ATLStatus, Invoice, InvoiceItem, Buyer, SellerProfile, AuditLog, HSCode


class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 0
    readonly_fields = ("rate", "sales_tax", "further_tax", "sro_schedule")


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("fbr_invoice_number", "invoice_type", "buyer_business_name",
                    "invoice_total", "status", "invoice_date", "created_at")
    list_filter = ("status", "invoice_type", "invoice_date")
    search_fields = ("fbr_invoice_number", "buyer_business_name", "buyer_ntn_cnic")
    readonly_fields = ("fbr_invoice_number", "fbr_dated", "fbr_payload",
                       "fbr_response", "submitted_at", "created_at")
    inlines = [InvoiceItemInline]


@admin.register(Buyer)
class BuyerAdmin(admin.ModelAdmin):
    list_display = ("business_name", "ntn_cnic", "registration_type", "province")
    search_fields = ("business_name", "ntn_cnic")
    list_filter = ("registration_type", "province")


@admin.register(SellerProfile)
class SellerProfileAdmin(admin.ModelAdmin):
    list_display = ("business_name", "ntn_cnic", "province", "user")
    search_fields = ("business_name", "ntn_cnic", "user__username")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "username", "action", "ip", "path")
    list_filter = ("action", "created_at")
    search_fields = ("username", "ip", "path")
    readonly_fields = [f.name for f in AuditLog._meta.fields]

    def has_add_permission(self, r):    return False
    def has_change_permission(self, r, obj=None): return False
    def has_delete_permission(self, r, obj=None): return False


@admin.register(HSCode)
class HSCodeAdmin(admin.ModelAdmin):
    list_display = ("hs_code", "description", "default_sale_type", "schedule_hint", "is_active")
    list_filter = ("default_sale_type", "is_active")
    search_fields = ("hs_code", "description")
    list_editable = ("default_sale_type", "schedule_hint", "is_active")


@admin.register(ATLStatus)
class ATLStatusAdmin(admin.ModelAdmin):
    list_display = ("reg_no", "period", "status", "owner", "uploaded_at")
    list_filter = ("status", "period")
    search_fields = ("reg_no",)

# ---- Phase 7: Tax rule tables (Finance Act changes yahan se, code se nahi) ----
from .models import TaxSaleType, FurtherTaxConfig, FurtherTaxExemptHS, TaxScenario
from .tax_engine import invalidate_rules_cache


class _RuleAdmin(admin.ModelAdmin):
    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        invalidate_rules_cache()

    def delete_model(self, request, obj):
        super().delete_model(request, obj)
        invalidate_rules_cache()


@admin.register(TaxSaleType)
class TaxSaleTypeAdmin(_RuleAdmin):
    list_display = ("name", "rate", "charges_st", "further_tax_applies",
                    "sro_schedule", "retail_price_based",
                    "effective_from", "effective_to", "is_active")
    list_filter = ("is_active", "charges_st", "further_tax_applies")
    search_fields = ("name", "legal_reference")


@admin.register(FurtherTaxConfig)
class FurtherTaxConfigAdmin(_RuleAdmin):
    list_display = ("rate", "effective_from", "effective_to", "is_active")


@admin.register(FurtherTaxExemptHS)
class FurtherTaxExemptHSAdmin(_RuleAdmin):
    list_display = ("hs_prefix", "description", "sro_reference",
                    "effective_from", "effective_to", "is_active")
    list_filter = ("sro_reference", "is_active")
    search_fields = ("hs_prefix", "description")


@admin.register(TaxScenario)
class TaxScenarioAdmin(admin.ModelAdmin):
    list_display = ("code", "description", "sale_type", "is_active")
    search_fields = ("code", "description", "sale_type")
