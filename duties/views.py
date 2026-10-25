from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views.decorators.http import require_POST

from accounts.permissions import is_dispatcher
from lifecycle import history

from . import notifications, permissions
from .forms import (
    DutyForm,
    DutyPositionFormSet,
    HelperUnitForm,
    QualificationFormSet,
    ReportFormSet,
    WithdrawalRequestForm,
    copy_formset_class,
)
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
        {
            "report": report,
            "can_withdraw": permissions.can_withdraw_report(request.user, report),
            "can_request_withdrawal": permissions.can_request_withdrawal(request.user, report),
            "can_decide": permissions.can_decide_report(request.user, report),
            "can_take_back": permissions.can_take_back_report(request.user, report),
        }
        for report in permissions.visible_reports(request.user)
        .filter(duty=duty)
        .select_related(
            "duty", "unit", "reported_by", "withdrawal_requested_by", "position__qualification"
        )
    ]
    positions = list(duty.positions_with_staffing())
    # A single position "any qualification" needs no breakdown.
    show_positions = len(positions) > 1 or any(p.qualification_id for p in positions)
    return render(
        request,
        "duties/duty_detail.html",
        {
            "duty": duty,
            "reports": reports,
            "positions": positions,
            "show_positions": show_positions,
            # Older reports may have a qualification entered as free text.
            "show_qualifications": show_positions
            or any(row["report"].qualification for row in reports),
            "can_report": permissions.can_report(request.user, duty),
            # The history names helpers of all units, so only dispatchers see it.
            "history": history.entries(history.scope_for_duty(duty.pk))
            if is_dispatcher(request.user)
            else None,
            "offered_units": duty.units.all() if not duty.all_units else None,
        },
    )


def _save_positions(duty, formset):
    formset.instance = duty
    formset.save()
    if duty.pay_type == Duty.PayType.NONE:
        # Without duty pay, rates of positions mean nothing.
        duty.positions.exclude(pay_rate=None).update(pay_rate=None)
    return formset.summary()


def _copy_source(request):
    """The duty named by ``?copy=<id>``, if any."""
    try:
        return Duty.objects.filter(pk=int(request.GET.get("copy", ""))).first()
    except ValueError:
        return None


def _copy_initial(request, source):
    """Form and position values for a new duty copied from ``source``.

    Date and times are left out (they have to be entered), as are reports and the status.
    Positions with a qualification that is no longer active are left out.
    """
    form_initial = {
        "title": source.title,
        "location": source.location,
        "description": source.description,
        "pay_type": source.pay_type,
        "pay_rate": source.pay_rate,
        "all_units": str(source.all_units),
        "units": list(source.units.values_list("pk", flat=True)),
    }
    positions = []
    skipped = []
    for position in source.positions.select_related("qualification"):
        if position.qualification and not position.qualification.is_active:
            skipped.append(position.qualification.name)
            continue
        positions.append(
            {
                "count": position.count,
                "qualification": position.qualification_id,
                "pay_rate": position.pay_rate,
            }
        )
    if skipped:
        messages.warning(
            request,
            _("These qualifications are no longer active and were not copied: %(names)s")
            % {"names": ", ".join(skipped)},
        )
    return form_initial, positions


def duty_create(request):
    _dispatcher_only(request)
    source = _copy_source(request) if request.method == "GET" else None
    if source:
        form_initial, positions = _copy_initial(request, source)
        formset_class = copy_formset_class(positions)
        return render(
            request,
            "duties/duty_form.html",
            {
                "form": DutyForm(initial=form_initial),
                "formset": formset_class(instance=Duty(), prefix="positions", initial=positions),
                "copied_from": source,
            },
        )
    form = DutyForm(request.POST or None)
    formset = DutyPositionFormSet(request.POST or None, instance=Duty(), prefix="positions")
    if request.method == "POST" and all([form.is_valid(), formset.is_valid()]):
        duty = form.save(commit=False)
        duty.created_by = request.user
        duty.helpers_needed = formset.total()
        duty.save()
        form.save_m2m()
        _save_positions(duty, formset)
        history.record(duty, history.Action.CREATED, request.user, history.scope_for_duty(duty.pk))
        if "release" in request.POST:
            _release(request, duty)
        else:
            messages.success(
                request,
                _("Duty saved as a draft. The units will only see it once you release it."),
            )
        return redirect(duty)
    return render(request, "duties/duty_form.html", {"form": form, "formset": formset})


