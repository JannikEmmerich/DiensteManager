"""Emails to the unit leads about billings (switch "Billings" under "My account")."""

from django.urls import reverse

from accounts.models import Role, User
from duties.notifications import send

from . import pdf
from .validators import mask_iban


def _unit_leads(unit):
    return User.objects.filter(role=Role.UNIT_LEAD, unit=unit, is_active=True, notify_billing=True)


def _send(request, billing, template, attach_pdf=False):
    def attach():
        return (pdf.filename(billing), pdf.render(billing), "application/pdf")

    send(
        request,
        _unit_leads(billing.unit),
        f"billing/email/{template}",
        {
            "billing": billing,
            "billing_url": request.build_absolute_uri(reverse("billing_detail", args=[billing.pk])),
        },
        attach=attach if attach_pdf else None,
    )


def bank_account_changed(request, bank_account):
    """Tells the (other) unit leads, so that a changed IBAN does not go unnoticed."""
    send(
        request,
        _unit_leads(bank_account.unit),
        "billing/email/bank_account_changed",
        {
            "bank_account": bank_account,
            "iban": mask_iban(bank_account.iban),
            "changed_by": request.user,
            "unit_pay_url": request.build_absolute_uri(
                reverse("unit_pay", args=[bank_account.unit_id])
            ),
        },
    )


def billing_created(request, billing):
    _send(request, billing, "billing_created", attach_pdf=True)


def billing_paid(request, billing):
    _send(request, billing, "billing_paid")


def billing_cancelled(request, billing):
    _send(request, billing, "billing_cancelled")
