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

from .models import Invoice, InvoiceItem, AuditLog
from .tax_engine import compute_item
from .fbr_client import get_fbr_client


def log_event(request, action, **detail):
    """Har ahem event AuditLog mein save karo (kabhi crash na kare)."""
    try:
        user = request.user if request.user.is_authenticated else None
        AuditLog.objects.create(
            user=user,
            username=(user.username if user else detail.pop("username", "")),
            action=action, detail=detail,
            ip=(request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
                or request.META.get("REMOTE_ADDR", "")),
            path=request.path,
        )
    except Exception:
        pass


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

    # --- Invoice date: pehle hi parse karo (galat format pe crash nahi, 0113) ---
    from datetime import datetime as _dt
    try:
        _dt.strptime(p.get("invoiceDate", ""), "%Y-%m-%d")
    except (ValueError, TypeError):
        log_event(request, "invoice_failed", errors=["0113"],
                  invoice_type=p.get("invoiceType", ""))
        return JsonResponse({
            "ok": False, "invoiceId": None, "invoiceNumber": "", "dated": "",
            "validationResponse": {
                "statusCode": "01", "status": "Invalid",
                "error": "Invoice date is not in proper format (YYYY-MM-DD)",
                "invoiceStatuses": [{"itemSNo": "1", "statusCode": "01",
                    "status": "Invalid", "invoiceNo": "", "errorCode": "0113",
                    "error": "Invoice date is not in proper format (YYYY-MM-DD)"}],
            },
            "totals": {"value": 0, "salesTax": 0, "furtherTax": 0, "total": 0},
        })

    # --- Sale Invoice: stale debit-note fields saaf karo (UI toggle bug guard) ---
    if p.get("invoiceType") != "Debit Note":
        p["invoiceRefNo"] = ""
        p["reason"] = ""
        p["reasonRemarks"] = ""

    # Seller = SELECTED business — sirf apna (owner check). Browser ke seller
    # fields ignore hote hain, profile hi server-side truth hai.
    from .models import SellerProfile
    profile = SellerProfile.objects.filter(
        user=request.user, pk=p.get("sellerProfileId")).first() \
        or SellerProfile.objects.filter(user=request.user).first()
    if not profile:
        return JsonResponse({"ok": False, "error": "Pehle Business add karein"}, status=400)
    p["sellerNTNCNIC"] = profile.ntn_cnic
    p["sellerBusinessName"] = profile.business_name
    p["sellerProvince"] = profile.province
    p["sellerAddress"] = profile.address

    # ---- Debit Note: validate against the referenced invoice in DB ----
    # (mirrors FBR server-side checks: 0057, 0029/0035, 0034, 0067, 0027, 0028)
    ref_invoice = None
    if p.get("invoiceType") == "Debit Note":
        from datetime import datetime, timedelta

        def _fbr_error(code, msg):
            log_event(request, "invoice_failed", errors=[code],
                      invoice_type="Debit Note")
            return JsonResponse({
                "ok": False, "invoiceId": None, "invoiceNumber": "", "dated": "",
                "validationResponse": {
                    "statusCode": "01", "status": "Invalid", "error": msg,
                    "invoiceStatuses": [{
                        "itemSNo": "1", "statusCode": "01", "status": "Invalid",
                        "invoiceNo": "", "errorCode": code, "error": msg}],
                },
                "totals": {"value": 0, "salesTax": 0, "furtherTax": 0, "total": 0},
            })

        ref_no = (p.get("invoiceRefNo") or "").strip()
        if not ref_no:
            return _fbr_error("0026", "Invoice Reference No. is mandatory requirement for debit note")

        reason = (p.get("reason") or "").strip()
        if not reason:
            return _fbr_error("0027", "Reason is mandatory requirement for debit note")
        if reason == "Others" and not (p.get("reasonRemarks") or "").strip():
            return _fbr_error("0028", "Remarks are required where reason is 'Others'")

        ref_invoice = Invoice.objects.filter(
            owner=request.user, fbr_invoice_number=ref_no, status="valid").first()
        if not ref_invoice:
            return _fbr_error("0057", "Reference invoice for debit note does not exist")

        try:
            dn_date = datetime.strptime(p.get("invoiceDate", ""), "%Y-%m-%d").date()
        except ValueError:
            return _fbr_error("0113", "Invoice date is not in proper format (YYYY-MM-DD)")

        if dn_date < ref_invoice.invoice_date:
            return _fbr_error("0035", "Debit Note date must be greater or same as reference invoice date")
        if dn_date > ref_invoice.invoice_date + timedelta(days=180):
            return _fbr_error("0034", "Debit note can only be added within 180 days of reference invoice date")

    unreg = p.get("buyerRegistrationType") == "Unregistered"

    # ---- 3rd Schedule: Retail Price (MRP) lazmi ----
    from .tax_engine import SALE_TYPES as _ST
    for it in raw_items:
        st = it.get("saleType", "Goods at standard rate")
        if _ST.get(st, {}).get("retail_price_based") and not (
                float(it.get("fixedNotifiedValueOrRetailPrice", 0) or 0) > 0):
            return JsonResponse({"ok": False,
                "error": "3rd Schedule item pe Retail Price (MRP) zaroori hai"},
                status=400)

    # ---- Re-run the tax engine server-side (authoritative) ----
    total_value = total_st = total_ft = Decimal("0")
    clean_items = []
    for it in raw_items:
        sale_type = it.get("saleType", "Goods at standard rate")
        value = it.get("valueSalesExcludingST", 0) or 0
        try:
            calc = compute_item(sale_type, value, buyer_unregistered=unreg,
                                hs_code=it.get("hsCode", ""),
                                retail_price=it.get("fixedNotifiedValueOrRetailPrice", 0))
        except ValueError as e:
            return JsonResponse({"ok": False, "error": str(e)}, status=400)

        it = dict(it)
        it["rate"] = calc["rate"]
        it["salesTaxApplicable"] = float(calc["sales_tax"])
        it["furtherTax"] = float(calc["further_tax"])
        it["sroScheduleNo"] = calc["sro_schedule"]
        _mrp = it.get("fixedNotifiedValueOrRetailPrice", 0) or 0
        it["fixedNotifiedValueOrRetailPrice"] = (
            float(_mrp if _mrp else value) if calc["retail_price_based"] else 0
        )
        clean_items.append(it)

        total_value += Decimal(str(value))
        total_st += calc["sales_tax"]
        total_ft += calc["further_tax"]

    payload = dict(p)
    payload["items"] = clean_items
    invoice_total = total_value + total_st + total_ft

    # Debit note amounts cannot exceed the referenced invoice (error 0067)
    if ref_invoice is not None:
        if (total_value > ref_invoice.total_value
                or total_st > ref_invoice.total_sales_tax
                or invoice_total > ref_invoice.invoice_total):
            return JsonResponse({
                "ok": False, "invoiceId": None, "invoiceNumber": "", "dated": "",
                "validationResponse": {
                    "statusCode": "01", "status": "Invalid",
                    "error": "Debit note amounts exceed referenced invoice",
                    "invoiceStatuses": [{
                        "itemSNo": "1", "statusCode": "01", "status": "Invalid",
                        "invoiceNo": "", "errorCode": "0067",
                        "error": "Quantity, sale value or tax amounts of the debit note are greater than those of the referenced invoice"}],
                },
                "totals": {"value": float(total_value), "salesTax": float(total_st),
                           "furtherTax": float(total_ft), "total": float(invoice_total)},
            })

    # ---- Call FBR (mock or real, per settings) ----
    client = get_fbr_client(profile)
    result = client.post_invoice(payload)
    vr = result.get("validationResponse", {})
    valid = vr.get("status") == "Valid"

    # ---- Persist (audit trail: payload + response) ----
    inv = Invoice.objects.create(
        owner=request.user,
        seller_profile=profile,
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
        reason=payload.get("reason", ""),
        reason_remarks=payload.get("reasonRemarks", ""),
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

    # Buyer Book + Saved Products — valid invoice se khud yaad rakho
    if valid:
        try:
            from .models import Buyer, SavedItem
            bkey = {"owner": request.user}
            if payload.get("buyerNTNCNIC"):
                bkey["ntn_cnic"] = payload["buyerNTNCNIC"]
            else:
                bkey["business_name"] = payload.get("buyerBusinessName", "")
            b, _ = Buyer.objects.update_or_create(**bkey, defaults={
                "business_name": payload.get("buyerBusinessName", ""),
                "ntn_cnic": payload.get("buyerNTNCNIC", ""),
                "registration_type": payload.get("buyerRegistrationType", "Unregistered"),
                "province": payload.get("buyerProvince", "Sindh"),
                "address": payload.get("buyerAddress", ""),
            })
            Buyer.objects.filter(pk=b.pk).update(
                times_used=b.times_used + 1, last_used=timezone.now())
            for it in clean_items:
                si, _ = SavedItem.objects.update_or_create(
                    owner=request.user, hs_code=it.get("hsCode", ""),
                    description=it.get("productDescription", ""),
                    defaults={"sale_type": it.get("saleType", ""),
                              "uom": it.get("uoM", ""),
                              "last_value": it.get("valueSalesExcludingST", 0)})
                SavedItem.objects.filter(pk=si.pk).update(times_used=si.times_used + 1)
        except Exception:
            pass

    log_event(request,
              "invoice_valid" if valid else "invoice_failed",
              invoice_id=inv.pk,
              fbr_number=result.get("invoiceNumber", ""),
              invoice_type=payload.get("invoiceType", ""),
              total=float(invoice_total),
              errors=[st.get("errorCode") for st in vr.get("invoiceStatuses", [])
                      if st.get("errorCode")] if not valid else [])

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
    """Server-rendered invoice list — search + status filter + pagination.
    Data isolation: SIRF request.user ki invoices (owner filter)."""
    from django.core.paginator import Paginator
    from django.db.models import Q

    qs = Invoice.objects.filter(owner=request.user)
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    if q:
        qs = qs.filter(Q(fbr_invoice_number__icontains=q) |
                       Q(buyer_business_name__icontains=q) |
                       Q(buyer_ntn_cnic__icontains=q))
    if status in ("valid", "failed"):
        qs = qs.filter(status=status)
    biz = request.GET.get("biz", "").strip()
    if biz.isdigit():
        qs = qs.filter(seller_profile_id=biz)

    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get("page"))
    from .models import SellerProfile
    return render(request, "digital_invoicing/list.html", {
        "page": page, "q": q, "status": status, "biz": biz,
        "total": paginator.count,
        "businesses": SellerProfile.objects.filter(user=request.user).order_by("business_name"),
    })


