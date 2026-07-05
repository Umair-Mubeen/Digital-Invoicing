"""admin.py — surface invoices in your existing Django admin panel."""
from django.contrib import admin
from .models import Invoice, InvoiceItem, Buyer, SellerProfile


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