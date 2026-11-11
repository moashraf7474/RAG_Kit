"""Provision the RAG infrastructure.

Creates the Qdrant collection (with payload indexes) and the S3 bucket, so a
fresh install is ready before the first request rather than lazily on first
upload. Idempotent — safe to run on every boot.
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create the Qdrant collection and object storage bucket if missing."

    def add_arguments(self, parser):
        parser.add_argument(
            "--wait",
            type=int,
            default=0,
            help="Seconds to keep retrying while dependencies come up.",
        )

    def handle(self, *args, **options):
        import time

        deadline = time.time() + options["wait"]

        for label, action in (
            ("object storage bucket", self._bucket),
            ("vector collection", self._collection),
            ("dev user", self._dev_user),
        ):
            while True:
                try:
                    action()
                    self.stdout.write(self.style.SUCCESS(f"✓ {label} ready"))
                    break
                except Exception as exc:
                    if time.time() < deadline:
                        time.sleep(3)
                        continue
                    # Do not abort startup: the health endpoint reports this,
                    # and the API is still useful for auth and admin.
                    self.stdout.write(self.style.WARNING(f"✗ {label}: {exc}"))
                    break

    def _bucket(self):
        from rag.storage import ensure_bucket

        ensure_bucket()

    def _dev_user(self):
        """Recreate the account the frontend signs in as, if it is missing.

        The frontend's credentials live in .env.frontend but the account lives
        in the database, so wiping the volumes (`make clean`, `down -v`) leaves
        the two out of sync and every request 401s. Recreating it here makes a
        wipe self-healing.

        Only runs when DEV_PASSWORD is set, so production — which should use
        real per-user auth — never gets a backdoor account.
        """
        from os import environ

        from django.contrib.auth import get_user_model

        password = environ.get("DEV_PASSWORD", "")
        if not password:
            raise RuntimeError("DEV_PASSWORD not set; skipping (expected in production)")

        username = environ.get("DEV_USERNAME", "ragtester")
        User = get_user_model()
        user, created = User.objects.get_or_create(
            username=username,
            defaults={"email": environ.get("DEV_EMAIL", "dev@example.com")},
        )

        # Always reset the password: the env file is the source of truth, so a
        # changed DEV_PASSWORD should take effect on the next boot.
        user.set_password(password)
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.save()
        return "created" if created else "updated"

    def _collection(self):
        from rag.vectordb import ensure_collection

        ensure_collection()
