"""
Throttling of unauthenticated login attempts, backed by Redis.

This module deliberately mirrors ``app/utils/session_tracker.py``: same way of
building the client (a fresh ``redis.from_url`` per call, read from
``current_app.config['REDIS_URL']``), same ``siiap:`` key convention, and the
same circuit breaker so a Redis outage costs one timeout instead of one per
request.

Two scopes, and they are NOT symmetric:

  * username scope — **the only one that can deny a request**. It is the
    reliable key: it survives NAT, it cannot be forged by the client, and it is
    the thing an attacker must actually iterate over to spray or stuff
    credentials. Its policy is an escalating per-account delay (about 1 min,
    then 5, then 15), never a lockout: a correct password clears it instantly
    and no administrator is ever needed to unlock an account.
  * IP scope — **counts and logs, never blocks**. See ``client_ip()``.

Usage from the login route::

    verdict = register_login_attempt(username, ip)
    if verdict is not None:
        ...  # 429 + Retry-After: verdict.retry_after
    ...
    clear_login_failures(username)        # on a successful login

COUNTING HAPPENS AT THE GATE — THIS IS LOAD-BEARING
---------------------------------------------------
``register_login_attempt`` increments before it decides, and decides from the
value its own ``INCR`` returned. The previous version was a read-only check
followed by a separate ``register_failed_login`` after the password had been
verified; with one eventlet worker every Redis call, every ``User.query`` and
every scrypt verification in between is a greenlet yield point, so N concurrent
POSTs for one username all read ``count = 0`` and all sailed through. "5 per 15
minutes" was really "5 rounds of unbounded size". Incrementing first closes
that: N concurrent attempts get N distinct counter values, so at most
``LOGIN_THROTTLE_USER_MAX`` of them can ever be under budget.

The consequence is that the counter counts *attempts*, not confirmed failures —
a successful login is counted too. That is why ``clear_login_failures`` must be
called on every successful login: it deletes the counter and any active
penalty, so a legitimate user never carries a count forward.

FAILURE MODE — FAIL OPEN, ON PURPOSE
------------------------------------
If Redis is unreachable (or the breaker is open) every function here degrades
to "no opinion": ``register_login_attempt`` returns ``None`` (allow) and the
recording functions become no-ops. Nothing raises, so a Redis outage can never
turn into a 500 on the login endpoint.

Why fail open rather than closed:
  * Redis is already a soft dependency everywhere else in this app — the online
    tracker degrades to zero, Socket.IO falls back to ``message_queue=None``.
    Making login the one hard dependency would mean a Redis restart locks every
    student, coordinator and admin out of the system, and with the 5-minute
    circuit breaker each outage would keep locking them out in 5-minute blocks.
  * Admissions work is deadline-shaped; a total login outage is a certain,
    immediate harm, while the credential-spraying it would prevent is a
    possible harm that still has to get past password hashing.
  * The audit trail does not depend on Redis: ``auth.login_failed`` rows are
    written to the ``log`` table in Postgres regardless (see
    app/services/auth_audit_service.py), so an attack mounted during a Redis
    outage is still visible after the fact. Those rows are capped per username
    per window rather than written one per attempt, so the trail survives a
    flood without becoming one.
The tradeoff is accepted knowingly: whoever can take Redis down can also take
the throttle down. If that ever stops being acceptable, the change is one
``return`` in ``register_login_attempt`` — not a redesign.
"""

import hashlib
import logging
import os
import time
from typing import NamedTuple, Optional, Tuple

import redis as redis_lib
from flask import current_app, request

logger = logging.getLogger(__name__)

# Redis key prefix — one namespace for every scope, matching 'siiap:online:'.
_KEY_PREFIX = 'siiap:loginfail:'

# ── Circuit breaker ───────────────────────────────────────────────────────────
# Same contract as session_tracker: after a failure, skip every attempt for
# _CIRCUIT_RESET seconds instead of paying the socket timeout on each request.
_circuit_open_until: float = 0.0
_CIRCUIT_RESET = 300  # seconds (5 min)

# ── Tunables ──────────────────────────────────────────────────────────────────
# Read at call time from app config, then the environment, then these defaults.
# They are NOT declared in app/config.py so that the throttle keeps working on a
# deployment that never heard of them; setting the env var in docker/.env is
# enough to re-tune without a code change.
_DEFAULTS = {
    # Attempts allowed per username before the escalating delay starts.
    'LOGIN_THROTTLE_USER_MAX': 5,

    # Lifetime of the username counter, from its first attempt. FIXED window:
    # it is never extended by later attempts, so the escalation always resets on
    # its own and an attacker cannot hold somebody's account down forever with
    # one attempt per window.
    'LOGIN_THROTTLE_USER_WINDOW': 15 * 60,

    # Observation budget for a client address. Crossing it only produces a
    # WARNING log line — see client_ip() for why it must never deny.
    'LOGIN_THROTTLE_IP_MAX': 200,
    'LOGIN_THROTTLE_IP_WINDOW': 5 * 60,
}

