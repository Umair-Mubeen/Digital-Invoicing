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
        parser.add_argument("--all", action="store_true",
                            help="Sab 28 scenarios (IRIS-eligibility default "
                                 "ko override)")
        parser.add_argument("--allow-mock", action="store_true",
                            help="Mock client ke against chalne do (sirf dev "
                                 "smoke-test — ye CERTIFICATION NAHI hai)")
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

        # --- Preflight: mock ke against "28/28 PASS" jhoota certification hai ---
        from django.conf import settings
        from digital_invoicing.fbr_client import get_fbr_client, MockFBRClient
        client = get_fbr_client(profile)
        if isinstance(client, MockFBRClient) and not opts["allow_mock"]:
            why = []
            if getattr(settings, "FBR_USE_MOCK", True):
                why.append("settings.FBR_USE_MOCK=True (.env mein FBR_USE_MOCK=0 karein)")
            if not getattr(profile, "fbr_token", ""):
                why.append(f"'{profile.business_name}' ka FBR token khali hai "
                           "(Settings page se daalein)")
            raise CommandError(
                "MOCK client active hai — ye asli sandbox test NAHI hoga.\n  - "
                + "\n  - ".join(why or ["config check karein"])
                + "\nSirf dev smoke-test chahiye to --allow-mock lagayein.")

        # Default scope: IRIS eligibility (Tech Doc p.47-51) jab profile par
        # nature/sector set hon aur --only na diya gaya ho
        if not opts.get("only") and not opts.get("all"):
            from digital_invoicing.scenario_eligibility import (
                eligible_for_profile)
            elig = eligible_for_profile(profile)
            if elig:
                opts["only"] = ",".join(elig)
                self.stdout.write(self.style.WARNING(
                    f"Scope: {len(elig)} IRIS-eligible scenarios "
                    f"({opts['only']}) — override with --only/--all"))

        mode = "SANDBOX" if profile.use_sandbox else "PRODUCTION"
        if isinstance(client, MockFBRClient):
            mode = "MOCK (certification nahi)"
        self.stdout.write(self.style.WARNING(
            f"Target: {mode} · business: {profile.business_name} "
            f"({profile.ntn_cnic})"))
        if not profile.use_sandbox and not isinstance(client, MockFBRClient):
            raise CommandError(
                "Profile PRODUCTION par hai — scenarios sirf sandbox par "
                "chalayein (Settings → Sandbox mode ON).")
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
