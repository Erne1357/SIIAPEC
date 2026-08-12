# app/routes/api/admin/users_admin_api.py
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from sqlalchemy import and_, not_, or_
from app import db
from app.models.user import User
from app.models.user_history import UserHistory
from app.models.program import Program
from app.models.user_program import UserProgram
from app.services.user_history_service import UserHistoryService
from app.services import program_scope_service as scope_service
from app.utils.permissions import permission_required, program_scope_required
from app.utils.validators import (
    EMAIL_MAX_LENGTH,
    InputValidationError,
    validate_person_name,
    validate_short_text,
)
from app.utils.datetime_utils import now_local
import json
import re

api_admin_users = Blueprint("api_admin_users", __name__, url_prefix="/api/v1/admin/users")

def _sanitize(s: str | None) -> str | None:
    """Sanitiza strings eliminando espacios y retornando None si está vacío"""
    if s is None:
        return None
    s = s.strip()
    return s or None


def _validate_if_changed(raw, current, validator, **kwargs):
    """
    Run `validator` only when the submitted value differs from the stored one.

    Resubmitting the value already in the column is a no-op, so it is returned
    untouched. This keeps rows that predate app/utils/validators.py editable
    while still refusing to accept a new value that breaks the rules.
    """
    if isinstance(raw, str) and raw.strip() == (current or '').strip():
        return current
    return validator(raw, **kwargs)


def _validation_error_response(message: str):
    """Envelope estándar para un valor rechazado por app/utils/validators.py."""
    return jsonify({
        "data": None,
        "flash": [{"level": "danger", "message": message}],
        "error": {"code": "VALIDATION", "message": message},
        "meta": {}
    }), 400


# ─── Búsqueda con alcance: un filtro sobre un campo prohibido es un oráculo ───
#
# La proyección al nivel reducido corre DESPUÉS de la consulta, así que no
# protege ningún campo que el WHERE ya haya tocado: si el número de control
# entra en el predicado, el simple hecho de que la fila vuelva —o no— responde
# «¿el número de control de esta persona contiene X?». El valor se filtra una
# adivinanza a la vez aunque jamás aparezca en el payload. Lo mismo vale para
# el orden (publica el valor relativo de la columna) y para cualquier conteo
# calculado sobre ese filtro.
#
# Regla: sólo se puede buscar sobre campos que el llamador puede VER en ESA
# fila. Como el alcance es por fila y el filtro es por consulta, el predicado
# se parte en dos ramas mutuamente excluyentes (dentro / fuera de alcance).

#: Columnas buscables cuando la fila es visible COMPLETA para el llamador.
_FULL_SEARCH_COLUMNS = (
    User.first_name,
    User.last_name,
    User.mother_last_name,
    User.email,
    User.username,       # para un estudiante ES el número de control
    User.control_number,
)

#: Columnas buscables cuando la fila sólo puede salir al nivel reducido.
#: Se derivan del catálogo del servicio para que no puedan divergir de la
#: lista blanca de la proyección.
_REDUCED_SEARCH_COLUMNS = tuple(
    getattr(User, name)
    for name in sorted(scope_service.CROSS_PROGRAM_SEARCHABLE_FIELDS)
)


def _search_clause(columns, search: str):
    """OR de ILIKE sobre `columns` — nunca se llama con columnas prohibidas."""
    pattern = f"%{search}%"
    return or_(*[column.ilike(pattern) for column in columns])


def _in_scope_clause(scope: set, caller_id: int):
    """
    Predicado SQL «esta fila está dentro del alcance del llamador».

    Espejo exacto en SQL de `scope_service.user_in_scope(..., allow_self=True)`
    para un alcance NO global: el propio usuario, más quien tenga un
    `UserProgram` en alguno de los programas del llamador. Una cuenta de staff
    (sin `UserProgram`) queda fuera, igual que en el servicio.

    Va como EXISTS y no como JOIN a propósito: se evalúa fila por fila dentro
    de la consulta —que es lo que permite aplicar un conjunto de campos
    distinto a cada fila— y no duplica al usuario con varias inscripciones.
    """
    clauses = [User.id == caller_id]
    if scope:
        clauses.append(User.user_program.any(UserProgram.program_id.in_(scope)))
    return or_(*clauses)


@api_admin_users.get("")
@login_required
@permission_required('admin_users.api.list')
def list_users():
    """
    Lista usuarios con filtros opcionales.

    Alcance: el registro completo (`include_sensitive`: número de control,
    programa, historial, estado) sólo sale para usuarios dentro de los
    programas del llamador. El resto se proyecta al nivel reducido
    (nombre y correo) y, si el llamador ni siquiera tiene ese nivel, se omite.

    Los FILTROS respetan el mismo reparto que la proyección, porque un filtro
    sobre un campo prohibido lo convierte en oráculo (ver la nota sobre
    `_FULL_SEARCH_COLUMNS`):

      - `search` sobre número de control / username / apellido materno …:
        el conjunto completo sólo se evalúa contra las filas que el llamador
        puede ver completas; contra las demás se evalúa el conjunto reducido
        (nombre y correo, `CROSS_PROGRAM_SEARCHABLE_FIELDS`).
      - `role` y `active`: `role` y `is_active` no están en la lista blanca del
        nivel reducido, así que tampoco se evalúan sobre filas ajenas. Las filas
        de otros programas no se filtran por ellos —ni entran ni salen por su
        valor— y `meta.cross_program_filters_ignored` lo declara.
      - `program`: `program_id` SÍ está en la lista blanca, así que aplica a
        todas las filas.
      - Orden (`last_name`, `first_name`): ambos están en la lista blanca; no
        se ordena nunca por número de control, que publicaría su valor relativo.
    """
    # Parámetros de paginación
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 20, type=int), 100)

    # Filtros
    role_filter = request.args.get('role', type=str)
    program_filter = request.args.get('program', type=int)
    active_filter = request.args.get('active', 'all', type=str)
    search = request.args.get('search', type=str)

    # Alcance del llamador: None = global (todo visible completo).
    scope = scope_service.accessible_program_ids(current_user)
    may_summarize = scope_service.may_view_cross_program_summary(current_user)

    # Query base
    query = User.query

    # Filtro por programa — permitido sobre cualquier fila.
    if program_filter:
        query = query.filter(
            User.user_program.any(UserProgram.program_id == program_filter)
        )

    # Predicados que SÓLO pueden evaluarse sobre una fila visible completa.
    full_only = []
    ignored_cross_program = []

    if role_filter:
        full_only.append(User.role.has(name=role_filter))
        ignored_cross_program.append('role')

    if active_filter == 'true':
        full_only.append(User.is_active.is_(True))
        ignored_cross_program.append('active')
    elif active_filter == 'false':
        full_only.append(User.is_active.is_(False))
        ignored_cross_program.append('active')

    if search:
        full_only.append(_search_clause(_FULL_SEARCH_COLUMNS, search))

    if scope is None:
        # Alcance global: no hay filas ajenas, un solo conjunto de campos.
        ignored_cross_program = []
        if full_only:
            query = query.filter(and_(*full_only))
    else:
        in_scope = _in_scope_clause(scope, current_user.id)
        full_branch = and_(in_scope, *full_only) if full_only else in_scope

        if may_summarize:
            # Dos ramas disjuntas por construcción (P / NOT P): la unión de
            # «dentro de alcance Y conjunto completo» con «fuera de alcance Y
            # conjunto reducido». La única rama que menciona control_number,
            # username, role o is_active va conjugada con `in_scope`, así que
            # ninguna fila puede entrar por un campo que el llamador no vería.
            reduced_branch = not_(in_scope)
            if search:
                reduced_branch = and_(
                    reduced_branch,
                    _search_clause(_REDUCED_SEARCH_COLUMNS, search),
                )
            query = query.filter(or_(full_branch, reduced_branch))
        else:
            # Sin nivel reducido las filas ajenas no se devolvían igual, pero
            # seguían contando en `pagination.total`: ese conteo también era un
            # oráculo. Ahora ni entran en la consulta.
            ignored_cross_program = []
            query = query.filter(full_branch)

    # Ejecutar query con paginación
    pagination = query.order_by(User.last_name, User.first_name).paginate(
        page=page,
        per_page=per_page,
        error_out=False
    )

    # Una sola consulta de alcance para toda la página.
    in_scope = scope_service.users_in_scope(
        current_user, [u.id for u in pagination.items]
    )
    may_summarize = scope_service.may_view_cross_program_summary(current_user)

    users_data = []
    restricted = 0
    for user in pagination.items:
        if user.id in in_scope:
            users_data.append(user.to_dict(include_sensitive=True))
        elif may_summarize:
            # Proyección sobre la lista blanca: caen foto, historial, número de
            # control, rol, estado y cualquier campo que se añada más adelante.
            #
            # `restricted` va DENTRO de la fila, no sólo en meta: la consola
            # pinta fila por fila y necesita saber, en cada una, si la ausencia
            # de `is_active` significa "cuenta desactivada" o "no tienes derecho
            # a saberlo". Sin la marca, el JS leía la ausencia como `false` y
            # etiquetaba de «Inactivo» a cuentas activas de otros programas.
            row = scope_service.to_cross_program_summary(user.to_dict())
            row['restricted'] = True
            users_data.append(row)
            restricted += 1
        else:
            restricted += 1

    return jsonify({
        "data": {
            "users": users_data,
            "pagination": {
                "page": pagination.page,
                "per_page": pagination.per_page,
                "total": pagination.total,
                "pages": pagination.pages,
                "has_next": pagination.has_next,
                "has_prev": pagination.has_prev
            }
        },
        "error": None,
        "meta": {
            "restricted_count": restricted,
            # Filtros que NO se aplicaron a las filas de otros programas porque
            # recaen sobre campos prohibidos entre programas. La consola puede
            # decirlo en voz alta en lugar de dejar creer que la lista está
            # filtrada por igual.
            "cross_program_filters_ignored": ignored_cross_program,
        }
    }), 200


@api_admin_users.get("/<int:user_id>")
@login_required
@permission_required('admin_users.api.list')
def get_user(user_id):
    """
    Obtiene información detallada de un usuario específico.

    Fuera del alcance del llamador se devuelve el nivel reducido: nombre y
    correo, sin historial (prohibido entre programas), sin número de control y
    sin foto.
    """
    user = User.query.get(user_id)
    if not user:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Usuario no encontrado."}],
            "error": {"code": "NOT_FOUND", "message": "Usuario no existe"},
            "meta": {}
        }), 404

    # Obtener programa
    user_program = UserProgram.query.filter_by(user_id=user_id).first()

    if not scope_service.user_in_scope(current_user, user):
        if not scope_service.may_view_cross_program_summary(current_user):
            return jsonify({
                "data": None,
                "flash": [{"level": "danger", "message": "No tienes acceso a este usuario."}],
                "error": {"code": "FORBIDDEN", "message": "Usuario fuera de tu alcance"},
                "meta": {}
            }), 403

        # La marca viaja también dentro de `user` para que el modal de detalle
        # aplique la misma prueba que la tabla y no invente valores por omisión.
        restricted_user = scope_service.to_cross_program_summary(user.to_dict())
        restricted_user['restricted'] = True

        return jsonify({
            "data": {
                "user": restricted_user,
                "program": None,
                "history": []
            },
            "error": None,
            "meta": {"restricted": True}
        }), 200

    # Obtener historial
    history_entries = UserHistoryService.get_user_history(user_id=user_id, limit=50)

    return jsonify({
        "data": {
            "user": user.to_dict(include_sensitive=True),
            "program": {
                "id": user_program.program.id,
                "name": user_program.program.name,
                "slug": user_program.program.slug
            } if user_program else None,
            "history": [entry.to_dict() for entry in history_entries]
        },
        "error": None,
        "meta": {}
    }), 200


