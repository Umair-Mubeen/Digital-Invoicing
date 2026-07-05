# Digital Invoicing — Install (tested on Django 5/6)

1. Copy this `digital_invoicing/` folder next to your other apps.

2. settings.py:
   INSTALLED_APPS += ["digital_invoicing"]

   FBR_USE_MOCK = True
   FBR_API_TOKEN = ""
   FBR_POST_URL = "https://gw.fbr.gov.pk/di_data/v1/di/postinvoicedata_sb"

3. Project urls.py:
   from django.urls import include, path
   urlpatterns += [ path("digital-invoicing/", include("digital_invoicing.urls")) ]
   # path hyphen use kar sakta hai; namespace hamesha digital_invoicing (underscore)

4. Migrate (migrations folder included — ready):
   python manage.py migrate

5. Open:  /digital-invoicing/create/   (login required)

## NoReverseMatch fix
In templates ALWAYS use underscore namespace:
   {% url 'digital_invoicing:submit' %}     ✓
   {% url 'digital-invoicing:submit' %}     ✗  (hyphen = error)

## Template blocks
templates/digital_invoicing/invoicing.html extends "base.html" and uses
blocks: title, extra_head, content. Agar aapke base.html ke block names
alag hain to invoicing.html mein rename kar lein.

## Go live (PRAL token):
   FBR_USE_MOCK = False
   FBR_API_TOKEN = "token"
   FBR_POST_URL = ".../postinvoicedata"
   pip install requests