@login_required
def dashboard(request):
    """Server-rendered dashboard — lifetime + is-mahine ke totals."""
    from datetime import date
    qs = Invoice.objects.filter(owner=request.user, status="valid")
    agg = qs.aggregate(
        count=Count("id"), value=Sum("total_value"),
        st=Sum("total_sales_tax"), ft=Sum("total_further_tax"),
    )
    today = date.today()
    m = qs.filter(invoice_date__year=today.year, invoice_date__month=today.month)\
          .aggregate(count=Count("id"), st=Sum("total_sales_tax"))
    return render(request, "digital_invoicing/dashboard.html", {
        "count": agg["count"] or 0,
        "value": agg["value"] or 0,
        "sales_tax": agg["st"] or 0,
        "further_tax": agg["ft"] or 0,
        "m_count": m["count"] or 0,
        "m_st": m["st"] or 0,
        "month_name": today.strftime("%B %Y"),
        "recent": qs[:8],
    })


@login_required
def create_invoice(request):
    from .models import SellerProfile
    businesses = SellerProfile.objects.filter(user=request.user).order_by("business_name")
    if not businesses.exists():
        from django.shortcuts import redirect
        return redirect("digital_invoicing:profile")
    recent_valid = Invoice.objects.filter(owner=request.user, status="valid")\
        .values_list("fbr_invoice_number", flat=True)[:50]
    biz_json = [{"id": b.pk, "name": b.business_name, "ntn": b.ntn_cnic,
                 "province": b.province, "address": b.address} for b in businesses]
    from .models import Buyer, SavedItem
    buyers_json = [{"id": b.pk, "name": b.business_name, "ntn": b.ntn_cnic,
                    "reg": b.registration_type, "province": b.province,
                    "address": b.address}
                   for b in Buyer.objects.filter(owner=request.user)[:200]]
    items_json = [{"hs": i.hs_code, "desc": i.description, "st": i.sale_type,
                   "uom": i.uom, "val": float(i.last_value)}
                  for i in SavedItem.objects.filter(owner=request.user)[:12]]
    return render(request, "digital_invoicing/invoicing.html",
                  {"businesses": businesses, "biz_json": biz_json,
                   "buyers_json": buyers_json, "items_json": items_json,
                   "recent_valid": recent_valid})


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
    """Autocomplete: /reference/hscodes/?q=steel
    DB-first (tax-intelligent HSCode directory — schedule/sale-type suggestions
    ke saath); DB khali ho to mock/FBR reference client fallback."""
    from .models import HSCode
    from django.db.models import Q
    q = request.GET.get("q", "").strip()
    qs = HSCode.objects.filter(is_active=True)
    if q:
        qs = qs.filter(Q(hs_code__icontains=q) | Q(description__icontains=q))
    rows = [{
        "hS_CODE": h.hs_code, "description": h.description,
        "uoms": h.uom_list(),
        "saleType": h.default_sale_type,
        "schedule": h.schedule_hint, "note": h.note,
    } for h in qs[:25]]
    if not rows and not HSCode.objects.exists():
        c = get_reference_client()
        rows = c.hs_codes(q)
    return JsonResponse({"data": rows})


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
    log_event(request, "invoice_printed", invoice_id=inv.pk,
              fbr_number=inv.fbr_invoice_number)
    return render(request, "digital_invoicing/print.html", {"inv": inv})


