"""R3 (Rule 150R) — day-end closing snapshots for every business.

Cron (raat ke baad, DEPLOY.md):
  10 0 * * *  cd /srv/app && ./venv/bin/python manage.py daily_closing
Backfill: python manage.py daily_closing --date 2026-07-15
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Rule 150R daily closing — per-business immutable day-end snapshot"

    def add_arguments(self, parser):
        parser.add_argument("--date", help="YYYY-MM-DD (default: yesterday)")

    def handle(self, *args, **opts):
        from datetime import date
        from digital_invoicing.services import ClosingService
        on = date.fromisoformat(opts["date"]) if opts.get("date") else None
        results = ClosingService.run_daily(on)
        made = sum(1 for _, c in results if c)
        self.stdout.write(f"closings created={made} "
                          f"already_existed={len(results)-made}")
