from allauth.account.decorators import secure_admin_login
from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _

# The admin uses the regular sign-in (including two-factor authentication)
# instead of its own login form.
admin.site.login = secure_admin_login(admin.site.login)
admin.site.site_header = format_lazy("{} – {}", settings.SITE_NAME, _("Administration"))
admin.site.site_title = settings.SITE_NAME
admin.site.index_title = _("Administration")

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("", include("accounts.urls")),
    path("", include("core.urls")),
]
