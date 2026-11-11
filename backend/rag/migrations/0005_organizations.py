from django.db import migrations, models
from django.utils.text import slugify


def create_organizations_and_backfill(apps, schema_editor):
    Organization = apps.get_model("api", "Organization")
    User = apps.get_model("api", "User")
    Document = apps.get_model("rag", "Document")
    Conversation = apps.get_model("rag", "Conversation")
    AuditLog = apps.get_model("rag", "AuditLog")

    def organization_for(user):
        if user.organization_id:
            return Organization.objects.get(pk=user.organization_id)
        slug = slugify(f"{user.username}-{user.pk}")[:64] or f"organization-{user.pk}"
        organization = Organization.objects.create(
            name=f"{user.username}'s organization", slug=slug
        )
        User.objects.filter(pk=user.pk).update(
            organization_id=organization.pk,
            role="ADMIN",
        )
        return organization

    for user in User.objects.all().iterator():
        organization = organization_for(user)
        Document.objects.filter(owner_id=user.pk).update(
            organization_id=organization.pk,
            uploaded_by_id=user.pk,
        )
        Conversation.objects.filter(owner_id=user.pk).update(
            organization_id=organization.pk
        )

    for audit_log in AuditLog.objects.filter(
        actor_id__isnull=False, organization_id__isnull=True
    ).iterator():
        actor = User.objects.filter(pk=audit_log.actor_id).first()
        if actor and actor.organization_id:
            AuditLog.objects.filter(pk=audit_log.pk).update(
                organization_id=actor.organization_id
            )


def prevent_reverse(apps, schema_editor):
    raise RuntimeError("Organization backfill is irreversible.")


class Migration(migrations.Migration):
    # PostgreSQL cannot alter the legacy foreign-key columns in the same
    # transaction that backfills referenced users and documents.
    atomic = False

    dependencies = [
        ("api", "0002_organization"),
        ("rag", "0004_add_evaluation_report"),
    ]

    operations = [
        migrations.AddField(
            model_name="document",
            name="organization",
            field=models.ForeignKey(
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name="documents",
                to="api.organization",
                verbose_name="organization",
            ),
        ),
        migrations.AddField(
            model_name="document",
            name="uploaded_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="uploaded_documents",
                to="api.user",
                verbose_name="uploaded by",
            ),
        ),
        migrations.AddField(
            model_name="document",
            name="is_private",
            field=models.BooleanField(default=False, verbose_name="private"),
        ),
        migrations.AddField(
            model_name="conversation",
            name="organization",
            field=models.ForeignKey(
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name="conversations",
                to="api.organization",
                verbose_name="organization",
            ),
        ),
        migrations.AddField(
            model_name="auditlog",
            name="organization",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name="audit_logs",
                to="api.organization",
                verbose_name="organization",
            ),
        ),
        migrations.RunPython(create_organizations_and_backfill, prevent_reverse),
        migrations.RemoveConstraint(
            model_name="document",
            name="unique_document_per_owner",
        ),
        migrations.RemoveField(model_name="document", name="owner"),
        migrations.RemoveField(model_name="conversation", name="owner"),
        migrations.AlterField(
            model_name="document",
            name="organization",
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                related_name="documents",
                to="api.organization",
                verbose_name="organization",
            ),
        ),
        migrations.AlterField(
            model_name="conversation",
            name="organization",
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                related_name="conversations",
                to="api.organization",
                verbose_name="organization",
            ),
        ),
        migrations.AddConstraint(
            model_name="document",
            constraint=models.UniqueConstraint(
                fields=("organization", "name"), name="unique_document_per_org"
            ),
        ),
        migrations.AddIndex(
            model_name="document",
            index=models.Index(
                fields=["organization", "status"],
                name="rag_documen_organiz_78e6fe_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="document",
            index=models.Index(
                fields=["organization", "-created_at"],
                name="rag_documen_organiz_97f6fe_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="conversation",
            index=models.Index(
                fields=["organization", "-modified_at"],
                name="rag_convers_organiz_765cd4_idx",
            ),
        ),
        migrations.AlterModelOptions(
            name="document",
            options={
                "db_table": "rag_documents",
                "indexes": [
                    models.Index(
                        fields=["organization", "status"],
                        name="rag_documen_organiz_78e6fe_idx",
                    ),
                    models.Index(
                        fields=["organization", "-created_at"],
                        name="rag_documen_organiz_97f6fe_idx",
                    ),
                ],
                "ordering": ["-created_at"],
                "verbose_name": "document",
                "verbose_name_plural": "documents",
            },
        ),
        migrations.AlterModelOptions(
            name="conversation",
            options={
                "db_table": "rag_conversations",
                "indexes": [
                    models.Index(
                        fields=["organization", "-modified_at"],
                        name="rag_convers_organiz_765cd4_idx",
                    )
                ],
                "ordering": ["-modified_at"],
                "verbose_name": "conversation",
                "verbose_name_plural": "conversations",
            },
        ),
    ]
