"""admin.py — surface invoices in your existing Django admin panel."""
from django.contrib import admin
from .models import Invoice, InvoiceItem, Buyer, SellerProfile, AuditLog, HSCode


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