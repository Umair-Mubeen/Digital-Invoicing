"""Data prep before unique constraints (0010).

1. fbr_invoice_number: "" -> NULL (MySQL unique index multiple NULLs allow
   karta hai, multiple "" nahi). Genuine duplicates milen to sirf pehli
   entry number rakhti hai; baqi NULL + status untouched (audit trail
   fbr_response mein mehfooz hai).
2. public_id: har existing row ko UUID.
3. InvoiceItem ke naye PRAL fields historical fbr_payload JSON se backfill.

Idempotent — dobara chalane par kuch nahi badalta.
"""
import uuid
from decimal import Decimal, InvalidOperation
from django.db import migrations


def forwards(apps, schema_editor):
    Invoice = apps.get_model("digital_invoicing", "Invoice")
    InvoiceItem = apps.get_model("digital_invoicing", "InvoiceItem")

    # 1. "" -> NULL
    Invoice.objects.filter(fbr_invoice_number="").update(fbr_invoice_number=None)

    # 1b. duplicate real numbers: keep oldest, NULL the rest
    seen = set()
    for inv in Invoice.objects.exclude(fbr_invoice_number=None)\
                              .order_by("created_at").only("id", "fbr_invoice_number"):
        if inv.fbr_invoice_number in seen:
            Invoice.objects.filter(pk=inv.pk).update(fbr_invoice_number=None)
        else:
            seen.add(inv.fbr_invoice_number)

    # 2. public_id backfill
    for inv in Invoice.objects.filter(public_id=None).only("id"):
        Invoice.objects.filter(pk=inv.pk).update(public_id=uuid.uuid4())

    # 3. item-field backfill from fbr_payload
    def dec(v):
        try:
            return Decimal(str(v or 0))
        except (InvalidOperation, ValueError):
            return Decimal("0")

    for inv in Invoice.objects.exclude(fbr_payload=None).only("id", "fbr_payload"):
        payload_items = (inv.fbr_payload or {}).get("items") or []
        db_items = list(InvoiceItem.objects.filter(invoice_id=inv.pk).order_by("id"))
        for db_it, p_it in zip(db_items, payload_items):
            InvoiceItem.objects.filter(pk=db_it.pk).update(
                sales_tax_withheld=dec(p_it.get("salesTaxWithheldAtSource")),
                extra_tax=dec(p_it.get("extraTax")),
                fed_payable=dec(p_it.get("fedPayable")),
                discount=dec(p_it.get("discount")),
                total_values=dec(p_it.get("totalValues")),
                sro_item_serial_no=(p_it.get("sroItemSerialNo") or "")[:60],
            )


def backwards(apps, schema_editor):
    # Reversible no-op: NULL -> "" restore optional; data loss nahi hota.
    Invoice = apps.get_model("digital_invoicing", "Invoice")
    Invoice.objects.filter(fbr_invoice_number=None).update(fbr_invoice_number="")


class Migration(migrations.Migration):
    dependencies = [
        ("digital_invoicing", "0008_invoice_public_id_invoiceitem_discount_and_more"),
    ]
    operations = [migrations.RunPython(forwards, backwards)]
