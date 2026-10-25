from django.db.models import F
from django.shortcuts import render
from django.utils import timezone

from accounts.permissions import is_dispatcher
from duties.models import Duty, Report
from duties.permissions import visible_duties

OPEN_DUTIES_ON_HOME = 5


def home(request):
    user = request.user
    dispatcher = is_dispatcher(user)
    open_duties = (
        visible_duties(user)
        .filter(status=Duty.Status.RELEASED, starts_at__gt=timezone.now())
        .with_staffing(None if dispatcher else user.unit_id)
        .filter(confirmed_count__lt=F("helpers_needed"))
        .order_by("starts_at", "title")[:OPEN_DUTIES_ON_HOME]
    )
    context = {"open_duties": open_duties}
    if dispatcher:
        context["pending_report_count"] = Report.objects.filter(
            status=Report.Status.REPORTED,
            duty__status=Duty.Status.RELEASED,
            duty__ends_at__gte=timezone.now(),
        ).count()
        context["draft_count"] = Duty.objects.filter(status=Duty.Status.DRAFT).upcoming().count()
    return render(request, "core/home.html", context)
