"""Rebuild Qdrant vectors from the durable Postgres chunk mirror."""

from django.core.management.base import BaseCommand, CommandError
from langchain_core.documents import Document as LCDocument

from rag import vectordb
from rag.models import Chunk


class Command(BaseCommand):
    help = "Re-embed every Postgres chunk into a fresh Qdrant collection."

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Required because this replaces the current vector collection.",
        )
        parser.add_argument("--batch-size", type=int, default=200)

    def handle(self, *args, **options):
        if not options["confirm"]:
            raise CommandError(
                "Refusing to replace vectors without --confirm. "
                "Postgres chunks will be used to rebuild them."
            )
        if options["batch_size"] < 1:
            raise CommandError("--batch-size must be greater than zero.")

        client = vectordb.get_client()
        name = vectordb.get_config().vector_db.collection_name
        if client.collection_exists(name):
            client.delete_collection(name)
            self.stdout.write(f"Dropped collection '{name}'.")

        vectordb.get_vectorstore.cache_clear()
        vectordb.ensure_collection()

        total = Chunk.objects.count()
        if not total:
            self.stdout.write(self.style.WARNING("No chunks in Postgres. Nothing to do."))
            return

        done = 0
        batch_size = options["batch_size"]
        chunks = Chunk.objects.select_related("document").iterator(chunk_size=batch_size)
        batch: list[LCDocument] = []

        for chunk in chunks:
            metadata = dict(chunk.metadata or {})
            metadata.pop("owner_id", None)
            metadata.update(
                {
                    "id": chunk.content_hash,
                    "document_id": str(chunk.document_id),
                    "organization_id": str(chunk.document.organization_id),
                    "uploaded_by_id": chunk.document.uploaded_by_id,
                    "is_private": chunk.document.is_private,
                    "type": chunk.content_type,
                    "page": chunk.page,
                }
            )
            batch.append(LCDocument(page_content=chunk.content, metadata=metadata))
            if len(batch) >= batch_size:
                vectordb.upload(batch)
                done += len(batch)
                batch = []
                self.stdout.write(f"  {done}/{total}")

        if batch:
            vectordb.upload(batch)
            done += len(batch)

        self.stdout.write(self.style.SUCCESS(f"Re-embedded {done} chunks."))
