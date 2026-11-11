import api.models
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0002_organization"),
    ]

    operations = [
        migrations.AlterModelManagers(
            name="user",
            managers=[("objects", api.models.UserManager())],
        ),
    ]
