"""Milestone 5 — retry queue runner (Manual v1.6 §4.2).

Cron har 5 minute (DEPLOY.md):
  */5 * * * *  cd /srv/app && ./venv/bin/python manage.py retry_pending_invoices
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Due pending_retry invoices ko FBR par resubmit karta hai"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)

    def handle(self, *args, **opts):
        from digital_invoicing.services import RetryQueueService
        s = RetryQueueService.process_due(limit=opts["limit"])
        self.stdout.write(
            f"processed={s['processed']} valid={s['valid']} "
            f"rescheduled={s['rescheduled']} failed={s['failed']}")
