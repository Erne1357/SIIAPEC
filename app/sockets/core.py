"""
Handlers de conexión / desconexión de Socket.IO.

Salas utilizadas:
  user:{user_id}                 — sala privada por usuario (notificaciones personales)
  role:{role_name}               — sala por rol (eventos dirigidos a un grupo)
  role:coordinator               — sala funcional: todos los que tienen coordinator.page.view
                                   (program_admin, postgraduate_admin, coordinator)
  coordinator:program:{pid}      — sala por programa para coordinadores scoped a ese programa
  coordinator:programs:all       — sala para coordinadores con acceso global (postgraduate_admin)
  deliberation:{program_id}      — sala de deliberación por programa

Los clientes NO necesitan suscribirse manualmente; al conectar el servidor
los une automáticamente a sus salas según su sesión Flask-Login.

La ÚNICA sala que el cliente pide explícitamente es `deliberation:{program_id}`
(evento `join_deliberation`). Por eso ese handler valida permiso Y alcance antes
de unir; el resto de las salas se resuelven en el servidor y no aceptan ningún
id del cliente. Cualquier handler nuevo que reciba un id del cliente debe seguir
la misma regla: `has_permission(...)` responde QUÉ, `program_in_scope(...)`
responde A QUIÉN, y ninguna de las dos implica la otra.

Nota (Phase 9): Las salas SocketIO se mantienen basadas en roles porque el
broadcasting es notificación, no control de acceso. La sala role:coordinator
se asigna por permiso (coordinator.page.view) en lugar de solo por role.name
para que program_admin y postgraduate_admin también la reciban.

Nota (Fase 3 gap resuelto): coordinator:program:{pid} y coordinator:programs:all
permiten emitir eventos user-scoped (admission:status_changed, permanence:status_changed,
extension:decided) solo a los coordinadores del programa relevante, en lugar de
inundar a todos los coordinadores vía role:coordinator.
"""

import logging
from flask_login import current_user
from flask_socketio import join_room, disconnect

logger = logging.getLogger(__name__)

#: Capability required to sit in `deliberation:{program_id}`. Same codename that
#: gates the HTTP endpoint publishing the same rows
#: (`GET /api/v1/deliberation/program/<id>/applicants`), so the socket cannot
#: become a cheaper door to data the REST layer protects.
DELIBERATION_ROOM_PERMISSION = 'deliberation.api.list_applicants'


def register_core_handlers(socketio):

    @socketio.on('connect')
    def handle_connect():
        """
        Evento de conexión.
        Se llama automáticamente cuando el cliente abre el socket.
        Rechaza la conexión si el usuario no está autenticado.
        """
        if not current_user.is_authenticated:
            logger.warning('[WS] Conexión rechazada: usuario no autenticado')
            return False  # Desconecta al cliente

        # Sala personal
        join_room(f'user:{current_user.id}')

        # Sala por rol
        if current_user.role:
            join_room(f'role:{current_user.role.name}')

        # Sala funcional de coordinación: todos los que gestionan programas
        # (program_admin, postgraduate_admin, coordinator)
        if current_user.has_permission('coordinator.page.view'):
            join_room('role:coordinator')

            # Salas program-scoped para eventos user-targeted
            # None = acceso global (postgraduate_admin) → sala catch-all
            # set  = acceso scoped → una sala por programa accesible
            try:
                accessible_pids = current_user.get_accessible_program_ids()
                if accessible_pids is None:
                    join_room('coordinator:programs:all')
                else:
                    for pid in accessible_pids:
                        join_room(f'coordinator:program:{pid}')
            except Exception as exc:
                logger.warning(f'[WS] No se pudieron resolver programas accesibles: {exc}')

        logger.debug(
            f'[WS] Conectado: user={current_user.id} '
            f'role={current_user.role.name if current_user.role else None}'
        )

    @socketio.on('disconnect')
    def handle_disconnect():
        if current_user.is_authenticated:
            logger.debug(f'[WS] Desconectado: user={current_user.id}')

    @socketio.on('join_deliberation')
    def handle_join_deliberation(data):
        """
        Permite a un coordinador/admin unirse a la sala de deliberación
        de un programa específico para recibir actualizaciones en tiempo real.

        Payload esperado: { "program_id": 42 }

        This is the only room the client picks: `program_id` arrives from the
        browser, so the same two questions every HTTP route asks must be asked
        here, in the same order and with the same helpers.

          1. WHAT  — `deliberation.api.list_applicants`, the codename that gates
                     `GET /api/v1/deliberation/program/<id>/applicants`. The room
                     carries that endpoint's data (applicant name + decision), so
                     it must not be reachable with a weaker capability.
          2. TO WHOM — `program_in_scope()`, the shared predicate. Holding the
                     permission never implies reach: a program_admin holds it for
                     every program by virtue of their role, and the room is where
                     `deliberation:updated` publishes named admission decisions.

        Without both checks any authenticated account — an applicant included —
        could emit `{"program_id": N}` and receive every decision taken in any
        program, live and by name.

        Fails closed: a malformed payload, a missing permission, an out-of-scope
        program or an unexpected error all end in `return False` without a join.
        The return value is only the ack for this event; unlike `connect`, it does
        not drop the socket, and it must not — the caller may legitimately be a
        coordinator asking about one program too many.
        """
        if not current_user.is_authenticated:
            return False

        if not isinstance(data, dict):
            return False

        try:
            program_id = int(data.get('program_id'))
        except (TypeError, ValueError):
            return False

        try:
            from app.services import program_scope_service as scope_service

            if not current_user.has_permission(DELIBERATION_ROOM_PERMISSION):
                logger.warning(
                    f'[WS] join_deliberation denegado (sin permiso): '
                    f'user={current_user.id} program_id={program_id}'
                )
                return False

            if not scope_service.program_in_scope(current_user, program_id):
                logger.warning(
                    f'[WS] join_deliberation denegado (fuera de alcance): '
                    f'user={current_user.id} program_id={program_id}'
                )
                return False
        except Exception as exc:
            logger.warning(
                f'[WS] join_deliberation denegado (error al validar): '
                f'user={current_user.id} program_id={program_id} ({exc})'
            )
            return False

        join_room(f'deliberation:{program_id}')
        logger.debug(
            f'[WS] user={current_user.id} joined deliberation:{program_id}'
        )
        return True
