from django import forms
from django.core.validators import MaxValueValidator
from django.db.models import Max, Q
from django.utils.translation import gettext_lazy as _

from accounts.forms import clean_unit_selection, unit_selection_fields
from accounts.models import CalendarFeed, Unit
from accounts.permissions import is_dispatcher

from . import staffing
from .models import Duty, DutyPosition, Qualification, Report

# Upper limit of helpers per position and per duty (the database fields are small integers).
MAX_HELPERS = 10000


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
            "report_deadline",
            "description",
            "pay_type",
            "pay_rate",
            "all_units",
            "units",
        ]
        field_classes = {
            "starts_at": MinuteDateTimeField,
            "ends_at": MinuteDateTimeField,
            "report_deadline": MinuteDateTimeField,
        }
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
        report_deadline = cleaned_data.get("report_deadline")
        if starts_at and report_deadline and report_deadline >= starts_at:
            self.add_error("report_deadline", _("The report deadline must be before the start."))
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
        fields = ["count", "qualification", "pay_rate"]
        widgets = {
            "count": forms.NumberInput(attrs={"min": 1, "max": MAX_HELPERS}),
            "pay_rate": forms.NumberInput(attrs={"min": "0.01", "step": "0.01"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["count"].validators.append(MaxValueValidator(MAX_HELPERS))
        field = self.fields["qualification"]
        field.empty_label = _("– choose –")
        # Inactive qualifications only for positions that already have them.
        used = Q(pk=self.instance.qualification_id) if self.instance.qualification_id else Q()
        field.queryset = Qualification.objects.filter(Q(is_active=True) | used)


def _has_helpers(position):
    """Active reports for the position, or confirmed helpers who fill it."""
    return (
        position.reports.filter(status__in=Report.ACTIVE).exists()
        or position.assigned_reports.filter(status=Report.Status.CONFIRMED).exists()
    )


class BaseDutyPositionFormSet(forms.BaseInlineFormSet):
    """The positions of a duty: at least one, each qualification only once.

    More rows are added in the browser ("Add another qualification").
    """

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        # Errors on rows marked for deletion would be ignored, so this is a formset error.
        kept_back = [
            form.instance.label
            for form in self.deleted_forms
            if form.instance.pk and _has_helpers(form.instance)
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
            changes_qualification = form.instance.pk and "qualification" in form.changed_data
            if changes_qualification and _has_helpers(form.instance):
                form.add_error(
                    "qualification",
                    _("Helpers have been reported for this position; its qualification stays."),
                )
            qualification = form.cleaned_data.get("qualification")
            if qualification in seen:
                form.add_error("qualification", _("This qualification is listed twice."))
            seen.add(qualification)
        if self.total() > MAX_HELPERS:
            raise forms.ValidationError(
                _("A duty can have at most %(max)d helpers in total.") % {"max": MAX_HELPERS}
            )

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
    extra=0,
    min_num=1,
    validate_min=True,
    can_delete=True,
)


def copy_formset_class(positions):
    """The positions formset with one row per position to copy (the first row is the minimum)."""
    return forms.inlineformset_factory(
        Duty,
        DutyPosition,
        form=DutyPositionForm,
        formset=BaseDutyPositionFormSet,
        extra=max(len(positions) - 1, 0),
        min_num=1,
        validate_min=True,
        can_delete=True,
    )


class QualificationForm(forms.ModelForm):
    class Meta:
        model = Qualification
        fields = ["name", "abbreviation", "includes", "sort_order", "is_active"]
        widgets = {"sort_order": forms.NumberInput(attrs={"min": 0, "step": 10})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields["includes"]
        field.queryset = Qualification.objects.exclude(pk=self.instance.pk)
        field.empty_label = "–"
        if self.instance.pk is None:
            # New qualifications come last.
            last = Qualification.objects.aggregate(last=Max("sort_order"))["last"]
            self.initial["sort_order"] = (last or 0) + 10

    def has_changed(self):
        # A new row without name and abbreviation is empty, whatever its order says.
        if self.instance.pk is None:
            return bool(self["name"].value() or self["abbreviation"].value())
        return super().has_changed()


class BaseQualificationFormSet(forms.BaseModelFormSet):
    """The catalogue; more rows are added in the browser ("Add another qualification")."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # An empty row to start with, as long as there are none.
        self.extra = 0 if self.get_queryset().exists() else 1

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        # "Also counts as" must not go round in circles. New qualifications cannot be
        # chosen yet, so only the saved ones can form a circle.
        includes = dict(Qualification.objects.values_list("pk", "includes"))
        names = dict(Qualification.objects.values_list("pk", "name"))
        for form in self.forms:
            if form.instance.pk and form.cleaned_data:
                included = form.cleaned_data.get("includes")
                includes[form.instance.pk] = included.pk if included else None
                names[form.instance.pk] = form.cleaned_data.get("name")
        circles = set()
        for pk in includes:
            seen = staffing.chain(pk, includes)
            if includes.get(seen[-1]) is not None:  # back to one in the chain
                start = seen.index(includes[seen[-1]])
                circles.add(frozenset(seen[start:]))
        if circles:
            raise forms.ValidationError(
                _("These qualifications count as each other: %(qualifications)s")
                % {
                    "qualifications": "; ".join(
                        sorted(", ".join(sorted(names[pk] for pk in circle)) for circle in circles)
                    )
                }
            )


QualificationFormSet = forms.modelformset_factory(
    Qualification, form=QualificationForm, formset=BaseQualificationFormSet
)


class HelperUnitForm(forms.Form):
    """The unit whose helpers a dispatcher enters."""

    unit = forms.ModelChoiceField(label=_("Unit"), queryset=Unit.objects.none())

    def __init__(self, *args, duty, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["unit"].queryset = Unit.objects.all() if duty.all_units else duty.units.all()


class ReportForm(forms.ModelForm):
    class Meta:
        model = Report
        fields = ["name", "position", "note"]
        labels = {"name": _("Name of the helper"), "position": _("Qualification")}

    def __init__(self, *args, duty, positions, **kwargs):
        super().__init__(*args, **kwargs)
        if len(positions) <= 1:
            # Nothing to choose: the only position (if any) is taken.
            del self.fields["position"]
            self.instance.position = positions[0] if positions else None
            return
        labels = {position.pk: position_choice_label(position) for position in positions}
        field = self.fields["position"]
        field.queryset = duty.positions.select_related("qualification")
        field.label_from_instance = lambda position: labels.get(position.pk, position.label)
        field.required = True
        field.empty_label = None
        # The first position with open places is preselected, so that empty rows count as
        # unchanged.
        field.initial = next((p for p in positions if p.open_count), positions[0]).pk


def position_choice_label(position):
    """E.g. "Paramedic (RS) – 2 open", for a position from ``positions_with_staffing()``."""
    label = position.label
    if position.open_count:
        return _("%(position)s – %(count)d open") % {
            "position": label,
            "count": position.open_count,
        }
    return _("%(position)s – all taken") % {"position": label}


class BaseReportFormSet(forms.BaseFormSet):
    """Several helpers at once; more rows are added in the browser, empty ones are ignored."""

    def __init__(self, *args, duty, unit, **kwargs):
        form_kwargs = kwargs.setdefault("form_kwargs", {})
        form_kwargs["duty"] = duty
        form_kwargs["positions"] = list(duty.positions_with_staffing())
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

    @property
    def positions(self):
        return self.form_kwargs["positions"]


ReportFormSet = forms.formset_factory(
    ReportForm, formset=BaseReportFormSet, extra=0, min_num=1, validate_min=True
)


class WithdrawalRequestForm(forms.ModelForm):
    """A unit asks the dispatchers to withdraw a report."""

    class Meta:
        model = Report
        fields = ["withdrawal_note"]
        labels = {"withdrawal_note": _("Reason (optional)")}
        help_texts = {
            "withdrawal_note": _("For the dispatchers, e.g. who could step in instead."),
        }


class CalendarFeedForm(forms.ModelForm):
    """What the calendar subscription lists, depending on the role."""

    class Meta:
        model = CalendarFeed
        fields = ["only_own_reports", "include_drafts"]

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        del self.fields["only_own_reports" if is_dispatcher(user) else "include_drafts"]
