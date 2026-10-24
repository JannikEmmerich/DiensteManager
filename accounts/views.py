import logging
from smtplib import SMTPException

from allauth.account import app_settings as allauth_account_settings
from allauth.account.utils import perform_login
from allauth.mfa.models import Authenticator
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from django.views.i18n import set_language as django_set_language

from . import invitations, permissions
from .forms import AcceptInvitationForm, InvitationForm, ProfileForm, UnitForm
from .models import Unit, User

logger = logging.getLogger(__name__)


def _redirect_back(request, default):
    """Redirects to ``next`` if it points to this site, otherwise to ``default``."""
    target = request.POST.get("next") or request.GET.get("next")
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(target)
    return redirect(default)


def _send(request, invitation):
    try:
        invitations.send_invitation(request, invitation)
    except (SMTPException, OSError):
        logger.exception("Could not send the invitation to %s", invitation.email)
        messages.warning(
            request,
            _(
                "The invitation for %(email)s has been saved, but the email could not be "
                "sent. Please use “Resend” later."
            )
            % {"email": invitation.email},
        )
    else:
        messages.success(request, _("Invitation sent to %(email)s.") % {"email": invitation.email})


# --- Language ---------------------------------------------------------------------------


@login_not_required
@require_POST
def set_language(request):
    """Switches the UI language; for signed-in users the choice is stored on the account."""
    response = django_set_language(request)
    language = request.POST.get("language", "")
    if request.user.is_authenticated and language in dict(settings.LANGUAGES):
        request.user.language = language
        request.user.save(update_fields=["language"])
    return response


# --- Accepting an invitation (no sign-in required) ------------------------------------------


@login_not_required
@never_cache
def accept_invitation(request, token):
    invitation = invitations.pending_invitation(token)
    if invitation is None:
        return render(request, "accounts/invitation_invalid.html", status=404)
    if request.user.is_authenticated:
        return render(request, "accounts/invitation_signed_in.html", {"invitation": invitation})

    # Needed for "Sign in with HiOrg-Server": the HiOrg sign-in checks this invitation.
    invitations.remember_in_session(request, invitation)

    form = AcceptInvitationForm(request.POST or None, invitation=invitation)
    if request.method == "POST" and form.is_valid():
        try:
            user = invitations.create_account_with_password(
                invitation.pk,
                first_name=form.cleaned_data["first_name"],
                last_name=form.cleaned_data["last_name"],
                password=form.cleaned_data["password1"],
            )
        except invitations.InvitationInvalid:
            return render(request, "accounts/invitation_invalid.html", status=404)
        invitations.forget_from_session(request)
        messages.success(request, _("Welcome! Your account is ready."))
        return perform_login(
            request,
            user,
            email_verification=allauth_account_settings.EMAIL_VERIFICATION,
            redirect_url=reverse("home"),
        )
    return render(
        request, "accounts/accept_invitation.html", {"invitation": invitation, "form": form}
    )


# --- Managing invitations ----------------------------------------------------------------


def invitation_list(request):
    if not permissions.can_invite(request.user):
        raise PermissionDenied
    invitation_qs = permissions.visible_invitations(request.user).select_related(
        "unit", "invited_by", "account"
    )
    return render(request, "accounts/invitation_list.html", {"invitations": invitation_qs})


def invitation_create(request):
    if not permissions.can_invite(request.user):
        raise PermissionDenied
    form = InvitationForm(
        request.POST or None,
        inviter=request.user,
        initial={"unit": request.GET.get("unit")},
    )
    if request.method == "POST" and form.is_valid():
        invitation = form.save(commit=False)
        invitation.invited_by = request.user
        _send(request, invitation)
        return _redirect_back(request, "invitation_list")
    return render(
        request,
        "accounts/invitation_form.html",
        {"form": form, "validity_days": settings.INVITATION_VALIDITY_DAYS},
    )


def _manageable_invitation(request, pk):
    invitation = get_object_or_404(permissions.visible_invitations(request.user), pk=pk)
    if not permissions.can_manage_invitation(request.user, invitation):
        raise PermissionDenied
    return invitation


