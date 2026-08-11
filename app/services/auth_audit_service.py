# app/services/auth_audit_service.py
"""
Authentication audit trail.

WHERE THESE EVENTS GO
---------------------
Into the ``log`` table (``app/models/log.py``), never into ``user_history``.
The reasoning lives in the model docstring; the short version is that
``user_history`` is staff-readable, is eagerly serialized by
``User.to_dict(include_sensitive=True)``, and its ``admin_id`` column is
auto-filled from ``current_user`` — all three are wrong for an event produced
by an unauthenticated request.

Every event is also written to the application logger, so the container log
holds the full-fidelity trail (including the events this module deliberately
does not persist).

WHAT IS STORED, AND WHAT IS DELIBERATELY NOT
--------------------------------------------
* **Client address — network prefix only** (IPv4 ``/24``, IPv6 ``/48``).
  The inner nginx now derives ``real_ip`` from the private ranges, so
  ``request.remote_addr`` is the subscriber's actual address: a personal datum
  under LFPDPPP, and exactly the kind of PII that must not be accumulated in a
  queryable table that ends up in every database dump. The prefix still answers
  the questions an audit asks — "one source or many?", "campus network or the
  open internet?", "did the successful login come from the same place as the
  failures?" — without pinning a household. The **full address is emitted on
  the application logger**, which is ops-only, short-lived and not reachable
  from the app, so incident response loses nothing.
* **User-Agent — truncated** to ``_UA_MAX_LEN`` characters. Enough to tell a
  browser from ``curl`` or a scripted client, and it stops a hostile client
  from writing kilobytes per attempt into a table an unauthenticated caller can
  append to.
* **Username — truncated.** An attempt against a name that does not exist has
  no ``user_id`` to point at, and knowing *which* names are being sprayed is
  the whole point of the trail.

VOLUME CAP
----------
A failed attempt does **not** produce a row. Per username and per window we
write at most two: one on the **first** failure (``auth.login_failed``) and one
when the failure count reaches ``LOGIN_THROTTLE_USER_MAX``
(``auth.login_throttled``). Everything in between is logger-only. That is what
an investigation needs — when the attack on this account started, and that it
went far enough for the throttle to start delaying it — without letting an
unauthenticated caller decide how many rows to insert.

This counter is **not** the throttle's counter and is not meant to be. The
throttle counts *every* attempt against a username, because that is what it
must do to decide whether to delay one; the audit counts *failures*, because
that is the thing worth recording. They share the tunables so the second row
lands where the throttle starts acting, and nothing else.

The counter is process-local on purpose:

* the deployment is ``gunicorn --worker-class eventlet --workers 1``, so
  process-local *is* global;
* it adds **no I/O** to the pre-authentication path — nothing new to time,
  nothing new to fail. Reusing the Redis throttle counter would have coupled
  the audit trail to a dependency that is explicitly allowed to fail open;
* if the worker count is ever raised to N the cap simply becomes N times as
  loose, which is still bounded.

It is bounded by an LRU of ``_SUBJECTS_MAX`` usernames, so a spray across many
invented names cannot grow it without limit.

Two accepted consequences, stated rather than hidden: a worker restart resets
the windows (at most one extra row per account), and the window is keyed by
username only, so two different sources attacking the same account share one
budget — the logger line still records both.

TIMING
------
The login route pays a password hash even for a username that does not exist,
so response time cannot reveal which usernames are real. That guarantee is only
worth something if the *audit* is symmetric too — a Postgres round trip is an
order of magnitude above the hash delta being hidden, so "known username =>
extra INSERT + COMMIT" would be a louder oracle than the one being closed.

Therefore ``record_login_failure`` runs the **same code path** whether or not a
user was found: same counter update, same payload construction, same logger
line, and the decision to insert depends on the window counter alone — never on
whether ``user_id`` is ``None``. A row for an unknown username is written with
``user_id = NULL`` (the column is nullable) rather than skipped.
"""

import hashlib
import json
import logging
import os
from collections import OrderedDict
from ipaddress import ip_address, ip_network, IPv4Address
from time import monotonic
from typing import Any, Dict, Optional, Tuple

from flask import current_app

from app import db
from app.models.log import Log

# Used only when there is no application context (never on a request path).
_fallback_logger = logging.getLogger(__name__)

# ── Truncation limits ─────────────────────────────────────────────────────────
_UA_MAX_LEN = 120
_USERNAME_MAX_LEN = 64
_REASON_MAX_LEN = 40

# ── Process-local failure windows ─────────────────────────────────────────────
# key -> (window_start_monotonic, failures_in_window). LRU-capped.
_SUBJECTS_MAX = 4096
_failure_windows: "OrderedDict[str, Tuple[float, int]]" = OrderedDict()

# Fallbacks for the throttle tunables. They are re-read from config/env on every
# call (same resolution order as app/utils/rate_limit.py) so the "threshold
# tripped" row lines up with the window the throttle actually enforces. The
# values are duplicated rather than imported because rate_limit exposes them
# only through a private helper.
_WINDOW_DEFAULT = 15 * 60
_THRESHOLD_DEFAULT = 5


def _logger():
    """The app logger when there is a context, a module logger otherwise."""
    try:
        return current_app.logger
    except RuntimeError:
        return _fallback_logger


def _setting(name: str, default: int) -> int:
    """Resolve a tunable: app config -> environment -> default. Never raises."""
    value = None
    try:
        value = current_app.config.get(name)
    except RuntimeError:
        value = None
    if value is None:
        value = os.getenv(name)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _subject_key(username: str) -> str:
    """
    Window key for an attempted username.

    Hashed so the in-memory map never holds a readable list of the accounts
    under attack, and built identically for existing and non-existing usernames
    so the two branches cost the same.
    """
    digest = hashlib.sha256((username or '').strip().lower().encode('utf-8')).hexdigest()
    return digest[:32]


