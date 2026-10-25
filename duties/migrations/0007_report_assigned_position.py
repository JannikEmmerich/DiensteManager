import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Max


def fill_in(apps, schema_editor):
    """Confirmed helpers fill the position they were reported for (as they were paid so
    far); positions for any qualification get the qualification "Helfer"."""
    DutyPosition = apps.get_model("duties", "DutyPosition")
    Qualification = apps.get_model("duties", "Qualification")
    Report = apps.get_model("duties", "Report")
    for report in Report.objects.filter(status="confirmed", position__isnull=False):
        report.assigned_position_id = report.position_id
        report.save(update_fields=["assigned_position"])
    unqualified = DutyPosition.objects.filter(qualification__isnull=True)
    if unqualified.exists():
        last = Qualification.objects.aggregate(last=Max("sort_order"))["last"] or 0
        helper, _created = Qualification.objects.get_or_create(
            name="Helfer", defaults={"sort_order": last + 10}
        )
        unqualified.update(qualification=helper)


class Migration(migrations.Migration):
    dependencies = [
        ("duties", "0006_qualification_includes"),
    ]

    operations = [
        migrations.AddField(
            model_name="report",
            name="assigned_position",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="assigned_reports",
                to="duties.dutyposition",
                verbose_name="assigned to",
            ),
        ),
        migrations.RunPython(fill_in, migrations.RunPython.noop),
    ]