@api_admin_users.patch("/<int:user_id>")
@login_required
@permission_required('admin_users.api.update')
# allow_self=True (por omisión) a propósito, pero sólo para el NOMBRE: editarse
# el propio nombre ya es autoservicio en `PATCH /api/v1/users/me`, así que
# negarlo aquí sólo sería incoherente. El correo es otra cosa y lo rechaza el
# cuerpo de la vista con un mensaje específico — ver ahí el porqué.
@program_scope_required(user_id_kwarg='user_id')
def update_user(user_id):
    """Actualiza información básica del usuario"""
    user = User.query.get(user_id)
    if not user:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Usuario no encontrado."}],
            "error": {"code": "NOT_FOUND", "message": "Usuario no existe"},
            "meta": {}
        }), 404
    
    payload = request.get_json(silent=True) or {}
    changed_fields = {}

    # Same rules as self-registration and PATCH /users/me (app/utils/validators.py):
    # an admin edit is another write path into columns the staff consoles render,
    # so the value is either clean or the request is rejected.
    #
    # Validation gates NEW values only. The edit modal always PATCHes all four
    # fields, prefilled from the stored record, so validating unconditionally
    # would make any legacy row whose name predates these rules ("Ramirez, Juan",
    # "Jose Luis (Pepe)") permanently unsavable: even an edit that only touches
    # the e-mail would be rejected on a field the admin never typed in. Rows
    # already in the database are cleaned by a data migration, not by locking
    # their owner out of the console.
    try:
        if 'first_name' in payload:
            first_name = _validate_if_changed(
                payload['first_name'], user.first_name,
                validate_person_name, label="Nombre",
            )
        if 'last_name' in payload:
            last_name = _validate_if_changed(
                payload['last_name'], user.last_name,
                validate_person_name, label="Apellido paterno",
            )
        if 'mother_last_name' in payload:
            mother_last_name = _validate_if_changed(
                payload['mother_last_name'], user.mother_last_name,
                validate_person_name, label="Apellido materno", required=False,
            )
        if 'email' in payload:
            email = _validate_if_changed(
                payload['email'], user.email,
                validate_short_text,
                label="Correo electrónico",
                required=True,
                max_length=EMAIL_MAX_LENGTH,
            )
    except InputValidationError as e:
        return _validation_error_response(e.message)

    # Actualizar campos
    if 'first_name' in payload:
        new_value = first_name
        if new_value and new_value != user.first_name:
            changed_fields['first_name'] = {'old': user.first_name, 'new': new_value}
            user.first_name = new_value

    if 'last_name' in payload:
        new_value = last_name
        if new_value and new_value != user.last_name:
            changed_fields['last_name'] = {'old': user.last_name, 'new': new_value}
            user.last_name = new_value

    if 'mother_last_name' in payload:
        new_value = mother_last_name
        if new_value != user.mother_last_name:
            changed_fields['mother_last_name'] = {'old': user.mother_last_name, 'new': new_value}
            user.mother_last_name = new_value

    if 'email' in payload:
        new_value = email
        if new_value and new_value != user.email:
            # Nadie cambia su propio correo desde la consola de administración.
            #
            # El correo es el canal de recuperación de la cuenta: es a donde
            # viaja el enlace de un solo uso de `reset_password` y la única vía
            # de vuelta cuando se pierde la contraseña. Quien puede reescribirlo
            # sobre sí mismo puede apuntar su propia recuperación a un buzón
            # ajeno a la institución y conservar la entrada después de causar
            # baja, sin que nadie más intervenga. Por eso `PATCH /users/me`
            # tampoco lo expone: el nombre es autoservicio, el correo no.
            #
            # La condición cuelga de "el valor cambia" a propósito: el modal
            # envía siempre los cuatro campos precargados, y rechazar por la
            # simple presencia de 'email' impediría a un administrador
            # corregirse una tilde del nombre.
            if user_id == current_user.id:
                # Los campos de nombre ya se asignaron en la sesión más arriba;
                # la petición se rechaza entera, así que se descartan.
                db.session.rollback()
                return jsonify({
                    "data": None,
                    "flash": [{"level": "danger", "message": (
                        "No puedes cambiar tu propio correo: es el canal de "
                        "recuperación de tu cuenta. Pídeselo al jefe de posgrado."
                    )}],
                    "error": {"code": "FORBIDDEN", "message": "Cambio de correo propio no permitido"},
                    "meta": {}
                }), 403

            existing = User.query.filter(User.email == new_value, User.id != user_id).first()
            if existing:
                return jsonify({
                    "data": None,
                    "flash": [{"level": "danger", "message": "El email ya está en uso."}],
                    "error": {"code": "VALIDATION", "message": "Email duplicado"},
                    "meta": {}
                }), 400
            changed_fields['email'] = {'old': user.email, 'new': new_value}
            user.email = new_value
    
    # Registrar cambios
    if changed_fields:
        UserHistoryService.log_basic_info_update(user_id=user_id, changed_fields=changed_fields)

    db.session.commit()

    if changed_fields:
        try:
            from app.extensions import socketio
            payload_ws = {
                'action': 'updated',
                'user_id': user.id,
                'role': user.role.name if user.role else None,
                'email': user.email,
                'full_name': f'{user.first_name} {user.last_name}',
                'changed_fields': list(changed_fields.keys()),
            }
            socketio.emit('admin_user:changed', payload_ws, room='role:postgraduate_admin')
            socketio.emit('admin_user:changed', payload_ws, room='role:coordinator')
        except Exception:
            pass

    return jsonify({
        "data": {"user": user.to_dict(include_sensitive=True)},
        "flash": [{"level": "success", "message": "Usuario actualizado exitosamente."}],
        "error": None,
        "meta": {"changed_fields": list(changed_fields.keys())}
    }), 200

