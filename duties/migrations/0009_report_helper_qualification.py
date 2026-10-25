import django.db.models.deletion
from django.db import migrations, models
from django.db.models import OuterRef, Subquery


def fill_in(apps, schema_editor):
    """So far helpers had the qualification of the position they were reported for."""
    DutyPosition = apps.get_model("duties", "DutyPosition")
    Report = apps.get_model("duties", "Report")
    Report.objects.filter(position__isnull=False).update(
        helper_qualification=Subquery(
            DutyPosition.objects.filter(pk=OuterRef("position")).values("qualification")[:1]
        )
    )


class Migration(migrations.Migration):
    dependencies = [
        ("duties", "0008_position_qualification_required"),
    ]

    operations = [
        migrations.AddField(
            model_name="report",
            name="helper_qualification",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="duties.qualification",
                verbose_name="qualification of the helper",
            ),
        ),
        migrations.RunPython(fill_in, migrations.RunPython.noop),
    ]
