from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views.decorators.http import require_POST

from accounts.permissions import is_dispatcher

from . import notifications, permissions
from .forms import DutyForm, HelperUnitForm, ReportFormSet
from .models import Duty, Report

DUTIES_PER_PAGE = 50


def _staffing_unit_id(user):
    """Units additionally see how many helpers they have reported themselves."""
    return None if is_dispatcher(user) else user.unit_id


def _dispatcher_only(request):
    if not is_dispatcher(request.user):
        raise PermissionDenied


# --- Duties ------------------------------------------------------------------------------


def duty_list(request):
    show_past = request.GET.get("show") == "past"
    duties = permissions.visible_duties(request.user).with_staffing(_staffing_unit_id(request.user))
    # Explicit ordering: the default ordering does not apply to the grouped staffing query.
    if show_past:
        duties = duties.past().order_by("-starts_at", "title")
    else:
        duties = duties.upcoming().order_by("starts_at", "title")
    page = Paginator(duties, DUTIES_PER_PAGE).get_page(request.GET.get("page"))
    return render(request, "duties/duty_list.html", {"page": page, "show_past": show_past})


def duty_detail(request, pk):
    duty = get_object_or_404(
        permissions.visible_duties(request.user).with_staffing(_staffing_unit_id(request.user)),
        pk=pk,
    )
    reports = [
        (
            report,
            permissions.can_withdraw_report(request.user, report),
            permissions.can_decide_report(request.user, report),
        )
        for report in permissions.visible_reports(request.user)
        .filter(duty=duty)
        .select_related("duty", "unit", "reported_by")
    ]
    return render(
        request,
        "duties/duty_detail.html",
        {
            "duty": duty,
            "reports": reports,
            "can_report": permissions.can_report(request.user, duty),
            "offered_units": duty.units.all() if not duty.all_units else None,
        },
    )


def duty_create(request):
    _dispatcher_only(request)
    form = DutyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        duty = form.save(commit=False)
        duty.created_by = request.user
        duty.save()
        form.save_m2m()
        if "release" in request.POST:
            _release(request, duty)
        else:
            messages.success(
                request,
                _("Duty saved as a draft. The units will only see it once you release it."),
            )
        return redirect(duty)
    return render(request, "duties/duty_form.html", {"form": form})


def duty_update(request, pk):
    _dispatcher_only(request)
    duty = get_object_or_404(Duty, pk=pk)
    if duty.status == Duty.Status.CANCELLED:
        messages.error(request, _("Cancelled duties can no longer be edited."))
        return redirect(duty)
    # Before validating: the form already applies its values to the instance.
    offered_before = duty.offered_unit_ids()
    form = DutyForm(request.POST or None, instance=duty)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Saved."))
        if duty.status == Duty.Status.RELEASED:
            changes = [name for name in notifications.RELEVANT_CHANGES if name in form.changed_data]
            if changes:
                notifications.duty_changed(request, duty, changes)
            added_units = duty.offered_unit_ids() - offered_before
            if added_units:
                notifications.duty_released(request, duty, added_units)
        return redirect(duty)
    return render(request, "duties/duty_form.html", {"form": form, "duty": duty})


def _change_status(duty, old_status, new_status, timestamp_field):
    """Changes the status unless another request got there first; returns whether it did.

    Changing it in the database only if it is still ``old_status`` makes sure that a
    double click does not send the notifications twice.
    """
    now = timezone.now()
    changed = Duty.objects.filter(pk=duty.pk, status=old_status).update(
        status=new_status, updated_at=now, **{timestamp_field: now}
    )
    if changed:
        duty.status = new_status
        setattr(duty, timestamp_field, now)
    return bool(changed)


def _release(request, duty):
    if duty.has_started:
        messages.error(request, _("This duty has already started and can no longer be released."))
    elif _change_status(duty, Duty.Status.DRAFT, Duty.Status.RELEASED, "released_at"):
        messages.success(request, _("Duty released. The units have been notified."))
        notifications.duty_released(request, duty)
    else:
        messages.error(request, _("Only drafts can be released."))


@require_POST
def duty_release(request, pk):
    _dispatcher_only(request)
    duty = get_object_or_404(Duty, pk=pk)
    _release(request, duty)
    return redirect(duty)


@require_POST
def duty_cancel(request, pk):
    _dispatcher_only(request)
    duty = get_object_or_404(Duty, pk=pk)
    if _change_status(duty, Duty.Status.RELEASED, Duty.Status.CANCELLED, "cancelled_at"):
        messages.success(request, _("Duty cancelled."))
        notifications.duty_cancelled(request, duty)
    else:
        messages.error(request, _("Only released duties can be cancelled."))
    return redirect(duty)


@require_POST
def duty_delete(request, pk):
    _dispatcher_only(request)
    duty = get_object_or_404(Duty, pk=pk)
    if duty.status != Duty.Status.DRAFT:
        messages.error(request, _("Only drafts can be deleted. Cancel released duties instead."))
        return redirect(duty)
    duty.delete()
    messages.success(request, _("Duty “%(title)s” deleted.") % {"title": duty.title})
    return redirect("duty_list")


# --- Reports -----------------------------------------------------------------------------


