from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm
from django.utils.translation import gettext_lazy as _

from .models import CalendarFeed, Invitation, Unit, User


class UserCreationAdminForm(AdminUserCreationForm):
    class Meta(AdminUserCreationForm.Meta):
        model = User
        fields = ("email", "role", "unit")
        field_classes = {}


class UserChangeAdminForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = "__all__"
        field_classes = {}


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    form = UserChangeAdminForm
    add_form = UserCreationAdminForm
    ordering = ["email"]
    list_display = ["email", "first_name", "last_name", "role", "unit", "is_active", "is_superuser"]
    list_filter = ["role", "unit", "is_active", "is_superuser"]
    search_fields = ["email", "first_name", "last_name"]
    fieldsets = [
        (None, {"fields": ["email", "password"]}),
        (_("Personal info"), {"fields": ["first_name", "last_name", "language"]}),
        (_("Assignment"), {"fields": ["role", "unit"]}),
        (
            _("Permissions"),
            {"fields": ["is_active", "is_staff", "is_superuser", "groups", "user_permissions"]},
        ),
        (_("Important dates"), {"fields": ["last_login", "date_joined"]}),
    ]
    add_fieldsets = [
        (
            None,
            {
                "classes": ["wide"],
                "fields": ["email", "role", "unit", "usable_password", "password1", "password2"],
            },
        ),
    ]


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ["name", "created_at"]
    search_fields = ["name"]


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = ["email", "role", "unit", "status_display", "expires_at", "invited_by"]
    list_filter = ["role", "unit"]
    search_fields = ["email"]
    readonly_fields = [
        "email",
        "role",
        "unit",
        "language",
        "invited_by",
        "created_at",
        "expires_at",
        "accepted_at",
        "account",
    ]

    @admin.display(description=_("status"))
    def status_display(self, obj):
        return obj.status.label

    def has_add_permission(self, request):
        # Invitations are created in the portal; only there the link is sent by email.
        return False


@admin.register(CalendarFeed)
class CalendarFeedAdmin(admin.ModelAdmin):
    list_display = ["user", "only_own_reports", "include_drafts", "created_at"]
    search_fields = ["user__email"]
    # The address only exists for its account; deleting it here ends the subscription.
    readonly_fields = ["user", "token", "created_at"]

    def has_add_permission(self, request):
        return False
