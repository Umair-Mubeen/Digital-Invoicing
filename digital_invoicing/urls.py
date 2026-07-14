"""urls.py — wire into your project's urls.py:
    path("invoicing/", include("digital_invoicing.urls")),
"""
from django.urls import path
from . import views

app_name = "digital_invoicing"

urlpatterns = [
    path("submit/", views.submit_invoice, name="submit"),
    path("validate/", views.validate_invoice, name="validate"),
    path("invoices/<int:pk>/resubmit/", views.resubmit_invoice, name="resubmit"),
    path("invoices/<int:pk>/cancel/", views.cancel_invoice, name="cancel"),
    path("invoices/<int:pk>/items/<int:item_pk>/cancel/",
         views.cancel_invoice_item, name="cancel_item"),
    path("invoices/<int:pk>/items/<int:item_pk>/edit/",
         views.edit_invoice_item, name="edit_item"),
    path("invoices/<int:pk>/eligibility/",
         views.invoice_modification_eligibility, name="eligibility"),
    path("invoices/", views.invoice_list, name="list"),
    path("invoices/<int:pk>/print/", views.invoice_print, name="print"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("reports/", views.reports, name="reports"),
    path("reports/sales-register.csv", views.sales_register_csv, name="sales_register_csv"),
    path("create/", views.create_invoice, name="create"),
    path("profile/", views.seller_profile, name="profile"),
    path("activity/", views.activity, name="activity"),
    path("buyers/", views.buyers, name="buyers"),
    path("products/", views.products, name="products"),
    path("inventory/", views.inventory, name="inventory"),
    path("suppliers/", views.suppliers, name="suppliers"),
    path("purchases/", views.purchases, name="purchases"),
    path("atl/", views.atl_report, name="atl_report"),
    path("atl/check/", views.atl_check, name="atl_check"),
    path("atl/evidence/upload/", views.atl_evidence_upload, name="atl_evidence_upload"),
    path("atl/evidence/<int:pk>/", views.atl_evidence_view, name="atl_evidence_view"),
    path("atl/report.pdf", views.atl_report_pdf, name="atl_report_pdf"),
    path("atl-upload/", views.atl_upload, name="atl_upload"),
    # Auth (self-service SaaS onboarding)
    path("signup/", views.signup, name="signup"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
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