"""Relational state for the RAG system.

Qdrant owns the vectors; Postgres owns everything you want to query, list,
authorise or audit. Upstream kept document status in a Redis key-value store,
which loses state on restart — a table is both simpler and durable.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class Status(models.TextChoices):
    """Ported from `admin_api_lib/models/status.py`."""

    UPLOADING = "UPLOADING", _("Uploading")
    PROCESSING = "PROCESSING", _("Processing")
    ENRICHING = "ENRICHING", _("Enriching")
    READY = "READY", _("Ready")
    ERROR = "ERROR", _("Error")


class SourceType(models.TextChoices):
    FILE = "FILE", _("File")
    URL = "URL", _("URL")
    SITEMAP = "SITEMAP", _("Sitemap")
    CONFLUENCE = "CONFLUENCE", _("Confluence")


class ContentType(models.TextChoices):
    """Ported from `rag_core_lib/impl/data_types/content_type.py`."""

    TEXT = "TEXT", _("Text")
    TABLE = "TABLE", _("Table")
    IMAGE = "IMAGE", _("Image")
    SUMMARY = "SUMMARY", _("Summary")


class QualityScore(models.TextChoices):
    """Ingestion quality levels."""

    GOOD = "GOOD", _("Good")
    WARNING = "WARNING", _("Warning")
    BAD = "BAD", _("Bad")


class Document(models.Model):
    """One ingested source: an uploaded file, a URL, a sitemap or a Confluence space."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "api.Organization",
        on_delete=models.CASCADE,
        related_name="documents",
        verbose_name=_("organization"),
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_documents",
        verbose_name=_("uploaded by"),
    )
    is_private = models.BooleanField(_("private"), default=False)
    name = models.CharField(_("name"), max_length=512)
    source_type = models.CharField(
        _("source type"), max_length=16, choices=SourceType.choices, default=SourceType.FILE
    )
    source_uri = models.TextField(_("source URI"), blank=True, default="")
    status = models.CharField(
        _("status"), max_length=16, choices=Status.choices, default=Status.UPLOADING
    )
    error_message = models.TextField(_("error message"), blank=True, default="")

    # Object storage
    storage_key = models.CharField(_("storage key"), max_length=1024, blank=True, default="")
    content_type = models.CharField(_("MIME type"), max_length=255, blank=True, default="")
    size_bytes = models.BigIntegerField(_("size in bytes"), default=0)
    checksum = models.CharField(_("sha256"), max_length=64, blank=True, default="")

    chunk_count = models.PositiveIntegerField(_("chunk count"), default=0)
    task_id = models.CharField(_("celery task id"), max_length=255, blank=True, default="")
    # Source-specific options, e.g. {"space_key": "ENG"} for Confluence.
    source_options = models.JSONField(_("source options"), default=dict, blank=True)
    # Latest ingestion report for quick admin access.
    last_ingestion_report = models.ForeignKey(
        "IngestionReport",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("last ingestion report"),
    )

    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    modified_at = models.DateTimeField(_("modified at"), auto_now=True)

    def __init__(self, *args, **kwargs):
        """Accept the pre-tenancy owner argument during the transition.

        This keeps old management scripts and fixtures from silently creating
        unscoped rows; the legacy user is translated into its organization and
        retained as ``uploaded_by``.
        """
        legacy_owner = kwargs.pop("owner", None)
        legacy_owner_id = kwargs.pop("owner_id", None)
        if legacy_owner is not None:
            kwargs.setdefault("organization", legacy_owner.organization)
            kwargs.setdefault("uploaded_by", legacy_owner)
        elif legacy_owner_id is not None:
            kwargs.setdefault("organization_id", legacy_owner_id)
            kwargs.setdefault("uploaded_by_id", legacy_owner_id)
        super().__init__(*args, **kwargs)

    @property
    def owner(self):
        """Deprecated alias for the uploader; access control uses organization."""
        return self.uploaded_by

    @property
    def owner_id(self):
        """Deprecated alias retained for old in-memory callers."""
        return self.uploaded_by_id

    class Meta:
        db_table = "rag_documents"
        verbose_name = _("document")
        verbose_name_plural = _("documents")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"], name="unique_document_per_org"
            )
        ]
        indexes = [
            models.Index(
                fields=["organization", "status"],
                name="rag_documen_organiz_78e6fe_idx",
            ),
            models.Index(
                fields=["organization", "-created_at"],
                name="rag_documen_organiz_97f6fe_idx",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class IngestionReport(models.Model):
    """Per-ingestion diagnostics for a document."""

    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, related_name="ingestion_reports", verbose_name=_("document")
    )
    # Page-level diagnostics
    pages_detected = models.PositiveIntegerField(_("pages detected"), default=0)
    pages_with_text = models.PositiveIntegerField(_("pages with text"), default=0)
    pages_without_text = models.PositiveIntegerField(_("pages without text"), default=0)
    total_extracted_chars = models.PositiveIntegerField(_("total extracted characters"), default=0)
    avg_chars_per_page = models.FloatField(_("average characters per page"), default=0.0)
    # Extraction metadata
    extractor_used = models.CharField(_("extractor used"), max_length=255, blank=True, default="")
    ocr_used = models.BooleanField(_("OCR used"), default=False)
    ocr_language = models.CharField(_("OCR language"), max_length=64, blank=True, default="")
    # Chunk counts by type
    text_chunks = models.PositiveIntegerField(_("text chunks"), default=0)
    table_chunks = models.PositiveIntegerField(_("table chunks"), default=0)
    image_chunks = models.PositiveIntegerField(_("image chunks"), default=0)
    summary_chunks = models.PositiveIntegerField(_("summary chunks"), default=0)
    failed_summaries = models.PositiveIntegerField(_("failed summaries"), default=0)
    # Pipeline status
    embedding_status = models.CharField(_("embedding status"), max_length=32, blank=True, default="")
    vector_upload_status = models.CharField(_("vector upload status"), max_length=32, blank=True, default="")
    # Diagnostics
    warnings = models.JSONField(_("warnings"), default=list, blank=True)
    quality_score = models.CharField(
        _("quality score"), max_length=16, choices=QualityScore.choices, default=QualityScore.WARNING
    )
    # Timing
    ingestion_duration_seconds = models.FloatField(_("ingestion duration seconds"), default=0.0)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        db_table = "rag_ingestion_reports"
        verbose_name = _("ingestion report")
        verbose_name_plural = _("ingestion reports")
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["document", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.document.name} · {self.quality_score} · {self.created_at:%Y-%m-%d %H:%M}"


class Chunk(models.Model):
    """Mirror of a Qdrant point.

    Keeping the text in Postgres means citations, the admin UI and re-indexing
    never need a vector round-trip, and a Qdrant rebuild is a re-embed away.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, related_name="chunks", verbose_name=_("document")
    )
    # sha256 of the content; this is `metadata.id` in Qdrant.
    content_hash = models.CharField(_("content hash"), max_length=64, db_index=True)
    content = models.TextField(_("content"))
    content_type = models.CharField(
        _("content type"), max_length=16, choices=ContentType.choices, default=ContentType.TEXT
    )
    page = models.CharField(_("page"), max_length=255, blank=True, default="")
    metadata = models.JSONField(_("metadata"), default=dict, blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        db_table = "rag_chunks"
        verbose_name = _("chunk")
        verbose_name_plural = _("chunks")
        ordering = ["document", "id"]
        indexes = [models.Index(fields=["document", "content_type"])]

    def __str__(self) -> str:
        return f"{self.document.name} · {self.content_type} · {self.content[:40]}"


class Conversation(models.Model):
    """A chat thread. History is server-side so the client cannot forge context."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "api.Organization",
        on_delete=models.CASCADE,
        related_name="conversations",
        verbose_name=_("organization"),
    )
    title = models.CharField(_("title"), max_length=255, blank=True, default="")
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    modified_at = models.DateTimeField(_("modified at"), auto_now=True)

    class Meta:
        db_table = "rag_conversations"
        verbose_name = _("conversation")
        verbose_name_plural = _("conversations")
        ordering = ["-modified_at"]
        indexes = [
            models.Index(
                fields=["organization", "-modified_at"],
                name="rag_convers_organiz_765cd4_idx",
            )
        ]

    def __str__(self) -> str:
        return self.title or str(self.id)


