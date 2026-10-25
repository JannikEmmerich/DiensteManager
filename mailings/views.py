import logging
from smtplib import SMTPException

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from accounts.models import Unit
from accounts.permissions import is_dispatcher
from duties.models import Duty, Report

from . import sending
from .forms import MailingForm
from .models import Mailing

logger = logging.getLogger(__name__)

MAILINGS_PER_PAGE = 50


def _dispatcher_only(request):
    if not is_dispatcher(request.user):
        raise PermissionDenied


def _initial(request):
    """Prefills the form when it is opened from a duty or a unit."""
    initial = {}
    duty_id = request.GET.get("duty", "")
    unit_id = request.GET.get("unit", "")
    if duty_id.isdecimal():
        duty = Duty.objects.exclude(status=Duty.Status.DRAFT).filter(pk=duty_id).first()
        if duty is not None:
            initial.update(duty=duty, subject=duty.title)
            # Preferably the units that have reported helpers, otherwise those it is offered to.
            units = Unit.objects.filter(
                reports__duty=duty, reports__status__in=Report.ACTIVE
            ).distinct()
            if not units and not duty.all_units:
                units = duty.units.all()
            if units:
                initial.update(all_units="False", units=list(units))
    elif unit_id.isdecimal():
        initial.update(all_units="False", units=Unit.objects.filter(pk=unit_id))
    return initial


def mailing_list(request):
    _dispatcher_only(request)
    # Explicit ordering: the default ordering does not apply to the grouped count query.
    mailings = (
        Mailing.objects.select_related("sent_by", "duty")
        .annotate(recipient_count=Count("recipients"))
        .order_by("-sent_at", "-pk")
    )
    page = Paginator(mailings, MAILINGS_PER_PAGE).get_page(request.GET.get("page"))
    return render(request, "mailings/mailing_list.html", {"page": page})


def mailing_detail(request, pk):
    _dispatcher_only(request)
    mailing = get_object_or_404(Mailing.objects.select_related("sent_by", "duty"), pk=pk)
    return render(
        request,
        "mailings/mailing_detail.html",
        {
            "mailing": mailing,
            "recipients": mailing.recipients.select_related("unit").order_by(
                "unit__name", "last_name", "first_name"
            ),
        },
    )


def mailing_create(request):
    _dispatcher_only(request)
    form = MailingForm(request.POST or None, initial=_initial(request))
    if request.method == "POST" and form.is_valid():
        recipients = list(form.recipients().order_by("pk"))
        mailing = form.save(commit=False)
        mailing.sent_by = request.user
        try:
            # Recorded only if the emails went out (or are queued, with EMAIL_QUEUE).
            with transaction.atomic():
                mailing.save()
                form.save_m2m()
                mailing.recipients.set(recipients)
                sending.send(
                    request, mailing, recipients, copy_to_sender=form.cleaned_data["send_copy"]
                )
        except (SMTPException, OSError):
            logger.exception("Could not send the email “%s”", mailing.subject)
            messages.error(request, _("The email could not be sent. Please try again later."))
        else:
            if settings.EMAIL_QUEUE:
                text = ngettext(
                    "The email to %(count)d person is being sent.",
                    "The email to %(count)d people is being sent.",
                    len(recipients),
                )
            else:
                text = ngettext(
                    "Email sent to %(count)d person.",
                    "Email sent to %(count)d people.",
                    len(recipients),
                )
            messages.success(request, text % {"count": len(recipients)})
            return redirect(mailing)
    duty_id = str(form["duty"].value() or "")
    duty = form.fields["duty"].queryset.filter(pk=duty_id).first() if duty_id.isdecimal() else None
    return render(request, "mailings/mailing_form.html", {"form": form, "duty": duty})


def mailing_recipient_count(request):
    """htmx: how many people the email in the form would reach."""
    _dispatcher_only(request)
    form = MailingForm(request.GET)
    form.is_valid()
    return render(request, "mailings/_recipient_count.html", {"count": form.recipients().count()})
