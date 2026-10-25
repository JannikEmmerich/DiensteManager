from django import forms
from django.contrib.auth.forms import SetPasswordMixin
from django.utils import translation
from django.utils.translation import gettext_lazy as _

from . import permissions
from .models import LANGUAGE_CHOICES, Invitation, Role, Unit, User


class InvitationForm(forms.ModelForm):
    class Meta:
        model = Invitation
        fields = ["email", "role", "unit", "language"]
        labels = {"language": _("Language of the invitation")}
        help_texts = {
            "language": _("Used for the email and as the initial language of the new account."),
        }

    def __init__(self, *args, inviter, **kwargs):
        super().__init__(*args, **kwargs)
        roles = permissions.invitable_roles(inviter)
        self.fields["role"].choices = [(role.value, role.label) for role in roles]
        if Role.MEMBER in roles:
            self.fields["role"].initial = Role.MEMBER
        self.fields["unit"].queryset = permissions.invitable_units(inviter)
        self.fields["unit"].empty_label = _("– none (internal account) –")
        self.fields["language"].choices = LANGUAGE_CHOICES
        self.fields["language"].required = True
        self.fields["language"].initial = translation.get_language()
        if not permissions.is_dispatcher(inviter):
            # Unit leads always invite into their own unit.
            self.initial["unit"] = inviter.unit_id
            self.fields["unit"].disabled = True

    def clean_email(self):
        email = User.objects.normalize_email(self.cleaned_data["email"])
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(_("An account with this email address already exists."))
        if Invitation.objects.pending().filter(email=email).exists():
            raise forms.ValidationError(
                _("There is already a pending invitation for this email address.")
            )
        return email

    def clean(self):
        cleaned_data = super().clean()
        role = cleaned_data.get("role")
        unit = cleaned_data.get("unit")
        if role == Role.DISPATCHER and unit is not None:
            self.add_error("unit", _("Internal accounts do not belong to a unit."))
        elif role in (Role.UNIT_LEAD, Role.MEMBER) and unit is None:
            self.add_error("unit", _("Please select a unit."))
        return cleaned_data


class AcceptInvitationForm(SetPasswordMixin, forms.Form):
    """Creates the account of an invitation with a password."""

    first_name = forms.CharField(label=_("First name"), max_length=150)
    last_name = forms.CharField(label=_("Last name"), max_length=150)
    password1, password2 = SetPasswordMixin.create_password_fields()

    def __init__(self, *args, invitation, **kwargs):
        super().__init__(*args, **kwargs)
        self.invitation = invitation

    def clean(self):
        cleaned_data = super().clean()
        self.validate_passwords()
        # Check the password rules (e.g. "not too similar to the name") against the future account.
        self.validate_password_for_user(
            User(
                email=self.invitation.email,
                first_name=cleaned_data.get("first_name", ""),
                last_name=cleaned_data.get("last_name", ""),
            )
        )
        return cleaned_data


class ProfileForm(forms.ModelForm):
    NOTIFICATION_FIELDS = ["notify_new_duties", "notify_reports"]

    class Meta:
        model = User
        fields = ["first_name", "last_name", "language", "notify_new_duties", "notify_reports"]
        labels = {
            "notify_new_duties": _("New duties for my unit"),
            "notify_reports": _("Reports"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["first_name"].required = True
        self.fields["last_name"].required = True
        self.fields["language"].choices = [
            ("", _("Automatic (browser setting)")),
            *LANGUAGE_CHOICES,
        ]
        user = self.instance
        if permissions.is_dispatcher(user):
            # Dispatchers release the duties themselves.
            del self.fields["notify_new_duties"]
            self.fields["notify_reports"].help_text = _("When units report or withdraw helpers.")
        elif user.role == Role.UNIT_LEAD:
            self.fields["notify_reports"].help_text = _(
                "When reports of my unit are confirmed or declined, and when a duty with "
                "reports of my unit changes or is cancelled."
            )
        else:
            self.fields["notify_reports"].help_text = _(
                "When helpers I reported are confirmed or declined, and when their duty "
                "changes or is cancelled."
            )

    def personal_fields(self):
        return [field for field in self if field.name not in self.NOTIFICATION_FIELDS]

    def notification_fields(self):
        return [field for field in self if field.name in self.NOTIFICATION_FIELDS]


class UnitForm(forms.ModelForm):
    class Meta:
        model = Unit
        fields = ["name"]
