import pytest
from django.urls import reverse
from rest_framework import status

from rag.models import Document


@pytest.mark.django_db
def test_api_users_me_unauthorized(client):
    response = client.get(reverse("api-users-me"))
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
def test_api_users_me_authorized(api_client, regular_user):
    api_client.force_authenticate(user=regular_user)
    response = api_client.get(reverse("api-users-me"))
    assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_resolve_download_requires_auth(api_client, user_factory):
    owner = user_factory.create(username="owner@example.com")
    document = Document.objects.create(owner=owner, name="secret.pdf", storage_key="x/secret.pdf")
    response = api_client.get(
        reverse("rag-documents-resolve-download", kwargs={"pk": document.id})
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
def test_resolve_download_returns_fresh_url(api_client, regular_user, monkeypatch):
    Document.objects.create(
        owner=regular_user, name="notes.pdf", storage_key="u/notes.pdf"
    )
    api_client.force_authenticate(user=regular_user)
    from rag import storage

    monkeypatch.setattr(storage, "presigned_url", lambda key: f"https://fresh/{key}")
    response = api_client.get(
        reverse("rag-documents-resolve-download", kwargs={"pk": Document.objects.get().id})
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"download_url": "https://fresh/u/notes.pdf"}


@pytest.mark.django_db
def test_resolve_download_is_tenant_scoped(api_client, regular_user, user_factory):
    other = user_factory.create(username="other@example.com")
    document = Document.objects.create(owner=other, name="others.pdf", storage_key="x/others.pdf")
    api_client.force_authenticate(user=regular_user)
    response = api_client.get(
        reverse("rag-documents-resolve-download", kwargs={"pk": document.id})
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND
