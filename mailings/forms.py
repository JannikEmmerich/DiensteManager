from django import forms
from django.utils.translation import gettext_lazy as _

from accounts.forms import clean_unit_selection, unit_selection_fields
from accounts.models import Unit, User
from duties.models import Duty

from .models import Mailing, unit_accounts


class MailingForm(forms.ModelForm):
    all_units, units = unit_selection_fields(
        _("Units"), _("Only relevant if the email goes to selected units only.")
    )
    duty = forms.ModelChoiceField(
        queryset=Duty.objects.exclude(status=Duty.Status.DRAFT),
        required=False,
        widget=forms.HiddenInput,
    )
    send_copy = forms.BooleanField(label=_("Send me a copy"), required=False, initial=True)

    class Meta:
        model = Mailing
        fields = ["subject", "body", "audience", "all_units", "units", "duty"]
        widgets = {
            "audience": forms.RadioSelect(attrs={"class": "form-check-input"}),
            "body": forms.Textarea(attrs={"rows": 10}),
        }

    def clean(self):
        cleaned_data = super().clean()
        if clean_unit_selection(self, cleaned_data) and not self.recipients().exists():
            raise forms.ValidationError(_("Nobody in the selected units would receive this email."))
        return cleaned_data

    def recipients(self):
        """Accounts the email goes to, according to the valid parts of the form."""
        audience = self.cleaned_data.get("audience")
        if audience is None or self.cleaned_data.get("all_units") is None:
            return User.objects.none()
        units = (
            Unit.objects.all() if self.cleaned_data["all_units"] else self.cleaned_data.get("units")
        )
        if units is None:
            return User.objects.none()
        return unit_accounts(audience, units)
