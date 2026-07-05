<<<<<<< HEAD
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
=======
# Digital-Invoicing
>>>>>>> 80277f4541cdef37e114ef4f19c0c59903f46a04
