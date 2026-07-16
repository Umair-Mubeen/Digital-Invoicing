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


def _json_for_html(data):
    """JSON jo <script> block ke andar SAFE hai — user strings ke andar
    </script> ya <!-- injection ko unicode-escape kar deta hai."""
    import json as _j
    return (_j.dumps(data)
            .replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026"))


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
def cancel_invoice_item(request, pk, item_pk):
    """Ek item cancel (Manual v1.6 partial cancellation)."""
    from .services import InvoiceCancellationService, SubmissionError
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)
    try:
        body = InvoiceCancellationService(request.user).cancel_item(
            pk, item_pk, remarks=request.POST.get("remarks", ""))
    except SubmissionError as e:
        return JsonResponse({"ok": False, "error": e.message}, status=400)
    log_event(request, "item_cancelled", invoice_id=pk, item_id=item_pk)
    return JsonResponse(body)


@login_required
def edit_invoice_item(request, pk, item_pk):
    """Ek item edit — once only; tax server recompute (Manual v1.6)."""
    from .services import InvoiceCancellationService, SubmissionError
    import json as _json
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)
    try:
        changes = _json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    try:
        body = InvoiceCancellationService(request.user).edit_item(
            pk, item_pk, changes)
    except SubmissionError as e:
        return JsonResponse({"ok": False, "error": e.message}, status=400)
    log_event(request, "item_edited", invoice_id=pk, item_id=item_pk,
              fields=sorted(changes.keys()))
    return JsonResponse(body)


@login_required
def invoice_modification_eligibility(request, pk):
    """UI ke liye: kya cancel/edit allowed hai + 10% limit status."""
    from .services import InvoiceCancellationService, SubmissionError
    try:
        body = InvoiceCancellationService(request.user).eligibility(pk)
    except SubmissionError as e:
        return JsonResponse({"ok": False, "error": e.message}, status=404)
    return JsonResponse(body)



def _filter_invoices(request):
    """Shared list/CSV filtering — ek hi jagah rules (koi duplication nahi).
    Returns (queryset, filters_dict). Data isolation: owner=request.user."""
    from django.db.models import Q

    # Perf: fbr_payload/fbr_response bade JSON blobs hain — list mein load
    # na karo (sirf detail/print par chahiye hote hain)
    qs = (Invoice.objects.filter(owner=request.user)
          .defer("fbr_payload", "fbr_response"))
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    if q:
        qs = qs.filter(Q(fbr_invoice_number__icontains=q) |
                       Q(buyer_business_name__icontains=q) |
                       Q(buyer_ntn_cnic__icontains=q))
    _valid_statuses = {c[0] for c in Invoice.STATUS}
    if status in _valid_statuses:
        qs = qs.filter(status=status)
    biz = request.GET.get("biz", "").strip()
    if biz.isdigit():
        qs = qs.filter(seller_profile_id=biz)
    itype = request.GET.get("type", "").strip()
    if itype in ("Sale Invoice", "Debit Note"):
        qs = qs.filter(invoice_type=itype)
    date_from = request.GET.get("from", "").strip()
    date_to = request.GET.get("to", "").strip()
    if date_from:
        qs = qs.filter(invoice_date__gte=date_from)
    if date_to:
        qs = qs.filter(invoice_date__lte=date_to)
    return qs, {"q": q, "status": status, "biz": biz, "itype": itype,
                "date_from": date_from, "date_to": date_to}


@login_required
def invoice_list(request):
    """Server-rendered invoice list — search + status filter + pagination.
    Data isolation: SIRF request.user ki invoices (owner filter)."""
    from django.core.paginator import Paginator
    from django.db.models import Sum

    qs, f = _filter_invoices(request)
    q, status, biz = f["q"], f["status"], f["biz"]
    itype, date_from, date_to = f["itype"], f["date_from"], f["date_to"]

    # Filtered totals (chips) — ek query
    agg = qs.aggregate(v=Sum("total_value"), st=Sum("total_sales_tax"),
                       ft=Sum("total_further_tax"))
    lstats = {"value": agg["v"] or 0, "st": agg["st"] or 0,
              "ft": agg["ft"] or 0}

    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get("page"))
    from .models import SellerProfile
    qsdict = request.GET.copy()
    qsdict.pop("page", None)
    return render(request, "digital_invoicing/list.html", {
        "page": page, "q": q, "status": status, "biz": biz,
        "itype": itype, "date_from": date_from, "date_to": date_to,
        "qstring": qsdict.urlencode(),
        "total": paginator.count,
        "lstats": lstats,
        "status_choices": Invoice.STATUS,
        "businesses": SellerProfile.objects.filter(user=request.user).order_by("business_name"),
    })


