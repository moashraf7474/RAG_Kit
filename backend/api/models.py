import uuid

from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _


class Organization(models.Model):
    """A customer company. Documents and conversations belong to an organization."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(_("name"), max_length=255)
    slug = models.SlugField(_("slug"), max_length=64, unique=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    modified_at = models.DateTimeField(_("modified at"), auto_now=True)

    class Meta:
        db_table = "organizations"
        verbose_name = _("organization")
        verbose_name_plural = _("organizations")
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Role(models.TextChoices):
    ADMIN = "ADMIN", _("Admin")
    MEMBER = "MEMBER", _("Member")
    VIEWER = "VIEWER", _("Viewer")


class UserManager(BaseUserManager):
    """Create a personal organization when a user is created outside the API."""

    use_in_migrations = True

    def _organization_for(self, username: str, organization=None):
        if organization is not None:
            return organization
        base_slug = slugify(username)[:48] or "organization"
        slug = f"{base_slug}-{uuid.uuid4().hex[:12]}"[:64]
        return Organization.objects.create(name=f"{username}'s organization", slug=slug)

    def _create_user(self, username, email, password, **extra_fields):
        if not username:
            raise ValueError("The username must be set")
        organization = self._organization_for(
            username, extra_fields.pop("organization", None)
        )
        user = self.model(
            username=username,
            email=self.normalize_email(email),
            organization=organization,
            **extra_fields,
        )
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(username, email, password, **extra_fields)

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", Role.ADMIN)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(username, email, password, **extra_fields)


class User(AbstractUser):
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="users",
        null=True,
        blank=True,
        verbose_name=_("organization"),
    )
    role = models.CharField(
        _("role"), max_length=16, choices=Role.choices, default=Role.MEMBER
    )
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    modified_at = models.DateTimeField(_("modified at"), auto_now=True)

    objects = UserManager()

    class Meta:
        db_table = "users"
        verbose_name = _("user")
        verbose_name_plural = _("users")

    @property
    def is_org_admin(self) -> bool:
        return self.is_superuser or self.role == Role.ADMIN

    def __str__(self):
        return self.email if self.email else self.username