# ESTA ES LA CONTINUACIÓN - PEGAR DESPUÉS DE LA PARTE 1

@api_admin_users.post("/<int:user_id>/reset-password")
@login_required
@permission_required('admin_users.api.reset_password')
# allow_self=True a propósito: la acción sobre uno mismo la rechaza el cuerpo de
# la vista con un mensaje específico, no con el 403 genérico de alcance.
@program_scope_required(user_id_kwarg='user_id')
def reset_password(user_id):
    """
    Invalida la contraseña del usuario y le envía por correo un enlace de un
    solo uso para que defina una nueva.

    El administrador nunca ve ni dicta una contraseña: no existe contraseña
    por defecto en el sistema.
    """
    from app.services import password_reset_service as prs

    # No permitir resetear la propia contraseña
    if user_id == current_user.id:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "No puedes resetear tu propia contraseña."}],
            "error": {"code": "FORBIDDEN", "message": "Acción no permitida"},
            "meta": {}
        }), 403

    try:
        result = prs.issue_admin_reset(user_id=user_id, admin_id=current_user.id)
    except prs.TokenNotFound as e:
        db.session.rollback()
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Usuario no encontrado."}],
            "error": {"code": "NOT_FOUND", "message": str(e)},
            "meta": {}
        }), 404
    except prs.MissingEmail as e:
        db.session.rollback()
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": str(e)}],
            "error": {"code": "BUSINESS_ERROR", "message": str(e)},
            "meta": {}
        }), 400

    db.session.commit()

    user = result['user']
    return jsonify({
        "data": {"expires_at": result['expires_at'].isoformat() if result['expires_at'] else None},
        "flash": [{
            "level": "success",
            "message": (
                f"Se envió a {user.email} un enlace para que "
                f"{user.first_name} {user.last_name} defina una contraseña nueva. "
                f"El enlace vence en {result['ttl_minutes']} minutos y la contraseña "
                f"anterior quedó invalidada."
            )
        }],
        "error": None,
        "meta": {}
    }), 200


@api_admin_users.patch("/<int:user_id>/toggle-active")
@login_required
@permission_required('admin_users.api.update')
@program_scope_required(user_id_kwarg='user_id')
def toggle_active(user_id):
    """Activa o desactiva un usuario"""
    user = User.query.get(user_id)
    if not user:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Usuario no encontrado."}],
            "error": {"code": "NOT_FOUND", "message": "Usuario no existe"},
            "meta": {}
        }), 404
    
    # No permitir desactivarse a sí mismo
    if user_id == current_user.id:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "No puedes desactivarte a ti mismo."}],
            "error": {"code": "FORBIDDEN", "message": "Acción no permitida"},
            "meta": {}
        }), 403
    
    # Toggle estado
    user.is_active = not user.is_active
    action = 'activated' if user.is_active else 'deactivated'
    message = f"Usuario {user.first_name} {user.last_name} {'activado' if user.is_active else 'desactivado'}."

    # Registrar en historial
    UserHistoryService.log_user_activation(user_id=user_id, is_active=user.is_active)

    db.session.commit()

    # Deactivation has to reach the open sockets too. load_user() already turns
    # the next HTTP request anonymous, but a Socket.IO connection is authorised
    # once at the handshake and then keeps its rooms for the lifetime of the
    # sid, so without this the tab keeps receiving live payloads. Runs after the
    # commit so a failed write never evicts anybody.
    if not user.is_active:
        from app.sockets.emitters import disconnect_user_sockets
        disconnect_user_sockets(user_id)

    return jsonify({
        "data": {"is_active": user.is_active},
        "flash": [{"level": "success", "message": message}],
        "error": None,
        "meta": {}
    }), 200


