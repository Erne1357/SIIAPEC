# app/routes/api/attendance_api.py
from flask import Blueprint, request, jsonify,current_app
from flask_login import login_required, current_user
from app.utils.permissions import permission_required
from app.services.events_service import EventsService
from app.services.user_history_service import UserHistoryService
from app.models.event import Event
from app import db

api_attendance = Blueprint('api_attendance', __name__, url_prefix='/api/v1/attendance')


# ============================================================================
# Alcance por evento — permiso ≠ alcance
# ============================================================================

def _deny(code: str, message: str, status: int):
    """Denegación con el envelope del proyecto y mensaje en español."""
    return jsonify({
        "ok": False,
        "data": None,
        "flash": [{"level": "danger", "message": message}],
        "error": {"code": code, "message": message},
        "meta": {}
    }), status


def _load_manageable_event(event_id: int):
    """
    Carga el evento y exige que `current_user` pueda ADMINISTRARLO.

    La lista de asistencia pertenece al evento, así que la manda quien manda en
    el evento (`EventsService.user_may_manage_event`): su programa, o el
    alcance global si el evento es institucional (`program_id = NULL`).

    404 —y el MISMO mensaje— para «no existe» y para «no es tuyo». Estas rutas
    se direccionan con un id crudo que el llamador puede teclear, y el listado
    por el que llega (`GET /api/v1/events`) ya viene filtrado por alcance: un
    evento que no administra nunca estuvo en su lista, así que su existencia no
    es suya. Distinguir los dos casos convertiría el recorrido de ids en un
    oráculo de enumeración, que es justo lo que hacía la rama anterior
    (404 ausente / 403 ajeno). Es también el código que ya usaba
    `register_to_event` y el que usa `events_api._check_event_access`.

    Returns:
        (event, None) si procede; (None, respuesta_error) si no.
    """
    event = db.session.get(Event, event_id)
    if not event or not EventsService.user_may_manage_event(current_user, event):
        return None, _deny("NOT_FOUND", "Evento no encontrado.", 404)
    return event, None


@api_attendance.route('/event/<int:event_id>/register', methods=['POST'])
@login_required
def register_to_event(event_id: int):
    """
    Registrarse a un evento de capacidad múltiple/ilimitada.

    Esta ruta llevaba `@login_required` y NADA más, y el servicio tampoco
    miraba el evento: era la raíz del módulo. Cualquier cuenta escribía una
    fila `EventAttendance` contra cualquier id de evento de la institución —
    incluso en borrador o privado de otro posgrado— y esa fila era después
    LEÍDA COMO PERMISO por el detalle público y por la lista de /events.

    La regla es la del módulo: gestión (`user_may_manage_event`) o
    participación (`user_may_participate_in_event`). El servicio la vuelve a
    aplicar —es él quien escribe la fila y quien lo llama desde
    `respond_to_invitation`—; aquí se aplica también para responder con el
    código correcto en vez de un 400 genérico.

    404, no 403: el evento que no se puede ver no debe poder confirmarse
    recorriendo ids.
    """
    event = db.session.get(Event, event_id)
    if not event:
        return _deny("NOT_FOUND", "Evento no encontrado.", 404)

    if not (
        EventsService.user_may_manage_event(current_user, event)
        or EventsService.user_may_participate_in_event(current_user, event)
    ):
        return _deny("NOT_FOUND", "Evento no encontrado.", 404)

    data = request.get_json() or {}
    try:
        attendance = EventsService.register_to_event(
            event_id=event_id,
            user_id=current_user.id,
            notes=data.get('notes')
        )
        current_app.logger.info(f"Usuario {current_user.id} se registró a evento {event_id}")

        # Registrar en el historial
        try:
            UserHistoryService.log_event_registration(
                user_id=current_user.id,
                event_title=event.title,
                event_type=event.type
            )
            db.session.commit()
        except Exception as e:
            current_app.logger.error(f"Error al registrar registro de evento en historial: {e}")

        return jsonify({
            "ok": True,
            "id": attendance.id,
            "message": "Registro exitoso"
        }), 201
        
    except ValueError as e:
        current_app.logger.warning(f"Error al registrar a evento {event_id} usuario {current_user.id}: {e}")
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception(
            f"Error 500 al registrar usuario {current_user.id} a evento {event_id}"
        )
        return jsonify({"ok": False, "error": str(e)}), 500


@api_attendance.route('/event/<int:event_id>/unregister', methods=['POST'])
@login_required
def unregister_from_event(event_id: int):
    """Cancelar registro a un evento"""
    try:
        EventsService.unregister_from_event(
            event_id=event_id,
            user_id=current_user.id
        )
        
        return jsonify({
            "ok": True,
            "message": "Registro cancelado"
        }), 200
        
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500