def duty_update(request, pk):
    _dispatcher_only(request)
    duty = get_object_or_404(Duty, pk=pk)
    if duty.status == Duty.Status.CANCELLED:
        messages.error(request, _("Cancelled duties can no longer be edited."))
        return redirect(duty)
    # Before validating: the form already applies its values to the instance.
    offered_before = duty.offered_unit_ids()
    positions_before = [str(position) for position in duty.positions.all()]
    form = DutyForm(request.POST or None, instance=duty)
    formset = DutyPositionFormSet(request.POST or None, instance=duty, prefix="positions")
    if request.method == "POST" and all([form.is_valid(), formset.is_valid()]):
        duty.helpers_needed = formset.total()
        form.save()
        positions_after = _save_positions(duty, formset)
        changes = history.form_changes(form)
        changed_fields = [
            name for name in notifications.RELEVANT_CHANGES if name in form.changed_data
        ]
        if positions_after != positions_before:
            # Only recorded: the helpers needed are the dispatchers' business, e.g. one
            # helper fewer because they found one themselves. No email for that.
            changes.append(history.change("positions", positions_before, positions_after))
        if changes:
            history.record(
                duty, history.Action.CHANGED, request.user, history.scope_for_duty(duty.pk), changes
            )
        messages.success(request, _("Saved."))
        confirmed = duty.reports.filter(status=Report.Status.CONFIRMED).count()
        if confirmed > duty.helpers_needed:
            messages.warning(
                request,
                _(
                    "More helpers are confirmed (%(confirmed)d) than needed (%(needed)d). "
                    "Take back the reports of those you no longer need; their units are "
                    "notified."
                )
                % {"confirmed": confirmed, "needed": duty.helpers_needed},
            )
        if duty.status == Duty.Status.RELEASED:
            if changed_fields:
                notifications.duty_changed(request, duty, changed_fields)
            added_units = duty.offered_unit_ids() - offered_before
            if added_units:
                notifications.duty_released(request, duty, added_units)
        return redirect(duty)
    return render(
        request, "duties/duty_form.html", {"form": form, "formset": formset, "duty": duty}
    )


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
        history.record(duty, history.Action.RELEASED, request.user, history.scope_for_duty(duty.pk))
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
    if duty.status == Duty.Status.RELEASED:
        if duty.has_billed_reports:
            messages.error(request, _("This duty has been billed and can no longer be cancelled."))
            return redirect(duty)
        if duty.has_started and not request.POST.get("confirm_started"):
            messages.error(
                request,
                _("Please confirm that you want to cancel a duty that has already started."),
            )
            return redirect(duty)
    if _change_status(duty, Duty.Status.RELEASED, Duty.Status.CANCELLED, "cancelled_at"):
        history.record(
            duty, history.Action.CANCELLED, request.user, history.scope_for_duty(duty.pk)
        )
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
    history.record(duty, history.Action.DELETED, request.user, history.scope_for_duty(duty.pk))
    duty.delete()
    messages.success(request, _("Duty “%(title)s” deleted.") % {"title": duty.title})
    return redirect("duty_list")


def qualification_list(request):
    """The catalogue of qualifications that positions can ask for."""
    _dispatcher_only(request)
    formset = QualificationFormSet(request.POST or None, prefix="qualifications")
    if request.method == "POST" and formset.is_valid():
        formset.save()
        messages.success(request, _("Saved."))
        return redirect("qualification_list")
    return render(request, "duties/qualification_list.html", {"formset": formset})


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
        reports = _save_new_reports(
            request, formset, duty, unit, history.Action.REPORTED, reported_by=request.user
        )
        if reports is not None:
            messages.success(
                request,
                ngettext("%(count)d helper reported.", "%(count)d helpers reported.", len(reports))
                % {"count": len(reports)},
            )
            notifications.helpers_reported(request, duty, unit, reports)
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
            history.Action.ADDED,
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
            return redirect(duty)
    return render(
        request,
        "duties/report_add.html",
        {"duty": duty, "unit_form": unit_form, "formset": formset},
    )


def _save_new_reports(request, formset, duty, unit, action, **values):
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
                history.record(report, action, request.user, history.scope_for_duty(duty.pk))
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
    # Only if still not confirmed: a second click must not notify twice, and a report
    # confirmed in the meantime can only be withdrawn by the dispatchers.
    if Report.objects.filter(pk=report.pk, status=Report.Status.REPORTED).update(
        status=Report.Status.WITHDRAWN, withdrawn_at=now
    ):
        report.status = Report.Status.WITHDRAWN
        report.withdrawn_at = now
        history.record(
            report,
            history.Action.WITHDRAWN,
            request.user,
            history.scope_for_duty(report.duty_id),
        )
        messages.success(request, _("Report for %(name)s withdrawn.") % {"name": report.name})
        notifications.report_withdrawn(request, report)
    else:
        report.refresh_from_db(fields=["status"])
        if report.status == Report.Status.CONFIRMED:
            messages.error(
                request,
                _("This report has been confirmed meanwhile; ask the dispatchers to withdraw it."),
            )
    return redirect(report.duty)


