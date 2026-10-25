from django.conf import settings

from accounts import permissions
from duties.permissions import is_unit_account


def site(request):
    """Site name, version, HiOrg status and the permissions needed for the navigation."""
    user = request.user
    return {
        "SITE_NAME": settings.SITE_NAME,
        "DISPATCH_NAME": settings.DISPATCH_NAME,
        "APP_VERSION": settings.VERSION,
        "APP_REVISION": settings.REVISION[:7],
        "HIORG_ENABLED": bool(settings.HIORG_CLIENT_ID),
        "can": {
            "invite": permissions.can_invite(user),
            "manage_units": permissions.is_dispatcher(user),
            "view_own_unit": permissions.is_unit_lead(user),
            "view_duties": permissions.is_dispatcher(user) or is_unit_account(user),
            "manage_duties": permissions.is_dispatcher(user),
            "send_emails": permissions.is_dispatcher(user),
            "manage_billing": permissions.is_dispatcher(user),
        },
    }
