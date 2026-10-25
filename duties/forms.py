from django import forms
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from accounts.forms import clean_unit_selection, unit_selection_fields
from accounts.models import Unit

from .models import Duty, DutyPosition, Qualification, Report


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
    all_units, units = unit_selection_fields(
        _("Offered to"), _("Only relevant if the duty is offered to selected units only.")
    )

    class Meta:
        model = Duty
        fields = [
            "title",
            "location",
            "starts_at",
            "ends_at",
            "description",
            "pay_type",
            "pay_rate",
            "all_units",
            "units",
        ]
        field_classes = {"starts_at": MinuteDateTimeField, "ends_at": MinuteDateTimeField}
        widgets = {
            "pay_type": forms.RadioSelect(attrs={"class": "form-check-input"}),
            "pay_rate": forms.NumberInput(attrs={"min": "0.01", "step": "0.01"}),
            "description": forms.Textarea(attrs={"rows": 4}),
        }
        help_texts = {
            "pay_rate": _(
                "Per helper hour, or per helper for the whole duty. Positions can have "
                "their own rate."
            ),
        }

    def clean(self):
        cleaned_data = super().clean()
        starts_at = cleaned_data.get("starts_at")
        ends_at = cleaned_data.get("ends_at")
        if starts_at and ends_at and ends_at <= starts_at:
            self.add_error("ends_at", _("The end must be after the start."))
        if cleaned_data.get("pay_type") == Duty.PayType.NONE:
            cleaned_data["pay_rate"] = None
        elif "pay_rate" in cleaned_data and cleaned_data["pay_rate"] is None:
            self.add_error("pay_rate", _("Please enter the rate."))
        if clean_unit_selection(self, cleaned_data) and not cleaned_data.get("all_units"):
            self._check_units_with_reports(cleaned_data)
        return cleaned_data

    def _check_units_with_reports(self, cleaned_data):
        """A unit with active reports must keep seeing the duty."""
        if self.instance.pk is None:
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


class DutyPositionForm(forms.ModelForm):
    class Meta:
        model = DutyPosition
        fields = ["qualification", "count", "pay_rate"]
        widgets = {
            "count": forms.NumberInput(attrs={"min": 1}),
            "pay_rate": forms.NumberInput(attrs={"min": "0.01", "step": "0.01"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields["qualification"]
        field.empty_label = _("Any qualification")
        # Inactive qualifications only for positions that already have them.
        used = Q(pk=self.instance.qualification_id) if self.instance.qualification_id else Q()
        field.queryset = Qualification.objects.filter(Q(is_active=True) | used)


class BaseDutyPositionFormSet(forms.BaseInlineFormSet):
    """The positions of a duty: at least one, each qualification only once."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.extra = 2 if self.instance.pk is None else 1

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        # Errors on rows marked for deletion would be ignored, so this is a formset error.
        kept_back = [
            form.instance.label
            for form in self.deleted_forms
            if form.instance.pk and form.instance.reports.filter(status__in=Report.ACTIVE).exists()
        ]
        if kept_back:
            raise forms.ValidationError(
                _("Helpers have been reported for these positions; keep them: %(positions)s")
                % {"positions": ", ".join(kept_back)}
            )
        seen = set()
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            qualification = form.cleaned_data.get("qualification")
            if qualification in seen:
                form.add_error("qualification", _("This qualification is listed twice."))
            seen.add(qualification)

    def total(self):
        """The number of helpers of all positions."""
        return sum(
            form.cleaned_data["count"]
            for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get("DELETE")
        )

    def summary(self):
        """The saved positions as text, e.g. for the change history."""
        return [str(position) for position in self.instance.positions.all()]


DutyPositionFormSet = forms.inlineformset_factory(
    Duty,
    DutyPosition,
    form=DutyPositionForm,
    formset=BaseDutyPositionFormSet,
    min_num=1,
    validate_min=True,
    can_delete=True,
)


class QualificationForm(forms.ModelForm):
    class Meta:
        model = Qualification
        fields = ["name", "abbreviation", "sort_order", "is_active"]
        widgets = {"sort_order": forms.NumberInput(attrs={"min": 0, "step": 10})}


QualificationFormSet = forms.modelformset_factory(Qualification, form=QualificationForm, extra=3)


class HelperUnitForm(forms.Form):
    """The unit whose helpers a dispatcher enters."""

    unit = forms.ModelChoiceField(label=_("Unit"), queryset=Unit.objects.none())

    def __init__(self, *args, duty, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["unit"].queryset = Unit.objects.all() if duty.all_units else duty.units.all()


class ReportForm(forms.ModelForm):
    class Meta:
        model = Report
        fields = ["position", "name", "qualification", "note"]
        labels = {
            "name": _("Name of the helper"),
            "qualification": _("Qualification of the helper"),
        }
        widgets = {
            "qualification": forms.TextInput(attrs={"placeholder": _("e.g. first aider")}),
        }

    def __init__(self, *args, duty, **kwargs):
        super().__init__(*args, **kwargs)
        positions = list(duty.positions.select_related("qualification"))
        if len(positions) <= 1:
            # Nothing to choose: the only position (if any) is taken.
            del self.fields["position"]
            self.instance.position = positions[0] if positions else None
            return
        field = self.fields["position"]
        field.queryset = duty.positions.all()
        field.label_from_instance = lambda position: position.label
        field.required = True
        field.empty_label = None
        # The first position is preselected, so that empty rows count as unchanged.
        field.initial = positions[0].pk


class BaseReportFormSet(forms.BaseFormSet):
    """Several helpers at once; empty rows are ignored."""

    def __init__(self, *args, duty, unit, **kwargs):
        kwargs.setdefault("form_kwargs", {})["duty"] = duty
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