@api_admin_users.post("/<int:user_id>/assign-control-number")
@login_required
@permission_required('admin_users.api.assign_control_number')
@program_scope_required(user_id_kwarg='user_id', allow_self=False)
def assign_control_number(user_id):
    """Asigna un número de control al usuario"""
    user = User.query.get(user_id)
    if not user:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Usuario no encontrado."}],
            "error": {"code": "NOT_FOUND", "message": "Usuario no existe"},
            "meta": {}
        }), 404
    
    # Solo aspirantes pueden recibir un número de control desde este endpoint.
    # (Para correcciones de estudiantes ya transicionados, usar el flujo del coordinador.)
    current_role = getattr(getattr(user, 'role', None), 'name', None)
    if current_role != 'applicant':
        return jsonify({
            "data": None,
            "flash": [{"level": "warning", "message": f"Solo se puede asignar número de control a aspirantes. Rol actual: {current_role or 'desconocido'}."}],
            "error": {"code": "INVALID_ROLE", "message": "Rol no elegible para asignación de número de control"},
            "meta": {}
        }), 400

    payload = request.get_json(silent=True) or {}
    control_number = _sanitize(payload.get('control_number'))

    if not control_number:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Número de control es requerido."}],
            "error": {"code": "VALIDATION", "message": "Campo requerido"},
            "meta": {}
        }), 400
    
    # Validar formato: M o D seguido de 8 dígitos
    if not re.match(r'^[MD]\d{8}$', control_number):
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Formato inválido. Debe ser M o D seguido de 8 dígitos."}],
            "error": {"code": "VALIDATION", "message": "Formato inválido"},
            "meta": {}
        }), 400
    
    # Unicidad institucional. La consulta sigue siendo global —así debe ser: el
    # número de control es único en toda la institución— pero la RESPUESTA ya no
    # dice por qué se rechazó. Antes contestaba "ya está asignado", y como el
    # guard de alcance sólo mira al usuario destino (un aspirante propio), quien
    # llama podía preguntar por cualquier número del padrón y leer la respuesta:
    # un oráculo de existencia sobre el mismo dato que el buscador de alumnos ya
    # protege (CROSS_PROGRAM_SEARCHABLE_FIELDS). El texto vive en el servicio
    # para que los tres puntos de rechazo digan exactamente lo mismo.
    #
    # La colisión sí queda registrada en el log de la aplicación (ops-only, sin
    # PII persistida y sin filas que el llamador pueda provocar en la base):
    # el sondeo requiere una petición autenticada por intento, así que lo que
    # protege de verdad es la detección del volumen, no un mensaje más.
    existing = User.query.filter(User.control_number == control_number).first()
    if existing:
        from flask import current_app
        from app.services.acceptance_service import CONTROL_NUMBER_REJECTED_MESSAGE
        current_app.logger.warning(
            '[control_number] Colisión al asignar número de control '
            f'(admin_id={current_user.id}, target_user_id={user_id}, '
            f'control_number={control_number})'
        )
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": CONTROL_NUMBER_REJECTED_MESSAGE}],
            "error": {"code": "VALIDATION", "message": CONTROL_NUMBER_REJECTED_MESSAGE},
            "meta": {}
        }), 400

    # Verificar que tenga programa
    user_program = UserProgram.query.filter_by(user_id=user_id).first()
    if not user_program:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "El usuario debe tener un programa asignado primero."}],
            "error": {"code": "VALIDATION", "message": "Sin programa asignado"},
            "meta": {}
        }), 400

    # Validar tipo de programa
    program_type = control_number[0]
    program_name = user_program.program.name.lower()

    if program_type == 'M' and 'maestr' not in program_name:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "El número debe comenzar con 'M' para maestrías."}],
            "error": {"code": "VALIDATION", "message": "Tipo no coincide"},
            "meta": {}
        }), 400
    elif program_type == 'D' and 'doctor' not in program_name:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "El número debe comenzar con 'D' para doctorados."}],
            "error": {"code": "VALIDATION", "message": "Tipo no coincide"},
            "meta": {}
        }), 400

    # Ejecutar transición completa a estudiante (sin chequeos rigurosos del flujo
    # de aceptación — variante administrativa para casos legacy).
    from app.services import acceptance_service as svc

    try:
        up = svc.assign_control_number_admin(
            user_id=user_id,
            program_id=user_program.program_id,
            control_number=control_number,
            admin_id=current_user.id,
        )
    except ValueError as e:
        db.session.rollback()
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": str(e)}],
            "error": {"code": "VALIDATION", "message": str(e)},
            "meta": {}
        }), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": f"Error al asignar número de control: {e}"}],
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500

    return jsonify({
        "data": {
            "control_number": user.control_number,
            "username": user.username,
            "role": getattr(user.role, 'name', None),
            "admission_status": up.admission_status,
            "current_semester": up.current_semester,
        },
        "flash": [{"level": "success", "message": f"Número de control {control_number} asignado y transición a estudiante completada."}],
        "error": None,
        "meta": {}
    }), 200


