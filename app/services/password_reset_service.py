"""
Token-based password set/reset flow.

Generates opaque random tokens stored in `password_reset_token`.  Used by:
  - Bulk-import student onboarding (purpose='set_password').
  - Future "forgot password" flow (purpose='reset_password').

Service is framework-agnostic: routes pass user IDs and tokens explicitly,
service does not touch request/session/current_user.
"""

import secrets
import string
from datetime import timedelta

from werkzeug.security import generate_password_hash

from app import db
from app.models.password_reset_token import PasswordResetToken
from app.models.user import User
from app.services.user_history_service import UserHistoryService
from app.utils.datetime_utils import now_local

# TTL of a link issued interactively by an administrator (account creation from
# the console, or a password reset for one named person). Short on purpose: the
# admin is sitting in front of the screen and can tell the holder to look at
# their inbox right now. The 7-day TTL used by the bulk import exists only
# because that flow mails hundreds of students at once and nobody is waiting.
INTERACTIVE_TTL_MINUTES = 45


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------

class PasswordResetError(Exception):
    """Base error for password reset operations."""


class TokenNotFound(PasswordResetError):
    pass


class TokenExpired(PasswordResetError):
    pass


class TokenAlreadyUsed(PasswordResetError):
    pass


class WeakPassword(PasswordResetError):
    pass


class MissingEmail(PasswordResetError):
    """The account has no usable mailbox, so no link can ever reach it."""


# ---------------------------------------------------------------------------
# Token operations
# ---------------------------------------------------------------------------

def generate_token(
    user_id: int,
    purpose: str = 'set_password',
    ttl_days: int = 7,
    created_by_id: int | None = None,
    ttl_minutes: int | None = None,
) -> PasswordResetToken:
    """
    Create and persist a new opaque password reset token for the user.

    `ttl_minutes`, when given, wins over `ttl_days`: interactive flows want a
    link that dies in under an hour, batch flows want days.

    Caller is responsible for committing the surrounding transaction or
    calling db.session.commit() afterwards.
    """
    if purpose not in ('set_password', 'reset_password'):
        raise ValueError(f"Invalid purpose: {purpose!r}")

    ttl = (
        timedelta(minutes=ttl_minutes)
        if ttl_minutes is not None
        else timedelta(days=ttl_days)
    )

    raw = secrets.token_urlsafe(48)
    prt = PasswordResetToken(
        token=raw,
        user_id=user_id,
        purpose=purpose,
        expires_at=now_local() + ttl,
        created_by=created_by_id,
    )
    db.session.add(prt)
    db.session.flush()
    return prt


def random_password(length: int = 24) -> str:
    """
    Generate a password nobody is meant to know, type or read.

    Every provisioning path stores one of these so the row is never left with a
    guessable secret; the holder always arrives through a single-use token and
    picks their own.
    """
    alphabet = string.ascii_letters + string.digits + string.punctuation
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def build_reset_password_url(token: str) -> str:
    """
    Absolute URL of the set-password page with the token embedded.

    Pinned to APP_BASE_URL, never to the live request: this link travels by
    e-mail and grants password control over the account until it expires.
    Building it from the request meant the requester chose the host (nginx
    forwards `Host` verbatim and ProxyFix honours `X-Forwarded-Host`), so a
    staff-triggered creation could be made to mail out a valid token pointing
    at an attacker's domain.
    """
    from app.utils.urls import external_url, get_base_url
    try:
        return external_url('pages_auth.reset_password_page', token=token)
    except Exception:
        # No application context: still absolute, still anchored to the server
        # configuration and never to the request.
        return f"{get_base_url()}/reset-password/{token}"


