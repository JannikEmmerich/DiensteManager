from django import forms
from django.utils.translation import gettext_lazy as _

from accounts.models import Unit

from .models import Duty, Report


class DateTimeLocalInput(forms.DateTimeInput):
    """The browser's date and time picker."""

    input_type = "datetime-local"

    def __init__(self, attrs=None):
        super().__init__(attrs, format="%Y-%m-%dT%H:%M")


class MinuteDateTimeField(forms.DateTimeField):
    """Date and time to the minute, like the browser's picker."""

    widget = DateTimeLocalInput

    def has_changed(self, initial, data):
        # Stored values may have seconds (e.g. imported duties); the picker never sends any.
        if initial is not None:
            initial = initial.replace(second=0, microsecond=0)
        return super().has_changed(initial, data)


class DutyForm(forms.ModelForm):
    all_units = forms.TypedChoiceField(
        label=_("Offered to"),
        choices=[("True", _("All units")), ("False", _("Only the selected units"))],
        coerce=lambda value: value == "True",
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
        initial="True",
    )
    units = forms.ModelMultipleChoiceField(
        label=_("Selected units"),
        queryset=Unit.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "form-check-input"}),
        help_text=_("Only relevant if the duty is offered to selected units only."),
    )

    class Meta:
        model = Duty
        fields = [
            "title",
            "location",
            "starts_at",
            "ends_at",
            "helpers_needed",
            "description",
            "all_units",
            "units",
        ]
        field_classes = {"starts_at": MinuteDateTimeField, "ends_at": MinuteDateTimeField}
        widgets = {
            "helpers_needed": forms.NumberInput(attrs={"min": 1}),
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def clean(self):
        cleaned_data = super().clean()
        starts_at = cleaned_data.get("starts_at")
        ends_at = cleaned_data.get("ends_at")
        if starts_at and ends_at and ends_at <= starts_at:
            self.add_error("ends_at", _("The end must be after the start."))
        if cleaned_data.get("all_units"):
            cleaned_data["units"] = Unit.objects.none()
        elif "units" in cleaned_data and not cleaned_data["units"]:
            self.add_error("units", _("Please select at least one unit."))
        else:
            self._check_units_with_reports(cleaned_data)
        return cleaned_data

    def _check_units_with_reports(self, cleaned_data):
        """A unit with active reports must keep seeing the duty."""
        if self.instance.pk is None or "units" not in cleaned_data:
            return
        selected = {unit.pk for unit in cleaned_data["units"]}
        dropped = (
            Unit.objects.filter(reports__duty=self.instance, reports__status__in=Report.ACTIVE)
            .exclude(pk__in=selected)
            .distinct()
        )
        if dropped:
            self.add_error(
                "units",
                _("These units have reported helpers and must stay selected: %(units)s")
                % {"units": ", ".join(unit.name for unit in dropped)},
            )


class ReportForm(forms.ModelForm):
    class Meta:
        model = Report
        fields = ["name", "qualification", "note"]
        labels = {"name": _("Name of the helper")}
        widgets = {
            "qualification": forms.TextInput(attrs={"placeholder": _("e.g. first aider")}),
        }


class BaseReportFormSet(forms.BaseFormSet):
    """Several helpers at once; empty rows are ignored."""

    def __init__(self, *args, duty, unit, **kwargs):
        super().__init__(*args, **kwargs)
        self.duty = duty
        self.unit = unit

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        already_reported = {
            name.casefold()
            for name in Report.objects.filter(duty=self.duty, unit=self.unit)
            .exclude(status=Report.Status.WITHDRAWN)
            .values_list("name", flat=True)
        }
        seen = set()
        for form in self.forms:
            name = form.cleaned_data.get("name")
            if not name:
                continue
            key = name.casefold()
            if key in already_reported:
                form.add_error("name", _("This person has already been reported for this duty."))
            elif key in seen:
                form.add_error("name", _("This person is listed twice."))
            seen.add(key)

    def new_reports(self):
        """Unsaved reports of the filled-in rows."""
        return [form.save(commit=False) for form in self.forms if form.has_changed()]


ReportFormSet = forms.formset_factory(
    ReportForm, formset=BaseReportFormSet, extra=2, min_num=1, validate_min=True
)
