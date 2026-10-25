from django.urls import path

from . import views

urlpatterns = [
    path("language/", views.set_language, name="set_language"),
    path("profile/", views.profile, name="profile"),
    path("invitation/<str:token>/", views.accept_invitation, name="accept_invitation"),
    path("invitations/", views.invitation_list, name="invitation_list"),
    path("invitations/new/", views.invitation_create, name="invitation_create"),
    path("invitations/<int:pk>/resend/", views.invitation_resend, name="invitation_resend"),
    path("invitations/<int:pk>/revoke/", views.invitation_revoke, name="invitation_revoke"),
    path("units/", views.unit_list, name="unit_list"),
    path("units/new/", views.unit_create, name="unit_create"),
    path("units/<int:pk>/", views.unit_detail, name="unit_detail"),
    path("units/<int:pk>/edit/", views.unit_update, name="unit_update"),
    path("users/<int:pk>/active/", views.set_account_active, name="set_account_active"),
    path("users/<int:pk>/role/", views.account_role, name="account_role"),
    path("users/<int:pk>/edit/", views.account_edit, name="account_edit"),
]
