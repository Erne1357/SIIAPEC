"""
Helpers para emisión de eventos Socket.IO.

Centraliza patrones repetidos de emit para mantener consistencia y evitar
boilerplate try/except en cada servicio.

Todos los emits envuelven errores silenciosamente (fire-and-forget) para
que fallas de Redis/socketio no tumben la request original.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def emit_user_and_coordinators(event: str, payload: dict, user_id: int, program_id: Optional[int]):
    """
    Emite un evento user-targeted al usuario afectado y a los coordinadores
    del programa (scoped) + coordinadores globales (coordinator:programs:all).

    Útil para admission:status_changed, permanence:status_changed,
    extension:decided: el usuario necesita la notificación y los coordinadores
    del programa correspondiente también deben verla en tiempo real.

    Args:
        event: nombre del evento socket (ej. 'admission:status_changed')
        payload: diccionario serializable con los datos del evento
        user_id: usuario afectado (sala user:{id})
        program_id: programa al que pertenece el evento. Si es None, no se
                    emite a coordinator:program:{pid} (solo global y usuario).
    """
    try:
        from app.extensions import socketio
        socketio.emit(event, payload, room=f'user:{user_id}')
        socketio.emit(event, payload, room='coordinator:programs:all')
        if program_id is not None:
            socketio.emit(event, payload, room=f'coordinator:program:{program_id}')
    except Exception as exc:
        logger.warning(f'[WS] emit_user_and_coordinators fallo ({event}): {exc}')


def emit_to_coordinators(event: str, payload: dict, program_id: Optional[int]):
    """
    Emite un evento solo a coordinadores (sin user:{id}).

    Args:
        event: nombre del evento
        payload: diccionario serializable
        program_id: si None, solo a coordinator:programs:all; si int, también
                    a coordinator:program:{pid}.
    """
    try:
        from app.extensions import socketio
        socketio.emit(event, payload, room='coordinator:programs:all')
        if program_id is not None:
            socketio.emit(event, payload, room=f'coordinator:program:{program_id}')
    except Exception as exc:
        logger.warning(f'[WS] emit_to_coordinators fallo ({event}): {exc}')


def emit_broadcast(event: str, payload: dict):
    """
    Emite un evento a todos los clientes conectados (sin sala).

    Útil para cambios en páginas públicas (event:changed, program:changed).
    """
    try:
        from app.extensions import socketio
        socketio.emit(event, payload)
    except Exception as exc:
        logger.warning(f'[WS] emit_broadcast fallo ({event}): {exc}')


def emit_to_user(event: str, payload: dict, user_id: int):
    """Emite un evento solo a la sala user:{user_id}."""
    try:
        from app.extensions import socketio
        socketio.emit(event, payload, room=f'user:{user_id}')
    except Exception as exc:
        logger.warning(f'[WS] emit_to_user fallo ({event}): {exc}')


def disconnect_user_sockets(user_id: int, namespace: str = '/') -> int:
    """
    Close every live Socket.IO connection belonging to `user_id`.

    Why this exists
    ---------------
    The rooms a client sits in are decided once, on the connect handshake
    (app/sockets/core.py), and are then held server-side for the lifetime of the
    sid. Every emitter above pushes into those rooms without re-resolving the
    user, and Flask's before_request handlers never run for socket frames, so
    the 15-minute idle expiry does not apply either. Deactivating an account
    therefore changed nothing for an already-open tab: a deactivated
    coordinator kept receiving live deliberation, permanence and notification
    payloads for as long as the tab stayed open. The account has to be evicted
    explicitly.

    Mechanism
    ---------
    `user:{id}` is the same private room emit_to_user() publishes to, so its
    participant list is exactly the set of sids to kill. Once the socket is
    closed, the client's automatic reconnect re-runs the handshake, which calls
    load_user — deactivated resolves to anonymous and the connection is
    refused. That is what makes the eviction stick.

    Limitation
    ----------
    python-socketio keeps its room→sid table inside each server process (Redis
    carries the *emits*, not the room membership), so this only sees the sids
    held by the process that runs it. In this deployment the web tier is a
    single gunicorn eventlet worker and the Celery processes hold no client
    connections, so that is every live socket. If the web tier is ever scaled
    past one worker, a connection parked on a sibling worker survives until it
    reconnects or the pod recycles; closing that gap needs a revocation marker
    the emit path checks, not a wider disconnect call.

    Returns the number of sids the eviction was issued for — 0 when the user
    has no live connection in this process, or when Socket.IO has not been
    initialised at all.
    """
    try:
        from app.extensions import socketio

        server = getattr(socketio, 'server', None)
        if server is None:
            return 0

        room = f'user:{user_id}'
        # get_participants() yields (sid, eio_sid) on python-socketio >= 5.3 and
        # bare sids on older releases; it iterates over a copy, so disconnecting
        # inside the loop is safe.
        participants = list(server.manager.get_participants(namespace, room))
        sids = [p[0] if isinstance(p, tuple) else p for p in participants]

        for sid in sids:
            try:
                server.disconnect(sid, namespace=namespace)
            except Exception as exc:
                logger.warning(f'[WS] disconnect fallo (sid={sid}): {exc}')

        if sids:
            logger.info(
                f'[WS] Sesiones socket cerradas para user={user_id}: {len(sids)}'
            )
        return len(sids)
    except Exception as exc:
        logger.warning(f'[WS] disconnect_user_sockets fallo (user={user_id}): {exc}')
        return 0
