from decimal import Decimal

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from duties.models import Duty

from . import services
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
    """Which billable helpers go into the billing, with all values of their lines.

    Every value is prefilled from the helper and the duty and can be changed for this
    billing: date, duty, helper, qualification, rate and, for hourly pay, the hours.
    """

    note = forms.CharField(
        label=_("Note"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("Printed on the statement."),
    )

    # The editable values of each line besides the hours; empty means "as prefilled" only
    # for forms that do not send the field at all.
    VALUES = ["date", "title", "helper", "qualification", "rate"]

    def __init__(self, *args, reports, **kwargs):
        super().__init__(*args, **kwargs)
        self.reports = reports
        small = {"class": "form-control-sm"}
        for report in reports:
            duty = report.duty
            position = report.filled_position
            self.fields[f"include_{report.pk}"] = forms.BooleanField(
                label=report.name, required=False, initial=True
            )
            self.fields[f"date_{report.pk}"] = forms.DateField(
                label=_("Date"),
                required=False,
                initial=timezone.localdate(duty.starts_at),
                widget=forms.DateInput(attrs={**small, "type": "date"}, format="%Y-%m-%d"),
            )
            self.fields[f"title_{report.pk}"] = forms.CharField(
                label=_("Duty"),
                required=False,
                max_length=200,
                initial=duty.title,
                widget=forms.TextInput(attrs=small),
            )
            self.fields[f"helper_{report.pk}"] = forms.CharField(
                label=_("Helper"),
                required=False,
                max_length=150,
                initial=report.name,
                widget=forms.TextInput(attrs=small),
            )
            self.fields[f"qualification_{report.pk}"] = forms.CharField(
                label=_("Qualification"),
                required=False,
                max_length=100,
                initial=position.short_label if position else "",
                widget=forms.TextInput(attrs=small),
            )
            self.fields[f"rate_{report.pk}"] = forms.DecimalField(
                label=_("Rate in euros"),
                required=False,
                initial=report.pay_rate,
                min_value=Decimal("0.01"),
                max_digits=8,
                decimal_places=2,
                widget=forms.NumberInput(attrs={**small, "step": "0.01"}),
            )
            if duty.pay_type == Duty.PayType.HOURLY:
                self.fields[f"hours_{report.pk}"] = forms.DecimalField(
                    label=_("Hours"),
                    required=False,
                    initial=duty.duration_hours,
                    min_value=Decimal("0.25"),
                    max_value=Decimal("99.75"),
                    decimal_places=2,
                    widget=forms.NumberInput(attrs={**small, "step": "0.25"}),
                )

    def lines(self):
        """For the template: each report with its fields (hours: None for flat pay)."""
        return [
            {
                "report": report,
                "include": self[f"include_{report.pk}"],
                **{name: self[f"{name}_{report.pk}"] for name in self.VALUES},
                "texts": [self[f"{name}_{report.pk}"] for name in self.VALUES[:4]],
                "hours": self[f"hours_{report.pk}"]
                if f"hours_{report.pk}" in self.fields
                else None,
            }
            for report in self.reports
        ]

    def clean(self):
        cleaned_data = super().clean()
        selected = []
        for report in self.reports:
            if not cleaned_data.get(f"include_{report.pk}"):
                continue
            line = services.ReportLine(report)
            for name, attribute in [
                ("date", "duty_date"),
                ("title", "duty_title"),
                ("helper", "helper_name"),
                ("qualification", "qualification"),
                ("rate", "rate"),
            ]:
                key = f"{name}_{report.pk}"
                if self.add_prefix(key) not in self.data or key in self.errors:
                    continue  # not sent (or invalid): as prefilled
                value = cleaned_data.get(key)
                if value in (None, "") and name != "qualification":
                    self.add_error(key, _("This field is required."))
                    continue
                setattr(line, attribute, value)
            if f"hours_{report.pk}" in self.fields:
                line.hours = cleaned_data.get(f"hours_{report.pk}")
                if line.hours is None:
                    if f"hours_{report.pk}" not in self.errors:
                        self.add_error(f"hours_{report.pk}", _("Please enter the hours."))
                    continue
            selected.append(line)
        cleaned_data["selected"] = selected
        return cleaned_data


class ExtraLineForm(forms.Form):
    """Another item of a billing, e.g. a vehicle flat rate."""

    date = forms.DateField(
        label=_("Date"), widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
    )
    description = forms.CharField(label=_("Description"), max_length=200)
    quantity = forms.DecimalField(
        label=_("Quantity"),
        required=False,
        min_value=Decimal("0.01"),
        max_digits=7,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"step": "any"}),
        help_text=_("Optional, e.g. kilometres."),
    )
    rate = forms.DecimalField(
        label=_("Price in euros"),
        max_digits=8,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"step": "0.01"}),
        help_text=_("Per unit with a quantity; negative for a deduction."),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].initial = timezone.localdate()

    def has_changed(self):
        # The prefilled date alone does not make a line.
        return any(
            self[name].value() not in (None, "") for name in ("description", "quantity", "rate")
        )

    def line(self):
        return services.ExtraLine(
            description=self.cleaned_data["description"],
            rate=self.cleaned_data["rate"],
            date=self.cleaned_data["date"],
            quantity=self.cleaned_data["quantity"],
        )


class BaseExtraLineFormSet(forms.BaseFormSet):
    def extra_lines(self):
        return [form.line() for form in self.forms if form.has_changed()]


ExtraLineFormSet = forms.formset_factory(ExtraLineForm, formset=BaseExtraLineFormSet, extra=0)
