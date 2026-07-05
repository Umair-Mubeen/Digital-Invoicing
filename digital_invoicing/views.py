"""
views.py  —  Digital Invoicing views (plugs into your uploaded UI).

Your UI's buildPayload() already produces the FBR DI API v1.12 shape (flat:
sellerNTNCNIC, buyerNTNCNIC, items[...]). This view accepts that payload as-is.

Security note: the browser also computes tax, but we DO NOT trust it — the
server re-runs the tax engine from saleType + value + buyer registration and
overwrites salesTaxApplicable / furtherTax before posting to FBR. Client-side
numbers are for display only; the server's numbers are authoritative.
"""

import json
from decimal import Decimal
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST
from django.db.models import Sum, Count

from .models import Invoice, InvoiceItem
from .tax_engine import compute_item
from .fbr_client import get_fbr_client


@login_required
@require_POST
def submit_invoice(request):
    """Accepts the flat FBR v1.12 payload produced by the UI's buildPayload()."""
    try:
        p = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)

    raw_items = p.get("items", [])
    if not raw_items:
        return JsonResponse({"ok": False, "error": "Add at least one item"}, status=400)

    # Seller details come from the user's saved profile (server-side truth),
    # never from the browser payload.
    from .models import SellerProfile
    profile = SellerProfile.objects.filter(user=request.user).first()
    if profile:
        p["sellerNTNCNIC"] = profile.ntn_cnic
        p["sellerBusinessName"] = profile.business_name
        p["sellerProvince"] = profile.province
        p["sellerAddress"] = profile.address

    unreg = p.get("buyerRegistrationType") == "Unregistered"

    # ---- Re-run the tax engine server-side (authoritative) ----
    total_value = total_st = total_ft = Decimal("0")
    clean_items = []
    for it in raw_items:
        sale_type = it.get("saleType", "Goods at standard rate")
        value = it.get("valueSalesExcludingST", 0) or 0
        try:
            calc = compute_item(sale_type, value, buyer_unregistered=unreg)
        except ValueError as e:
            return JsonResponse({"ok": False, "error": str(e)}, status=400)

        it = dict(it)
        it["rate"] = calc["rate"]
        it["salesTaxApplicable"] = float(calc["sales_tax"])
        it["furtherTax"] = float(calc["further_tax"])
        it["sroScheduleNo"] = calc["sro_schedule"]
        it["fixedNotifiedValueOrRetailPrice"] = (
            float(value) if calc["retail_price_based"] else 0
        )
        clean_items.append(it)

        total_value += Decimal(str(value))
        total_st += calc["sales_tax"]
        total_ft += calc["further_tax"]

    payload = dict(p)
    payload["items"] = clean_items
    invoice_total = total_value + total_st + total_ft

    # ---- Call FBR (mock or real, per settings) ----
    client = get_fbr_client()
    result = client.post_invoice(payload)
    vr = result.get("validationResponse", {})
    valid = vr.get("status") == "Valid"

    # ---- Persist (audit trail: payload + response) ----
    inv = Invoice.objects.create(
        owner=request.user,
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
        total_value=total_value,
        total_sales_tax=total_st,
        total_further_tax=total_ft,
        invoice_total=invoice_total,
        status="valid" if valid else "failed",
        fbr_invoice_number=result.get("invoiceNumber", ""),
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
        )

    return JsonResponse({
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
    })


@login_required
def invoice_list(request):
    """Server-rendered invoice list (Django template)."""
    invoices = Invoice.objects.filter(owner=request.user)
    return render(request, "digital_invoicing/list.html", {"invoices": invoices})


