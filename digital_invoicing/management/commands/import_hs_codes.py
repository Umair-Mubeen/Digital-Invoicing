"""
CSV se HS codes import: python manage.py import_hs_codes hs_codes_fbr.csv
CSV columns: hs_code, description, uoms, default_sale_type, schedule_hint, note
(Purane 3-column CSV bhi chalta hai — extra columns optional.)
Existing codes UPDATE hote hain, naye CREATE — data kabhi duplicate nahi.
"""
import csv
from django.core.management.base import BaseCommand, CommandError
from digital_invoicing.models import HSCode


class Command(BaseCommand):
    help = "Import/update HS codes from CSV"

    def add_arguments(self, parser):
        parser.add_argument("csv_path")

    def handle(self, *args, **opts):
        path = opts["csv_path"]
        try:
            f = open(path, encoding="utf-8-sig")
        except OSError as e:
            raise CommandError(f"CSV nahi khuli: {e}")
        created = updated = 0
        with f:
            for row in csv.DictReader(f):
                code = (row.get("hs_code") or "").strip()
                if not code:
                    continue
                defaults = {
                    "description": (row.get("description") or "").strip(),
                    "uoms": (row.get("uoms") or "").strip(),
                    "default_sale_type": (row.get("default_sale_type") or "Goods at standard rate").strip(),
                    "schedule_hint": (row.get("schedule_hint") or "").strip(),
                    "note": (row.get("note") or "").strip(),
                    "is_active": True,
                }
                _, was_created = HSCode.objects.update_or_create(hs_code=code, defaults=defaults)
                created += was_created
                updated += (not was_created)
        self.stdout.write(self.style.SUCCESS(f"HS codes: {created} naye, {updated} update."))