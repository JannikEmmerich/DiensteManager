from django.conf import settings
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils.http import content_disposition_header

from .models import DemoAttachment, DemoEmail

EMAILS_PER_PAGE = 50


def _visible_emails(request, show_all=False):
    """The emails sent to the signed-in account; for admins all of them if wanted."""
    if not settings.DEMO_MODE:
        raise Http404
    if show_all and request.user.is_superuser:
        return DemoEmail.objects.all()
    return DemoEmail.objects.filter(recipients__contains=f",{request.user.email.lower()},")


def inbox(request):
    show_all = request.user.is_superuser and request.GET.get("all") == "1"
    emails = _visible_emails(request, show_all).prefetch_related("attachments")
    page = Paginator(emails, EMAILS_PER_PAGE).get_page(request.GET.get("page"))
    return render(request, "demo/inbox.html", {"page": page, "show_all": show_all})


def inbox_email(request, pk):
    email = get_object_or_404(_visible_emails(request, show_all=True), pk=pk)
    return render(request, "demo/inbox_email.html", {"email": email})


def inbox_attachment(request, pk):
    emails = _visible_emails(request, show_all=True)
    attachment = get_object_or_404(DemoAttachment, pk=pk, email__in=emails)
    response = HttpResponse(bytes(attachment.content), content_type=attachment.mimetype)
    response["Content-Disposition"] = content_disposition_header(True, attachment.filename)
    return response
