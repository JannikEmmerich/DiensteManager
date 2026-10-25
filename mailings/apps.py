from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class MailingsConfig(AppConfig):
    name = "mailings"
    verbose_name = _("Emails")
