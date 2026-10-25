# Separate from 0007: PostgreSQL does not alter a table with pending updates in the
# same transaction.
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("duties", "0007_report_assigned_position"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="dutyposition",
            options={
                "ordering": ["qualification__sort_order", "pk"],
                "verbose_name": "position",
                "verbose_name_plural": "positions",
            },
        ),
        migrations.AlterField(
            model_name="dutyposition",
            name="qualification",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="positions",
                to="duties.qualification",
                verbose_name="qualification",
            ),
        ),
    ]