@login_required
def seller_profile(request):
    """Businesses manager — list + add + edit. Ek user ke multiple businesses."""
    from .models import SellerProfile
    businesses = SellerProfile.objects.filter(user=request.user).order_by("business_name")
    edit_id = request.GET.get("edit") or request.POST.get("edit_id")
    editing = businesses.filter(pk=edit_id).first() if edit_id else None
    saved = False
    # Delete a business (owner-checked)
    if request.method == "POST" and request.POST.get("delete_id"):
        businesses.filter(pk=request.POST.get("delete_id")).delete()
        from django.shortcuts import redirect
        return redirect("digital_invoicing:profile")
    if request.method == "POST":
        data = {
            "ntn_cnic": request.POST.get("ntn_cnic", "").strip(),
            "business_name": request.POST.get("business_name", "").strip(),
            "province": request.POST.get("province", "Sindh"),
            "address": request.POST.get("address", "").strip(),
            "fbr_token": request.POST.get("fbr_token", "").strip(),
            "use_sandbox": request.POST.get("use_sandbox") == "on",
        }
        if editing:
            for k, v in data.items():
                setattr(editing, k, v)
            editing.save()
        else:
            SellerProfile.objects.create(user=request.user, **data)
        log_event(request, "profile_saved", business_name=data["business_name"])
        saved = True
        editing = None
        businesses = SellerProfile.objects.filter(user=request.user).order_by("business_name")
    return render(request, "digital_invoicing/profile.html",
                  {"businesses": businesses, "editing": editing, "saved": saved,
                   "provinces": [p[0] for p in SellerProfile.PROVINCES]})


