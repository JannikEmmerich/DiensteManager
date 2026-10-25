from django.contrib import admin

from .models import HistoryEntry


@admin.register(HistoryEntry)
class HistoryEntryAdmin(admin.ModelAdmin):
    list_display = ["created_at", "object_repr", "action", "user_repr", "scope"]
    list_filter = ["action", "content_type"]
    search_fields = ["object_repr", "user_repr", "scope"]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        # The history documents what happened; it is not edited afterwards.
        return False
