from django.contrib import admin

from .models import Mailing


@admin.register(Mailing)
class MailingAdmin(admin.ModelAdmin):
    list_display = ["subject", "sent_by", "sent_at", "audience"]
    list_filter = ["audience"]
    search_fields = ["subject", "body"]
    date_hierarchy = "sent_at"
    readonly_fields = [
        "subject",
        "body",
        "audience",
        "all_units",
        "units",
        "duty",
        "sent_by",
        "sent_at",
        "recipients",
    ]

    def has_add_permission(self, request):
        # Emails are written in the portal; only there they are sent.
        return False