def _count_failure(username: str) -> int:
    """Count this failure inside the current window and return the new total."""
    window = _setting('LOGIN_THROTTLE_USER_WINDOW', _WINDOW_DEFAULT)
    key = _subject_key(username)
    now = monotonic()

    started, count = _failure_windows.get(key, (None, 0))
    if started is None or (now - started) >= window:
        # Fixed window, like the throttle: the first failure sets the start and
        # later ones never extend it, so an attacker cannot keep an account in
        # "already reported" state forever with one attempt per window.
        started, count = now, 0

    count += 1
    _failure_windows[key] = (started, count)
    _failure_windows.move_to_end(key)
    while len(_failure_windows) > _SUBJECTS_MAX:
        _failure_windows.popitem(last=False)
    return count


def _clear_failures(username: str) -> None:
    """Drop the window after a successful login (mirrors clear_login_failures)."""
    _failure_windows.pop(_subject_key(username), None)


def _truncate(value: Optional[str], limit: int) -> str:
    return (value or '')[:limit]


def _ip_prefix(ip: Optional[str]) -> str:
    """
    Network prefix of the client address: /24 for IPv4, /48 for IPv6.

    See the module docstring — the full address is PII and stays in the
    application log only.
    """
    try:
        addr = ip_address((ip or '').strip())
    except ValueError:
        return 'unknown'
    prefix = 24 if isinstance(addr, IPv4Address) else 48
    return str(ip_network(f'{addr}/{prefix}', strict=False))


class AuthAuditService:
    """
    Records authentication events. Every method is best-effort and never
    raises: auditing must not be able to turn a login into a 500.
    """

    ACTION_LOGIN_SUCCESS = 'auth.login_success'
    ACTION_LOGIN_FAILED = 'auth.login_failed'
    ACTION_LOGIN_THROTTLED = 'auth.login_throttled'

    # ── Public API ────────────────────────────────────────────────────────────

    @staticmethod
    def record_login_success(user_id: int, username: str, ip: str, user_agent: str) -> None:
        """
        Record a successful login. Always persisted: it is the baseline an
        investigation compares failures against, and its volume is bounded by
        real users rather than by an attacker.
        """
        _clear_failures(username)
        _logger().info(
            f"[auth] Inicio de sesión correcto "
            f"(user_id={user_id}, username={_truncate(username, _USERNAME_MAX_LEN)!r}, "
            f"ip={ip}, ua={_truncate(user_agent, _UA_MAX_LEN)!r})"
        )
        AuthAuditService._write(
            action=AuthAuditService.ACTION_LOGIN_SUCCESS,
            user_id=user_id,
            payload=AuthAuditService._payload(username, ip, user_agent),
        )

    @staticmethod
    def record_login_failure(
        username: str,
        ip: str,
        user_agent: str,
        reason: str,
        user_id: Optional[int] = None,
    ) -> None:
        """
        Record a failed login attempt.

        ``user_id`` is ``None`` when the username does not exist. Nothing below
        branches on it — see the TIMING section of the module docstring.

        ``reason`` is one of ``unknown_user`` | ``bad_password`` |
        ``inactive_account``. It is stored, but it is never sent to the client:
        the route answers every one of them with the same body.
        """
        count = _count_failure(username)
        threshold = _setting('LOGIN_THROTTLE_USER_MAX', _THRESHOLD_DEFAULT)

        payload = AuthAuditService._payload(username, ip, user_agent)
        payload['reason'] = _truncate(reason, _REASON_MAX_LEN)
        payload['failed_attempts'] = count

        # Always logged, whether or not a row is written: the container log is
        # the full-fidelity trail, and the cost is identical on both branches.
        _logger().warning(
            f"[auth] Intento de inicio de sesión fallido "
            f"(motivo={reason}, intento={count}, cuenta_existente={user_id is not None}, "
            f"username={_truncate(username, _USERNAME_MAX_LEN)!r}, ip={ip}, "
            f"ua={_truncate(user_agent, _UA_MAX_LEN)!r})"
        )

        if count == 1:
            action = AuthAuditService.ACTION_LOGIN_FAILED
        elif count == threshold:
            # Exactly at the threshold, never above it: if the throttle is
            # degraded (Redis down => fail open) the counter keeps climbing and
            # this must still produce one row, not one per attempt.
            action = AuthAuditService.ACTION_LOGIN_THROTTLED
        else:
            return

        AuthAuditService._write(action=action, user_id=user_id, payload=payload)

    # ── Internals ─────────────────────────────────────────────────────────────

    @staticmethod
    def _payload(username: str, ip: str, user_agent: str) -> Dict[str, Any]:
        """Build the stored payload. Same shape for every event."""
        return {
            'username': _truncate(username, _USERNAME_MAX_LEN),
            'ip_prefix': _ip_prefix(ip),
            'user_agent': _truncate(user_agent, _UA_MAX_LEN),
        }

    @staticmethod
    def _write(action: str, user_id: Optional[int], payload: Dict[str, Any]) -> None:
        """
        Insert one audit row and commit it.

        The audit owns its own transaction: it is an independent, best-effort
        write, and the caller must not have to reason about a rollback that
        auditing caused. On failure the event survives on the logger.
        """
        try:
            db.session.add(Log(
                user_id=user_id,
                action=action,
                description=json.dumps(payload, ensure_ascii=False),
            ))
            db.session.commit()
        except Exception as e:
            try:
                db.session.rollback()
            except Exception:
                pass
            _logger().error(f"[auth] No se pudo registrar el evento de auditoría '{action}': {e}")