@api_admin_users.delete("/<int:user_id>")
@login_required
@permission_required('admin_users.api.delete')
@program_scope_required(user_id_kwarg='user_id')
def delete_user(user_id):
    """Elimina un usuario (solo admin general)"""
    user = User.query.get(user_id)
    if not user:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Usuario no encontrado."}],
            "error": {"code": "NOT_FOUND", "message": "Usuario no existe"},
            "meta": {}
        }), 404
    
    # No permitir eliminarse a sí mismo
    if user_id == current_user.id:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "No puedes eliminarte a ti mismo."}],
            "error": {"code": "FORBIDDEN", "message": "Acción no permitida"},
            "meta": {}
        }), 403
    
    payload = request.get_json(silent=True) or {}
    force = payload.get('force', False)
    
    # Verificar datos relacionados
    has_submissions = len(user.submissions) > 0
    
    if has_submissions and not force:
        return jsonify({
            "data": None,
            "flash": [{"level": "warning", "message": "El usuario tiene documentos. Usa 'force=true' para eliminar."}],
            "error": {"code": "VALIDATION", "message": "Usuario tiene datos relacionados"},
            "meta": {"has_submissions": True}
        }), 400
    
    # Registrar eliminación
    user_name = f"{user.first_name} {user.last_name}"
    user_email = user.email
    user_role = user.role.name if user.role else None
    UserHistoryService.log_user_deletion(user_id=user_id, user_name=user_name)

    db.session.commit()

    # Eliminar usuario
    db.session.delete(user)
    db.session.commit()

    # Misma razón que en toggle_active: las salas de Socket.IO se unen una sola
    # vez en el handshake y no se vuelven a resolver contra la base. Sin esto,
    # la pestaña de un usuario ya borrado sigue recibiendo el feed hasta que
    # reconecte por su cuenta.
    from app.sockets.emitters import disconnect_user_sockets
    disconnect_user_sockets(user_id)

    try:
        from app.extensions import socketio
        payload_ws = {
            'action': 'deleted',
            'user_id': user_id,
            'role': user_role,
            'email': user_email,
            'full_name': user_name,
        }
        socketio.emit('admin_user:changed', payload_ws, room='role:postgraduate_admin')
        socketio.emit('admin_user:changed', payload_ws, room='role:coordinator')
    except Exception:
        pass

    return jsonify({
        "data": None,
        "flash": [{"level": "success", "message": f"Usuario {user_name} eliminado."}],
        "error": None,
        "meta": {}
    }), 200


