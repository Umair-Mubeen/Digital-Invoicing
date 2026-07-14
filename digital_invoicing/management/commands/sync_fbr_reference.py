"""Milestone 4 — FBR Reference API sync (Tech Spec v1.12 §5.5 + §5.8).

Usage:
  python manage.py sync_fbr_reference                 # report only
  python manage.py sync_fbr_reference --apply         # unambiguous rates apply
  python manage.py sync_fbr_reference --province 7    # originationSupplier

Cron (rozana subah, DEPLOY.md dekhen):
  15 6 * * *  cd /srv/app && ./venv/bin/python manage.py sync_fbr_reference --apply
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Sync TaxSaleType rows with FBR transtypecode + SaleTypeToRate"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true",
                            help="Unambiguous (single-rate) drifts apply karo")
        parser.add_argument("--province", type=int, default=8,
                            help="originationSupplier province ID (default 8 Sindh)")

    def handle(self, *args, **opts):
        from digital_invoicing.reference_data import ReferenceSyncService
        svc = ReferenceSyncService()

        ids = svc.sync_trans_type_ids()
        self.stdout.write(f"Trans type IDs: {len(ids['matched'])} matched, "
                          f"{len(ids['unmatched'])} unmatched "
                          f"(FBR total {ids['fbr_types']})")
        for name in ids["unmatched"]:
            self.stdout.write(f"  UNMATCHED: {name}")

        report = svc.check_rate_drift(province_id=opts["province"])
        drifts = [d for d in report if d["drift"]]
        self.stdout.write(f"Rate check: {len(report)} types checked, "
                          f"{len(drifts)} drift(s)")
        for d in drifts:
            tag = "AUTO" if d["auto_applicable"] else "MANUAL (multi-rate)"
            self.stdout.write(f"  DRIFT [{tag}] {d['sale_type']}: ours "
                              f"{d['current']!r} vs FBR {d['fbr_rates']}")

        if opts["apply"]:
            applied = svc.apply_rate_updates(report)
            self.stdout.write(self.style.SUCCESS(
                f"Applied {len(applied)}: {', '.join(applied) or '—'}"))
        elif drifts:
            self.stdout.write("Run with --apply to update unambiguous rates.")
