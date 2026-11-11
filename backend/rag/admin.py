"""Unfold admin for RAG entities — structured sidebar and grouped fields."""

from __future__ import annotations

from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin, TabularInline

from rag.ingest import delete_document
from rag.models import (
    AuditLog,
    Chunk,
    Conversation,
    Document,
    EvaluationReport,
    IngestionReport,
    Message,
    Status,
    QualityScore,
    UsageRecord,
)
from rag.tasks import ingest_document_task


@admin.register(UsageRecord)
class UsageRecordAdmin(ModelAdmin):
    list_display = [
        "organization",
        "operation",
        "model",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "created_at",
    ]
    list_filter = ["operation", "model", "created_at"]
    search_fields = ["organization__name", "model"]
    list_per_page = 50
    date_hierarchy = "created_at"
    autocomplete_fields = ["organization"]
    readonly_fields = [
        "organization",
        "operation",
        "model",
        "prompt_tokens",
        "completion_tokens",
        "created_at",
    ]

    @admin.display(description=_("Total tokens"))
    def total_tokens(self, obj):
        return obj.prompt_tokens + obj.completion_tokens


# ── inlines ──────────────────────────────────────────────────────────────────


class ChunkInline(TabularInline):
    model = Chunk
    extra = 0
    can_delete = False
    fields = ["content_type", "page", "content"]
    readonly_fields = fields
    max_num = 0
    show_change_link = True


class MessageInline(TabularInline):
    model = Message
    extra = 0
    fields = ["role", "content", "finish_reason", "created_at"]
    readonly_fields = fields
    can_delete = False
    show_change_link = True


# ── Document ─────────────────────────────────────────────────────────────────


@admin.register(Document)
class DocumentAdmin(ModelAdmin):
    list_display = [
        "name",
        "organization",
        "uploaded_by",
        "is_private",
        "source_type",
        "status_badge",
        "chunk_count",
        "quality_badge",
        "created_at",
    ]
    list_display_links = ["name", "organization"]
    list_filter = ["status", "source_type", "created_at"]
    search_fields = ["name", "source_uri", "organization__name", "uploaded_by__username"]
    list_per_page = 25
    date_hierarchy = "created_at"
    autocomplete_fields = ["organization", "uploaded_by"]
    inlines = [ChunkInline]
    actions = ["reindex_documents", "delete_selected"]

    fieldsets = (
        (_("Source"), {
            "fields": ["name", "organization", "uploaded_by", "is_private", "source_type", "source_uri", "source_options"],
            "icon": "upload",
        }),
        (_("Storage"), {
            "fields": ["storage_key", "content_type", "size_bytes", "checksum"],
            "icon": "cloud",
        }),
        (_("Status"), {
            "fields": ["status", "error_message", "chunk_count", "task_id", "last_ingestion_report"],
            "icon": "info",
        }),
        (_("Timestamps"), {
            "fields": ["created_at", "modified_at"],
            "icon": "schedule",
        }),
    )
    readonly_fields = [
        "storage_key",
        "checksum",
        "chunk_count",
        "task_id",
        "created_at",
        "modified_at",
        "last_ingestion_report",
    ]

    @admin.display(description=_("Status"), ordering="status")
    def status_badge(self, obj):
        color = {
            Status.UPLOADING: "orange",
            Status.PROCESSING: "blue",
            Status.ENRICHING: "purple",
            Status.READY: "green",
            Status.ERROR: "red",
        }.get(obj.status, "gray")
        return f'<span class="badge" style="background:{color}">{obj.get_status_display()}</span>'

    @admin.display(description=_("Quality"), ordering="last_ingestion_report__quality_score")
    def quality_badge(self, obj):
        report = obj.last_ingestion_report
        if not report:
            return "-"
        color = {
            QualityScore.GOOD: "green",
            QualityScore.WARNING: "orange",
            QualityScore.BAD: "red",
        }.get(report.quality_score, "gray")
        return f'<span class="badge" style="background:{color}">{report.get_quality_score_display()}</span>'

    @admin.action(description=_("Re-run ingestion for selected documents"))
    def reindex_documents(self, request, queryset):
        for document in queryset:
            document.status = Status.PROCESSING
            document.error_message = ""
            document.save(update_fields=["status", "error_message", "modified_at"])
            ingest_document_task.delay(str(document.id))
        self.message_user(
            request, _("Queued %(count)d document(s).") % {"count": queryset.count()},
            messages.SUCCESS,
        )

    def delete_model(self, request, obj):
        delete_document(obj)

    def delete_queryset(self, request, queryset):
        for document in queryset:
            delete_document(document)


# ── Chunk ────────────────────────────────────────────────────────────────────


@admin.register(Chunk)
class ChunkAdmin(ModelAdmin):
    list_display = ["document", "content_type", "page", "content_preview", "created_at"]
    list_display_links = ["document"]
    list_filter = ["content_type", "created_at"]
    search_fields = ["content", "document__name"]
    list_per_page = 50
    autocomplete_fields = ["document"]
    readonly_fields = ["id", "document", "content_hash", "metadata", "created_at"]

    @admin.display(description=_("Preview"))
    def content_preview(self, obj):
        return (obj.content[:120] + "...") if len(obj.content) > 120 else obj.content


# ── Ingestion Report ─────────────────────────────────────────────────────────