# ---------------------------------------------------------------------------
# Auth: self-service signup / login / logout (SaaS onboarding)
# ---------------------------------------------------------------------------
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.shortcuts import redirect


def signup(request):
    if request.user.is_authenticated:
        return redirect("digital_invoicing:create")
    form = UserCreationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        # optional email
        email = (request.POST.get("email") or "").strip()
        if email:
            user.email = email
            user.save(update_fields=["email"])
        auth_login(request, user)
        log_event(request, "signup")
        return redirect("digital_invoicing:profile")   # pehla kaam: business profile
    return render(request, "digital_invoicing/signup.html", {"form": form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect("digital_invoicing:create")
    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        auth_login(request, form.get_user())
        log_event(request, "login")
        nxt = request.GET.get("next") or request.POST.get("next")
        return redirect(nxt or "digital_invoicing:create")
    if request.method == "POST":
        log_event(request, "login_failed",
                  username=request.POST.get("username", ""))
    return render(request, "digital_invoicing/login.html",
                  {"form": form, "next": request.GET.get("next", "")})


def logout_view(request):
    if request.method == "POST":
        log_event(request, "logout")
        auth_logout(request)
    return redirect("digital_invoicing:login")



@login_required
def activity(request):
    """User ki apni activity — audit log (SaaS transparency)."""
    logs = AuditLog.objects.filter(user=request.user)[:100]
    return render(request, "digital_invoicing/activity.html", {"logs": logs})


# ---------------------------------------------------------------------------
# Buyer management (dedicated) + Sales Tax ATL status
# ---------------------------------------------------------------------------
def _current_period():
    from datetime import date
    return date.today().strftime("%Y-%m")


def _atl_status_for(user, reg_no, period=None):
    """Buyer ka ATL status is period ke liye (na mile to latest available)."""
    from .models import ATLStatus
    if not reg_no:
        return None
    period = period or _current_period()
    rec = ATLStatus.objects.filter(owner=user, reg_no=reg_no, period=period).first()
    if not rec:
        rec = ATLStatus.objects.filter(owner=user, reg_no=reg_no).first()
    return rec


@login_required
def buyers(request):
    """Buyers manager — list + add/edit/delete + ATL status column."""
    from .models import Buyer
    rows = Buyer.objects.filter(owner=request.user)
    edit_id = request.GET.get("edit") or request.POST.get("edit_id")
    editing = rows.filter(pk=edit_id).first() if edit_id else None
    saved = False

    if request.method == "POST" and request.POST.get("delete_id"):
        rows.filter(pk=request.POST.get("delete_id")).delete()
        return redirect("digital_invoicing:buyers")

    if request.method == "POST" and not request.POST.get("delete_id"):
        data = {
            "business_name": request.POST.get("business_name", "").strip(),
            "ntn_cnic": request.POST.get("ntn_cnic", "").strip(),
            "strn": request.POST.get("strn", "").strip(),
            "registration_type": request.POST.get("registration_type", "Unregistered"),
            "province": request.POST.get("province", "Sindh"),
            "address": request.POST.get("address", "").strip(),
            "fbr_token": request.POST.get("fbr_token", "").strip(),
            "use_sandbox": request.POST.get("use_sandbox") == "on",
        }
        if editing:
            for k, v in data.items():
                setattr(editing, k, v)
            editing.save()
        else:
            Buyer.objects.create(owner=request.user, **data)
        saved = True
        editing = None
        rows = Buyer.objects.filter(owner=request.user)

    period = _current_period()
    buyer_list = []
    for b in rows:
        rec = _atl_status_for(request.user, b.ntn_cnic or b.strn, period)
        buyer_list.append({"b": b, "atl": rec.status if rec else None,
                           "atl_period": rec.period if rec else None})

    from .models import SellerProfile
    return render(request, "digital_invoicing/buyers.html", {
        "buyer_list": buyer_list, "editing": editing, "saved": saved,
        "provinces": [p[0] for p in SellerProfile.PROVINCES],
        "reg_types": ["Registered", "Unregistered"],
        "period": period,
    })


@login_required
def atl_upload(request):
    """FBR Sales Tax ATL CSV upload — reg_no + status (+ optional period).
    CSV headers (flexible): reg_no/registration/ntn/strn, status, period."""
    from .models import ATLStatus
    import csv, io
    result = None
    if request.method == "POST" and request.FILES.get("atl_file"):
        period = request.POST.get("period", "").strip() or _current_period()
        f = request.FILES["atl_file"]
        try:
            text = f.read().decode("utf-8-sig", errors="ignore")
            reader = csv.DictReader(io.StringIO(text))
            # normalise header names
            def pick(row, *names):
                for n in names:
                    for k in row:
                        if k and k.strip().lower() == n:
                            return (row[k] or "").strip()
                return ""
            n = 0
            for row in reader:
                reg = pick(row, "reg_no", "registration_no", "registration",
                           "ntn", "strn", "cnic", "registrationno")
                if not reg:
                    continue
                status = pick(row, "status", "atl_status", "active") or "Active"
                status = "Active" if status.lower().startswith(("a", "1", "y")) else "Inactive"
                p = pick(row, "period", "month") or period
                ATLStatus.objects.update_or_create(
                    owner=request.user, reg_no=reg, period=p,
                    defaults={"status": status})
                n += 1
            result = f"{n} records imported for {period}."
            log_event(request, "profile_saved", detail_note=f"ATL upload: {n} ({period})")
        except Exception as e:
            result = f"Upload error: {e}"
    return render(request, "digital_invoicing/atl_upload.html",
                  {"result": result, "period": _current_period()})