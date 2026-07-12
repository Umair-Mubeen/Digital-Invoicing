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
    """Thin HTTP layer — saara business logic services.InvoiceSubmissionService
    mein hai. Accepts the flat FBR v1.12 payload from the UI's buildPayload()."""
    from .services import InvoiceSubmissionService, SubmissionError

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)

    try:
        body = InvoiceSubmissionService(request.user).submit(payload)
    except SubmissionError as e:
        if e.simple:
            return JsonResponse({"ok": False, "error": e.message}, status=400)
        log_event(request, "invoice_failed", errors=[e.code],
                  invoice_type=payload.get("invoiceType", ""))
        return JsonResponse(e.fbr_shaped())

    audit = body.pop("_audit", {})
    log_event(request,
              "invoice_valid" if body["ok"] else "invoice_failed",
              invoice_id=audit.get("invoice_id"),
              fbr_number=body.get("invoiceNumber", ""),
              invoice_type=audit.get("invoice_type", ""),
              total=audit.get("total", 0),
              errors=audit.get("errors", []))
    return JsonResponse(body)


@login_required
@require_POST
def validate_invoice(request):
    """PRAL validateinvoicedata — submit se pehle FBR-verified check.
    Kuch save nahi hota, invoice number issue nahi hota."""
    from .services import InvoiceValidationService, SubmissionError
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    try:
        body = InvoiceValidationService(request.user).validate(payload)
    except SubmissionError as e:
        if e.simple:
            return JsonResponse({"ok": False, "error": e.message}, status=400)
        return JsonResponse(e.fbr_shaped())
    log_event(request, "invoice_validated" if body["ok"] else "validation_failed",
              invoice_type=payload.get("invoiceType", ""))
    return JsonResponse(body)


@login_required
@require_POST
def resubmit_invoice(request, pk):
    """Failed invoice ka one-click resubmit (Manual v1.6 §4.2)."""
    from .services import InvoiceResubmissionService, SubmissionError
    try:
        body = InvoiceResubmissionService(request.user).resubmit(pk)
    except SubmissionError as e:
        return JsonResponse({"ok": False, "error": e.message}, status=400)
    log_event(request,
              "invoice_resubmit_valid" if body["ok"] else "invoice_resubmit_failed",
              invoice_id=pk, fbr_number=body.get("invoiceNumber", ""))
    return JsonResponse(body)


