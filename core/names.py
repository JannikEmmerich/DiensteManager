"""The name of the dispatchers, set with DISPATCH_NAME (default "Disposition").

It is the same in every language and fills the placeholder %(DISPATCH_NAME)s of texts.
Templates get it from the context (core.context_processors.site, the emails from
duties.notifications).
"""

from django.conf import settings
from django.utils.functional import lazy


def _dispatch_text(text, values):
    return str(text) % {"DISPATCH_NAME": settings.DISPATCH_NAME, **values}


def dispatch_text(text, **values):
    """``text`` with the name of the dispatchers (and ``values``) filled in.

    Evaluated only when used, so that a lazy ``text`` is translated into the language
    active then, e.g. for labels and help texts of models and forms.
    """
    return lazy(_dispatch_text, str)(text, values)