def report_request_withdrawal(request, pk):
    """Units ask the dispatchers to withdraw a confirmed report (or one after the deadline)."""
    report = _visible_report(request, pk)
    if not permissions.can_request_withdrawal(request.user, report):
        raise PermissionDenied
    # The reason is optional, so the form may arrive empty.
    form = WithdrawalRequestForm(
        request.POST if request.method == "POST" else None, instance=report
    )
    if request.method == "POST" and form.is_valid():
        now = timezone.now()
        note = form.cleaned_data["withdrawal_note"]
        # Only once, and only while the report is still active.
        if Report.objects.filter(
            pk=report.pk, status__in=Report.ACTIVE, withdrawal_requested_at=None
        ).update(
            withdrawal_requested_at=now,
            withdrawal_requested_by=request.user,
            withdrawal_note=note,
        ):
            report.withdrawal_requested_at = now
            report.withdrawal_requested_by = request.user
            report.withdrawal_note = note
            history.record(
                report,
                history.Action.WITHDRAWAL_REQUESTED,
                request.user,
                history.scope_for_duty(report.duty_id),
                [history.change("withdrawal_note", "", note)] if note else [],
            )
            messages.success(
                request,
                _("The dispatchers have been asked to withdraw the report for %(name)s.")
                % {"name": report.name},
            )
            notifications.withdrawal_requested(request, report)
        return redirect(report.duty)
    return render(request, "duties/withdrawal_request_form.html", {"report": report, "form": form})


@require_POST
def report_take_back(request, pk):
    """Dispatchers withdraw a report: a confirmed one, or one the unit asked to withdraw."""
    _dispatcher_only(request)
    report = _visible_report(request, pk)
    if not permissions.can_take_back_report(request.user, report):
        messages.error(request, _("This report can no longer be changed."))
        return redirect(report.duty)
    requested = report.withdrawal_requested
    now = timezone.now()
    if Report.objects.filter(pk=report.pk, status__in=Report.ACTIVE).update(
        status=Report.Status.WITHDRAWN, withdrawn_at=now
    ):
        report.status = Report.Status.WITHDRAWN
        report.withdrawn_at = now
        history.record(
            report,
            history.Action.WITHDRAWN,
            request.user,
            history.scope_for_duty(report.duty_id),
        )
        messages.success(request, _("Report for %(name)s withdrawn.") % {"name": report.name})
        notifications.report_taken_back(request, report, requested)
    return redirect(report.duty)


@require_POST
def report_reject_withdrawal(request, pk):
    """Dispatchers keep a report that the unit asked to withdraw."""
    _dispatcher_only(request)
    report = _visible_report(request, pk)
    if Report.objects.filter(
        pk=report.pk, status__in=Report.ACTIVE, withdrawal_requested_at__isnull=False
    ).update(withdrawal_requested_at=None, withdrawal_requested_by=None, withdrawal_note=""):
        history.record(
            report,
            history.Action.WITHDRAWAL_REJECTED,
            request.user,
            history.scope_for_duty(report.duty_id),
        )
        messages.success(
            request,
            _("%(name)s stays on the duty; the unit has been told.") % {"name": report.name},
        )
        notifications.withdrawal_rejected(request, report)
    return redirect(report.duty)


# A decision changes these statuses; confirmed reports are taken back instead of declined.
DECIDABLE = {
    Report.Status.CONFIRMED: [Report.Status.REPORTED, Report.Status.DECLINED],
    Report.Status.DECLINED: [Report.Status.REPORTED],
}


def _decide(request, reports, status):
    """Confirms or declines the reports; returns those that actually changed.

    Reports withdrawn or confirmed in the meantime stay so.
    """
    now = timezone.now()
    values = {"status": status, "decided_at": now, "decided_by": request.user}
    if status == Report.Status.DECLINED:
        # A request to withdraw a declined report is settled.
        values.update(withdrawal_requested_at=None, withdrawal_requested_by=None)
    changed = []
    for report in reports:
        if Report.objects.filter(pk=report.pk, status__in=DECIDABLE[status]).update(**values):
            report.status = status
            report.decided_at = now
            report.decided_by = request.user
            changed.append(report)
            history.record(
                report,
                history.Action.CONFIRMED
                if status == Report.Status.CONFIRMED
                else history.Action.DECLINED,
                request.user,
                history.scope_for_duty(report.duty_id),
            )
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
    pending = duty.reports.filter(status=Report.Status.REPORTED).select_related(
        "unit", "position__qualification"
    )
    reports = _decide(request, list(pending), Report.Status.CONFIRMED)
    if reports:
        notifications.reports_decided(request, duty, reports)
    messages.success(
        request,
        ngettext("%(count)d report confirmed.", "%(count)d reports confirmed.", len(reports))
        % {"count": len(reports)},
    )
    return redirect(duty)