# Escalating delay applied to a username that is over budget: the first attempt
# past LOGIN_THROTTLE_USER_MAX waits ~1 min, the next ~5 min, every one after
# that ~15 min. Override with LOGIN_THROTTLE_USER_DELAYS='60,300,900'.
_DEFAULT_USER_DELAYS: Tuple[int, ...] = (60, 5 * 60, 15 * 60)


class ThrottleVerdict(NamedTuple):
    """Returned when an attempt must be rejected before touching the database."""
    scope: str        # always 'username' — the IP scope never denies
    retry_after: int  # seconds, for the Retry-After header


def _setting(name: str) -> int:
    """Resolve a tunable: app config → environment → default."""
    value = None
    try:
        value = current_app.config.get(name)
    except RuntimeError:
        # No app context (should not happen on a request path, but never raise).
        value = None
    if value is None:
        value = os.getenv(name)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return _DEFAULTS[name]
    return parsed if parsed > 0 else _DEFAULTS[name]


def _user_delays() -> Tuple[int, ...]:
    """Resolve the escalation ladder: app config → environment → default."""
    raw = None
    try:
        raw = current_app.config.get('LOGIN_THROTTLE_USER_DELAYS')
    except RuntimeError:
        raw = None
    if raw is None:
        raw = os.getenv('LOGIN_THROTTLE_USER_DELAYS')
    if raw is None:
        return _DEFAULT_USER_DELAYS

    parts = raw if isinstance(raw, (list, tuple)) else str(raw).split(',')
    try:
        values = tuple(int(str(p).strip()) for p in parts if str(p).strip())
    except (TypeError, ValueError):
        return _DEFAULT_USER_DELAYS
    values = tuple(v for v in values if v > 0)
    return values or _DEFAULT_USER_DELAYS


def _get_client() -> redis_lib.Redis:
    """Return a Redis client. Raises while the circuit breaker is open."""
    if time.monotonic() < _circuit_open_until:
        raise redis_lib.ConnectionError('Redis circuit breaker open')
    url = current_app.config.get('REDIS_URL', 'redis://redis:6379/0')
    return redis_lib.from_url(url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2)


def _open_circuit(e: Exception, context: str) -> None:
    global _circuit_open_until
    _circuit_open_until = time.monotonic() + _CIRCUIT_RESET
    logger.warning(f'[rate_limit] {context}: {e} — throttle deshabilitado por {_CIRCUIT_RESET}s')


def _handle_error(e: Exception, context: str) -> None:
    """Swallow the error, arming the breaker unless it is the breaker's own."""
    if 'circuit breaker' not in str(e):
        _open_circuit(e, context)


def _username_digest(username: str) -> str:
    """
    Stable, non-reversible handle for a username.

    The username is hashed so Redis never stores a list of the account names
    someone is attacking, and so exotic characters cannot break the key space.
    The readable record of who was attacked lives in the ``log`` table, not
    here.
    """
    return hashlib.sha256((username or '').strip().lower().encode('utf-8')).hexdigest()[:32]


def _user_key(username: str) -> str:
    """Counter key: how many attempts this account has spent in the window."""
    return f'{_KEY_PREFIX}u:{_username_digest(username)}'


def _block_key(username: str) -> str:
    """
    Penalty key: exists only while an account is serving its delay, and its TTL
    IS the remaining delay. Separate from the counter on purpose — see
    register_login_attempt for why a penalty must never be re-armed by the
    attempts it is already rejecting.
    """
    return f'{_KEY_PREFIX}ub:{_username_digest(username)}'


def _ip_key(ip: str) -> str:
    return f'{_KEY_PREFIX}ip:{ip or "unknown"}'


def client_ip() -> str:
    """
    Best available client address for this request.

    Uses request.remote_addr, i.e. whatever ProxyFix resolved from the trusted
    hop count — never a raw X-Forwarded-For read, which any client can forge and
    which would let an attacker exhaust another address's budget.

    Topology: edge nginx (TLS, real client) → published loopback port → the
    container nginx, which sets real_ip from the private ranges with
    real_ip_recursive off and forwards a single 'X-Forwarded-For: $remote_addr'
    entry, which ProxyFix (x_for=1) turns into remote_addr. So this is normally
    the real first-hop client. It is still only used for counting: see
    register_login_attempt.
    """
    return request.remote_addr or 'unknown'