@login_required
def invoice_list_csv(request):
    """Filtered invoice list ka CSV — wahi filters jo screen par lage hain."""
    import csv as _csv
    from django.http import HttpResponse
    qs, f = _filter_invoices(request)
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="invoices.csv"'
    resp.write("\ufeff")          # Excel UTF-8 BOM
    w = _csv.writer(resp)
    w.writerow(["Invoice Date", "FBR Invoice Number", "Type", "Buyer",
                "Buyer NTN/CNIC", "Status", "Value excl. ST", "Sales Tax",
                "Further Tax", "Invoice Total"])
    for inv in qs.order_by("-invoice_date", "-id")[:5000]:
        w.writerow([inv.invoice_date, inv.fbr_invoice_number or "",
                    inv.invoice_type, inv.buyer_business_name,
                    inv.buyer_ntn_cnic or "", inv.get_status_display(),
                    inv.total_value, inv.total_sales_tax,
                    inv.total_further_tax, inv.invoice_total])
    log_event(request, "invoices_exported", rows=qs.count())
    return resp


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

    # ---- Dashboard v2 (UI sprint) — sab chhote aggregates ----
    from .models import InvoiceItem as _II, AuditLog as _AL, SellerProfile as _SP
    t = qs.filter(invoice_date=today).aggregate(
        n=Count("id"), v=Sum("total_value"))
    today_stats = {"count": t["n"] or 0, "value": float(t["v"] or 0)}

    status_map = {r["status"]: r["n"] for r in status_rows}
    action_chips = [
        ("pending_retry", "Pending retry", status_map.get("pending_retry", 0)),
        ("failed", "Failed", status_map.get("failed", 0)),
        ("draft", "Draft", status_map.get("draft", 0)),
        ("cancelled", "Cancelled", status_map.get("cancelled", 0)),
    ]

    item_qs = _II.objects.filter(invoice__in=qs)
    top_products = list(item_qs.values("product_description")
                        .annotate(v=Sum("value_excl_st"))
                        .order_by("-v")[:5])
    top_hs = list(item_qs.values("hs_code")
                  .annotate(v=Sum("value_excl_st"), n=Count("id"))
                  .order_by("-v")[:5])
    scenario_stats = list(qs.exclude(scenario_id="")
                          .values("scenario_id").annotate(n=Count("id"))
                          .order_by("-n")[:6])
    recent_activity = list(_AL.objects.filter(user=request.user)
                           .order_by("-created_at")[:6])

    last_valid = qs.order_by("-submitted_at").first()
    profile = _SP.objects.filter(user=request.user).first()
    fbr_status = {
        "sandbox": (profile.use_sandbox if profile else True),
        "last_valid_at": last_valid.submitted_at if last_valid else None,
        "retry_depth": status_map.get("pending_retry", 0),
    }

    return render(request, "digital_invoicing/dashboard.html", {
        "today_stats": today_stats, "action_chips": action_chips,
        "top_products": top_products, "top_hs": top_hs,
        "scenario_stats": scenario_stats, "recent_activity": recent_activity,
        "fbr_status": fbr_status,
        "count": agg["count"] or 0,
        "value": agg["value"] or 0,
        "sales_tax": agg["st"] or 0,
        "further_tax": agg["ft"] or 0,
        "m_count": m["count"] or 0,
        "m_st": m["st"] or 0,
        "month_name": today.strftime("%B %Y"),
        "recent": qs[:8],
        "chart": _json_for_html({
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
    from .tax_engine import load_rules, get_scenarios, LEGACY_ALIASES
    sale_types, further_rate, ft_exempt = load_rules()
    tax_cfg = {
        "saleTypes": {n: {"rate": float(c["rate"]), "further": c["further"],
                          "sro": c["sro"], "st": c["charges_st"],
                          "retail": c["retail_price_based"],
                          "rateType": c.get("rate_type", "percent"),
                          "perUnit": float(c.get("per_unit", 0) or 0),
                          "label": c.get("rate_label", ""),
                          "sroItem": c.get("sro_item", "")}
                      for n, c in sale_types.items()
                      if n not in LEGACY_ALIASES},   # dropdown: official only
        "furtherRate": float(further_rate),
        "ftExemptHS": sorted(ft_exempt),
        "scenarios": [{"code": c, "desc": d, "saleType": s}
                      for c, d, s in get_scenarios()],
    }
    return render(request, "digital_invoicing/invoicing.html",
                  {"businesses": businesses, "biz_json": biz_json,
                   "buyers_json": buyers_json, "items_json": items_json,
                   "recent_valid": recent_valid,
                   "tax_cfg": _json_for_html(tax_cfg)})


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
            # Token semantics (footgun fix): field khali chhorna = purana
            # token barqarar. Delete SIRF explicit "remove_token" se.
            # (Form ab ciphertext blob render nahi karta.)
            new_token = data.pop("fbr_token")
            if request.POST.get("remove_token") == "on":
                editing.fbr_token = ""
                log_event(request, "token_removed",
                          business_name=editing.business_name)
            elif new_token:
                editing.fbr_token = new_token
                log_event(request, "token_updated",
                          business_name=editing.business_name)
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


@login_required
def help_page(request):
    """Static help — quick start, shortcuts, common errors."""
    return render(request, "digital_invoicing/help.html", {})


@login_required
def account(request):
    """User account — email update, password change, recent security events."""
    from django.contrib.auth.forms import PasswordChangeForm
    from django.contrib.auth import update_session_auth_hash
    pw_form = PasswordChangeForm(request.user)
    saved = pw_saved = False
    if request.method == "POST" and request.POST.get("form") == "email":
        email = request.POST.get("email", "").strip()
        request.user.email = email
        request.user.save(update_fields=["email"])
        log_event(request, "profile_saved", field="email")
        saved = True
    elif request.method == "POST" and request.POST.get("form") == "password":
        pw_form = PasswordChangeForm(request.user, request.POST)
        if pw_form.is_valid():
            user = pw_form.save()
            update_session_auth_hash(request, user)   # logout na ho
            log_event(request, "password_changed")
            pw_saved = True
            pw_form = PasswordChangeForm(request.user)
    security_events = (AuditLog.objects.filter(
        user=request.user,
        action__in=["login", "login_failed", "password_changed",
                    "token_updated", "token_removed"])[:8])
    return render(request, "digital_invoicing/account.html", {
        "pw_form": pw_form, "saved": saved, "pw_saved": pw_saved,
        "security_events": security_events,
    })


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
            locked_msg = ("Too many failed attempts — try again "
                          "after 10 minutes.")
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



def _period_nav(period):
    """Reports ke liye prev/next month strings (typing kam — 1 click)."""
    from datetime import date
    try:
        y, m = int(period[:4]), int(period[5:7])
    except (ValueError, IndexError):
        t = date.today()
        y, m = t.year, t.month
    prev = (y - 1, 12) if m == 1 else (y, m - 1)
    nxt = (y + 1, 1) if m == 12 else (y, m + 1)
    today = date.today()
    return {
        "prev": "%04d-%02d" % prev, "next": "%04d-%02d" % nxt,
        "label": date(y, m, 1).strftime("%B %Y"),
        "is_current": (y, m) == (today.year, today.month),
    }


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
        "by_sale_type": svc.sale_type_report(business_id, period),
        "period_nav": _period_nav(period),
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
    from django.db.models import Q
    items = Product.objects.filter(owner=request.user).select_related(
        "category", "brand")
    pq = request.GET.get("q", "").strip()
    if pq:
        items = items.filter(Q(name__icontains=pq) | Q(sku__icontains=pq) |
                             Q(hs_code__icontains=pq))
    cat = request.GET.get("cat", "").strip()
    if cat.isdigit():
        items = items.filter(category_id=cat)
    stype = request.GET.get("stype", "").strip()
    if stype:
        items = items.filter(sale_type=stype)
    return render(request, "digital_invoicing/products.html", {
        "items": items, "editing": editing,
        "q": pq, "cat": cat, "stype": stype,
        "categories": Category.objects.filter(owner=request.user),
        "pstats": {
            "total": Product.objects.filter(owner=request.user).count(),
            "tracked": Product.objects.filter(owner=request.user,
                                              track_stock=True).count(),
            "out": sum(1 for x in items if x.track_stock and (x.stock or 0) <= 0),
            "no_hs": Product.objects.filter(owner=request.user,
                                            hs_code="").count(),
        },
        "official_sale_types": [n for n in __import__(
            "digital_invoicing.tax_engine", fromlist=["SALE_TYPES"]
        ).SALE_TYPES],
        "sale_types": Product.objects.filter(owner=request.user)
                             .values_list("sale_type", flat=True).distinct(),
    })


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
    from django.db.models import Q
    pis = PurchaseInvoice.objects.filter(owner=request.user)\
        .select_related("supplier")
    pq = request.GET.get("q", "").strip()
    if pq:
        pis = pis.filter(Q(supplier_name__icontains=pq) |
                         Q(supplier_invoice_no__icontains=pq) |
                         Q(supplier_ntn_cnic__icontains=pq))
    pfrom = request.GET.get("from", "").strip()
    pto = request.GET.get("to", "").strip()
    if pfrom:
        pis = pis.filter(invoice_date__gte=pfrom)
    if pto:
        pis = pis.filter(invoice_date__lte=pto)
    pis = pis[:100]
    return render(request, "digital_invoicing/purchases.html", {
        "purchases": pis, "q": pq, "date_from": pfrom, "date_to": pto,
        "suppliers_json": _json_for_html(
            [{"id": s.pk, "name": s.business_name, "ntn": s.ntn_cnic}
             for s in Supplier.objects.filter(owner=request.user)[:200]]),
        "products_json": _json_for_html(
            [{"id": p.pk, "name": p.name, "hs": p.hs_code,
              "price": float(p.default_price)}
             for p in Product.objects.filter(owner=request.user,
                                             is_active=True)[:300]]),
        "businesses": SellerProfile.objects.filter(user=request.user),
    })


@login_required
def activity(request):
    """User's own audit log (SaaS transparency) — filters + pagination."""
    from django.core.paginator import Paginator
    qs = AuditLog.objects.filter(user=request.user)
    action = request.GET.get("action", "").strip()
    valid_actions = {a[0] for a in AuditLog.ACTIONS}
    if action in valid_actions:
        qs = qs.filter(action=action)
    date_from = request.GET.get("from", "").strip()
    date_to = request.GET.get("to", "").strip()
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)

    if request.GET.get("export") == "csv":
        import csv as _csv
        from django.http import HttpResponse
        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="activity.csv"'
        resp.write("\ufeff")
        w = _csv.writer(resp)
        w.writerow(["Time", "Action", "Detail", "IP"])
        for l in qs[:5000]:
            w.writerow([l.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                        l.get_action_display(),
                        "; ".join(f"{k}={v}" for k, v in (l.detail or {}).items()),
                        l.ip])
        return resp

    paginator = Paginator(qs, 30)
    page = paginator.get_page(request.GET.get("page"))
    qsdict = request.GET.copy()
    qsdict.pop("page", None)
    return render(request, "digital_invoicing/activity.html", {
        "logs": page.object_list, "page": page,
        "actions": AuditLog.ACTIONS, "action": action,
        "date_from": date_from, "date_to": date_to,
        "total": paginator.count, "qstring": qsdict.urlencode(),
    })


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
    from django.db.models import Q as _Q
    all_rows = Buyer.objects.filter(owner=request.user)
    rows = all_rows
    bq = request.GET.get("q", "").strip()
    if bq:
        rows = rows.filter(_Q(business_name__icontains=bq) |
                           _Q(ntn_cnic__icontains=bq))
    breg = request.GET.get("reg", "").strip()
    if breg in ("Registered", "Unregistered"):
        rows = rows.filter(registration_type=breg)
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
    atl_active = sum(1 for x in buyer_list if x.get("atl") == "Active")
    stats = {
        "total": all_rows.count(),
        "registered": all_rows.filter(registration_type="Registered").count(),
        "unregistered": all_rows.filter(registration_type="Unregistered").count(),
        "atl_active": atl_active,
    }
    return render(request, "digital_invoicing/buyers.html", {
        "q": bq, "reg": breg, "stats": stats,
        "buyer_list": buyer_list, "editing": editing, "saved": saved,
        "provinces": [p[0] for p in SellerProfile.PROVINCES],
        "reg_types": ["Registered", "Unregistered"],
        "period": period,
    })


@login_required
def atl_report(request):
    """Monthly ATL evidence — us month ke sab buyers/suppliers ka status."""
    from .services import ATLReportService
    from datetime import date
    period = request.GET.get("period") or date.today().strftime("%Y-%m")
    rows = ATLReportService(request.user).month_report(period)
    return render(request, "digital_invoicing/atl_report.html", {
        "period": period, "rows": rows,
        "missing": sum(1 for r in rows if r["reg_no"] and not r["atl"]),
    })


@login_required
@require_POST
def atl_check(request):
    """Ek party ya sab missing parties ka FBR STATL check."""
    from .services import ATLReportService, SubmissionError
    period = request.POST.get("period", "")
    svc = ATLReportService(request.user)
    try:
        if request.POST.get("all") == "1":
            done, failed = svc.check_all_missing(period)
            log_event(request, "atl_checked_all", period=period,
                      done=done, failed=failed)
            return JsonResponse({"ok": True, "done": done, "failed": failed})
        rec = svc.check_party(request.POST.get("reg_no", ""), period)
        log_event(request, "atl_checked", reg_no=rec.reg_no,
                  period=period, status=rec.status)
        return JsonResponse({"ok": True, "status": rec.status})
    except SubmissionError as e:
        return JsonResponse({"ok": False, "error": e.message}, status=400)


@login_required
def atl_report_pdf(request):
    """Monthly ATL evidence PDF — audit ke liye save karne wali file."""
    from .services import ATLReportService
    from .models import SellerProfile
    from datetime import date, datetime as _dt
    from django.http import HttpResponse
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                    Paragraph, Spacer)

    period = request.GET.get("period") or date.today().strftime("%Y-%m")
    rows = ATLReportService(request.user).month_report(period)
    biz = SellerProfile.objects.filter(user=request.user).first()

    resp = HttpResponse(content_type="application/pdf")
    resp["Content-Disposition"] = (
        f'attachment; filename="ATL-Evidence-{period}.pdf"')

    NAVY = colors.HexColor("#0A2647")
    GREEN = colors.HexColor("#0D9E72")
    RED = colors.HexColor("#C0392B")
    LINE = colors.HexColor("#E3EAF2")

    doc = SimpleDocTemplate(resp, pagesize=A4,
                            leftMargin=16*mm, rightMargin=16*mm,
                            topMargin=16*mm, bottomMargin=16*mm)
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], textColor=NAVY,
                        fontSize=16, spaceAfter=2)
    sub = ParagraphStyle("sub", parent=ss["Normal"], textColor=colors.grey,
                         fontSize=9, spaceAfter=10)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], textColor=NAVY,
                        fontSize=12, spaceBefore=10, spaceAfter=4)

    story = [
        Paragraph("ATL Status Evidence Report", h1),
        Paragraph(
            f"Business: {biz.business_name if biz else '-'} "
            f"(NTN {biz.ntn_cnic if biz else '-'}) &nbsp;•&nbsp; "
            f"Tax Period: <b>{period}</b> &nbsp;•&nbsp; "
            f"Generated: {_dt.now().strftime('%d %b %Y %H:%M')} "
            f"&nbsp;•&nbsp; TaxBuddy Umair — Digital Invoicing", sub),
        Paragraph(
            "Sales Tax Active Taxpayer List (STATL) status of all "
            "counterparties transacted with during the period — evidence for "
            "further-tax and input-tax admissibility.", sub),
    ]

    def party_table(title, ptype):
        data = [[ "#", "Name", "NTN/CNIC", "Transactions", "ATL Status",
                  "FBR PDF", "Checked on"]]
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.5, LINE),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.white, colors.HexColor("#F7FAF9")]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
        n = 0
        for r in rows:
            if r["party_type"] != ptype:
                continue
            n += 1
            atl = r["atl"] or "NOT CHECKED"
            data.append([str(n), r["name"][:34], r["reg_no"] or "—",
                         r["tx_label"], atl,
                         "Attached" if r.get("pdf_pk") else "—",
                         r["checked_at"].strftime("%d-%b-%Y")
                         if r["checked_at"] else "—"])
            ri = len(data) - 1
            style.append(("TEXTCOLOR", (4, ri), (4, ri),
                          GREEN if atl == "Active"
                          else RED if atl == "Inactive" else colors.grey))
            style.append(("FONTNAME", (4, ri), (4, ri), "Helvetica-Bold"))
        if n == 0:
            data.append(["", f"No {ptype.lower()} transactions this period",
                         "", "", "", "", ""])
        t = Table(data, colWidths=[8*mm, 48*mm, 28*mm, 30*mm, 22*mm, 20*mm, 22*mm],
                  repeatRows=1)
        t.setStyle(TableStyle(style))
        return [Paragraph(title, h2), t, Spacer(1, 4)]

    story += party_table("Buyers (Sales)", "Buyer")
    story += party_table("Suppliers (Purchases)", "Supplier")
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "Note: Status as recorded from FBR STATL on the check date shown. "
        "Retain this document with the sales tax return working papers for "
        f"period {period}.", sub))
    doc.build(story)
    log_event(request, "atl_pdf_saved", period=period, parties=len(rows))
    return resp


