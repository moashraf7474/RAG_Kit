import logging
import os
import sys

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class RagConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "rag"
    verbose_name = "RAG"

    def ready(self) -> None:
        """Check vector identity when the Django server starts.

        Management commands and Celery do their own vector work on demand; a
        startup probe there would make migrations depend on Qdrant. The API
        server still boots on a mismatch, but logs a clear critical action so
        an operator can run ``reembed_all``.
        """
        if not any(command in sys.argv for command in ("runserver", "gunicorn", "uvicorn")):
            return
        if os.environ.get("RAG_VERIFY_VECTOR_ON_STARTUP", "true").lower() in {
            "0",
            "false",
            "no",
        }:
            return
        try:
            from rag.vectordb import ensure_collection

            ensure_collection()
        except Exception as exc:
            logger.critical("Vector collection identity check failed: %s", exc)
