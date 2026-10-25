from django.urls import path

from . import views

urlpatterns = [
    path("", views.inbox, name="demo_inbox"),
    path("<int:pk>/", views.inbox_email, name="demo_inbox_email"),
    path("attachments/<int:pk>/", views.inbox_attachment, name="demo_inbox_attachment"),
]
