"""REST API.

Replaces upstream's `rag_api.py` (chat) and `admin_api.py` (documents) with two
DRF viewsets. Every queryset is filtered by `request.user`: authentication alone
is not authorisation, and documents are per-user data.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import uuid
import zipfile

from asgiref.sync import async_to_sync
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q
from django.http import StreamingHttpResponse
from django.core.files.uploadedfile import SimpleUploadedFile
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from api.permissions import IsOrgAdmin
from rag import storage
from rag.audit import log_action, log_document_delete, log_document_reindex, log_query
from rag.graph import answer as run_answer
from rag.graph import astream_answer
from rag.ingest import delete_document
from rag.llm import get_trace_callbacks
from rag.models import AuditLog, Chunk, Conversation, Document, EvaluationReport, IngestionReport, Message, SourceType, Status
from rag.security import validate_upload
from rag.serializers import (
    AuditLogSerializer,
    BulkUploadSerializer,
    ChatRequestSerializer,
    ChatResponseSerializer,
    ChunkSerializer,
    ConversationDetailSerializer,
    ConversationSerializer,
    DocumentSerializer,
    DocumentUploadSerializer,
    EvaluationReportSerializer,
    IngestionReportSerializer,
    SourceUploadSerializer,
)
from rag.tasks import ingest_document_task

logger = logging.getLogger(__name__)


def visible_documents(user):
    """Return documents the caller may read within their organization."""
    if not getattr(user, "organization_id", None):
        return Document.objects.none()
    queryset = Document.objects.filter(organization_id=user.organization_id)
    if getattr(user, "is_org_admin", False):
        return queryset
    return queryset.filter(Q(is_private=False) | Q(uploaded_by=user)).distinct()


class DocumentViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Document management: upload, ingest sources, poll status, delete."""

    serializer_class = DocumentSerializer
    permission_classes = [IsAuthenticated]
    # File upload uses multipart; the source/scraper endpoint sends JSON.
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "documents"

    def get_queryset(self):
        return visible_documents(self.request.user)

    @extend_schema(
        request=DocumentUploadSerializer,
        responses={202: DocumentSerializer},
        description="Upload a file and queue it for ingestion.",
    )
    @action(detail=False, methods=["post"], url_path="upload")
    def upload(self, request):
        serializer = DocumentUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        uploaded = serializer.validated_data["file"]
        organization = request.user.organization

        name = uploaded.sanitized_name
        content_type = uploaded.resolved_content_type
        storage_key = f"{request.user.pk}/{uuid.uuid4()}/{name}"

        # Store the bytes before creating the row, so a READY row always has a file.
        try:
            storage.upload_fileobj(uploaded, storage_key, content_type)
        except Exception:
            logger.exception("Object storage upload failed for '%s'.", name)
            return Response(
                {"detail": "Could not store the file. Please try again."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        document, created = Document.objects.update_or_create(
            organization=organization,
            name=name,
            defaults={
                "uploaded_by": request.user,
                "is_private": serializer.validated_data.get("is_private", False),
                "source_type": SourceType.FILE,
                "status": Status.PROCESSING,
                "storage_key": storage_key,
                "content_type": content_type,
                "size_bytes": uploaded.size,
                "error_message": "",
                "chunk_count": 0,
            },
        )
        self._queue(document)
        return Response(
            DocumentSerializer(document).data,
            status=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK,
        )

    @extend_schema(
        request=BulkUploadSerializer,
        responses={202: DocumentSerializer(many=True)},
        description="Upload multiple files or a ZIP archive and queue them for ingestion.",
    )
    @action(detail=False, methods=["post"], url_path="bulk-upload")
    def bulk_upload(self, request):
        serializer = BulkUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        organization = request.user.organization

        files_to_upload: list[tuple[str, io.BytesIO, str]] = []

        if data.get("zip_file"):
            zip_file = data["zip_file"]
            try:
                with zipfile.ZipFile(zip_file, "r") as zf:
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        name = info.filename
                        if not name or name.endswith("/"):
                            continue
                        content = zf.read(info)
                        candidate = SimpleUploadedFile(
                            name,
                            content,
                            content_type="application/octet-stream",
                        )
                        try:
                            safe_name, content_type = validate_upload(candidate)
                        except DjangoValidationError as exc:
                            return Response(
                                {"detail": exc.messages},
                                status=status.HTTP_400_BAD_REQUEST,
                            )
                        files_to_upload.append((safe_name, io.BytesIO(content), content_type))
            except zipfile.BadZipFile:
                return Response(
                    {"detail": "Invalid ZIP file."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if data.get("files"):
            for uploaded in data["files"]:
                try:
                    name, content_type = validate_upload(uploaded)
                except DjangoValidationError as exc:
                    return Response(
                        {"detail": exc.messages},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                content = uploaded.read()
                files_to_upload.append((name, io.BytesIO(content), content_type))

        if not files_to_upload:
            return Response(
                {"detail": "No valid files found."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        documents = []
        for name, content_io, content_type in files_to_upload:
            storage_key = f"{request.user.pk}/{uuid.uuid4()}/{name}"
            try:
                storage.upload_fileobj(content_io, storage_key, content_type)
            except Exception:
                logger.exception("Object storage upload failed for '%s'.", name)
                continue

            document, _ = Document.objects.update_or_create(
                organization=organization,
                name=name,
                defaults={
                    "uploaded_by": request.user,
                    "is_private": data.get("is_private", False),
                    "source_type": SourceType.FILE,
                    "status": Status.PROCESSING,
                    "storage_key": storage_key,
                    "content_type": content_type,
                    "size_bytes": content_io.getbuffer().nbytes,
                    "error_message": "",
                    "chunk_count": 0,
                },
            )
            self._queue(document)
            documents.append(document)

        return Response(
            DocumentSerializer(documents, many=True).data,
            status=status.HTTP_202_ACCEPTED,
        )

    @extend_schema(
        request=SourceUploadSerializer,
        responses={202: DocumentSerializer},
        description="Ingest a URL, sitemap or Confluence space.",
    )
    @action(detail=False, methods=["post"], url_path="source")
    def source(self, request):
        serializer = SourceUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        organization = request.user.organization

        document, created = Document.objects.update_or_create(
            organization=organization,
            name=data["name"],
            defaults={
                "uploaded_by": request.user,
                "is_private": data.get("is_private", False),
                "source_type": data["source_type"],
                "source_uri": data["source_uri"],
                "status": Status.PROCESSING,
                "source_options": {
                    "space_key": data.get("space_key", ""),
                    "verify_ssl": data.get("verify_ssl", True),
                },
                "error_message": "",
                "chunk_count": 0,
            },
        )
        self._queue(document)
        return Response(
            DocumentSerializer(document).data,
            status=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK,
        )

    @extend_schema(responses={202: DocumentSerializer}, description="Re-run ingestion.")
    @action(detail=True, methods=["post"])
    def reindex(self, request, pk=None):
        document = self.get_object()
        document.status = Status.PROCESSING
        document.error_message = ""
        document.save(update_fields=["status", "error_message", "modified_at"])
        self._queue(document)
        log_document_reindex(
            actor=request.user,
            document_id=str(document.id),
            ip_address=self._get_client_ip(request),
        )
        return Response(DocumentSerializer(document).data, status=status.HTTP_202_ACCEPTED)

    @extend_schema(
        responses={200: {"type": "object", "properties": {"download_url": {"type": "string"}}}},
        description="Fresh signed download URL. Citations and stored download_url values expire (~15 min); call this at click time.",
    )
    @action(detail=True, methods=["get"], url_path="resolve-download")
    def resolve_download(self, request, pk=None):
        document = self.get_object()
        return Response({"download_url": DocumentSerializer(document).data["download_url"]})

    @extend_schema(responses={200: ChunkSerializer(many=True)}, description="List a document's chunks.")
    @action(detail=True, methods=["get"])
    def chunks(self, request, pk=None):
        document = self.get_object()
        queryset = Chunk.objects.filter(document=document)
        page = self.paginate_queryset(queryset)
        serializer = ChunkSerializer(page if page is not None else queryset, many=True)
        return (
            self.get_paginated_response(serializer.data)
            if page is not None
            else Response(serializer.data)
        )

    @extend_schema(responses={200: IngestionReportSerializer(many=True)}, description="List ingestion reports for a document.")
    @action(detail=True, methods=["get"], url_path="reports")
    def reports(self, request, pk=None):
        document = self.get_object()
        queryset = document.ingestion_reports.all()
        page = self.paginate_queryset(queryset)
        serializer = IngestionReportSerializer(page if page is not None else queryset, many=True)
        return (
            self.get_paginated_response(serializer.data)
            if page is not None
            else Response(serializer.data)
        )

    def perform_destroy(self, instance):
        """Delete vectors and the stored file alongside the row."""
        delete_document(instance)
        log_document_delete(
            actor=self.request.user,
            document_id=str(instance.id),
            ip_address=self._get_client_ip(self.request),
        )

    @staticmethod
    def _get_client_ip(request) -> str | None:
        """Extract client IP from request, handling proxies."""
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR")

    @staticmethod
    def _queue(document: Document) -> None:
        """Dispatch ingestion after the transaction commits.

        Without `on_commit`, a fast worker can pick the task up before the row
        is visible and fail with DoesNotExist.
        """

        def dispatch():
            try:
                result = ingest_document_task.delay(str(document.id))
                Document.objects.filter(pk=document.pk).update(task_id=result.id)
            except Exception:
                logger.exception("Could not queue ingestion for %s.", document.id)
                Document.objects.filter(pk=document.pk).update(
                    status=Status.ERROR,
                    error_message="Could not queue ingestion. Is the worker running?",
                )

        transaction.on_commit(dispatch)


class ChatViewSet(viewsets.GenericViewSet):
    """Chat: retrieval-augmented answering over the caller's documents."""

    permission_classes = [IsAuthenticated]
    serializer_class = ChatRequestSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "chat"

    def get_queryset(self):
        if not getattr(self.request.user, "organization_id", None):
            return Conversation.objects.none()
        return Conversation.objects.filter(
            organization_id=self.request.user.organization_id
        )

    @extend_schema(
        request=ChatRequestSerializer,
        responses={
            200: ChatResponseSerializer,
            # Streaming responses are text/event-stream, not JSON.
            206: OpenApiResponse(description="Server-sent events when stream=true."),
        },
        description="Ask a question. Set stream=true for server-sent events.",
    )
    @action(detail=False, methods=["post"])
    def ask(self, request):
        serializer = ChatRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        conversation = self._get_conversation(request, data.get("conversation_id"))
        document_id = self._validate_document(request, data.get("document_id"))
        history = self._history(conversation)

        Message.objects.create(
            conversation=conversation, role=Message.Role.USER, content=data["message"]
        )
        log_query(
            actor=request.user,
            query=data["message"],
            ip_address=self._get_client_ip(request),
        )
        callbacks = get_trace_callbacks(
            session_id=str(conversation.id), user_id=str(request.user.pk), tags=["chat"]
        )

        if data.get("stream"):
            return self._stream(
                request, conversation, data["message"], history, document_id, data.get("answer_mode", "default"), callbacks
            )

        result = async_to_sync(run_answer)(
            data["message"],
            organization_id=str(request.user.organization_id),
            user_id=request.user.pk,
            is_org_admin=request.user.is_org_admin,
            history=history,
            document_id=document_id,
            answer_mode=data.get("answer_mode", "default"),
            callbacks=callbacks,
        )
        Message.objects.create(
            conversation=conversation,
            role=Message.Role.ASSISTANT,
            content=result["answer"],
            citations=result["citations"],
            finish_reason=result["finish_reason"],
        )
        self._title(conversation, data["message"])
        return Response({**result, "conversation_id": str(conversation.id)})

    def _stream(self, request, conversation, message, history, document_id, answer_mode, callbacks):
        """Server-sent events: citations first, then answer tokens."""

        def event_stream():
            collected: list[str] = []
            citations: list[dict] = []
            finish_reason = ""

            async def produce():
                async for event in astream_answer(
                    message,
                    organization_id=str(request.user.organization_id),
                    user_id=request.user.pk,
                    is_org_admin=request.user.is_org_admin,
                    history=history,
                    document_id=document_id,
                    answer_mode=answer_mode,
                    callbacks=callbacks,
                ):
                    yield event

            loop = asyncio.new_event_loop()
            try:
                iterator = produce().__aiter__()
                while True:
                    try:
                        event = loop.run_until_complete(iterator.__anext__())
                    except StopAsyncIteration:
                        break
                    if event["type"] == "token":
                        collected.append(event["token"])
                    elif event["type"] == "citations":
                        citations = event["citations"]
                    elif event["type"] == "done":
                        finish_reason = event.get("finish_reason", "stop")
                    elif event["type"] == "error":
                        collected.append(event["message"])
                        finish_reason = "error"
                    yield f"data: {json.dumps(event)}\n\n"
            except Exception:
                logger.exception("Chat stream failed.")
                yield f'data: {json.dumps({"type": "error", "message": "Stream failed."})}\n\n'
            finally:
                loop.close()
                # Persist whatever was produced, even on a partial stream.
                Message.objects.create(
                    conversation=conversation,
                    role=Message.Role.ASSISTANT,
                    content="".join(collected),
                    citations=citations,
                    finish_reason=finish_reason,
                )
                self._title(conversation, message)

        response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"  # let nginx pass chunks straight through
        return response

    @extend_schema(responses={200: ConversationSerializer(many=True)})
    @action(detail=False, methods=["get"])
    def conversations(self, request):
        queryset = self.get_queryset()
        page = self.paginate_queryset(queryset)
        serializer = ConversationSerializer(page if page is not None else queryset, many=True)
        return (
            self.get_paginated_response(serializer.data)
            if page is not None
            else Response(serializer.data)
        )

    @extend_schema(responses={200: ConversationDetailSerializer})
    @action(detail=True, methods=["get", "delete"], url_path="conversations")
    def conversation_detail(self, request, pk=None):
        conversation = self.get_queryset().filter(pk=pk).first()
        if conversation is None:
            return Response(
                {"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND
            )
        if request.method == "DELETE":
            conversation.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(ConversationDetailSerializer(conversation).data)

    # --- helpers ---

    @staticmethod
    def _get_client_ip(request) -> str | None:
        """Extract client IP from request, handling proxies."""
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR")

    def _get_conversation(self, request, conversation_id) -> Conversation:
        if conversation_id:
            conversation = Conversation.objects.filter(
                pk=conversation_id,
                organization_id=request.user.organization_id,
            ).first()
            if conversation is None:
                # Do not leak whether the id exists for another user.
                raise ValidationError({"conversation_id": "Unknown conversation."})
            return conversation
        return Conversation.objects.create(organization_id=request.user.organization_id)

    def _validate_document(self, request, document_id) -> str | None:
        if not document_id:
            return None
        exists = self.get_queryset().filter(pk=document_id).exists()
        if not exists:
            raise ValidationError({"document_id": "Unknown document."})
        return str(document_id)

    def _history(self, conversation: Conversation) -> list[dict]:
        messages = conversation.messages.order_by("created_at").values("role", "content")
        return list(messages)

    @staticmethod
    def _title(conversation: Conversation, first_message: str) -> None:
        if not conversation.title:
            conversation.title = first_message[:80]
            conversation.save(update_fields=["title", "modified_at"])
        else:
            conversation.save(update_fields=["modified_at"])


class AdminDashboardView(APIView):
    """Aggregated ingestion quality dashboard for admins."""

    permission_classes = [IsOrgAdmin]

    @extend_schema(
        responses={
            200: {
                "type": "object",
                "properties": {
                    "total_documents": {"type": "integer"},
                    "quality_distribution": {
                        "type": "object",
                        "properties": {
                            "GOOD": {"type": "integer"},
                            "WARNING": {"type": "integer"},
                            "BAD": {"type": "integer"},
                        },
                    },
                    "documents_with_warnings": {"type": "integer"},
                    "recent_failed_ingestions": {"type": "integer"},
                    "avg_ingestion_duration_seconds": {"type": "number"},
                },
            }
        },
        description="Admin dashboard with ingestion quality summary.",
    )
    def get(self, request):
        documents = Document.objects.filter(organization_id=request.user.organization_id)

        total = documents.count()
        quality_dist = {"GOOD": 0, "WARNING": 0, "BAD": 0}
        documents_with_warnings = 0
        total_duration = 0.0
        duration_count = 0
        recent_failed = 0

        for doc in documents:
            report = doc.last_ingestion_report
            if report:
                quality_dist[report.quality_score] = quality_dist.get(report.quality_score, 0) + 1
                if report.warnings:
                    documents_with_warnings += 1
                if report.ingestion_duration_seconds > 0:
                    total_duration += report.ingestion_duration_seconds
                    duration_count += 1
            if doc.status == Status.ERROR:
                recent_failed += 1

        avg_duration = total_duration / duration_count if duration_count > 0 else 0.0

        return Response({
            "total_documents": total,
            "quality_distribution": quality_dist,
            "documents_with_warnings": documents_with_warnings,
            "recent_failed_ingestions": recent_failed,
            "avg_ingestion_duration_seconds": round(avg_duration, 2),
        })


class CorpusReadinessView(APIView):
    """Tell an organization exactly which sources are queryable and why."""

    permission_classes = [IsOrgAdmin]

    @extend_schema(
        responses={200: OpenApiResponse(description="Organization corpus readiness report.")},
        description="Report indexed documents, failed ingestions, and warnings.",
    )
    def get(self, request):
        documents = Document.objects.filter(
            organization_id=request.user.organization_id
        ).select_related("last_ingestion_report")
        total = documents.count()
        ready_documents = list(documents.filter(status=Status.READY))
        queryable_documents = list(
            documents.filter(status__in=[Status.ENRICHING, Status.READY])
        )

        failed = [
            {
                "id": str(document.id),
                "name": document.name,
                "reason": document.error_message or "Unknown ingestion error.",
                "quality": (
                    document.last_ingestion_report.quality_score
                    if document.last_ingestion_report
                    else None
                ),
            }
            for document in documents.filter(status=Status.ERROR)
        ]
        needs_attention = [
            {
                "id": str(document.id),
                "name": document.name,
                "warnings": document.last_ingestion_report.warnings,
            }
            for document in queryable_documents
            if document.last_ingestion_report
            and document.last_ingestion_report.warnings
        ]
        ready_needs_attention = [
            document
            for document in ready_documents
            if document.last_ingestion_report and document.last_ingestion_report.warnings
        ]

        return Response(
            {
                "total_documents": total,
                "queryable_documents": len(queryable_documents),
                "enriching_documents": sum(
                    1 for document in queryable_documents if document.status == Status.ENRICHING
                ),
                "fully_indexed": len(ready_documents) - len(ready_needs_attention),
                "indexed_with_warnings": len(needs_attention),
                "failed": failed,
                "needs_attention": needs_attention,
                "readiness_percent": round(100 * len(ready_documents) / total, 1)
                if total
                else 0.0,
            }
        )


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only audit log for compliance queries."""

    permission_classes = [IsOrgAdmin]
    serializer_class = AuditLogSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "audit"

    def get_queryset(self):
        return AuditLog.objects.filter(
            organization_id=self.request.user.organization_id
        ).order_by("-created_at")


class EvaluationReportViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only evaluation reports for RAG quality metrics."""

    permission_classes = [IsAuthenticated]
    serializer_class = EvaluationReportSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "evaluation"

    def get_queryset(self):
        return EvaluationReport.objects.filter(
            document__in=visible_documents(self.request.user)
        ).order_by("-created_at")
