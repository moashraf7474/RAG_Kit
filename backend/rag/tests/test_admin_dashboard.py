"""Tests for admin dashboard."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from api.models import Organization, Role
from api.tests.factories import UserFactory
from rag.graph import _confidence
from rag.models import Document, IngestionReport, QualityScore, Status


def test_admin_dashboard_returns_summary() -> None:
    client = APIClient()
    user = MagicMock()
    user.is_authenticated = True
    client.force_authenticate(user=user)

    mock_documents = MagicMock()
    mock_documents.count.return_value = 10
    mock_documents.filter.return_value = mock_documents

    with patch("rag.views.Document.objects.filter", return_value=mock_documents), \
         patch("rag.views.IngestionReport.objects.filter", return_value=MagicMock()):
        from rag.views import AdminDashboardView
        view = AdminDashboardView()
        request = MagicMock()
        request.user = user
        response = view.get(request)

    assert response.status_code == 200
    assert "total_documents" in response.data
    assert "quality_distribution" in response.data
    assert "documents_with_warnings" in response.data
    assert "recent_failed_ingestions" in response.data
    assert "avg_ingestion_duration_seconds" in response.data


@pytest.mark.django_db
def test_corpus_readiness_is_org_scoped_and_explains_failures():
    organization = Organization.objects.create(name="Acme", slug="acme-readiness")
    other_organization = Organization.objects.create(name="Other", slug="other-readiness")
    admin = UserFactory.create(
        organization=organization,
        username="admin-readiness@example.com",
        role=Role.ADMIN,
    )

    ready = Document.objects.create(
        organization=organization, name="ready.txt", status=Status.READY
    )
    warning = Document.objects.create(
        organization=organization, name="warning.pdf", status=Status.READY
    )
    warning_report = IngestionReport.objects.create(
        document=warning,
        quality_score=QualityScore.WARNING,
        warnings=["Low character density."],
    )
    warning.last_ingestion_report = warning_report
    warning.save(update_fields=["last_ingestion_report"])
    failed = Document.objects.create(
        organization=organization,
        name="failed.pdf",
        status=Status.ERROR,
        error_message="No readable content could be extracted.",
    )
    failed_report = IngestionReport.objects.create(
        document=failed, quality_score=QualityScore.BAD
    )
    failed.last_ingestion_report = failed_report
    failed.save(update_fields=["last_ingestion_report"])
    Document.objects.create(
        organization=other_organization, name="other.txt", status=Status.READY
    )

    client = APIClient()
    client.force_authenticate(user=admin)
    response = client.get(reverse("corpus-readiness"))

    assert response.status_code == 200
    assert response.data["total_documents"] == 3
    assert response.data["fully_indexed"] == 1
    assert response.data["indexed_with_warnings"] == 1
    assert response.data["readiness_percent"] == 66.7
    assert response.data["failed"][0]["name"] == "failed.pdf"
    assert response.data["needs_attention"][0]["name"] == "warning.pdf"


def test_confidence_uses_top_citation_score():
    assert _confidence([]) == "unknown"
    assert _confidence([{"score": 0.8}]) == "high"
    assert _confidence([{"score": 0.2}]) == "medium"
    assert _confidence([{"score": 0.01}]) == "low"