@login_required
@require_POST
def atl_evidence_upload(request):
    """Har party ke against us month ki FBR ATL PDF save karo."""
    from .models import ATLStatus
    reg_no = (request.POST.get("reg_no") or "").strip()
    period = (request.POST.get("period") or "").strip()
    f = request.FILES.get("file")
    if not (reg_no and period and f):
        return JsonResponse({"ok": False, "error": "Reg no, period and PDF file are required"}, status=400)
    if f.size > 5 * 1024 * 1024:
        return JsonResponse({"ok": False, "error": "PDF is larger than 5MB"}, status=400)
    head = f.read(5); f.seek(0)
    if not (f.name.lower().endswith(".pdf") and head.startswith(b"%PDF")):
        return JsonResponse({"ok": False, "error": "Only PDF files are allowed"}, status=400)
    # ---- PDF ka content parho: NTN match + status extract ----
    def _pdf_text(fobj):
        try:
            from pypdf import PdfReader
            fobj.seek(0)
            reader = PdfReader(fobj)
            txt = " ".join((p.extract_text() or "")
                           for p in reader.pages[:3])
            fobj.seek(0)
            return txt
        except Exception:
            fobj.seek(0)
            return ""

    text = _pdf_text(f)
    norm = text.lower().replace("-", "").replace(" ", "")
    reg_digits = "".join(ch for ch in reg_no if ch.isdigit())

    verified = False
    detected = None
    manual = request.POST.get("status", "")

    if norm:
        if reg_digits and reg_digits not in norm.replace(".", ""):
            return JsonResponse({
                "ok": False,
                "error": (f"Reg no {reg_no} was not found in this PDF — "
                          "this appears to be another party's file. Please attach "
                          "the correct PDF.")}, status=400)
        if "inactive" in norm:
            detected, verified = "Inactive", True
        elif "active" in norm:
            detected, verified = "Active", True

    if not detected:
        # Text nahi (scanned) ya status nahi mila — manual confirm lazmi
        if manual in ("Active", "Inactive"):
            detected = manual
        else:
            return JsonResponse({
                "ok": False, "needs_status": True,
                "error": ("Could not read status from the PDF (scanned/non-"
                          "standard file). Please select the status manually below.")},
                status=400)

    rec, _ = ATLStatus.objects.get_or_create(
        owner=request.user, reg_no=reg_no, period=period,
        defaults={"status": detected})
    rec.status = detected
    rec.verified = verified
    if rec.evidence_pdf:
        rec.evidence_pdf.delete(save=False)      # purani replace
    rec.evidence_pdf.save(f"{reg_no}-{period}.pdf", f, save=True)
    log_event(request, "atl_evidence_uploaded", reg_no=reg_no,
              period=period, status=detected, verified=verified)
    return JsonResponse({"ok": True, "status": detected,
                         "verified": verified})


@login_required
def atl_evidence_view(request, pk):
    """Saved ATL PDF — sirf owner dekh sakta hai (public media nahi)."""
    from django.http import FileResponse, Http404
    from .models import ATLStatus
    rec = ATLStatus.objects.filter(owner=request.user, pk=pk).first()
    if not rec or not rec.evidence_pdf:
        raise Http404
    return FileResponse(rec.evidence_pdf.open("rb"),
                        content_type="application/pdf",
                        filename=f"ATL-{rec.reg_no}-{rec.period}.pdf")


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