def require_deliverable_email(user: User) -> str:
    """
    Return the account's mailbox, or raise MissingEmail.

    Called before any provisioning path that hands out access exclusively by
    e-mail. Without this the account would be created (or locked out of its old
    password) with no way for anybody to ever get in.
    """
    email = (user.email or '').strip()
    if not email or '@' not in email:
        raise MissingEmail(
            f"La cuenta de {user.first_name} {user.last_name} no tiene un correo "
            f"electrónico válido. Registra un correo antes de generar el enlace "
            f"de contraseña."
        )
    return email


def issue_admin_reset(user_id: int, admin_id: int) -> dict:
    """
    Administrator-triggered password reset.

    Invalidates the stored password (replacing it with an unknowable random
    one), forces `must_change_password`, and issues a single-use set-password
    link that is mailed to the account holder. Nobody — not even the acting
    administrator — ever learns a working credential.

    Caller commits.

    Returns {'user': User, 'expires_at': datetime, 'ttl_minutes': int}.
    Raises TokenNotFound if the user does not exist, MissingEmail if the
    account has no mailbox the link could reach.
    """
    user = User.query.get(user_id)
    if not user:
        raise TokenNotFound("Usuario no encontrado.")

    require_deliverable_email(user)

    # The old password stops working the moment the reset is requested: an
    # admin resets precisely because the current credential is lost or suspect.
    user.password = generate_password_hash(random_password())
    user.must_change_password = True

    prt = generate_token(
        user_id=user.id,
        purpose='reset_password',
        ttl_minutes=INTERACTIVE_TTL_MINUTES,
        created_by_id=admin_id,
    )
    token_link = build_reset_password_url(prt.token)

    # Writes the admin's history row and notifies the affected user; the single
    # e-mail of this flow is queued inside that notification, as before.
    UserHistoryService.log_password_reset(
        user_id=user.id,
        admin_id=admin_id,
        token_link=token_link,
        expires_at=prt.expires_at,
    )

    return {
        'user': user,
        'expires_at': prt.expires_at,
        'ttl_minutes': INTERACTIVE_TTL_MINUTES,
    }


def get_token(token: str) -> PasswordResetToken:
    """
    Fetch a token row.  Raises TokenNotFound / TokenExpired / TokenAlreadyUsed
    so callers get precise error codes for the API response.
    """
    prt = PasswordResetToken.query.filter_by(token=token).first()
    if not prt:
        raise TokenNotFound("Token inválido o inexistente.")
    if prt.is_used:
        raise TokenAlreadyUsed("Este enlace ya fue utilizado.")
    if prt.is_expired:
        raise TokenExpired("Este enlace ha expirado. Solicita uno nuevo.")
    return prt


def consume_token(token: str, new_password: str) -> User:
    """
    Validate token, set new password, mark token used, clear must_change_password.

    Returns the updated User on success.  Caller is expected to commit.
    """
    prt = get_token(token)

    is_valid, msg = _validate_password_strength(new_password)
    if not is_valid:
        raise WeakPassword(msg)

    user = User.query.get(prt.user_id)
    if not user:
        raise TokenNotFound("Usuario asociado al token no existe.")

    user.password = generate_password_hash(new_password)
    user.must_change_password = False
    prt.used_at = now_local()

    UserHistoryService.log_action(
        user_id=user.id,
        admin_id=None,
        action='password_set_via_token',
        details={'purpose': prt.purpose, 'token_id': prt.id},
    )
    return user


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_password_strength(password: str) -> tuple[bool, str]:
    """
    Local copy of auth_api.validate_password_strength to keep the service
    decoupled from the routes layer.
    """
    import re

    if len(password) < 8:
        return False, "La contraseña debe tener al menos 8 caracteres."
    if not re.search(r'[A-Z]', password):
        return False, "La contraseña debe contener al menos una letra mayúscula."
    if not re.search(r'[a-z]', password):
        return False, "La contraseña debe contener al menos una letra minúscula."
    if not re.search(r'\d', password):
        return False, "La contraseña debe contener al menos un número."
    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        return False, "La contraseña debe contener al menos un caracter especial (!@#$%^&*...)."
    return True, "OK"
