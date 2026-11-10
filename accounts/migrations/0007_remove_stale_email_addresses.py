from django.db import migrations


def remove_stale_addresses(apps, schema_editor):
    """An email address changed in the Django admin left the old one in allauth's list:
    it still signed in and received the password reset. The confirmed address of an
    account is the one it has now, as when an admin changes it in the portal."""
    EmailAddress = apps.get_model("account", "EmailAddress")
    emails = dict(apps.get_model("accounts", "User").objects.values_list("pk", "email"))
    affected = set()
    for address in EmailAddress.objects.filter(verified=True):
        if address.email.lower() != emails[address.user_id].lower():
            affected.add(address.user_id)
            address.delete()
    for user_id in affected:
        addresses = EmailAddress.objects.filter(user_id=user_id)
        addresses.filter(email__iexact=emails[user_id]).delete()
        addresses.update(primary=False)
        EmailAddress.objects.create(
            user_id=user_id, email=emails[user_id], verified=True, primary=True
        )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0006_alter_invitation_role_alter_user_role"),
        ("account", "0009_emailaddress_unique_primary_email"),
    ]

    operations = [
        migrations.RunPython(remove_stale_addresses, migrations.RunPython.noop),
    ]
