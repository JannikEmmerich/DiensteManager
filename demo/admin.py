from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import DemoAttachment, DemoEmail


class DemoAttachmentInline(admin.TabularInline):
    model = DemoAttachment
    fields = ["filename", "mimetype"]
    readonly_fields = fields
    extra = 0
    can_delete = False


@admin.register(DemoEmail)
class DemoEmailAdmin(admin.ModelAdmin):
    list_display = ["created_at", "addresses", "subject"]
    search_fields = ["recipients", "subject"]
    date_hierarchy = "created_at"
    inlines = [DemoAttachmentInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description=_("to"))
    def addresses(self, email):
        return ", ".join(email.to)
