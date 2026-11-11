import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Organization",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("name", models.CharField(max_length=255, verbose_name="name")),
                (
                    "slug",
                    models.SlugField(max_length=64, unique=True, verbose_name="slug"),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="created at"),
                ),
                (
                    "modified_at",
                    models.DateTimeField(auto_now=True, verbose_name="modified at"),
                ),
            ],
            options={
                "verbose_name": "organization",
                "verbose_name_plural": "organizations",
                "db_table": "organizations",
                "ordering": ["name"],
            },
        ),
        migrations.AddField(
            model_name="user",
            name="organization",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="users",
                to="api.organization",
                verbose_name="organization",
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[
                    ("ADMIN", "Admin"),
                    ("MEMBER", "Member"),
                    ("VIEWER", "Viewer"),
                ],
                default="MEMBER",
                max_length=16,
                verbose_name="role",
            ),
        ),
    ]
