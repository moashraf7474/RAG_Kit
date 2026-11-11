"""Organization and private-document isolation tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from rest_framework.test import APIClient
from django.urls import reverse
from api.models import Organization, Role
from rag.models import Document
from rag.views import visible_documents


@pytest.fixture
def user_factory(db):
    from api.tests.factories import UserFactory

    return UserFactory


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def organization_factory():
    def create(name: str = "Acme Corporation"):
        return Organization.objects.create(
            name=name,
            slug=f"{name.lower().replace(' ', '-')}-{Organization.objects.count()}",
        )

    return create


@pytest.mark.django_db
def test_org_members_share_public_documents(organization_factory, user_factory):
    organization = organization_factory()
    sarah = user_factory.create(organization=organization)
    ahmed = user_factory.create(organization=organization, username="ahmed@example.com")
    document = Document.objects.create(
        organization=organization,
        uploaded_by=sarah,
        name="handbook.pdf",
    )

    assert list(visible_documents(ahmed)) == [document]


@pytest.mark.django_db
def test_other_organizations_see_nothing(organization_factory, user_factory):
    org_a = organization_factory("Acme")
    org_b = organization_factory("Globex")
    sarah = user_factory.create(organization=org_a)
    outsider = user_factory.create(organization=org_b, username="outsider@example.com")
    Document.objects.create(organization=org_a, uploaded_by=sarah, name="secret.pdf")

    assert not visible_documents(outsider).exists()


@pytest.mark.django_db
def test_private_document_is_visible_to_uploader_and_admin_only(
    organization_factory, user_factory
):
    organization = organization_factory()
    sarah = user_factory.create(organization=organization)
    ahmed = user_factory.create(organization=organization, username="ahmed@example.com")
    admin = user_factory.create(
        organization=organization, username="admin@example.com", role=Role.ADMIN
    )
    document = Document.objects.create(
        organization=organization,
        uploaded_by=sarah,
        name="private.pdf",
        is_private=True,
    )

    assert list(visible_documents(sarah)) == [document]
    assert not visible_documents(ahmed).exists()
    assert list(visible_documents(admin)) == [document]


@pytest.mark.django_db
def test_document_detail_is_org_scoped(api_client, organization_factory, user_factory):
    org_a = organization_factory("Acme")
    org_b = organization_factory("Globex")
    owner = user_factory.create(organization=org_a)
    outsider = user_factory.create(organization=org_b, username="outsider@example.com")
    document = Document.objects.create(
        organization=org_a,
        uploaded_by=owner,
        name="handbook.pdf",
        storage_key="org-a/handbook.pdf",
    )

    api_client.force_authenticate(user=outsider)
    response = api_client.get(
        reverse("rag-documents-resolve-download", kwargs={"pk": document.id})
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_retrieval_carries_organization_and_private_visibility_filter():
    captured = []

    async def fake_search(
        query, *, k, score_threshold, filter_kwargs=None, extra_must=None
    ):
        captured.append((filter_kwargs, extra_must))
        return []

    with (
        patch("rag.retrieval.collection_available", return_value=True),
        patch("rag.retrieval.asearch", side_effect=fake_search),
    ):
        from rag.retrieval import retrieve

        await retrieve(
            "question",
            organization_id="org-1",
            user_id=42,
            is_org_admin=False,
        )

    assert captured
    assert all(filters["organization_id"] == "org-1" for filters, _ in captured)
    assert all(extra_must and len(extra_must) == 1 for _, extra_must in captured)