def report_create(request, pk):
    duty = get_object_or_404(permissions.visible_duties(request.user), pk=pk)
    if not permissions.is_unit_account(request.user) or is_dispatcher(request.user):
        raise PermissionDenied
    if not permissions.can_report(request.user, duty):
        messages.error(request, _("Helpers can no longer be reported for this duty."))
        return redirect(duty)
    unit = request.user.unit
    formset = ReportFormSet(request.POST or None, duty=duty, unit=unit)
    if request.method == "POST" and formset.is_valid():
        reports = _save_new_reports(request, formset, duty, unit, reported_by=request.user)
        if reports is not None:
            messages.success(
                request,
                ngettext("%(count)d helper reported.", "%(count)d helpers reported.", len(reports))
                % {"count": len(reports)},
            )
            notifications.helpers_reported(request, duty, unit, reports)
            if "another" in request.POST:
                return redirect("report_create", pk=duty.pk)
            return redirect(duty)
    return render(request, "duties/report_form.html", {"duty": duty, "formset": formset})


def report_add(request, pk):
    """Dispatchers enter helpers of a unit themselves (e.g. after an email); confirmed at once."""
    _dispatcher_only(request)
    duty = get_object_or_404(Duty, pk=pk)
    if duty.status != Duty.Status.RELEASED:
        messages.error(request, _("Helpers can only be added to released duties."))
        return redirect(duty)
    unit_form = HelperUnitForm(
        request.POST or None, duty=duty, initial={"unit": request.GET.get("unit")}
    )
    unit = unit_form.cleaned_data["unit"] if unit_form.is_valid() else None
    formset = ReportFormSet(request.POST or None, duty=duty, unit=unit)
    if request.method == "POST" and unit is not None and formset.is_valid():
        reports = _save_new_reports(
            request,
            formset,
            duty,
            unit,
            status=Report.Status.CONFIRMED,
            reported_by=request.user,
            decided_by=request.user,
            decided_at=timezone.now(),
        )
        if reports is not None:
            messages.success(
                request,
                ngettext(
                    "%(count)d helper added and confirmed.",
                    "%(count)d helpers added and confirmed.",
                    len(reports),
                )
                % {"count": len(reports)},
            )
            notifications.helpers_added(request, duty, unit, reports)
            if "another" in request.POST:
                return redirect(f"{reverse('report_add', args=[duty.pk])}?unit={unit.pk}")
            return redirect(duty)
    return render(
        request,
        "duties/report_add.html",
        {"duty": duty, "unit_form": unit_form, "formset": formset},
    )


def _save_new_reports(request, formset, duty, unit, **values):
    """Saves the filled-in rows; returns the reports, or None if a name was taken meanwhile."""
    reports = formset.new_reports()
    for report in reports:
        report.duty = duty
        report.unit = unit
        for name, value in values.items():
            setattr(report, name, value)
    try:
        with transaction.atomic():
            for report in reports:
                report.save()
    except IntegrityError:
        # Someone else reported the same person in the meantime.
        messages.error(request, _("One of these people has already been reported."))
        return None
    return reports


def _visible_report(request, pk):
    return get_object_or_404(
        permissions.visible_reports(request.user).select_related("duty", "unit"), pk=pk
    )


@require_POST
def report_withdraw(request, pk):
    report = _visible_report(request, pk)
    if not permissions.can_withdraw_report(request.user, report):
        raise PermissionDenied
    now = timezone.now()
    # Only if still active: a decision may have been made in the meantime, which is fine,
    # but a second click must not notify twice.
    if Report.objects.filter(pk=report.pk, status__in=Report.ACTIVE).update(
        status=Report.Status.WITHDRAWN, withdrawn_at=now
    ):
        report.status = Report.Status.WITHDRAWN
        report.withdrawn_at = now
        messages.success(request, _("Report for %(name)s withdrawn.") % {"name": report.name})
        notifications.report_withdrawn(request, report)
    return redirect(report.duty)


def _decide(request, reports, status):
    """Confirms or declines the reports; returns those that actually changed.

    Reports withdrawn in the meantime stay withdrawn.
    """
    now = timezone.now()
    changed = []
    for report in reports:
        if (
            Report.objects.filter(pk=report.pk)
            .exclude(status__in=[status, Report.Status.WITHDRAWN])
            .update(status=status, decided_at=now, decided_by=request.user)
        ):
            report.status = status
            report.decided_at = now
            report.decided_by = request.user
            changed.append(report)
    return changed


@require_POST
def report_decide(request, pk, decision):
    _dispatcher_only(request)
    report = _visible_report(request, pk)
    if not permissions.can_decide_report(request.user, report):
        messages.error(request, _("This report can no longer be changed."))
        return redirect(report.duty)
    status = Report.Status.CONFIRMED if decision == "confirm" else Report.Status.DECLINED
    if _decide(request, [report], status):
        notifications.reports_decided(request, report.duty, [report])
    else:
        report.refresh_from_db(fields=["status"])
    if report.status == Report.Status.WITHDRAWN:
        messages.error(request, _("This report can no longer be changed."))
    elif status == Report.Status.CONFIRMED:
        messages.success(request, _("%(name)s confirmed.") % {"name": report.name})
    else:
        messages.success(request, _("%(name)s declined.") % {"name": report.name})
    return redirect(report.duty)


@require_POST
def report_confirm_all(request, pk):
    _dispatcher_only(request)
    duty = get_object_or_404(Duty, pk=pk, status=Duty.Status.RELEASED)
    pending = duty.reports.filter(status=Report.Status.REPORTED).select_related("unit")
    reports = _decide(request, list(pending), Report.Status.CONFIRMED)
    if reports:
        notifications.reports_decided(request, duty, reports)
    messages.success(
        request,
        ngettext("%(count)d report confirmed.", "%(count)d reports confirmed.", len(reports))
        % {"count": len(reports)},
    )
    return redirect(duty)
