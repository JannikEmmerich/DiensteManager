from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _

from duties.models import Duty

from .models import BankAccount
from .validators import normalize_iban


class BankAccountForm(forms.ModelForm):
    class Meta:
        model = BankAccount
        fields = ["account_holder", "iban", "bic", "bank_name"]
        help_texts = {"bic": _("Optional for accounts within the SEPA area.")}

    def clean_iban(self):
        return normalize_iban(self.cleaned_data["iban"])

    def clean_bic(self):
        return normalize_iban(self.cleaned_data["bic"])


class BillingForm(forms.Form):
    """Which billable helpers go into the billing, and their hours for hourly pay."""

    note = forms.CharField(
        label=_("Note"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("Printed on the statement."),
    )

    def __init__(self, *args, reports, **kwargs):
        super().__init__(*args, **kwargs)
        self.reports = reports
        for report in reports:
            self.fields[f"include_{report.pk}"] = forms.BooleanField(
                label=report.name, required=False, initial=True
            )
            if report.duty.pay_type == Duty.PayType.HOURLY:
                self.fields[f"hours_{report.pk}"] = forms.DecimalField(
                    label=_("Hours"),
                    required=False,
                    initial=report.duty.duration_hours,
                    min_value=Decimal("0.25"),
                    max_value=Decimal("99.75"),
                    decimal_places=2,
                    widget=forms.NumberInput(attrs={"step": "0.25"}),
                )

    def lines(self):
        """For the template: each report with its checkbox and hours field (or None)."""
        return [
            (
                report,
                self[f"include_{report.pk}"],
                self[f"hours_{report.pk}"] if f"hours_{report.pk}" in self.fields else None,
            )
            for report in self.reports
        ]

    def clean(self):
        cleaned_data = super().clean()
        selected = []
        for report in self.reports:
            if not cleaned_data.get(f"include_{report.pk}"):
                continue
            hours = None
            if f"hours_{report.pk}" in self.fields:
                hours = cleaned_data.get(f"hours_{report.pk}")
                if hours is None:
                    if f"hours_{report.pk}" not in self.errors:
                        self.add_error(f"hours_{report.pk}", _("Please enter the hours."))
                    continue
            selected.append((report, hours))
        if not selected and not self.errors:
            raise forms.ValidationError(_("Please select at least one helper."))
        cleaned_data["selected"] = selected
        return cleaned_data
