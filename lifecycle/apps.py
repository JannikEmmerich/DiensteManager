from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class LifecycleConfig(AppConfig):
    name = "lifecycle"
    verbose_name = _("Change history and retention")
