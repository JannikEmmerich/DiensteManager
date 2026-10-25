"""Changing roles (unit leads in their unit, dispatchers everywhere) and editing whole
accounts (admins)."""

import pytest
from allauth.account.models import EmailAddress
from django.urls import reverse

from accounts.models import Role, User

from .conftest import PASSWORD, create_account


def _change_role(client, account, role):
    return client.post(reverse("account_role", args=[account.pk]), {"role": role})


# --- Unit lead or member ------------------------------------------------------------------------


def test_unit_lead_switches_roles_in_their_unit(client, unit_lead, member, unit):
    client.force_login(unit_lead)
    page = client.get(unit.get_absolute_url()).text
    assert reverse("account_role", args=[member.pk]) in page
    assert reverse("account_role", args=[unit_lead.pk]) not in page  # not their own
    response = _change_role(client, member, Role.UNIT_LEAD)
    assert response["Location"] == unit.get_absolute_url()
    member.refresh_from_db()
    assert member.role == Role.UNIT_LEAD
    page = client.get(unit.get_absolute_url()).text
    assert "member@example.org is now: Unit lead." in page
    _change_role(client, member, Role.MEMBER)
    member.refresh_from_db()
    assert member.role == Role.MEMBER


def test_unit_leads_only_in_their_unit(client, unit_lead, other_unit_lead):
    client.force_login(unit_lead)
    assert _change_role(client, other_unit_lead, Role.MEMBER).status_code == 403
    assert _change_role(client, unit_lead, Role.MEMBER).status_code == 403
    other_unit_lead.refresh_from_db()
    unit_lead.refresh_from_db()
    assert other_unit_lead.role == unit_lead.role == Role.UNIT_LEAD


def test_members_change_no_roles(client, member, unit_lead):
    client.force_login(member)
    assert _change_role(client, unit_lead, Role.MEMBER).status_code == 403


def test_nobody_becomes_a_dispatcher_this_way(client, dispatcher, member):
    client.force_login(dispatcher)
    _change_role(client, member, Role.DISPATCHER)
    member.refresh_from_db()
    assert member.role == Role.MEMBER


def test_dispatchers_switch_roles_in_all_units(client, dispatcher, member, other_unit_lead):
    client.force_login(dispatcher)
    _change_role(client, member, Role.UNIT_LEAD)
    _change_role(client, other_unit_lead, Role.MEMBER)
    member.refresh_from_db()
    other_unit_lead.refresh_from_db()
    assert (member.role, other_unit_lead.role) == (Role.UNIT_LEAD, Role.MEMBER)


def test_internal_accounts_keep_their_role(client, dispatcher, admin):
    other = create_account("other-dispatcher@example.org", Role.DISPATCHER)
    client.force_login(dispatcher)
    assert _change_role(client, other, Role.MEMBER).status_code == 403
    client.force_login(admin)
    assert _change_role(client, other, Role.MEMBER).status_code == 403  # needs a unit


# --- Admins edit everything ----------------------------------------------------------------------


def _account_data(account, **data):
    values = {
        "first_name": account.first_name or "Some",
        "last_name": account.last_name or "Body",
        "email": account.email,
        "role": account.role,
        "unit": account.unit_id or "",
        "language": account.language,
        "notify_reports": "on",
    }
    values.update(data)
    return values


def test_only_admins_edit_accounts(client, admin, dispatcher, unit_lead, member):
    url = reverse("account_edit", args=[member.pk])
    for user in [dispatcher, unit_lead]:
        client.force_login(user)
        assert client.get(url).status_code == 403
        assert (
            reverse("account_edit", args=[member.pk])
            not in client.get(member.unit.get_absolute_url()).text
        )
    client.force_login(admin)
    assert client.get(url).status_code == 200
    assert url in client.get(member.unit.get_absolute_url()).text
    # Not their own account: that is "My account".
    assert client.get(reverse("account_edit", args=[admin.pk])).status_code == 403


def test_admin_changes_name_email_and_unit(client, admin, member, other_unit):
    client.force_login(admin)
    response = client.post(
        reverse("account_edit", args=[member.pk]),
        _account_data(
            member,
            first_name="Mia",
            last_name="Muster",
            email="Mia@Example.org",
            unit=other_unit.pk,
        ),
    )
    assert response["Location"] == other_unit.get_absolute_url()
    member.refresh_from_db()
    assert (member.first_name, member.last_name, member.unit) == ("Mia", "Muster", other_unit)
    assert member.email == "mia@example.org"
    (address,) = EmailAddress.objects.filter(user=member)
    assert (address.email, address.primary, address.verified) == (member.email, True, True)
    # The person signs in with the new address.
    client.logout()
    response = client.post(
        reverse("account_login"), {"login": "mia@example.org", "password": PASSWORD}
    )
    assert response["Location"] == reverse("home")


def test_email_addresses_stay_unique(client, admin, member, unit_lead):
    client.force_login(admin)
    response = client.post(
        reverse("account_edit", args=[member.pk]),
        _account_data(member, email=unit_lead.email.upper()),
    )
    assert "An account with this email address already exists." in response.text


def test_admin_makes_a_dispatcher_and_back(client, admin, member, unit):
    client.force_login(admin)
    url = reverse("account_edit", args=[member.pk])
    # The unit is hidden in the browser for internal accounts; whatever is sent is dropped.
    client.post(url, _account_data(member, role=Role.DISPATCHER))
    member.refresh_from_db()
    assert (member.role, member.unit) == (Role.DISPATCHER, None)
    assert member.get_full_name() in client.get(reverse("unit_list")).text
    response = client.post(url, _account_data(member, role=Role.MEMBER, unit=""))
    assert "Please select a unit." in response.text
    client.post(url, _account_data(member, role=Role.UNIT_LEAD, unit=unit.pk))
    member.refresh_from_db()
    assert (member.role, member.unit) == (Role.UNIT_LEAD, unit)


def test_internal_accounts_are_listed_for_dispatchers(client, admin, dispatcher, member):
    client.force_login(dispatcher)
    page = client.get(reverse("unit_list")).text
    assert "Internal accounts" in page
    assert dispatcher.email in page and admin.email in page
    assert member.email not in page
    assert reverse("account_edit", args=[admin.pk]) not in page
    client.force_login(admin)
    page = client.get(reverse("unit_list")).text
    assert reverse("account_edit", args=[dispatcher.pk]) in page


@pytest.mark.parametrize("url_name", ["unit_update", "bank_account_edit"])
def test_admins_edit_unit_data(client, admin, unit, url_name):
    client.force_login(admin)
    assert client.get(reverse(url_name, args=[unit.pk])).status_code == 200


def test_admins_cannot_change_their_own_role(client, admin):
    client.force_login(admin)
    assert _change_role(client, admin, Role.MEMBER).status_code == 403
    assert User.objects.get(pk=admin.pk).is_superuser
