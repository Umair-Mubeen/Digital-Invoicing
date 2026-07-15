# PRAL SN008/SN027 samples: 3rd Schedule ka sroScheduleNo KHALI hota hai.
# 0012 ka legacy "Third Schedule" text FBR ka valid SRO ref nahi — clear.
from django.db import migrations


def fix(apps, schema_editor):
    TaxSaleType = apps.get_model("digital_invoicing", "TaxSaleType")
    TaxSaleType.objects.filter(
        name="3rd Schedule Goods",
        sro_schedule__in=("Third Schedule", "3rd Sched")).update(
        sro_schedule="")


class Migration(migrations.Migration):
    dependencies = [("digital_invoicing",
                     "0021_invoice_last_error_invoice_next_retry_at_and_more")]
    operations = [migrations.RunPython(fix, migrations.RunPython.noop)]
