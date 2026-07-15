# TaxBuddy Digital Invoicing — Ready-to-Run Django Project

FBR Digital Invoicing (DI API v1.12) — complete project, app pre-integrated
and tested. Mock FBR client included (no PRAL token needed to develop).

## Chalane ka tareeqa (3 commands)

```bash
pip install django              # (requests bhi: pip install requests — real FBR ke liye)
python manage.py migrate
python manage.py createsuperuser   # apna login banao
python manage.py runserver
```

Phir browser mein kholo: **http://127.0.0.1:8000/**
(login ke baad seedha invoice page khulega)

- Invoice banao → Submit → FBR number + QR milega (mock)
- Admin panel: http://127.0.0.1:8000/admin/ (invoices yahan saved)

## Structure

```
taxbuddy_invoicing/        ← project (settings, urls — sab wired)
digital_invoicing/         ← app (tax engine, FBR client, validators, models)
templates/base.html        ← simple base (apni site ka use karo to swap)
```

## PRAL token milne par (go live)

settings.py mein:
```python
FBR_USE_MOCK = False
FBR_API_TOKEN = "your-token"
FBR_POST_URL = "https://gw.fbr.gov.pk/di_data/v1/di/postinvoicedata"  # production
```

## Notes
- Tax engine rates (5%/15% examples) current Act/SRO se verify karke
  digital_invoicing/tax_engine.py mein adjust karein.
- Validators = FBR ke official Error Message Guide ke 27+ codes.
- Commercial launch se pehle ownership structure sort karein.

## PRAL Compliance Modules (Milestones 1–7)

| Module | File | Kya karta hai |
|---|---|---|
| Tax Engine | `digital_invoicing/tax_engine.py` | 24 official saleTypes (SN001–SN028), rate semantics: percent / fixed-per-unit / compound / exempt; date-effective DB rules + hardcoded fallback |
| Validators | `digital_invoicing/validators.py` | Error Guide ke sab 58 sale codes — 45 local enforce, 10 registry-only (docstring mein listed) |
| Cancellation | `services.InvoiceCancellationService` | Manual v1.6 §4.1: item cancel/edit (once-only, snapshots), 72h/month-end lock, 10% last-month limit; list page par "Items" button |
| Reference sync | `reference_data.ReferenceSyncService` | transtypecode + SaleTypeToRate sync, drift report, single-rate auto-apply, FBR-outage resilient |
| Retry queue | `services.RetryQueueService` | Transient failures auto-retry (5m→6h backoff, max 5); read-timeout = manual IRIS verify (duplicate guard) |
| Scenario runner | `sandbox_scenarios.py` | Sab 28 scenarios ke doc-verbatim payloads; validate-only ya post |

## Management Commands

```bash
python manage.py sync_fbr_reference [--apply] [--province 8]   # rozana cron
python manage.py retry_pending_invoices [--limit 50]           # har 5 min cron
python manage.py run_sandbox_scenarios --user <u> [--only SN001,SN021] [--post]
```

Cron lines: `deploy/DEPLOY.md`.

## Testing

```bash
DB_ENGINE=sqlite python manage.py test digital_invoicing   # 152 tests
```

Source of truth: PRAL Technical Spec v1.12, Error Message Guide (Sales),
DI User Manual v1.6, DI Scenarios doc v1.11. Rates admin se date-effective
rows ke zariye update hote hain — purani rows kabhi delete nahi hotin.
