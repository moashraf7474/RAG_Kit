"""DRF serializers — the API's validation boundary."""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from rag import storage
from rag.models import AuditLog, Chunk, Conversation, Document, EvaluationReport, IngestionReport, Message, SourceType
from rag.security import validate_public_url, validate_upload


class DocumentSerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = [
            "id",
            "name",
            "uploaded_by",
            "is_private",
            "source_type",
            "source_uri",
            "status",
            "error_message",
            "content_type",
            "size_bytes",
            "chunk_count",
            "download_url",
            "created_at",
            "modified_at",
        ]
        read_only_fields = fields

    def get_download_url(self, obj: Document) -> str:
        """Short-lived link to the original file; the source URI for web sources."""
        if obj.storage_key:
            return storage.presigned_url(obj.storage_key)
        return obj.source_uri


class DocumentUploadSerializer(serializers.Serializer):
    """File upload. Validation lives in `rag.security`."""

    file = serializers.FileField(write_only=True)
    is_private = serializers.BooleanField(required=False, default=False)

    def validate_file(self, value):
        try:
            name, content_type = validate_upload(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc
        # Stash the sanitized values for the view.
        value.sanitized_name = name
        value.resolved_content_type = content_type
        return value


class BulkUploadSerializer(serializers.Serializer):
    """Bulk upload: multiple files or a single ZIP archive."""

    files = serializers.ListField(
        child=serializers.FileField(),
        required=False,
        allow_empty=False,
    )
    zip_file = serializers.FileField(required=False, allow_empty_file=False)
    is_private = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        if not attrs.get("files") and not attrs.get("zip_file"):
            raise serializers.ValidationError(
                {"detail": "Provide either 'files' or 'zip_file'."}
            )
        return attrs


class SourceUploadSerializer(serializers.Serializer):
    """Ingestion of a URL, sitemap or Confluence space."""

    source_type = serializers.ChoiceField(
        choices=[SourceType.URL, SourceType.SITEMAP, SourceType.CONFLUENCE]
    )
    source_uri = serializers.CharField(max_length=2048)
    name = serializers.CharField(max_length=512, required=False, allow_blank=True)
    space_key = serializers.CharField(max_length=255, required=False, allow_blank=True)
    verify_ssl = serializers.BooleanField(required=False, default=True)
    is_private = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        try:
            attrs["source_uri"] = validate_public_url(attrs["source_uri"])
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"source_uri": exc.messages}) from exc

        if attrs["source_type"] == SourceType.CONFLUENCE and not attrs.get("space_key"):
            raise serializers.ValidationError(
                {"space_key": _("A space key is required for Confluence sources.")}
            )
        if not attrs.get("name"):
            attrs["name"] = attrs["source_uri"][:512]
        return attrs


class ChunkSerializer(serializers.ModelSerializer):
    class Meta:
        model = Chunk
        fields = ["id", "content", "content_type", "page", "metadata", "created_at"]
        read_only_fields = fields


class CitationSerializer(serializers.Serializer):
    page_content = serializers.CharField()
    type = serializers.CharField()
    document_id = serializers.CharField()
    document_name = serializers.CharField()
    document_url = serializers.CharField(allow_blank=True)
    page = serializers.CharField(allow_blank=True)
    score = serializers.FloatField(allow_null=True, required=False)


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = ["id", "role", "content", "citations", "finish_reason", "created_at"]
        read_only_fields = fields


class ConversationSerializer(serializers.ModelSerializer):
    message_count = serializers.IntegerField(source="messages.count", read_only=True)

    class Meta:
        model = Conversation
        fields = ["id", "title", "message_count", "created_at", "modified_at"]
        read_only_fields = fields


class ConversationDetailSerializer(serializers.ModelSerializer):
    messages = MessageSerializer(many=True, read_only=True)

    class Meta:
        model = Conversation
        fields = ["id", "title", "messages", "created_at", "modified_at"]
        read_only_fields = fields


class ChatRequestSerializer(serializers.Serializer):
    """Ported from upstream `ChatRequest`, plus server-side history."""

    message = serializers.CharField(max_length=8000, trim_whitespace=True)
    conversation_id = serializers.UUIDField(required=False, allow_null=True)
    # Restrict retrieval to a single document when set.
    document_id = serializers.UUIDField(required=False, allow_null=True)
    stream = serializers.BooleanField(required=False, default=False)
    answer_mode = serializers.ChoiceField(
        choices=["default", "beginner", "deep_dive"],
        required=False,
        default="default",
    )

    def validate_message(self, value):
        if not value.strip():
            raise serializers.ValidationError(_("Message cannot be empty."))
        return value


class ChatResponseSerializer(serializers.Serializer):
    """Ported from upstream `ChatResponse`."""

    answer = serializers.CharField()
    citations = CitationSerializer(many=True)
    confidence = serializers.ChoiceField(choices=["high", "medium", "low", "unknown"])
    finish_reason = serializers.CharField(allow_blank=True)
    conversation_id = serializers.UUIDField()


class IngestionReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = IngestionReport
        fields = [
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
        read_only_fields = fields


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = [
            "id",
            "actor",
            "action",
            "resource_type",
            "resource_id",
            "metadata",
            "ip_address",
            "created_at",
        ]
        read_only_fields = fields


class EvaluationReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = EvaluationReport
        fields = [
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
        read_only_fields = fields
