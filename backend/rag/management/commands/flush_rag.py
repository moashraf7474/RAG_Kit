"""Delete all RAG data: Postgres rows, Qdrant vectors, and S3 files.

This is a destructive, irreversible operation. It wipes every Document, Chunk,
Conversation, Message, and the associated vectors and object-storage files for
every user. It does NOT delete user accounts.

Usage::

    python manage.py flush_rag              # prompts for confirmation
    python manage.py flush_rag --yes        # skip the prompt (CI / scripts)
    python manage.py flush_rag --user 42    # wipe only one user's data
"""

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Permanently delete all RAG data (documents, vectors, files, conversations)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes",
            "-y",
            action="store_true",
            default=False,
            help="Skip the confirmation prompt.",
        )
        parser.add_argument(
            "--organization",
            type=str,
            default=None,
            metavar="ORG_UUID",
            help="Restrict the wipe to a single organization (default: all organizations).",
        )

    def handle(self, *args, **options):
        organization_id = options["organization"]
        scope = f"organization {organization_id}" if organization_id else "ALL organizations"

        if not options["yes"]:
            self.stdout.write(
                self.style.WARNING(
                    f"\nThis will permanently delete all documents, chunks, vectors,\n"
                    f"object-storage files, and conversations for {scope}.\n"
                    f"This cannot be undone.\n"
                )
            )
            answer = input("Type 'yes' to continue: ").strip().lower()
            if answer != "yes":
                raise CommandError("Aborted.")

        counts = {"documents": 0, "vectors": 0, "files": 0, "conversations": 0}

        # Capture organization-scoped object keys before deleting their rows.
        counts["files"] = self._delete_files(organization_id)
        counts["documents"] = self._delete_postgres(organization_id)
        counts["vectors"] = self._delete_vectors(organization_id)
        counts["conversations"] = self._delete_conversations(organization_id)

        self.stdout.write(
            self.style.SUCCESS(
                f"\n✓ Flush complete for {scope}:\n"
                f"  {counts['documents']} document(s) removed from Postgres\n"
                f"  {counts['vectors']} vector point(s) deleted from Qdrant\n"
                f"  {counts['files']} file(s) removed from object storage\n"
                f"  {counts['conversations']} conversation(s) removed\n"
            )
        )

    # ------------------------------------------------------------------
    # Postgres
    # ------------------------------------------------------------------

    def _delete_postgres(self, organization_id: str | None) -> int:
        from rag.models import Document

        qs = Document.objects.all()
        if organization_id is not None:
            qs = qs.filter(organization_id=organization_id)

        count = qs.count()
        # Chunks, IngestionReport, EvaluationReport are CASCADE-deleted with the Document.
        qs.delete()
        self.stdout.write(f"  Deleted {count} document row(s) from Postgres.")
        return count

    def _delete_conversations(self, organization_id: str | None) -> int:
        from rag.models import Conversation

        qs = Conversation.objects.all()
        if organization_id is not None:
            qs = qs.filter(organization_id=organization_id)

        count = qs.count()
        # Messages are CASCADE-deleted with the Conversation.
        qs.delete()
        self.stdout.write(f"  Deleted {count} conversation(s) from Postgres.")
        return count

    # ------------------------------------------------------------------
    # Qdrant
    # ------------------------------------------------------------------

    def _delete_vectors(self, organization_id: str | None) -> int:
        from qdrant_client.http import models as qmodels

        from rag.vectordb import get_client, get_config

        settings = get_config().vector_db
        client = get_client()

        if not client.collection_exists(settings.collection_name):
            self.stdout.write("  Qdrant collection does not exist; skipping.")
            return 0

        if organization_id is not None:
            qdrant_filter = qmodels.Filter(
                must=[
                    qmodels.FieldCondition(
                        key="metadata.organization_id",
                        match=qmodels.MatchValue(value=str(organization_id)),
                    )
                ]
            )
            # Count before deleting.
            count_result = client.count(
                collection_name=settings.collection_name,
                count_filter=qdrant_filter,
                exact=True,
            )
            count = count_result.count
            client.delete(
                collection_name=settings.collection_name,
                points_selector=qmodels.FilterSelector(filter=qdrant_filter),
            )
        else:
            count_result = client.count(
                collection_name=settings.collection_name, exact=True
            )
            count = count_result.count
            # Recreate the collection for a clean slate (faster than delete-all).
            client.delete_collection(settings.collection_name)
            from rag.vectordb import ensure_collection

            ensure_collection()

        self.stdout.write(f"  Deleted {count} vector point(s) from Qdrant.")
        return count

    # ------------------------------------------------------------------
    # Object storage
    # ------------------------------------------------------------------

    def _delete_files(self, organization_id: str | None) -> int:
        """Remove files from S3 / MinIO.

        If wiping all organizations, delete every object. For one organization,
        delete only the storage keys attached to that organization's documents.
        """
        import boto3.exceptions
        from botocore.exceptions import ClientError

        from rag.storage import get_s3_client
        from rag.conf import get_config

        settings = get_config().s3
        client = get_s3_client()
        bucket = settings.bucket

        try:
            client.head_bucket(Bucket=bucket)
        except ClientError:
            self.stdout.write("  Object storage bucket not found; skipping.")
            return 0

        if organization_id is not None:
            from rag.models import Document

            keys = [
                {"Key": key}
                for key in Document.objects.filter(
                    organization_id=organization_id
                )
                .exclude(storage_key="")
                .values_list("storage_key", flat=True)
            ]
        else:
            paginator = client.get_paginator("list_objects_v2")
            keys = []
            for page in paginator.paginate(Bucket=bucket):
                for obj in page.get("Contents", []):
                    keys.append({"Key": obj["Key"]})

        if not keys:
            self.stdout.write("  No files found in object storage.")
            return 0

        # S3 delete_objects accepts at most 1000 keys per call.
        deleted = 0
        for i in range(0, len(keys), 1000):
            batch = keys[i : i + 1000]
            client.delete_objects(Bucket=bucket, Delete={"Objects": batch})
            deleted += len(batch)

        self.stdout.write(f"  Deleted {deleted} file(s) from object storage.")
        return deleted