@api_attendance.route('/event/<int:event_id>/registrations', methods=['GET'])
@login_required
@permission_required('attendance.api.list_registrations')
def get_event_registrations(event_id: int):
    """
    Obtener la lista de registros de un evento.

    La pasa lista quien manda en el evento: `_load_manageable_event`, igual que
    `mark-attendance`. Aquí vivía la séptima copia de la pregunta —
    `if not manages and event.program_id is not None` — que abría la lista de
    CUALQUIER evento institucional, incluido uno en borrador, a cualquier
    portador de `attendance.api.list_registrations`. Nombre y correo sí están
    dentro del nivel que el dueño autoriza a cruzar entre programas, así que no
    era una fuga de datos prohibidos, pero la lista de asistentes de un evento
    que aún no existe para el público no la mira quien no lo organiza, y la
    rama local era exactamente el defecto recurrente.

    La única pantalla que consume esta ruta es la consola de administración
    (`app/static/js/admin/events/detail.js`); ninguna vista de participante
    pide la lista de asistentes. Por eso la regla es "gestión", no
    "gestión o participación": no hay audiencia que se quede sin nada. Esa
    consola tampoco ramifica por código de estado —`apiRequest` sólo compone un
    mensaje—, así que la denegación puede ser el 404 uniforme de la familia.
    """
    event, err = _load_manageable_event(event_id)
    if err:
        return err

    try:
        registrations = EventsService.get_event_registrations(
            event_id, include_notes=True
        )

        return jsonify({
            "ok": True,
            "event_id": event_id,
            # Constante hoy —sin gestión no se llega hasta aquí— pero sigue en
            # la respuesta porque la consola la lee (`data.can_manage !== false`)
            # para decidir si habilita los botones de pasar lista.
            "can_manage": True,
            "registrations": registrations,
            "total": len(registrations)
        }), 200

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@api_attendance.route('/event/<int:event_id>/mark-attendance', methods=['POST'])
@login_required
@permission_required('attendance.api.mark')
def mark_attendance(event_id: int):
    """
    Marcar asistencia de un usuario.

    ESCRITURA sobre la lista de un evento. El guardia es `_load_manageable_event`:
    el evento debe ser de un programa dentro del alcance, y si es institucional
    (sin programa) hace falta alcance global. Antes bastaba con el permiso:
    `event.program_id and ...` dejaba pasar de largo cualquier evento sin
    programa, y por ahí el coordinador de A escribía la asistencia de un
    alumno de B. El servicio remata el cerco: sólo actualiza registros que ya
    existen, así que nunca se inventa asistencia de nadie.
    """
    event, err = _load_manageable_event(event_id)
    if err:
        return err

    data = request.get_json() or {}
    # `user_id` viaja en el CUERPO como UUID público; el servicio sigue
    # recibiendo el entero. Un UUID desconocido cae en el mismo 400 de
    # "user_id es requerido" que la ausencia del campo.
    from app.models.user import User
    _target = User.by_uuid(data.get('user_id'))
    user_id = _target.id if _target else None
    attended = data.get('attended')
    notes = data.get('notes')
    reset = data.get('reset', False)  # NUEVO

    if not user_id:
        return jsonify({"ok": False, "error": "user_id es requerido"}), 400

    try:
        attendance = EventsService.mark_attendance(
            event_id=event_id,
            user_id=user_id,
            attended=attended if attended is not None else True,
            notes=notes,
            reset=reset  # NUEVO
        )
        
        return jsonify({
            "ok": True,
            "id": attendance.id,
            "status": attendance.status
        }), 200
        
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500


@api_attendance.route('/my-registrations', methods=['GET'])
@login_required
def my_registrations():
    """Obtener eventos en los que el usuario está registrado"""
    from app.models.event import EventAttendance
    
    registrations = db.session.query(EventAttendance, Event).join(
        Event, EventAttendance.event_id == Event.id
    ).filter(
        EventAttendance.user_id == current_user.id
    ).order_by(EventAttendance.registered_at.desc()).all()
    
    items = [{
        'event_id': event.id,
        'event_title': event.title,
        'event_type': event.type,
        'event_location': event.location,
        'capacity_type': event.capacity_type,
        'status': attendance.status,
        'registered_at': attendance.registered_at.isoformat(),
        'attended_at': attendance.attended_at.isoformat() if attendance.attended_at else None
    } for attendance, event in registrations]
    
    return jsonify({
        "ok": True,
        "registrations": items
    }), 200