def _invitation_response(request, invitation):
    if request.htmx:
        # Only return the table row (plus the messages).
        return render(request, "accounts/invitation_row_htmx.html", {"invitation": invitation})
    return _redirect_back(request, "invitation_list")


@require_POST
def invitation_resend(request, pk):
    invitation = _manageable_invitation(request, pk)
    if invitation.can_be_resent:
        _send(request, invitation)
    else:
        messages.error(request, _("This invitation can no longer be resent."))
    return _invitation_response(request, invitation)


@require_POST
def invitation_revoke(request, pk):
    invitation = _manageable_invitation(request, pk)
    if invitation.can_be_resent:
        invitation.revoked_at = timezone.now()
        invitation.save(update_fields=["revoked_at"])
        messages.success(
            request, _("Invitation for %(email)s revoked.") % {"email": invitation.email}
        )
    else:
        messages.error(request, _("This invitation can no longer be revoked."))
    return _invitation_response(request, invitation)


# --- Units ---------------------------------------------------------------------------


def unit_list(request):
    if not permissions.is_dispatcher(request.user):
        raise PermissionDenied
    units = Unit.objects.annotate(account_count=Count("members"))
    return render(request, "accounts/unit_list.html", {"units": units})


def unit_create(request):
    if not permissions.is_dispatcher(request.user):
        raise PermissionDenied
    form = UnitForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        unit = form.save()
        messages.success(request, _("Unit “%(name)s” created.") % {"name": unit.name})
        return redirect(unit)
    return render(request, "accounts/unit_form.html", {"form": form})


def unit_update(request, pk):
    if not permissions.is_dispatcher(request.user):
        raise PermissionDenied
    unit = get_object_or_404(Unit, pk=pk)
    form = UnitForm(request.POST or None, instance=unit)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Saved."))
        return redirect(unit)
    return render(request, "accounts/unit_form.html", {"form": form, "unit": unit})


def unit_detail(request, pk):
    unit = get_object_or_404(Unit, pk=pk)
    if not permissions.can_view_unit(request.user, unit):
        raise PermissionDenied
    accounts = [
        (account, permissions.can_block_account(request.user, account))
        for account in unit.members.all()
    ]
    pending = unit.invitations.filter(
        accepted_at__isnull=True, revoked_at__isnull=True
    ).select_related("unit", "invited_by")
    return render(
        request,
        "accounts/unit_detail.html",
        {
            "unit": unit,
            "accounts": accounts,
            "invitations": pending,
            "can_invite_here": permissions.invitable_units(request.user)
            .filter(pk=unit.pk)
            .exists(),
        },
    )


@require_POST
def set_account_active(request, pk):
    """Blocks (active=0) or unblocks (active=1) an account."""
    target = get_object_or_404(User, pk=pk)
    if not permissions.can_block_account(request.user, target):
        raise PermissionDenied
    target.is_active = request.POST.get("active") == "1"
    target.save(update_fields=["is_active"])
    if target.is_active:
        messages.success(request, _("Access for %(account)s unblocked.") % {"account": target})
    else:
        messages.success(request, _("Access for %(account)s blocked.") % {"account": target})
    return _redirect_back(request, target.unit or "home")


# --- My account -------------------------------------------------------------------------


def profile(request):
    previous_language = request.user.language
    form = ProfileForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Saved."))
        response = redirect("profile")
        if previous_language and not request.user.language:
            # "Automatic": drop the stored choice so that the browser setting applies again.
            response.delete_cookie(
                settings.LANGUAGE_COOKIE_NAME,
                path=settings.LANGUAGE_COOKIE_PATH,
                domain=settings.LANGUAGE_COOKIE_DOMAIN,
                samesite=settings.LANGUAGE_COOKIE_SAMESITE,
            )
        return response
    return render(
        request,
        "accounts/profile.html",
        {
            "form": form,
            "hiorg_accounts": request.user.socialaccount_set.filter(provider="hiorg"),
            "mfa_enabled": Authenticator.objects.filter(
                user=request.user, type=Authenticator.Type.TOTP
            ).exists(),
        },
    )
