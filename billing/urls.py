from django.urls import path

from . import views

urlpatterns = [
    path("", views.billing_list, name="billing_list"),
    path("new/<int:unit_pk>/", views.billing_create, name="billing_create"),
    path("<int:pk>/", views.billing_detail, name="billing_detail"),
    path("<int:pk>/pdf/", views.billing_pdf, name="billing_pdf"),
    path("<int:pk>/paid/", views.billing_mark_paid, name="billing_mark_paid"),
    path("<int:pk>/cancel/", views.billing_cancel, name="billing_cancel"),
    path("units/<int:pk>/", views.unit_pay, name="unit_pay"),
    path("units/<int:pk>/bank-account/", views.bank_account_edit, name="bank_account_edit"),
]
