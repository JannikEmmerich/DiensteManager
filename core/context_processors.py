from django.conf import settings

from accounts import permissions


def site(request):
    """Site name, HiOrg status and the permissions needed for the navigation."""
    user = request.user
    return {
        "SITE_NAME": settings.SITE_NAME,
        "HIORG_ENABLED": bool(settings.HIORG_CLIENT_ID),
        "can": {
            "invite": permissions.can_invite(user),
            "manage_units": permissions.is_dispatcher(user),
            "view_own_unit": permissions.is_unit_lead(user),
        },
    }
