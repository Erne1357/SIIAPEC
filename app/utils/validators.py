# app/utils/validators.py
"""
Shared validation for user-controlled text that gets stored in the database and
later rendered by staff-facing consoles.

Validation runs at the boundary so storage holds clean data; escaping stays a
rendering concern. Nothing here escapes or silently strips: a value is either
accepted (normalized) or rejected with a Spanish, UI-ready message.

Both entry points that accept person names — self-registration
(`pages_auth.register_page`) and profile update (`PATCH /api/v1/users/me`) —
must use these helpers so the rules cannot drift apart.
"""

from __future__ import annotations

import unicodedata

# Mirrors the String(50) columns on `user`: first_name, last_name,
# mother_last_name, username and scolarship_type.
NAME_MAX_LENGTH = 50
SHORT_TEXT_MAX_LENGTH = 50
# Mirrors user.email -> String(100).
EMAIL_MAX_LENGTH = 100

# Punctuation that legitimately appears in person names: apostrophes
# (D'Angelo, O'Brien), hyphens (Pérez-Reverte) and the period of an abbreviated
# given name (J. Carlos). Both the ASCII and the typographic apostrophe.
_NAME_PUNCTUATION = frozenset("'’-.")
_SPACE = " "

# Combining marks, so decomposed accents survive when NFC cannot compose them.
_COMBINING_MARK_CATEGORIES = ("Mn", "Mc")


class InputValidationError(ValueError):
    """
    A user-supplied value failed validation.

    `message` is written in Spanish and is safe to hand straight to a flash or
    to the `error.message` field of the API envelope.
    """

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


def _coerce_to_text(value, label: str) -> str:
    """JSON payloads can carry any type; anything but a string is a hard error."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise InputValidationError(f"El campo {label} debe ser texto.", field=label)
    return value


def _normalize_whitespace(value: str) -> str:
    """Trim the ends and collapse internal whitespace runs to a single space."""
    # NFC first: combining accents then count as one character against the
    # column limit, and visually identical names normalize to one storage form.
    value = unicodedata.normalize("NFC", value)
    # str.split() with no argument splits on every Unicode space (tab, newline,
    # NBSP, ...), so trimming and collapsing happen in a single pass.
    return _SPACE.join(value.split())


def _reject_unsafe_characters(value: str, label: str) -> None:
    """Reject markup delimiters and control/format code points outright."""
    if "<" in value or ">" in value:
        raise InputValidationError(
            f"El campo {label} no puede contener los caracteres «<» ni «>».",
            field=label,
        )
    for char in value:
        # Category C* covers control characters, format characters (zero-width
        # joiners, bidi overrides used for spoofing), surrogates, private-use
        # and unassigned code points. None of them belong in these fields.
        if unicodedata.category(char).startswith("C"):
            raise InputValidationError(
                f"El campo {label} contiene caracteres no permitidos.",
                field=label,
            )


def _enforce_max_length(value: str, label: str, max_length: int) -> None:
    if len(value) > max_length:
        raise InputValidationError(
            f"El campo {label} no puede tener más de {max_length} caracteres.",
            field=label,
        )


def validate_person_name(
    value,
    *,
    label: str,
    required: bool = True,
    max_length: int = NAME_MAX_LENGTH,
) -> str | None:
    """
    Validate and normalize a person-name field.

    Accepts the full range of real names — letters from any script with their
    accents, ñ/Ñ, apostrophes, hyphens, periods and spaces — so the check is
    unicode-aware rather than ASCII-only. Returns the normalized value, or None
    when the field is optional and empty.

    Raises InputValidationError (Spanish message) on anything else.
    """
    text = _normalize_whitespace(_coerce_to_text(value, label))

    if not text:
        if required:
            raise InputValidationError(
                f"El campo {label} es obligatorio.", field=label
            )
        return None

    _reject_unsafe_characters(text, label)
    _enforce_max_length(text, label, max_length)

    for char in text:
        if char == _SPACE or char in _NAME_PUNCTUATION:
            continue
        if char.isalpha():
            continue
        if unicodedata.category(char) in _COMBINING_MARK_CATEGORIES:
            continue
        raise InputValidationError(
            f"El campo {label} solo admite letras, espacios, apóstrofos, "
            "guiones y puntos.",
            field=label,
        )

    if not any(char.isalpha() for char in text):
        raise InputValidationError(
            f"El campo {label} debe contener al menos una letra.", field=label
        )

    return text


def validate_short_text(
    value,
    *,
    label: str,
    required: bool = False,
    max_length: int = SHORT_TEXT_MAX_LENGTH,
) -> str | None:
    """
    Validate and normalize a bounded free-text field (username, e-mail,
    scholarship type, ...).

    Deliberately keeps a permissive charset — these fields legitimately carry
    digits, '@' and other symbols — so the check only bounds the length and
    rejects the characters that let a stored value break out of an HTML or JS
    context. Returns the normalized value, or None when optional and empty.
    """
    text = _normalize_whitespace(_coerce_to_text(value, label))

    if not text:
        if required:
            raise InputValidationError(
                f"El campo {label} es obligatorio.", field=label
            )
        return None

    _reject_unsafe_characters(text, label)
    _enforce_max_length(text, label, max_length)

    return text