@login_required
def dashboard(request):
    """Server-rendered dashboard (Django template)."""
    qs = Invoice.objects.filter(owner=request.user, status="valid")
    agg = qs.aggregate(
        count=Count("id"), value=Sum("total_value"),
        st=Sum("total_sales_tax"), ft=Sum("total_further_tax"),
    )
    return render(request, "digital_invoicing/dashboard.html", {
        "count": agg["count"] or 0,
        "value": agg["value"] or 0,
        "sales_tax": agg["st"] or 0,
        "further_tax": agg["ft"] or 0,
        "recent": qs[:8],
    })


@login_required
def create_invoice(request):
    from .models import SellerProfile
    profile = SellerProfile.objects.filter(user=request.user).first()
    if not profile:
        from django.shortcuts import redirect
        return redirect("digital_invoicing:profile")
    return render(request, "digital_invoicing/invoicing.html", {"profile": profile})


# ---------------------------------------------------------------------------
# Reference Data endpoints (FBR DI Reference APIs — mock/real via settings)
# Feed these to UI dropdowns & autocomplete to prevent errors 0019/0053/0077/0099.
# ---------------------------------------------------------------------------
from .reference_data import get_reference_client, cached


@login_required
def ref_provinces(request):
    c = get_reference_client()
    return JsonResponse({"data": cached("ref:provinces", c.provinces)})


@login_required
def ref_uom(request):
    c = get_reference_client()
    return JsonResponse({"data": cached("ref:uom", c.uom)})


@login_required
def ref_doc_types(request):
    c = get_reference_client()
    return JsonResponse({"data": cached("ref:doctypes", c.doc_types)})


@login_required
def ref_trans_types(request):
    c = get_reference_client()
    return JsonResponse({"data": cached("ref:transtypes", c.trans_types)})


@login_required
def ref_sro_schedules(request):
    c = get_reference_client()
    return JsonResponse({"data": c.sro_schedules(
        rate_id=request.GET.get("rate_id"), date=request.GET.get("date"))})


@login_required
def ref_hs_codes(request):
    """Autocomplete: /reference/hscodes/?q=steel"""
    c = get_reference_client()
    return JsonResponse({"data": c.hs_codes(request.GET.get("q", ""))})


@login_required
def ref_hs_uom(request):
    """Allowed UOMs for an HS code (prevents error 0099):
       /reference/hs-uom/?hs_code=8523.4990"""
    c = get_reference_client()
    return JsonResponse({"data": c.hs_uom(request.GET.get("hs_code", ""))})


@login_required
def ref_check_buyer(request):
    """Buyer verification before invoicing (prevents 0053; STATL status):
       /reference/check-buyer/?reg_no=1234567"""
    reg_no = request.GET.get("reg_no", "").strip()
    if not reg_no:
        return JsonResponse({"error": "reg_no required"}, status=400)
    c = get_reference_client()
    return JsonResponse({
        "registration": c.reg_type(reg_no),
        "statl": c.statl_check(reg_no),
    })


@login_required
def invoice_print(request, pk):
    """Printable invoice — FBR number + QR + full detail (browser print → PDF)."""
    from django.shortcuts import get_object_or_404
    inv = get_object_or_404(Invoice, pk=pk, owner=request.user)
    return render(request, "digital_invoicing/print.html", {"inv": inv})


@login_required
def seller_profile(request):
    """Create/edit the user's business profile (seller details)."""
    from .models import SellerProfile
    profile = SellerProfile.objects.filter(user=request.user).first()
    saved = False
    if request.method == "POST":
        data = {
            "ntn_cnic": request.POST.get("ntn_cnic", "").strip(),
            "business_name": request.POST.get("business_name", "").strip(),
            "province": request.POST.get("province", "Sindh"),
            "address": request.POST.get("address", "").strip(),
        }
        if profile:
            for k, v in data.items():
                setattr(profile, k, v)
            profile.save()
        else:
            profile = SellerProfile.objects.create(user=request.user, **data)
        saved = True
    return render(request, "digital_invoicing/profile.html",
                  {"profile": profile, "saved": saved,
                   "provinces": [p[0] for p in SellerProfile.PROVINCES]})