from django.urls import path

from . import views

urlpatterns = [
    path("", views.mailing_list, name="mailing_list"),
    path("new/", views.mailing_create, name="mailing_create"),
    path("new/recipients/", views.mailing_recipient_count, name="mailing_recipients"),
    path("<int:pk>/", views.mailing_detail, name="mailing_detail"),
]