@api_admin_users.post("/social-service")
@login_required
@permission_required('admin_users.api.create_social_service')
def create_social_service():
    """
    Crea un usuario con rol 'social_service' y delega los permisos indicados.

    Body JSON:
      first_name        : str
      last_name         : str
      mother_last_name  : str | null
      email             : str
      is_internal       : bool (default True)
      permissions       : list[str]  — codenames a delegar (obligatorio, >=1)
      program_ids       : list[int] | null
      expires_at        : str ISO 8601 | null
    """
    from datetime import datetime
    from app.services.permission_service import (
        create_social_service_user,
        PermissionError as PermErr,
    )

    payload = request.get_json(silent=True) or {}

    required = ['first_name', 'last_name', 'email', 'permissions']
    missing = [f for f in required if not payload.get(f)]
    if missing:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": f"Campos requeridos: {', '.join(missing)}."}],
            "error": {"code": "VALIDATION", "message": "Campos faltantes"},
            "meta": {"missing": missing}
        }), 400

    if not isinstance(payload['permissions'], list) or len(payload['permissions']) == 0:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Debes seleccionar al menos un permiso."}],
            "error": {"code": "VALIDATION", "message": "permissions vacío"},
            "meta": {}
        }), 400

    expires_at = None
    if payload.get('expires_at'):
        try:
            expires_at = datetime.fromisoformat(payload['expires_at'])
        except ValueError:
            return jsonify({
                "data": None,
                "flash": [{"level": "danger", "message": "Fecha de expiración inválida."}],
                "error": {"code": "VALIDATION", "message": "expires_at inválido"},
                "meta": {}
            }), 400

    try:
        new_user, delegations, email_sent = create_social_service_user(
            creator_id=current_user.id,
            user_data={
                'first_name':       payload['first_name'],
                'last_name':        payload['last_name'],
                'mother_last_name': payload.get('mother_last_name'),
                'email':            payload['email'],
                'is_internal':      payload.get('is_internal', True),
            },
            permissions_to_delegate=payload['permissions'],
            program_ids=payload.get('program_ids'),
            expires_at=expires_at,
        )

        UserHistoryService.log_action(
            user_id=new_user.id,
            admin_id=current_user.id,
            action='social_service_created',
            details={
                'permissions_delegated': payload['permissions'],
                'program_ids': payload.get('program_ids') or [],
                'expires_at': payload.get('expires_at'),
            }
        )
        db.session.commit()

        created_msg = (
            f"Usuario {new_user.first_name} {new_user.last_name} creado con "
            f"{len(delegations)} delegación(es)."
        )
        if email_sent:
            flash_entry = {
                "level": "success",
                "message": (
                    f"{created_msg} Se envió a {new_user.email} un enlace para "
                    f"que defina su contraseña; vence en 45 minutos."
                ),
            }
        else:
            # The account and its activation token exist; only the mail could
            # not be queued. Say so instead of implying the user was notified.
            flash_entry = {
                "level": "warning",
                "message": (
                    f"{created_msg} No se pudo enviar el correo con el enlace de "
                    f"activación a {new_user.email}. Usa «Restablecer contraseña» "
                    f"para generar y enviar uno nuevo."
                ),
            }

        return jsonify({
            "data": {
                "user": new_user.to_dict(include_sensitive=True),
                "delegations_count": len(delegations),
                "activation_email_sent": email_sent,
            },
            "flash": [flash_entry],
            "error": None,
            "meta": {}
        }), 201

    except InputValidationError as e:
        # The service validates the person-name fields; the route is what turns
        # that into the envelope.
        db.session.rollback()
        return _validation_error_response(e.message)
    except PermErr as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": str(e)}],
            "error": {"code": "BUSINESS_ERROR", "message": str(e)},
            "meta": {}
        }), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Error al crear usuario."}],
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500


@api_admin_users.get("/<int:user_id>/history")
@login_required
@permission_required('admin_users.api.list')
@program_scope_required(user_id_kwarg='user_id')
def user_history(user_id):
    """
    Obtiene el historial completo de un usuario.

    El historial es información prohibida entre programas: no existe nivel
    reducido, el alcance lo resuelve `@program_scope_required`.
    """
    user = User.query.get(user_id)
    if not user:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Usuario no encontrado."}],
            "error": {"code": "NOT_FOUND", "message": "Usuario no existe"},
            "meta": {}
        }), 404
    
    history_entries = UserHistoryService.get_user_history(user_id=user_id)
    
    return jsonify({
        "data": {
            "user": {"id": user.id, "name": f"{user.first_name} {user.last_name}"},
            "history": [entry.to_dict() for entry in history_entries]
        },
        "error": None,
        "meta": {"total": len(history_entries)}
    }), 200