def register_login_attempt(username: str, ip: str) -> Optional[ThrottleVerdict]:
    """
    Count this login attempt and decide whether it may proceed.

    Returns a ThrottleVerdict when the attempt must be answered with 429, or
    None when it may go on to the database and the password check (including
    whenever Redis is unavailable — see the module docstring on failing open).

    Call this BEFORE the User lookup and before any password hashing, and call
    ``clear_login_failures`` after a successful login so the attempt this
    function just counted does not survive it.

    WHY THE IP SCOPE COUNTS BUT NEVER BLOCKS
    ----------------------------------------
    Per-IP limiting already happens at the edge nginx, where the address is the
    real first-hop client and the limit is enforced before a request costs this
    process anything (a general 20r/s zone plus a tight zone on the login
    route). Duplicating it here, behind a NAT hop, can only add false
    positives: the campus puts many legitimate users behind one public address,
    and if the edge or the real_ip configuration ever regresses, every request
    in the world arrives with the docker bridge gateway as its source — a
    single shared bucket whose only possible effect is to 429 the entire user
    base at once. A throttle that can take the whole system down is strictly
    worse than no throttle, so this scope is observation only: crossing the
    budget emits a WARNING so an operator can see a spray in progress, and
    nothing else.

    The username scope is the one that stops credential spraying, and by the
    owner's decision it is a per-account *delay*, never a lockout: escalating
    Retry-After, a fixed window that always expires on its own, cleared
    instantly by one correct password, and never requiring an administrator.
    """
    global _circuit_open_until

    user_max = _setting('LOGIN_THROTTLE_USER_MAX')
    user_window = _setting('LOGIN_THROTTLE_USER_WINDOW')
    ip_max = _setting('LOGIN_THROTTLE_IP_MAX')
    ip_window = _setting('LOGIN_THROTTLE_IP_WINDOW')
    delays = _user_delays()

    try:
        client = _get_client()
        ukey, bkey, ikey = _user_key(username), _block_key(username), _ip_key(ip)

        # Step 1 — is this account already serving a penalty?
        # Attempts made during a penalty are NOT counted and do NOT re-arm it.
        # If they did, an attacker hammering one account would keep pushing the
        # ladder back to its top rung forever, which is the lockout this design
        # refuses to have. Reading here cannot race the decision below: the
        # penalty key only ever exists because some INCR already returned a
        # value over budget.
        block_ttl = client.ttl(bkey)
        _circuit_open_until = 0.0  # success → reset the breaker
        if block_ttl is not None and block_ttl > 0:
            return ThrottleVerdict(scope='username', retry_after=max(int(block_ttl), 1))

        # Step 2 — count first, decide from what the counter returned.
        # SET NX + INCR pipelined: the SET only lands when the key is absent, so
        # the TTL is attached at creation and there is no INCR-without-EXPIRE
        # window. Both commands are atomic, so concurrent greenlets always get
        # distinct counter values.
        pipe = client.pipeline()
        pipe.set(ukey, 0, ex=user_window, nx=True)
        pipe.incr(ukey)
        pipe.ttl(ukey)
        pipe.set(ikey, 0, ex=ip_window, nx=True)
        pipe.incr(ikey)
        _, u_count, u_ttl, _, i_count = pipe.execute()

        u_count = int(u_count or 0)
        i_count = int(i_count or 0)

        # Repair a counter left without a TTL by an older build or a process
        # killed mid-write; otherwise it would pin the account forever. The IP
        # key gets the same treatment: it cannot deny anyone, but without a TTL
        # it climbs forever and the spray WARNING below then fires at every
        # multiple of ip_max until someone deletes the key by hand.
        if u_ttl is None or int(u_ttl) < 0:
            client.expire(ukey, user_window)
        if client.ttl(ikey) < 0:
            client.expire(ikey, ip_window)

        # IP scope: observe, warn, allow. Warn once per full budget so a spray
        # is visible in the log without the log itself becoming the flood.
        if i_count >= ip_max and i_count % ip_max == 0:
            logger.warning(
                f'[rate_limit] {i_count} intentos de login fallidos desde {ip} '
                f'en los últimos {ip_window}s (presunto ataque de fuerza bruta). '
                f'No se bloquea: el límite por IP vive en el nginx de borde.'
            )

        if u_count <= user_max:
            return None

        # Over budget: arm the next rung of the ladder. rung 1 → delays[0], and
        # so on, capped at the last entry.
        rung = min(u_count - user_max, len(delays))
        delay = delays[rung - 1]
        client.set(bkey, u_count, ex=delay)
        logger.warning(
            f'[rate_limit] Cuenta con demasiados intentos de inicio de sesión: '
            f'{u_count} intentos, se aplica una espera de {delay}s '
            f'(escalón {rung}/{len(delays)}, ip={ip}). '
            f'Se libera sola y una contraseña correcta la limpia de inmediato.'
        )
        return ThrottleVerdict(scope='username', retry_after=delay)

    except Exception as e:
        _handle_error(e, 'Error al registrar el intento de inicio de sesión')
        return None  # fail open


def clear_login_failures(username: str) -> None:
    """
    Drop the username counter and any active penalty after a successful login.

    This is what keeps the policy a delay instead of a lockout, and what stops
    the attempt counted at the gate from outliving a successful login. The IP
    counter is deliberately left alone: it never blocks anyone, and clearing it
    on every success would blind the spray warning exactly while a spray that
    guessed one password is in progress. Never raises.
    """
    global _circuit_open_until
    try:
        client = _get_client()
        client.delete(_user_key(username), _block_key(username))
        _circuit_open_until = 0.0
    except Exception as e:
        _handle_error(e, 'Error al limpiar el contador de intentos')