@login_required
@require_POST
def cancel_invoice(request, pk):
    """IRIS par cancel hui invoice ko system mein cancelled mark karo
    (books sync — PRAL v1.12 mein cancellation API nahi hai)."""
    from .services import InvoiceCancellationService, SubmissionError
    try:
        body = InvoiceCancellationService(request.user).mark_cancelled(
            pk, remarks=request.POST.get("remarks", ""))
    except SubmissionError as e:
        return JsonResponse({"ok": False, "error": e.message}, status=400)
    log_event(request, "invoice_cancelled", invoice_id=pk)
    return JsonResponse(body)



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
    # ---- Chart data (UI overhaul) ----
    from django.db.models.functions import TruncMonth
    from .models import PurchaseInvoice
    import json as _json

    # Aakhri 6 mahine — value/ST trend (sirf valid)
    months, labels = [], []
    y, mo = today.year, today.month
    for i in range(5, -1, -1):
        yy, mm = y, mo - i
        while mm <= 0:
            mm += 12; yy -= 1
        months.append((yy, mm))
        labels.append(date(yy, mm, 1).strftime("%b %y"))
    monthly = {(r["mth"].year, r["mth"].month): r for r in
               qs.annotate(mth=TruncMonth("invoice_date"))
                 .values("mth")
                 .annotate(v=Sum("total_value"), s=Sum("total_sales_tax"),
                           n=Count("id"))}
    trend_value = [float(monthly.get(k, {}).get("v") or 0) for k in months]
    trend_st = [float(monthly.get(k, {}).get("s") or 0) for k in months]

    # Status doughnut (sab statuses)
    status_rows = list(Invoice.objects.filter(owner=request.user)
                       .values("status").annotate(n=Count("id")))
    status_labels = [r["status"] for r in status_rows]
    status_counts = [r["n"] for r in status_rows]

    # Top 5 buyers by value
    top_buyers = list(qs.values("buyer_business_name")
                      .annotate(v=Sum("total_value"))
                      .order_by("-v")[:5])
    buyer_labels = [ (r["buyer_business_name"] or "—")[:22] for r in top_buyers]
    buyer_values = [float(r["v"] or 0) for r in top_buyers]

    return render(request, "digital_invoicing/dashboard.html", {
        "count": agg["count"] or 0,
        "value": agg["value"] or 0,
        "sales_tax": agg["st"] or 0,
        "further_tax": agg["ft"] or 0,
        "m_count": m["count"] or 0,
        "m_st": m["st"] or 0,
        "month_name": today.strftime("%B %Y"),
        "recent": qs[:8],
        "chart": _json.dumps({
            "labels": labels, "value": trend_value, "st": trend_st,
            "statusLabels": status_labels, "statusCounts": status_counts,
            "buyerLabels": buyer_labels, "buyerValues": buyer_values,
        }),
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
    # Phase 7: DB-driven tax rules UI ko bhi (display sync; server phir bhi
    # authoritative hai)
    from .tax_engine import load_rules
    sale_types, further_rate, ft_exempt = load_rules()
    tax_cfg = {
        "saleTypes": {n: {"rate": float(c["rate"]), "further": c["further"],
                          "sro": c["sro"], "st": c["charges_st"],
                          "retail": c["retail_price_based"]}
                      for n, c in sale_types.items()},
        "furtherRate": float(further_rate),
        "ftExemptHS": sorted(ft_exempt),
    }
    return render(request, "digital_invoicing/invoicing.html",
                  {"businesses": businesses, "biz_json": biz_json,
                   "buyers_json": buyers_json, "items_json": items_json,
                   "recent_valid": recent_valid,
                   "tax_cfg": __import__("json").dumps(tax_cfg)})


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


LOGIN_MAX_FAILS = 5
LOGIN_LOCK_SECONDS = 600


def _login_throttle_key(request):
    ip = (request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
          or request.META.get("REMOTE_ADDR", ""))
    return f"loginfail:{ip}:{request.POST.get('username', '')[:60].lower()}"


def login_view(request):
    from django.core.cache import cache
    if request.user.is_authenticated:
        return redirect("digital_invoicing:create")

    locked_msg = None
    if request.method == "POST":
        key = _login_throttle_key(request)
        if cache.get(key, 0) >= LOGIN_MAX_FAILS:
            locked_msg = ("Bohat zyada ghalat koshishein — 10 minute baad "
                          "dobara try karein.")
            log_event(request, "login_locked",
                      username=request.POST.get("username", ""))
            form = AuthenticationForm(request)
            return render(request, "digital_invoicing/login.html",
                          {"form": form, "locked": locked_msg,
                           "next": request.GET.get("next", "")})

    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        cache.delete(_login_throttle_key(request))
        auth_login(request, form.get_user())
        log_event(request, "login")
        nxt = request.GET.get("next") or request.POST.get("next")
        return redirect(nxt or "digital_invoicing:create")
    if request.method == "POST":
        key = _login_throttle_key(request)
        try:
            cache.add(key, 0, LOGIN_LOCK_SECONDS)
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, LOGIN_LOCK_SECONDS)
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
def reports(request):
    """Tax reports — return-filing ready (Phase 14)."""
    from .services import ReportService
    from .models import SellerProfile
    from datetime import date

    business_id = request.GET.get("biz") or None
    period = request.GET.get("period") or date.today().strftime("%Y-%m")
    svc = ReportService(request.user)

    from .services import PurchaseService
    summary = svc.tax_summary(business_id, period)
    input_tax = PurchaseService(request.user).input_tax_summary(
        business_id, period)
    return render(request, "digital_invoicing/reports.html", {
        "businesses": SellerProfile.objects.filter(user=request.user)
                                           .order_by("business_name"),
        "biz": business_id or "", "period": period,
        "summary": summary,
        "buyers": svc.buyer_report(business_id, period),
        "statuses": svc.status_report(business_id, period),
        "input_tax": input_tax,
        "net_payable": float(summary["totals"]["st"] or 0)
                       - float(input_tax["input_tax"] or 0),
    })


@login_required
def sales_register_csv(request):
    """Annexure-C style item-wise sales register — CSV download."""
    import csv
    from django.http import HttpResponse
    from .services import ReportService
    from datetime import date

    business_id = request.GET.get("biz") or None
    period = request.GET.get("period") or date.today().strftime("%Y-%m")
    rows = ReportService(request.user).sales_register(business_id, period)

    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = (
        f'attachment; filename="sales-register-{period}.csv"')
    w = csv.writer(resp)
    w.writerow(["Invoice No (FBR)", "Invoice Date", "Invoice Type",
                "Buyer NTN/CNIC", "Buyer Name", "Buyer Reg Type",
                "Buyer Province", "HS Code", "Description", "Sale Type",
                "Rate", "UoM", "Quantity", "Value Excl. ST", "Retail Price",
                "Sales Tax", "Further Tax", "ST Withheld", "Extra Tax",
                "FED", "Discount", "SRO/Schedule", "SRO Item Sr."])
    for it in rows:
        inv = it.invoice
        w.writerow([inv.fbr_invoice_number or "", inv.invoice_date,
                    inv.invoice_type, inv.buyer_ntn_cnic,
                    inv.buyer_business_name, inv.buyer_registration_type,
                    inv.buyer_province, it.hs_code, it.product_description,
                    it.sale_type, it.rate, it.uom, it.quantity,
                    it.value_excl_st, it.retail_price, it.sales_tax,
                    it.further_tax, it.sales_tax_withheld, it.extra_tax,
                    it.fed_payable, it.discount, it.sro_schedule,
                    it.sro_item_serial_no])
    log_event(request, "sales_register_export", period=period)
    return resp


@login_required
def products(request):
    """Product master — list + inline add/edit (Phase 9)."""
    from .models import Product, Category, Brand
    editing = None
    if request.method == "POST":
        pk = request.POST.get("pk")
        if pk:
            editing = Product.objects.filter(owner=request.user, pk=pk).first()
        cat = request.POST.get("category", "").strip()
        brand = request.POST.get("brand", "").strip()
        cat_obj = Category.objects.get_or_create(
            owner=request.user, name=cat)[0] if cat else None
        brand_obj = Brand.objects.get_or_create(
            owner=request.user, name=brand)[0] if brand else None
        data = {
            "sku": request.POST.get("sku", "").strip(),
            "name": request.POST.get("name", "").strip(),
            "hs_code": request.POST.get("hs_code", "").strip(),
            "sale_type": request.POST.get("sale_type",
                                          "Goods at standard rate"),
            "uom": request.POST.get("uom", "Numbers, pieces, units"),
            "default_price": request.POST.get("default_price", 0) or 0,
            "category": cat_obj, "brand": brand_obj,
            "track_stock": request.POST.get("track_stock") == "on",
        }
        if data["name"]:
            if editing:
                for k, v in data.items():
                    setattr(editing, k, v)
                editing.save()
            else:
                Product.objects.create(owner=request.user, **data)
            log_event(request, "product_saved", name=data["name"])
        editing = None
    edit_pk = request.GET.get("edit")
    if edit_pk:
        editing = Product.objects.filter(owner=request.user, pk=edit_pk).first()
    items = Product.objects.filter(owner=request.user).select_related(
        "category", "brand")
    return render(request, "digital_invoicing/products.html",
                  {"items": items, "editing": editing})


@login_required
def inventory(request):
    """Stock levels + manual adjustment (Phase 11)."""
    from .models import Product, StockMovement
    from .services import InventoryService
    if request.method == "POST":
        prod = Product.objects.filter(
            owner=request.user, pk=request.POST.get("product_id")).first()
        try:
            qty = float(request.POST.get("quantity", 0) or 0)
        except ValueError:
            qty = 0
        if prod and qty:
            InventoryService(request.user).move(
                prod, qty, "adjustment",
                note=request.POST.get("note", "")[:200])
            log_event(request, "stock_adjusted", product=prod.name, qty=qty)
    prods = Product.objects.filter(owner=request.user, track_stock=True)
    moves = StockMovement.objects.filter(owner=request.user)\
        .select_related("product")[:30]
    return render(request, "digital_invoicing/inventory.html",
                  {"products": prods, "moves": moves})


@login_required
def suppliers(request):
    """Supplier book — Buyers jaisa (Phase 12 support)."""
    from .models import Supplier
    editing = None
    if request.method == "POST":
        pk = request.POST.get("pk")
        if pk:
            editing = Supplier.objects.filter(owner=request.user,
                                              pk=pk).first()
        data = {
            "business_name": request.POST.get("business_name", "").strip(),
            "ntn_cnic": request.POST.get("ntn_cnic", "").strip(),
            "strn": request.POST.get("strn", "").strip(),
            "registration_type": request.POST.get("registration_type",
                                                  "Registered"),
            "province": request.POST.get("province", "Sindh"),
            "address": request.POST.get("address", "").strip(),
        }
        if data["business_name"]:
            if editing:
                for k, v in data.items():
                    setattr(editing, k, v)
                editing.save()
            else:
                Supplier.objects.create(owner=request.user, **data)
            log_event(request, "supplier_saved", name=data["business_name"])
        editing = None
    edit_pk = request.GET.get("edit")
    if edit_pk:
        editing = Supplier.objects.filter(owner=request.user,
                                          pk=edit_pk).first()
    return render(request, "digital_invoicing/suppliers.html",
                  {"items": Supplier.objects.filter(owner=request.user),
                   "editing": editing})


@login_required
def purchases(request):
    """Purchase invoices — input tax records (Phase 12).
    Note: FBR DI API par purchases submit nahi hotin — local books."""
    from .models import PurchaseInvoice, Supplier, SellerProfile, Product
    from .services import PurchaseService, SubmissionError
    if request.method == "POST":
        try:
            payload = json.loads(request.body)
            pi = PurchaseService(request.user).create(
                payload.get("header", {}), payload.get("items", []))
            log_event(request, "purchase_saved", purchase_id=pi.pk,
                      total=float(pi.invoice_total))
            return JsonResponse({"ok": True, "purchaseId": pi.pk})
        except SubmissionError as e:
            return JsonResponse({"ok": False, "error": e.message}, status=400)
        except json.JSONDecodeError:
            return JsonResponse({"ok": False, "error": "Invalid JSON"},
                                status=400)
    pis = PurchaseInvoice.objects.filter(owner=request.user)\
        .select_related("supplier")[:100]
    return render(request, "digital_invoicing/purchases.html", {
        "purchases": pis,
        "suppliers_json": json.dumps(
            [{"id": s.pk, "name": s.business_name, "ntn": s.ntn_cnic}
             for s in Supplier.objects.filter(owner=request.user)[:200]]),
        "products_json": json.dumps(
            [{"id": p.pk, "name": p.name, "hs": p.hs_code,
              "price": float(p.default_price)}
             for p in Product.objects.filter(owner=request.user,
                                             is_active=True)[:300]]),
        "businesses": SellerProfile.objects.filter(user=request.user),
    })


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