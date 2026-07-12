"""Existing plaintext FBR tokens ko encrypt karo (dual-read hone ki wajah se
kisi bhi order mein deploy safe hai). Idempotent — encrypted values skip."""
from django.db import migrations


def forwards(apps, schema_editor):
    from digital_invoicing.crypto import encrypt
    SellerProfile = apps.get_model("digital_invoicing", "SellerProfile")
    for sp in SellerProfile.objects.exclude(fbr_token=""):
        if not sp.fbr_token.startswith("enc$"):
            SellerProfile.objects.filter(pk=sp.pk).update(
                fbr_token=encrypt(sp.fbr_token))


def backwards(apps, schema_editor):
    from digital_invoicing.crypto import decrypt
    SellerProfile = apps.get_model("digital_invoicing", "SellerProfile")
    for sp in SellerProfile.objects.exclude(fbr_token=""):
        if sp.fbr_token.startswith("enc$"):
            SellerProfile.objects.filter(pk=sp.pk).update(
                fbr_token=decrypt(sp.fbr_token))


class Migration(migrations.Migration):
    dependencies = [("digital_invoicing", "0014_widen_token_field")]
    operations = [migrations.RunPython(forwards, backwards)]
