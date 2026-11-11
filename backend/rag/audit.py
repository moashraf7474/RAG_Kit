"""Audit logging for compliance and forensics."""

from __future__ import annotations

import logging
from typing import Any

from django.contrib.auth import get_user_model
from django.db import transaction

from rag.models import AuditLog

logger = logging.getLogger(__name__)
User = get_user_model()


def log_action(
    *,
    actor: User | None,
    action: str,
    resource_type: str = "",
    resource_id: str = "",
    metadata: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> None:
    """Write an audit log entry. Fire-and-forget: failures are logged but never raised."""
    try:
        entry = AuditLog(
            organization=getattr(actor, "organization", None),
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id else "",
            metadata=metadata or {},
            ip_address=ip_address,
        )
        entry.save(force_insert=True)
    except Exception:
        logger.exception("Failed to write audit log.")


def log_document_upload(actor: User | None, document_id: str, ip_address: str | None = None) -> None:
    log_action(
        actor=actor,
        action=AuditLog.Action.DOCUMENT_UPLOAD,
        resource_type="document",
        resource_id=document_id,
        ip_address=ip_address,
    )


def log_document_reindex(actor: User | None, document_id: str, ip_address: str | None = None) -> None:
    log_action(
        actor=actor,
        action=AuditLog.Action.DOCUMENT_REINDEX,
        resource_type="document",
        resource_id=document_id,
        ip_address=ip_address,
    )


def log_document_delete(actor: User | None, document_id: str, ip_address: str | None = None) -> None:
    log_action(
        actor=actor,
        action=AuditLog.Action.DOCUMENT_DELETE,
        resource_type="document",
        resource_id=document_id,
        ip_address=ip_address,
    )


def log_query(actor: User | None, query: str, ip_address: str | None = None) -> None:
    log_action(
        actor=actor,
        action=AuditLog.Action.QUERY,
        resource_type="query",
        metadata={"query": query[:1000]},
        ip_address=ip_address,
    )
