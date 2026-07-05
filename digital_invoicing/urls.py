"""urls.py — wire into your project's urls.py:
    path("invoicing/", include("digital_invoicing.urls")),
"""
from django.urls import path
from . import views

app_name = "digital_invoicing"

urlpatterns = [
    path("submit/", views.submit_invoice, name="submit"),
    path("invoices/", views.invoice_list, name="list"),
    path("invoices/<int:pk>/print/", views.invoice_print, name="print"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("create/", views.create_invoice, name="create"),
    path("profile/", views.seller_profile, name="profile"),
    # Reference data (dropdowns / autocomplete / buyer check)
    path("reference/provinces/", views.ref_provinces, name="ref_provinces"),
    path("reference/uom/", views.ref_uom, name="ref_uom"),
    path("reference/doc-types/", views.ref_doc_types, name="ref_doc_types"),
    path("reference/sale-types/", views.ref_trans_types, name="ref_trans_types"),
    path("reference/sro-schedules/", views.ref_sro_schedules, name="ref_sro_schedules"),
    path("reference/hscodes/", views.ref_hs_codes, name="ref_hs_codes"),
    path("reference/hs-uom/", views.ref_hs_uom, name="ref_hs_uom"),
    path("reference/check-buyer/", views.ref_check_buyer, name="ref_check_buyer"),
]