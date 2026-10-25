from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from accounts.models import Unit
from accounts.permissions import is_dispatcher
from lifecycle import history

from . import notifications, pdf, permissions, services
from .forms import BankAccountForm, BillingForm, ExtraLineFormSet
from .models import BankAccount, Billing, BillingItem
from .services import PayLine
from .validators import mask_iban

# Changes of these fields are reported to the unit leads (protection against fraud).
BANK_DETAILS = ("account_holder", "iban", "bic")
# Creating a statement warns about bank details changed within this time.
BANK_CHANGE_WARNING = timedelta(days=30)


def _dispatcher_only(request):
    if not is_dispatcher(request.user):
        raise PermissionDenied


def _unit_for_pay(request, pk):
    unit = get_object_or_404(Unit, pk=pk)
    if not permissions.can_view_unit_pay(request.user, unit):
        raise PermissionDenied
    return unit


# --- Duty pay of a unit and its bank details -----------------------------------------------


def unit_pay(request, pk):
    unit = _unit_for_pay(request, pk)
    years = services.unit_pay_years(unit)
    year = request.GET.get("year", "")
    year = int(year) if year.isdecimal() and int(year) in years else timezone.localdate().year
    lines = services.unit_pay_lines(unit, year)
    totals = dict.fromkeys(PayLine.STATES, Decimal(0))
    for line in lines:
        totals[line.state] += line.amount
    return render(
        request,
        "billing/unit_pay.html",
        {
            "unit": unit,
            "year": year,
            "years": years,
            "lines": lines,
            "totals": totals,
            "billings": unit.billings.all(),
            "bank_account": BankAccount.objects.filter(unit=unit).first(),
            # Also without billable helpers, e.g. for a vehicle flat rate only.
            "can_bill": is_dispatcher(request.user),
            "bank_history": history.entries(history.scope_for_unit(unit.pk)),
        },
    )


def bank_account_edit(request, pk):
    unit = _unit_for_pay(request, pk)
    bank_account = BankAccount.objects.filter(unit=unit).first()
    form = BankAccountForm(request.POST or None, instance=bank_account)
    if request.method == "POST" and form.is_valid():
        created = bank_account is None
        bank_account = form.save(commit=False)
        bank_account.unit = unit
        bank_account.updated_by = request.user
        bank_account.save()
        messages.success(request, _("Bank details saved."))
        if form.has_changed():
            changes = history.form_changes(form)
            for item in changes:
                if item["field"] == "iban":
                    item["old"], item["new"] = mask_iban(item["old"]), mask_iban(item["new"])
            history.record(
                bank_account,
                history.Action.CREATED if created else history.Action.CHANGED,
                request.user,
                history.scope_for_unit(unit.pk),
                changes,
            )
            if any(name in form.changed_data for name in BANK_DETAILS):
                notifications.bank_account_changed(request, bank_account)
        return redirect("unit_pay", pk=unit.pk)
    return render(request, "billing/bank_account_form.html", {"unit": unit, "form": form})


# --- Billings ---------------------------------------------------------------------------------


def billing_list(request):
    _dispatcher_only(request)
    return render(
        request,
        "billing/billing_list.html",
        {
            "billable": services.units_with_billable_reports(),
            "billings": Billing.objects.select_related("unit")[:100],
        },
    )


def billing_create(request, unit_pk):
    _dispatcher_only(request)
    unit = get_object_or_404(Unit, pk=unit_pk)
    reports = list(services.billable_reports(unit))
    bank_account = BankAccount.objects.filter(unit=unit).select_related("updated_by").first()
    form = BillingForm(request.POST or None, reports=reports)
    # Without the rows of other items in the request (none added), there are none.
    sent_extra = "extra-TOTAL_FORMS" in request.POST
    extra = ExtraLineFormSet(request.POST if sent_extra else None, prefix="extra")
    valid = request.method == "POST" and form.is_valid() and (not sent_extra or extra.is_valid())
    extra_lines = extra.extra_lines() if valid and sent_extra else []
    if valid and not form.cleaned_data["selected"] and not extra_lines:
        form.add_error(None, _("Please select at least one helper or add a line."))
        valid = False
    if valid:
        try:
            billing = services.create_billing(
                unit,
                form.cleaned_data["selected"],
                form.cleaned_data["note"],
                request.user,
                extra_lines=extra_lines,
            )
        except services.AlreadyBilled:
            messages.error(request, _("Some of these helpers have been billed in the meantime."))
            return redirect("billing_create", unit_pk=unit.pk)
        history.record(
            billing, history.Action.CREATED, request.user, history.scope_for_billing(billing.pk)
        )
        messages.success(request, _("Statement %(number)s created.") % {"number": billing.number})
        notifications.billing_created(request, billing)
        return redirect(billing)
    return render(
        request,
        "billing/billing_create.html",
        {
            "unit": unit,
            "form": form,
            "extra": extra,
            "bank_account": bank_account,
            "bank_recently_changed": bank_account is not None
            and bank_account.updated_at > timezone.now() - BANK_CHANGE_WARNING,
        },
    )


def _visible_billing(request, pk):
    return get_object_or_404(
        permissions.visible_billings(request.user).select_related("unit", "created_by"), pk=pk
    )


def billing_detail(request, pk):
    billing = _visible_billing(request, pk)
    return render(
        request,
        "billing/billing_detail.html",
        {
            "billing": billing,
            "items": billing.items.all(),
            "has_other_items": billing.items.filter(pay_type=BillingItem.PayType.OTHER).exists(),
            "history": history.entries(history.scope_for_billing(billing.pk)),
        },
    )


def billing_pdf(request, pk):
    billing = _visible_billing(request, pk)
    response = HttpResponse(pdf.render(billing), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{pdf.filename(billing)}"'
    return response


@require_POST
def billing_mark_paid(request, pk):
    _dispatcher_only(request)
    billing = get_object_or_404(Billing, pk=pk)
    if services.change_status(billing, Billing.Status.PAID, "paid_at"):
        history.record(
            billing, history.Action.PAID, request.user, history.scope_for_billing(billing.pk)
        )
        messages.success(
            request, _("Statement %(number)s marked as paid.") % {"number": billing.number}
        )
    else:
        messages.error(request, _("Only open statements can be marked as paid."))
    return redirect(billing)


@require_POST
def billing_cancel(request, pk):
    _dispatcher_only(request)
    billing = get_object_or_404(Billing, pk=pk)
    if services.change_status(billing, Billing.Status.CANCELLED, "cancelled_at"):
        history.record(
            billing, history.Action.VOIDED, request.user, history.scope_for_billing(billing.pk)
        )
        messages.success(
            request,
            _("Statement %(number)s cancelled. Its helpers can be billed again.")
            % {"number": billing.number},
        )
        notifications.billing_cancelled(request, billing)
    else:
        messages.error(request, _("Only open statements can be cancelled."))
    return redirect(billing)