@admin.register(IngestionReport)
class IngestionReportAdmin(ModelAdmin):
    list_display = [
        "id",
        "document",
        "quality_badge",
        "pages_detected",
        "pages_with_text",
        "text_chunks",
        "summary_chunks",
        "failed_summaries",
        "created_at",
    ]
    list_display_links = ["id", "document"]
    list_filter = ["quality_score", "created_at"]
    search_fields = ["document__name", "document__organization__name"]
    list_per_page = 25
    date_hierarchy = "created_at"
    readonly_fields = [
        "id",
        "document",
        "pages_detected",
        "pages_with_text",
        "pages_without_text",
        "total_extracted_chars",
        "avg_chars_per_page",
        "extractor_used",
        "ocr_used",
        "ocr_language",
        "text_chunks",
        "table_chunks",
        "image_chunks",
        "summary_chunks",
        "failed_summaries",
        "embedding_status",
        "vector_upload_status",
        "warnings",
        "quality_score",
        "ingestion_duration_seconds",
        "created_at",
    ]

    fieldsets = (
        (_("Document"), {"fields": ["document"], "icon": "file_text"}),
        (_("Pages"), {
            "fields": ["pages_detected", "pages_with_text", "pages_without_text", "avg_chars_per_page"],
            "icon": "description",
        }),
        (_("Extraction"), {
            "fields": ["extractor_used", "ocr_used", "ocr_language", "total_extracted_chars"],
            "icon": "search",
        }),
        (_("Chunks"), {
            "fields": ["text_chunks", "table_chunks", "image_chunks", "summary_chunks", "failed_summaries"],
            "icon": "dataset",
        }),
        (_("Pipeline"), {
            "fields": ["embedding_status", "vector_upload_status", "ingestion_duration_seconds"],
            "icon": "settings",
        }),
        (_("Diagnostics"), {
            "fields": ["warnings", "quality_score", "created_at"],
            "icon": "warning",
        }),
    )

    @admin.display(description=_("Quality"), ordering="quality_score")
    def quality_badge(self, obj):
        color = {
            QualityScore.GOOD: "green",
            QualityScore.WARNING: "orange",
            QualityScore.BAD: "red",
        }.get(obj.quality_score, "gray")
        return f'<span class="badge" style="background:{color}">{obj.get_quality_score_display()}</span>'


# ── Conversation ─────────────────────────────────────────────────────────────


@admin.register(Conversation)
class ConversationAdmin(ModelAdmin):
    list_display = ["id", "organization", "title", "message_count", "created_at", "modified_at"]
    list_display_links = ["id", "title"]
    search_fields = ["title", "organization__name"]
    list_per_page = 25
    date_hierarchy = "created_at"
    autocomplete_fields = ["organization"]
    inlines = [MessageInline]
    readonly_fields = ["id", "created_at", "modified_at"]

    @admin.display(description=_("Messages"))
    def message_count(self, obj):
        return obj.messages.count()


# ── Message ──────────────────────────────────────────────────────────────────


@admin.register(Message)
class MessageAdmin(ModelAdmin):
    list_display = ["id", "conversation", "role", "content_preview", "finish_reason", "created_at"]
    list_display_links = ["id", "conversation"]
    list_filter = ["role", "created_at"]
    search_fields = ["content", "conversation__title"]
    list_per_page = 50
    autocomplete_fields = ["conversation"]
    readonly_fields = ["id", "conversation", "role", "content", "citations", "finish_reason", "created_at"]

    @admin.display(description=_("Preview"))
    def content_preview(self, obj):
        return (obj.content[:120] + "...") if len(obj.content) > 120 else obj.content


# ── Audit Log ────────────────────────────────────────────────────────────────


@admin.register(AuditLog)
class AuditLogAdmin(ModelAdmin):
    list_display = ["id", "actor", "action_badge", "resource_type", "resource_id", "ip_address", "created_at"]
    list_display_links = ["id"]
    list_filter = ["action", "created_at"]
    search_fields = ["actor__username", "resource_type", "resource_id"]
    list_per_page = 50
    date_hierarchy = "created_at"
    readonly_fields = [
        "id", "actor", "action", "resource_type", "resource_id",
        "metadata", "ip_address", "created_at",
    ]

    @admin.display(description=_("Action"), ordering="action")
    def action_badge(self, obj):
        color = {
            AuditLog.Action.DOCUMENT_UPLOAD: "blue",
            AuditLog.Action.DOCUMENT_REINDEX: "orange",
            AuditLog.Action.DOCUMENT_DELETE: "red",
            AuditLog.Action.QUERY: "gray",
            AuditLog.Action.ADMIN_ACTION: "purple",
        }.get(obj.action, "gray")
        return f'<span class="badge" style="background:{color}">{obj.get_action_display()}</span>'


# ── Evaluation Report ────────────────────────────────────────────────────────


@admin.register(EvaluationReport)
class EvaluationReportAdmin(ModelAdmin):
    list_display = [
        "id",
        "document",
        "faithfulness_score",
        "answer_relevancy_score",
        "context_precision_score",
        "context_recall_score",
        "chunks_retrieved",
        "created_at",
    ]
    list_display_links = ["id", "document"]
    list_filter = ["document", "created_at"]
    search_fields = ["document__name", "query"]
    list_per_page = 25
    date_hierarchy = "created_at"
    autocomplete_fields = ["document"]
    readonly_fields = [
        "id",
        "document",
        "query",
        "answer",
        "faithfulness_score",
        "answer_relevancy_score",
        "context_precision_score",
        "context_recall_score",
        "chunks_retrieved",
        "evaluation_duration_seconds",
        "metadata",
        "created_at",
    ]

    fieldsets = (
        (_("Document"), {"fields": ["document"], "icon": "file_text"}),
        (_("Query & Answer"), {"fields": ["query", "answer"], "icon": "chat"}),
        (_("Scores"), {
            "fields": [
                "faithfulness_score",
                "answer_relevancy_score",
                "context_precision_score",
                "context_recall_score",
            ],
            "icon": "grade",
        }),
        (_("Metadata"), {
            "fields": ["chunks_retrieved", "evaluation_duration_seconds", "metadata", "created_at"],
            "icon": "info",
        }),
    )
