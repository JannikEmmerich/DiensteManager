from django.contrib import admin

from .models import BankAccount, Billing, BillingItem


@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):
    list_display = ["unit", "account_holder", "iban_display", "updated_at"]
    search_fields = ["unit__name", "account_holder"]
    readonly_fields = ["updated_at", "updated_by"]


class BillingItemInline(admin.TabularInline):
    model = BillingItem
    extra = 0
    can_delete = False
    fields = ["duty_date", "duty_title", "helper_name", "pay_type", "rate", "hours", "amount"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Billing)
class BillingAdmin(admin.ModelAdmin):
    list_display = ["number", "unit", "total", "status", "created_at"]
    list_filter = ["status", "year", "unit"]
    readonly_fields = [
        "unit",
        "total",
        "account_holder",
        "iban",
        "bic",
        "bank_name",
        "created_by",
        "created_at",
        "paid_at",
        "cancelled_at",
    ]
    inlines = [BillingItemInline]

    def has_add_permission(self, request):
        # Billings are created in the portal from the confirmed helpers.
        return False
