"""IBAN and BIC: normalising, checking and formatting."""

import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

IBAN_FORMAT = re.compile(r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$")
BIC_FORMAT = re.compile(r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?$")


def normalize_iban(value):
    return "".join(value.split()).upper()


def validate_iban(value):
    """Checks the format and the check digits (ISO 13616, modulo 97)."""
    iban = normalize_iban(value)
    if not IBAN_FORMAT.match(iban):
        raise ValidationError(_("This is not a valid IBAN."), code="invalid_iban")
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(char, 36)) for char in rearranged)
    if int(digits) % 97 != 1:
        raise ValidationError(
            _("The check digits of this IBAN are wrong. Please check it for typos."),
            code="invalid_iban_checksum",
        )


def validate_bic(value):
    if not BIC_FORMAT.match(normalize_iban(value)):
        raise ValidationError(_("This is not a valid BIC."), code="invalid_bic")


def format_iban(iban):
    """Groups of four, e.g. "DE89 3704 0044 0532 0130 00"."""
    return " ".join(iban[i : i + 4] for i in range(0, len(iban), 4))
