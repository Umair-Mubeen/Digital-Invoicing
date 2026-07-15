"""Milestone 7 — sab 28 PRAL sandbox scenarios ek command mein.

  python manage.py run_sandbox_scenarios --user umair              # validate-only
  python manage.py run_sandbox_scenarios --user umair --only SN021,SN022
  python manage.py run_sandbox_scenarios --user umair --post       # invoices CREATE hongi
"""
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "PRAL sandbox par SN001-SN028 scenarios validate/post karta hai"

    def add_arguments(self, parser):
        parser.add_argument("--user", required=True)
        parser.add_argument("--only", default="",
                            help="Comma-separated codes, e.g. SN001,SN005")
        parser.add_argument("--post", action="store_true",
                            help="validateinvoicedata ke bajaye post karo "
                                 "(sandbox par invoices CREATE hongi)")

    def handle(self, *args, **opts):
        from django.contrib.auth.models import User
        from digital_invoicing.models import SellerProfile
        from digital_invoicing.sandbox_scenarios import run_scenarios
        try:
            user = User.objects.get(username=opts["user"])
        except User.DoesNotExist:
            raise CommandError(f"User {opts['user']!r} nahi mila")
        profile = SellerProfile.objects.filter(user=user).first()
        if not profile:
            raise CommandError("Seller profile nahi mila")
        if not profile.use_sandbox and not opts.get("force_prod"):
            raise CommandError("Profile sandbox mode mein nahi hai — "
                               "pehle sandbox on karein")
        codes = ([c.strip().upper() for c in opts["only"].split(",")
                  if c.strip()] or None)
        results = run_scenarios(profile, codes=codes,
                                validate_only=not opts["post"])
        passed = sum(1 for _, ok, _ in results if ok)
        for code, ok, msg in results:
            style = self.style.SUCCESS if ok else self.style.ERROR
            self.stdout.write(style(f"{code}  {'PASS' if ok else 'FAIL'}  {msg}"))
        self.stdout.write(f"\n{passed}/{len(results)} passed")
        if passed < len(results):
            self.stdout.write("FAIL wale scenarios ka msg + payload check "
                              "karein; registry-only errors (0052/0056/...) "
                              "real sandbox data par depend karte hain.")
