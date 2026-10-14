"""Validate international lead contact numbers without guessing a country."""

import re

import phonenumbers


def normalize_phone(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    value = value.strip()
    if not re.fullmatch(r"\+[0-9\s().-]+", value):
        raise ValueError("Enter a valid phone number including its country code")
    try:
        number = phonenumbers.parse(value, None)
    except phonenumbers.NumberParseException as exc:
        raise ValueError("Enter a valid phone number including its country code") from exc
    if not phonenumbers.is_valid_number(number):
        raise ValueError("Enter a valid phone number including its country code")
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
