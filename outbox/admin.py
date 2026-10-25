from django.contrib import admin, messages
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from .models import OutgoingAttachment, OutgoingEmail


class OutgoingAttachmentInline(admin.TabularInline):
    model = OutgoingAttachment
    fields = ["filename", "mimetype"]
    readonly_fields = fields
    extra = 0
    can_delete = False


@admin.register(OutgoingEmail)
class OutgoingEmailAdmin(admin.ModelAdmin):
    list_display = ["created_at", "addresses", "subject", "status", "digest", "attempts"]
    list_filter = ["status", "digest"]
    search_fields = ["to", "subject"]
    date_hierarchy = "created_at"
    inlines = [OutgoingAttachmentInline]
    actions = ["send_again"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        # Emails are sent as they were written; only "Send again" changes them.
        return False

    @admin.display(description=_("to"))
    def addresses(self, email):
        return ", ".join(email.to)

    def has_send_permission(self, request):
        return request.user.has_perm("outbox.change_outgoingemail")

    @admin.action(description=_("Send again"), permissions=["send"])
    def send_again(self, request, queryset):
        count = queryset.filter(status=OutgoingEmail.Status.FAILED).update(
            status=OutgoingEmail.Status.QUEUED,
            attempts=0,
            last_error="",
            next_attempt_at=timezone.now(),
        )
        self.message_user(
            request,
            ngettext(
                "%(count)d failed email will be sent again.",
                "%(count)d failed emails will be sent again.",
                count,
            )
            % {"count": count},
            messages.SUCCESS,
        )
