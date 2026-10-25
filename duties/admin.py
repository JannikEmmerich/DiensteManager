from django.contrib import admin

from .models import Duty, DutyPosition, Qualification, Report


@admin.register(Qualification)
class QualificationAdmin(admin.ModelAdmin):
    list_display = ["name", "abbreviation", "sort_order", "is_active"]
    list_editable = ["sort_order", "is_active"]
    search_fields = ["name", "abbreviation"]


class DutyPositionInline(admin.TabularInline):
    model = DutyPosition
    extra = 0


class ReportInline(admin.TabularInline):
    model = Report
    extra = 0
    fields = ["unit", "name", "qualification", "note", "status", "reported_by", "created_at"]
    readonly_fields = ["reported_by", "created_at"]


@admin.register(Duty)
class DutyAdmin(admin.ModelAdmin):
    list_display = ["title", "starts_at", "location", "helpers_needed", "status", "source"]
    list_filter = ["status", "source", "all_units"]
    search_fields = ["title", "location"]
    date_hierarchy = "starts_at"
    filter_horizontal = ["units"]
    readonly_fields = [
        "source",
        "external_id",
        "created_by",
        "created_at",
        "updated_at",
        "released_at",
        "cancelled_at",
    ]
    inlines = [DutyPositionInline, ReportInline]


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ["name", "duty", "unit", "status", "created_at"]
    list_filter = ["status", "unit"]
    search_fields = ["name", "duty__title"]
    list_select_related = ["duty", "unit"]
    readonly_fields = ["reported_by", "created_at", "decided_by", "decided_at", "withdrawn_at"]
