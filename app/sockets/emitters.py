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
    Emite un evento a TODOS los clientes conectados (sin sala).

    Sólo es correcto cuando el payload es legible por cualquier usuario
    autenticado. Para `event:changed` NO lo es: el nivel de participante
    (`EventsService.user_may_participate_in_event`) exige publicado y visible
    para estudiantes, y además público-y-de-su-programa, o invitado. Use
    `emit_event_change()`, que aplica ese mismo predicado.
    """
    try:
        from app.extensions import socketio
        socketio.emit(event, payload)
    except Exception as exc:
        logger.warning(f'[WS] emit_broadcast fallo ({event}): {exc}')


def _program_participant_user_ids(program_id: int) -> set:
    """
    Ids de los usuarios con una fila en `user_program` para ese programa.

    Es el inverso exacto de `program_scope_service.program_ids_of_user()`: si
    esa funcion contesta que el programa esta entre los del usuario, el usuario
    esta en este conjunto. Por eso sirve como audiencia del nivel de
    participante sin reimplementar la regla.

    Devuelve set() ante cualquier fallo: un emit no debe tumbar la request.
    """
    try:
        from app import db
        from app.models.user_program import UserProgram

        rows = (
            db.session.query(UserProgram.user_id)
            .filter(UserProgram.program_id == program_id)
            .distinct()
            .all()
        )
        return {row[0] for row in rows}
    except Exception as exc:
        logger.warning(
            f'[WS] no se pudo resolver la audiencia del programa {program_id}: {exc}'
        )
        return set()


def _event_invitee_user_ids(event_id: int) -> set:
    """
    Ids invitados al evento. Una `EventInvitation` solo la escribe quien GESTIONA
    el evento, asi que es una concesion de tercero y vale cualquiera que sea la
    visibilidad o el programa — igual que en
    `EventsService.user_may_participate_in_event`. Cuenta cualquier `status`
    (pending / accepted / rejected): una invitacion rechazada puede
    reconsiderarse y el evento sigue siendo legible.
    """
    try:
        from app import db
        from app.models.event import EventInvitation

        rows = (
            db.session.query(EventInvitation.user_id)
            .filter(EventInvitation.event_id == event_id)
            .distinct()
            .all()
        )
        return {row[0] for row in rows}
    except Exception as exc:
        logger.warning(
            f'[WS] no se pudieron resolver los invitados del evento {event_id}: {exc}'
        )
        return set()


#: Sentinel: la audiencia es "cualquier usuario autenticado". Se distingue de
#: set() ("nadie"), que un `if audience:` confundiria.
EVERYONE = object()


def _event_participant_audience(event, ignore_status: bool = False):
    """
    Audiencia del nivel de PARTICIPANTE de un evento, resuelta a ids.

    Espeja `EventsService.user_may_participate_in_event` — el mismo predicado,
    leido del lado del emisor en vez del lado del lector:

        status != 'published'    -> nadie          (salvo `ignore_status`)
        not visible_to_students  -> nadie
        publico + institucional  -> EVERYONE
        publico + con programa   -> los del programa (+ invitados)
        privado                  -> solo invitados

    `ignore_status=True` sirve para 'archived' / 'concluded': el status ya
    cambio, pero la audiencia que hay que refrescar es la que veia el evento un
    instante antes.

    Returns:
        EVERYONE, o un set de user ids (posiblemente vacio).
    """
    if event is None:
        return set()

    if not ignore_status and getattr(event, 'status', None) != 'published':
        return set()
    if not getattr(event, 'visible_to_students', False):
        return set()

    is_public = getattr(event, 'visibility', None) == 'public'
    program_id = getattr(event, 'program_id', None)

    if is_public and program_id is None:
        return EVERYONE

    audience = set()
    if is_public and program_id is not None:
        audience |= _program_participant_user_ids(program_id)
    audience |= _event_invitee_user_ids(event.id)
    return audience


def _emit_to_participants(payload: dict, audience):
    """
    Entrega `event:changed` a la audiencia resuelta por
    `_event_participant_audience`.

    No hay sala de participantes por programa: las salas se fijan en el
    handshake (`app/sockets/core.py`) y solo los gestores reciben
    `coordinator:program:{pid}`. Asi que con audiencia acotada se abanica sobre
    `user:{id}`. Es viable porque `event:changed` lo dispara una accion
    administrativa puntual, no un flujo de alta frecuencia.
    """
    try:
        from app.extensions import socketio

        if audience is EVERYONE:
            socketio.emit('event:changed', payload)
            return
        for uid in audience:
            socketio.emit('event:changed', payload, room=f'user:{uid}')
    except Exception as exc:
        logger.warning(f'[WS] _emit_to_participants fallo: {exc}')


#: Acciones que sacan al evento de la cartelera. Tras ellas `status` ya no es
#: 'published', asi que el predicado de participante contestaria que no — pero
#: el evento SI estaba en la lista un instante antes y esa pagina necesita
#: refrescarse. Se le manda una senal SIN titulo (ver `emit_event_change`).
_EVENT_EXIT_ACTIONS = frozenset({'archived', 'concluded'})


def emit_event_change(payload: dict, event=None):
    """
    Publica `event:changed` respetando los dos niveles del modulo de eventos.

    Antes esto era un `emit_broadcast()` liso, sin sala. `create_event` lo
    disparaba incondicionalmente con `status` y `visibility` recien llegados del
    cuerpo de la peticion, asi que el titulo de un evento en BORRADOR o PRIVADO
    llegaba a cada aspirante y estudiante conectado en el instante de crearlo.

    Reparto:

    1. GESTION — `emit_to_coordinators(..., program_id)`, payload completo y
       siempre. Coincide con `EventsService.user_may_manage_event`: evento con
       programa -> los coordinadores de ese programa (+ los de alcance global);
       evento institucional -> solo `coordinator:programs:all`, que es el
       alcance global que esa regla exige.
    2. PARTICIPANTE — payload completo solo a quien pasa
       `EventsService.user_may_participate_in_event`, espejado en
       `_event_participant_audience`.
    3. SALIDA DE CARTELERA — en 'archived' / 'concluded' se manda
       `{action, event_id}` SIN titulo a la audiencia que veia el evento hasta
       ese momento. Es todo lo que las paginas publicas necesitan para recargar
       (`events/list.js` y `events/view.js` refetchean contra endpoints que ya
       validan alcance), y sin titulo no se filtra nada de un evento que el
       participante no pudiera ver ya.

    `event=None` — el caso de 'deleted', donde la fila ya no existe y no se
    pueden releer `status`, `visibility` ni `visible_to_students` — es
    deliberadamente conservador: solo gestion. Un participante con la pagina del
    evento abierta se entera en el siguiente fetch, en vez de recibir el titulo
    de algo que quiza nunca fue publico.

    Args:
        payload: dict con 'action', 'event_id', 'program_id' y 'title'.
        event: la instancia Event viva, o None si ya fue borrada.
    """
    emit_to_coordinators('event:changed', payload, payload.get('program_id'))

    if event is None:
        return

    audience = _event_participant_audience(event)
    if audience is EVERYONE or audience:
        _emit_to_participants(payload, audience)
        return

    if payload.get('action') in _EVENT_EXIT_ACTIONS:
        _emit_to_participants(
            {'action': payload.get('action'), 'event_id': payload.get('event_id')},
            _event_participant_audience(event, ignore_status=True),
        )


def emit_admin_user_change(payload: dict, program_ids=None):
    """
    Publica `admin_user:changed` (la lista de /settings/users).

    `role:coordinator` reúne a TODO titular de `coordinator.page.view`, o sea a
    cada program_admin de la institución, y el payload lleva nombre y correo de
    la cuenta afectada. Para una cuenta de personal eso es justo lo que
    `program_scope_service` niega a cualquier llamador sin alcance global
    ("una cuenta de personal no es alumno de nadie"), y para un aspirante o
    estudiante es un dato de alguien que puede no estar en su alcance.

    Reparto:
      - `role:postgraduate_admin` siempre: es el alcance global, ve a todos.
      - `coordinator:program:{pid}` por cada programa de la cuenta afectada,
        y sólo cuando la cuenta es aspirante/estudiante. Un program_admin ve a
        sus propios alumnos en esa pantalla, así que su lista sigue
        refrescándose sola.

    Por qué `role:postgraduate_admin` y no `coordinator:programs:all`
    ----------------------------------------------------------------
    `app/sockets/core.py` no crea ninguna sala llamada "global admin"; las dos
    candidatas son estas. `coordinator:programs:all` se une cuando
    `get_accessible_program_ids()` devuelve None **y** además se tiene
    `coordinator.page.view`, así que depende de dos condiciones y de un permiso
    delegable. `role:postgraduate_admin` se une por `role:{role.name}`, y el
    alcance global es hoy exactamente un rol
    (`User.has_global_program_scope()` lee un permiso DE ROL, nunca una
    delegación). Entre las dos se elige la que nunca puede entregar de más:
    este payload lleva identidad de personal, y equivocarse por defecto sólo
    cuesta un refresco manual de la lista.

    Este es el único emisor de `admin_user:changed`. Sus tres llamadores
    —`admin/users_api.update_user`, `admin/users_api.delete_user` y
    `permission_service.create_social_service_user`— deben pasar por aquí; un
    `socketio.emit('admin_user:changed', …, room='role:coordinator')` suelto
    reabre la fuga entera, porque esa sala es toda la institución.

    Args:
        payload: dict del evento (action, user_id, role, email, full_name…).
        program_ids: iterable de program_id de la cuenta afectada. None o vacío
                     (cuenta de personal, o cuenta sin programa) ⇒ sólo global.
    """
    try:
        from app.extensions import socketio
        socketio.emit('admin_user:changed', payload, room='role:postgraduate_admin')
        for pid in (program_ids or ()):
            socketio.emit('admin_user:changed', payload, room=f'coordinator:program:{pid}')
    except Exception as exc:
        logger.warning(f'[WS] emit_admin_user_change fallo: {exc}')


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
