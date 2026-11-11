from django.contrib.auth import get_user_model
from factory import Sequence, SubFactory
from factory.django import DjangoModelFactory

from api.models import Organization


class OrganizationFactory(DjangoModelFactory):
    name = Sequence(lambda n: f"Test Organization {n}")
    slug = Sequence(lambda n: f"test-organization-{n}")

    class Meta:
        model = Organization


class UserFactory(DjangoModelFactory):
    username = "sample@example.com"
    email = "sample@example.com"
    organization = SubFactory(OrganizationFactory)

    class Meta:
        model = get_user_model()
