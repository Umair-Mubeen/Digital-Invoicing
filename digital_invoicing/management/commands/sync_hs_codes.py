"""Official HS-code list ko FBR reference API (Tech Doc §5.4 itemdesccode)
se local HSCode table mein utaarta hai — invoice ka HS search phir local,
tez, aur FBR-outage par bhi zinda.

Rules:
- Curated rows (aapki tax-knowledge: default sale type, schedule hint, note)
  KABHI overwrite nahi hoti — sirf auto_synced rows ki description refresh
  hoti hai.
- Naye codes auto_synced=True ke saath ban'te hain (sale type default par;
  classification hamesha practitioner ki hai).
- Mock client par chalna FBR list nahi, sample list dega — is liye block,
  jab tak --allow-mock na ho (dev smoke-test).

Run (token set hone ke baad, one-time + phir kabhi kabhaar):
  python manage.py sync_hs_codes --user <username>
"""
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "FBR itemdesccode (§5.4) se poori official HS list local cache karo"

    def add_arguments(self, parser):
        parser.add_argument("--user", required=True,
                            help="Business owner jiske token se fetch hoga")
        parser.add_argument("--allow-mock", action="store_true",
                            help="Mock sample list bhi chalne do (dev only)")

    def handle(self, *args, **opts):
        from django.contrib.auth.models import User
        from django.conf import settings
        from digital_invoicing.models import HSCode, SellerProfile
        from digital_invoicing.reference_data import (get_reference_client,
                                                      MockReferenceClient)

        user = User.objects.filter(username=opts["user"]).first()
        if not user:
            raise CommandError("User nahi mila")
        profile = SellerProfile.objects.filter(user=user).first()

        client = get_reference_client()
        if isinstance(client, MockReferenceClient) and not opts["allow_mock"]:
            raise CommandError(
                "MOCK reference client active hai — ye FBR ki official list "
                "nahi, sample subset dega.\n  - .env: FBR_USE_MOCK=0\n"
                "  - business token set ho (Settings)\n"
                "Dev smoke-test ke liye --allow-mock.")

        rows = client.hs_codes(q="", limit=None) or []
        if not rows:
            raise CommandError("FBR se koi HS rows nahi aayin — token/IP "
                               "whitelisting check karein")

        created = updated = skipped_curated = 0
        for r in rows:
            code = str(r.get("hS_CODE", "")).strip()
            desc = str(r.get("description", "")).strip()[:255]
            if not code:
                continue
            row = HSCode.objects.filter(hs_code=code).first()
            if row is None:
                HSCode.objects.create(hs_code=code, description=desc,
                                      auto_synced=True)
                created += 1
            elif row.auto_synced:
                if row.description != desc:
                    row.description = desc
                    row.save(update_fields=["description", "updated_at"])
                    updated += 1
            else:
                skipped_curated += 1     # aapki curated row — untouched

        self.stdout.write(self.style.SUCCESS(
            f"HS sync: {created} new, {updated} refreshed, "
            f"{skipped_curated} curated untouched "
            f"(total FBR rows: {len(rows)})"))