class Message(models.Model):
    class Role(models.TextChoices):
        USER = "user", _("User")
        ASSISTANT = "assistant", _("Assistant")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages", verbose_name=_("conversation")
    )
    role = models.CharField(_("role"), max_length=16, choices=Role.choices)
    content = models.TextField(_("content"))
    citations = models.JSONField(_("citations"), default=list, blank=True)
    finish_reason = models.CharField(_("finish reason"), max_length=255, blank=True, default="")
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        db_table = "rag_messages"
        verbose_name = _("message")
        verbose_name_plural = _("messages")
        ordering = ["created_at"]
        indexes = [models.Index(fields=["conversation", "created_at"])]

    def __str__(self) -> str:
        return f"{self.role}: {self.content[:50]}"


class AuditLog(models.Model):
    """Immutable audit trail for compliance and forensics."""

    class Action(models.TextChoices):
        DOCUMENT_UPLOAD = "DOCUMENT_UPLOAD", _("Document Upload")
        DOCUMENT_REINDEX = "DOCUMENT_REINDEX", _("Document Reindex")
        DOCUMENT_DELETE = "DOCUMENT_DELETE", _("Document Delete")
        QUERY = "QUERY", _("Query")
        ADMIN_ACTION = "ADMIN_ACTION", _("Admin Action")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "api.Organization",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="audit_logs",
        verbose_name=_("organization"),
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="audit_logs",
        verbose_name=_("actor"),
    )
    action = models.CharField(_("action"), max_length=32, choices=Action.choices)
    resource_type = models.CharField(_("resource type"), max_length=64, blank=True, default="")
    resource_id = models.CharField(_("resource ID"), max_length=255, blank=True, default="")
    metadata = models.JSONField(_("metadata"), default=dict, blank=True)
    ip_address = models.GenericIPAddressField(_("IP address"), null=True, blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        db_table = "rag_audit_logs"
        verbose_name = _("audit log")
        verbose_name_plural = _("audit logs")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["actor", "-created_at"]),
            models.Index(fields=["action", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.action} by {self.actor} at {self.created_at:%Y-%m-%d %H:%M}"


class EvaluationReport(models.Model):
    """RAGAS evaluation result for a specific query/answer pair."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, related_name="evaluations", verbose_name=_("document")
    )
    query = models.TextField(_("query"))
    answer = models.TextField(_("answer"))
    # RAGAS scores
    faithfulness_score = models.FloatField(_("faithfulness score"), null=True, blank=True)
    answer_relevancy_score = models.FloatField(_("answer relevancy score"), null=True, blank=True)
    context_precision_score = models.FloatField(_("context precision score"), null=True, blank=True)
    context_recall_score = models.FloatField(_("context recall score"), null=True, blank=True)
    # Metadata
    chunks_retrieved = models.PositiveIntegerField(_("chunks retrieved"), default=0)
    evaluation_duration_seconds = models.FloatField(_("evaluation duration seconds"), default=0.0)
    metadata = models.JSONField(_("metadata"), default=dict, blank=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        db_table = "rag_evaluation_reports"
        verbose_name = _("evaluation report")
        verbose_name_plural = _("evaluation reports")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["document", "-created_at"]),
            models.Index(fields=["-created_at"]),
        ]

    def __str__(self) -> str:
        return f"Eval {self.id} · {self.document.name} · Faithfulness={self.faithfulness_score}"


class UsageRecord(models.Model):
    """Provider-reported LLM token usage, scoped to an organization."""

    organization = models.ForeignKey(
        "api.Organization",
        on_delete=models.CASCADE,
        related_name="usage",
        verbose_name=_("organization"),
    )
    operation = models.CharField(_("operation"), max_length=32)
    model = models.CharField(_("model"), max_length=128)
    prompt_tokens = models.PositiveIntegerField(_("prompt tokens"), default=0)
    completion_tokens = models.PositiveIntegerField(_("completion tokens"), default=0)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        db_table = "rag_usage"
        verbose_name = _("usage record")
        verbose_name_plural = _("usage records")
        indexes = [models.Index(fields=["organization", "-created_at"])]

    def __str__(self) -> str:
        total = self.prompt_tokens + self.completion_tokens
        return f"{self.organization} · {self.operation} · {self.model} · {total} tokens"
