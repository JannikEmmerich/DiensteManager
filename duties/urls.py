from django.urls import path

from . import views

urlpatterns = [
    path("", views.duty_list, name="duty_list"),
    path("new/", views.duty_create, name="duty_create"),
    path("qualifications/", views.qualification_list, name="qualification_list"),
    path("<int:pk>/", views.duty_detail, name="duty_detail"),
    path("<int:pk>/edit/", views.duty_update, name="duty_update"),
    path("<int:pk>/release/", views.duty_release, name="duty_release"),
    path("<int:pk>/cancel/", views.duty_cancel, name="duty_cancel"),
    path("<int:pk>/delete/", views.duty_delete, name="duty_delete"),
    path("<int:pk>/report/", views.report_create, name="report_create"),
    path("<int:pk>/add-helpers/", views.report_add, name="report_add"),
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
    path(
        "reports/<int:pk>/request-withdrawal/",
        views.report_request_withdrawal,
        name="report_request_withdrawal",
    ),
    path("reports/<int:pk>/take-back/", views.report_take_back, name="report_take_back"),
    path("reports/<int:pk>/keep/", views.report_reject_withdrawal, name="report_reject_withdrawal"),
    path("reports/<int:pk>/assign/", views.report_assign, name="report_assign"),
]
