from django.db import migrations
from django.db.models import Count


def merge(apps, schema_editor):
    """Each qualification only once per duty. Migration 0007 gave positions for any
    qualification the qualification "Helfer", also at duties that already had a position
    for it; such positions are merged into the first one (with its rate)."""
    DutyPosition = apps.get_model("duties", "DutyPosition")
    Report = apps.get_model("duties", "Report")
    twice = (
        DutyPosition.objects.values("duty", "qualification")
        .annotate(positions=Count("pk"))
        .filter(positions__gt=1)
    )
    for group in twice:
        first, *others = DutyPosition.objects.filter(
            duty=group["duty"], qualification=group["qualification"]
        ).order_by("pk")
        for other in others:
            first.count += other.count
            Report.objects.filter(position=other).update(position=first)
            Report.objects.filter(assigned_position=other).update(assigned_position=first)
            other.delete()
        first.save(update_fields=["count"])


class Migration(migrations.Migration):
    dependencies = [
        ("duties", "0009_report_helper_qualification"),
    ]

    operations = [
        migrations.RunPython(merge, migrations.RunPython.noop),
    ]
