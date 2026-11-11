from unittest.mock import MagicMock, patch

from rag.models import Status
from rag.tasks import ingest_document_task
from rag.views import DocumentViewSet


def test_fast_ingestion_queues_enrichment_after_text_is_indexed():
    document = MagicMock(status=Status.ENRICHING)
    queued = MagicMock(id="enrich-task-id")

    with patch("rag.tasks.ingest_text", return_value=7) as ingest_text, patch(
        "rag.tasks.Document.objects.get", return_value=document
    ), patch("rag.tasks.enrich_document_task.delay", return_value=queued) as enrich:
        with patch("rag.tasks.Document.objects.filter"):
            result = ingest_document_task.run("document-id")

    assert result == 7
    ingest_text.assert_called_once_with("document-id")
    enrich.assert_called_once_with("document-id")


def test_fast_ingestion_does_not_queue_enrichment_when_already_ready():
    document = MagicMock(status=Status.READY)

    with patch("rag.tasks.ingest_text", return_value=3), patch(
        "rag.tasks.Document.objects.get", return_value=document
    ), patch("rag.tasks.enrich_document_task.delay") as enrich:
        result = ingest_document_task.run("document-id")

    assert result == 3
    enrich.assert_not_called()


def test_document_viewset_extracts_forwarded_client_ip():
    request = MagicMock(META={"HTTP_X_FORWARDED_FOR": "203.0.113.4, 10.0.0.2"})

    assert DocumentViewSet._get_client_ip(request) == "203.0.113.4"
