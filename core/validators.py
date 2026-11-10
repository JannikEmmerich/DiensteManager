"""Checks for one-line texts that people enter and that end up in emails to others."""

import re
import unicodedata

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

# Control characters (line feed, tab, …) and the Unicode line and paragraph separators.
LINE_BREAKS = {"Cc", "Zl", "Zp"}
LINK = re.compile(r"://|www\.", re.IGNORECASE)


def validate_single_line(value):
    """No line breaks: a browser never sends them from a one-line field, and in an email
    they would let the text pose as paragraphs of the portal's own message."""
    if any(unicodedata.category(character) in LINE_BREAKS for character in value):
        raise ValidationError(_("Line breaks are not allowed here."), code="line_break")


def validate_name(value):
    """The name of a person: one line and no link. Names open emails to others, e.g. the
    invitation ("… has invited you"), which must not carry somebody's own message."""
    validate_single_line(value)
    if LINK.search(value):
        raise ValidationError(_("A name must not contain a link."), code="link")
