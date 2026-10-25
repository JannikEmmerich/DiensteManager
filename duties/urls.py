from django.urls import path

from . import views

urlpatterns = [
    path("", views.duty_list, name="duty_list"),
    path("new/", views.duty_create, name="duty_create"),
    path("<int:pk>/", views.duty_detail, name="duty_detail"),
    path("<int:pk>/edit/", views.duty_update, name="duty_update"),
    path("<int:pk>/release/", views.duty_release, name="duty_release"),
    path("<int:pk>/cancel/", views.duty_cancel, name="duty_cancel"),
    path("<int:pk>/delete/", views.duty_delete, name="duty_delete"),
    path("<int:pk>/report/", views.report_create, name="report_create"),
    path("<int:pk>/confirm-all/", views.report_confirm_all, name="report_confirm_all"),
    path(
        "reports/<int:pk>/confirm/",
        views.report_decide,
        {"decision": "confirm"},
        name="report_confirm",
    ),
    path(
        "reports/<int:pk>/decline/",
        views.report_decide,
        {"decision": "decline"},
        name="report_decline",
    ),
    path("reports/<int:pk>/withdraw/", views.report_withdraw, name="report_withdraw"),
]
