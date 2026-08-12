from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from app.utils.permissions import (
    permission_required,
    any_permission_required,
    guard_user_scope,
)
from app.services.appointments_service import AppointmentsService, AppointmentAccessDenied
from app.services.events_service import EventsService
from app.services.user_history_service import UserHistoryService
from app.models.appointment import Appointment
from app import db

api_appointments = Blueprint('api_appointments', __name__, url_prefix='/api/v1/appointments')

#: Permiso que distingue al coordinador (gestiona citas ajenas) del aspirante
#: (sólo gestiona la suya).
_STAFF_PERMISSION = 'appointments.api.assign'


def _public_user_id(user_id):
    """
    Internal user id → the public UUID a payload may publish.

    The Socket.IO ROOM argument keeps the integer: rooms are resolved
    server-side from the session and never come from the client.
    """
    from app.models.user import User
    from app.services.public_id_service import uuid_for
    return uuid_for(User, user_id)


def _deny(code: str, message: str, status: int):
    """Respuesta de denegación con el envelope del proyecto (mensaje en español)."""
    return jsonify({
        "ok": False,
        "data": None,
        "flash": [{"level": "danger", "message": message}],
        "error": {"code": code, "message": message},
        "meta": {}
    }), status


def _event_manage_denied(event):
    """
    Autoridad de gestión sobre el evento del que cuelga la cita.

    Regla única: `EventsService.user_may_manage_event` — evento CON programa
    exige alcance sobre ese programa; evento SIN programa (institucional)
    exige alcance global.

    La versión anterior recibía sólo el `program_id` y contestaba "procede"
    cuando era None, así que toda cita colgada de un evento institucional
    quedaba al alcance de cualquier coordinador: leer al aspirante de otro
    posgrado y también ESCRIBIR sobre él (cancelar, marcar estado, decidir
    solicitudes de cambio), que el contrato prohíbe de plano entre programas.

    Un evento inexistente (None) también se deniega: falla cerrado.

    Returns:
        None si procede; la respuesta 403 si no.
    """
    if not EventsService.user_may_manage_event(current_user, event):
        return _deny("FORBIDDEN", "No tienes acceso a este evento.", 403)
    return None


def _appointment_not_found():
    """
    404 deliberado: se usa TAMBIÉN cuando la cita existe pero es de otra
    persona y quien pregunta no es personal autorizado. Un 403 confirmaría
    que ese id existe y permitiría recorrer el espacio de ids.
    """
    return _deny("NOT_FOUND", "Cita no encontrada.", 404)


def _resolve_manage_context(appointment_id: int):
    """
    Carga la cita y decide si `current_user` puede gestionarla.

    Returns:
        (ctx, as_admin, error_response). `error_response` distinto de None
        significa que hay que devolverlo tal cual.
    """
    ctx = AppointmentsService.get_appointment_context(appointment_id)
    if not ctx:
        return None, False, _appointment_not_found()

    appt = ctx['appointment']
    if appt.applicant_id == current_user.id:
        return ctx, False, None

    # No es su cita: sólo el personal con permiso de gestión y con autoridad
    # sobre el evento del que cuelga la cita puede tocarla.
    if not current_user.has_permission(_STAFF_PERMISSION):
        return None, False, _appointment_not_found()

    denied = _event_manage_denied(ctx.get('event'))
    if denied:
        return None, False, denied

    return ctx, True, None


def _event_read_denied(appointment, event):
    """
    Autoridad de LECTURA sobre el evento del que cuelga una cita.

    Tener la cita no basta: la respuesta la da
    `AppointmentsService.user_may_read_event_of_appointment` (gestiona el
    evento, o se la asignó un tercero con autoridad, o puede participar en él
    ahora mismo). Sin esto, una cita que el propio aspirante se creó volvía a
    ser lo que era una fila de `EventAttendance`: una autorización firmada por
    el propio interesado.

    Returns:
        None si procede; la respuesta 403 si no.
    """
    if not AppointmentsService.user_may_read_event_of_appointment(
        current_user, appointment, event
    ):
        return _deny("FORBIDDEN", "No tienes acceso a este evento.", 403)
    return None


@api_appointments.route('', methods=['POST'])
@login_required
@any_permission_required('appointments.api.assign', 'appointments.api.book')
def assign():
    data = request.get_json() or {}

    try:
        event_id = int(data['event_id'])
        slot_id = int(data['slot_id'])
    except (KeyError, TypeError, ValueError):
        return _deny("VALIDATION_ERROR", "event_id y slot_id son requeridos.", 400)

    slot_ctx = AppointmentsService.get_slot_context(slot_id)
    if not slot_ctx or not slot_ctx['event'] or slot_ctx['event'].id != event_id:
        return _deny("NOT_FOUND", "Horario no encontrado.", 404)

    event = slot_ctx['event']
    is_staff = current_user.has_permission(_STAFF_PERMISSION)

    if is_staff:
        # El coordinador sólo agenda en eventos que gestiona…
        denied = _event_manage_denied(event)
        if denied:
            return denied
        # `applicant_id` viaja en el CUERPO como UUID público. Antes se leía
        # con int(); hoy un valor no resoluble deja `applicant_id` en None y la
        # guarda de alcance falla cerrado con el mismo 403 que un aspirante de
        # otro programa.
        from app.models.user import User as _User
        raw_applicant = data.get('applicant_id')
        if raw_applicant:
            _target = _User.by_uuid(raw_applicant)
            applicant_id = _target.id if _target else None
        else:
            applicant_id = current_user.id
        # …y sólo sobre aspirantes de esos mismos programas.
        denied = guard_user_scope(applicant_id)
        if denied:
            return denied
    else:
        # Un aspirante sólo puede agendarse a sí mismo. El applicant_id del
        # cuerpo se ignora deliberadamente.
        applicant_id = current_user.id

        # Regla ÚNICA de participación: `user_may_participate_in_event`. La
        # guarda local que había aquí era la QUINTA respuesta a la misma
        # pregunta y el único sitio del código que leía `visible_to_students`
        # sin leer `visibility`, así que un evento PRIVADO de cualquier
        # posgrado aceptaba la reserva de cualquier aspirante — y la cita
        # resultante abría después la lectura del evento.
        #
        # El predicado compartido NO mira `capacity_type`, así que admite los
        # eventos 1:1 ('single') y la entrevista de admisión sigue
        # funcionando igual: es el mismo predicado que ya gobierna
        # `GET /api/v1/events/public/<id>` y `GET /api/v1/events/<id>/slots`,
        # que son los que le entregan los horarios al aspirante. Si aquí
        # pasara y allí no, el aspirante no habría podido ni ver el slot.
        if not EventsService.user_may_participate_in_event(current_user, event):
            return _deny("FORBIDDEN", "No puedes agendar una cita en este evento.", 403)

    try:
        appt = AppointmentsService.assign_slot(
            event_id=event_id,
            slot_id=slot_id,
            applicant_id=applicant_id,
            assigned_by=current_user.id,
            notes=data.get('notes')
        )

        # Registrar en el historial Y enviar notificación
        try:
            from app.services.notification_service import NotificationService
            slot = slot_ctx['slot']

            # Registrar en historial
            UserHistoryService.log_appointment_assignment(
                user_id=applicant_id,
                event_title=event.title,
                appointment_datetime=slot.starts_at.isoformat(),
                assigned_by_admin=current_user.id
            )

            # Enviar notificación con correo
            NotificationService.notify_appointment_assigned(
                user_id=applicant_id,
                event_title=event.title,
                appointment_id=appt.id,
                slot_datetime=slot.starts_at.strftime('%d/%m/%Y a las %H:%M'),
                event_id=event.id,
                location=event.location
            )

            db.session.commit()
        except Exception as e:
            from flask import current_app
            current_app.logger.error(f"Error al registrar asignación de cita en historial: {e}")

        # Al aspirante (es su cita) y a los coordinadores CON alcance sobre el
        # programa del evento. `role:coordinator` metía el applicant_id de un
        # aspirante ajeno en la consola de cada program_admin del instituto.
        # Evento institucional (program_id None) → sólo alcance global, que es
        # la misma regla que `EventsService.user_may_manage_event`.
        try:
            from app.sockets.emitters import emit_user_and_coordinators
            emit_user_and_coordinators(
                'appointment:changed',
                {
                    'action': 'booked',
                    'appointment_id': appt.id,
                    'event_id': appt.event_id,
                    'slot_id': appt.slot_id,
                    'applicant_id': _public_user_id(appt.applicant_id),
                },
                appt.applicant_id,
                event.program_id,
            )
        except Exception:
            pass

        return jsonify({"ok": True, "id": appt.id}), 201
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

@api_appointments.route('/mine', methods=['GET'])
@login_required
@permission_required('appointments.api.list_own')
def my_appointments():
    """
    Citas propias, sólo identificadores y estado.

    No sirve NINGÚN campo del evento (ni título, ni sede, ni descripción), así
    que no necesita la guarda de lectura del evento: `event_id` y `slot_id` son
    los ids de la propia fila del llamante. Si algún día se añade aquí un campo
    del evento, hay que pasar por `_event_read_denied` como en /details.
    """
    appts = AppointmentsService.list_appointments_of_user(current_user.id)
    payload = [{
        "id": a.id,
        "event_id": a.event_id,
        "slot_id": a.slot_id,
        "status": a.status
    } for a in appts]
    return jsonify({"ok": True, "items": payload}), 200

@api_appointments.route('/<int:appointment_id>', methods=['DELETE'])
@login_required
@any_permission_required('appointments.api.cancel', 'appointments.api.assign')
def cancel(appointment_id:int):
    ctx, as_admin, err = _resolve_manage_context(appointment_id)
    if err:
        return err

    event_title = ctx['event_title']

    try:
        appt = AppointmentsService.cancel_appointment(
            appointment_id,
            reason=request.args.get('reason'),
            acting_user_id=current_user.id,
            as_admin=as_admin,
        )

        # Registrar en el historial (incluye notificación automática)
        try:
            reason = request.args.get('reason', 'Cancelada por el usuario')

            UserHistoryService.log_appointment_cancellation(
                user_id=appt.applicant_id,
                event_title=event_title,
                reason=reason,
                cancelled_by_admin=as_admin,
                admin_id=current_user.id if as_admin else None
            )

            db.session.commit()
        except Exception as e:
            from flask import current_app
            current_app.logger.error(f"Error al registrar cancelación de cita en historial: {e}")

        # Mismo reparto que en assign(): dueño de la cita + coordinadores con
        # alcance sobre el programa del evento.
        try:
            from app.sockets.emitters import emit_user_and_coordinators
            emit_user_and_coordinators(
                'appointment:changed',
                {
                    'action': 'cancelled',
                    'appointment_id': appt.id,
                    'event_id': appt.event_id,
                    'slot_id': appt.slot_id,
                    'applicant_id': _public_user_id(appt.applicant_id),
                },
                appt.applicant_id,
                ctx.get('program_id'),
            )
        except Exception:
            pass

        return jsonify({"ok": True, "id": appt.id, "status": appt.status}), 200
    except AppointmentAccessDenied:
        return _appointment_not_found()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

@api_appointments.route('/<int:appointment_id>/change-requests', methods=['POST'])
@login_required
@any_permission_required('appointments.api.change_request', 'appointments.api.assign')
def request_change(appointment_id:int):
    data = request.get_json() or {}

    ctx, as_admin, err = _resolve_manage_context(appointment_id)
    if err:
        return err

    try:
        acr = AppointmentsService.request_change(
            appointment_id=appointment_id,
            requested_by=current_user.id,
            reason=data.get('reason'),
            suggestions=data.get('suggestions'),
            as_admin=as_admin,
        )
        # Sólo a los coordinadores que gestionan el evento. No se emite al
        # aspirante: `requested_by` puede ser el id del coordinador y el único
        # consumidor es la consola de admin (admin/events/detail.js).
        try:
            from app.sockets.emitters import emit_to_coordinators
            emit_to_coordinators(
                'appointment:change_requested',
                {
                    'change_request_id': acr.id,
                    'appointment_id': appointment_id,
                    'requested_by': _public_user_id(current_user.id),
                },
                ctx.get('program_id'),
            )
        except Exception:
            pass
        return jsonify({"ok": True, "id": acr.id}), 201
    except AppointmentAccessDenied:
        return _appointment_not_found()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

@api_appointments.route('/<int:appointment_id>/details', methods=['GET'])
@login_required
def appointment_details(appointment_id: int):
    """Obtiene detalles completos de una cita incluyendo slot, window y evento"""
    from app.models.user import User

    ctx, _as_admin, err = _resolve_manage_context(appointment_id)
    if err:
        return err

    appt = ctx['appointment']
    slot = ctx['slot']
    event = ctx['event']
    if not slot:
        return _deny("NOT_FOUND", "Horario no encontrado.", 404)
    if not event:
        return _deny("NOT_FOUND", "Evento no encontrado.", 404)

    # `_resolve_manage_context` sólo ha comprobado que la cita es del llamante
    # (o que la gestiona). Los campos del evento se re-derivan aquí.
    denied = _event_read_denied(appt, event)
    if denied:
        return denied

    # Información del coordinador que asignó
    assigner = db.session.get(User, appt.assigned_by) if appt.assigned_by else None

    return jsonify({
        "ok": True,
        "appointment": {
            "id": appt.id,
            "status": appt.status,
            "notes": appt.notes,
            "created_at": appt.created_at.isoformat(),
            "slot": {
                "id": slot.id,
                "starts_at": slot.starts_at.isoformat(),
                "ends_at": slot.ends_at.isoformat(),
                "status": slot.status
            },
            "event": {
                "id": event.id,
                "title": event.title,
                "type": event.type,
                "location": event.location,
                "description": event.description
            },
            "assigned_by": {
                # Handle publico, como los demas de este archivo. Era la unica
                # clave de la API que seguia republicando la clave primaria
                # entera de un usuario, justo el identificador enumerable que
                # el cambio a UUID existe para retirar.
                "id": _public_user_id(assigner.id),
                "name": f"{assigner.first_name} {assigner.last_name}"
            } if assigner else None
        }
    }), 200

@api_appointments.route('/mine/active', methods=['GET'])
@login_required
def my_active_appointments():
    """
    Citas vigentes del usuario actual con detalles completos.

    La consulta filtra por `applicant_id == current_user.id` (dentro del
    servicio), así que no hace falta comprobar la propiedad fila a fila: aquí
    no entra la cita de otra persona. Lo que SÍ hace falta —y no había— es la
    guarda del EVENTO: este payload publica tipo, título y sede del evento, y
    la cita por sí sola no autoriza a leerlos. Una cita que el propio aspirante
    se reservó sobre un evento que después se cerró (privado, despublicado u
    oculto a estudiantes) deja de aparecer; la que le asignó su coordinador
    sigue apareciendo, que es la entrevista de admisión.
    """
    contexts = AppointmentsService.list_active_appointment_contexts(current_user.id)

    items = []
    for ctx in contexts:
        appt = ctx['appointment']
        slot = ctx['slot']
        event = ctx['event']
        pending_change = ctx['pending_change']

        if not AppointmentsService.user_may_read_event_of_appointment(
            current_user, appt, event
        ):
            continue

        items.append({
            "id": appt.id,
            "status": appt.status,
            "notes": appt.notes,
            "event_type": event.type,
            "event_title": event.title,
            "location": event.location,
            "starts_at": slot.starts_at.isoformat(),
            "ends_at": slot.ends_at.isoformat(),
            "created_at": appt.created_at.isoformat(),
            "pending_change_request": {
                "id": pending_change.id,
                "reason": pending_change.reason,
                "suggestions": pending_change.suggestions,
                "created_at": pending_change.created_at.isoformat()
            } if pending_change else None
        })

    return jsonify({"ok": True, "appointments": items}), 200

@api_appointments.route('/change-requests/by-event/<int:event_id>', methods=['GET'])
@login_required
@permission_required('appointments.api.assign')
def get_change_requests_by_event(event_id: int):
    """Lista solicitudes de cambio de cita pendientes para un evento específico"""
    from app.models.event import EventSlot
    from app.models.event import Event
    from app.models.user import User
    from app.models.appointment import AppointmentChangeRequest
    from sqlalchemy import select

    event = db.session.get(Event, event_id)
    if not event:
        return _deny("NOT_FOUND", "Evento no encontrado.", 404)

    denied = _event_manage_denied(event)
    if denied:
        return denied

    try:
        results = db.session.execute(
            select(AppointmentChangeRequest, Appointment, EventSlot, User)
            .join(Appointment, AppointmentChangeRequest.appointment_id == Appointment.id)
            .join(EventSlot, Appointment.slot_id == EventSlot.id)
            .join(User, Appointment.applicant_id == User.id)
            .where(
                Appointment.event_id == event_id,
                AppointmentChangeRequest.status == 'pending'
            )
            .order_by(AppointmentChangeRequest.created_at.desc())
        ).all()

        items = []
        for req, appt, slot, user in results:
            items.append({
                "id": req.id,
                "appointment_id": appt.id,
                "student": {
                    "id": str(user.uuid) if user.uuid else None,
                    "full_name": f"{user.first_name} {user.last_name}",
                    "email": user.email
                },
                "current_slot": {
                    "id": slot.id,
                    "starts_at": slot.starts_at.isoformat(),
                    "ends_at": slot.ends_at.isoformat()
                },
                "reason": req.reason,
                "suggestions": req.suggestions,
                "created_at": req.created_at.isoformat()
            })

        return jsonify({"ok": True, "change_requests": items, "count": len(items)}), 200

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@api_appointments.route('/change-requests/<int:req_id>/decision', methods=['PUT'])
@login_required
@permission_required('appointments.api.assign')
def decide_change(req_id:int):
    data = request.get_json() or {}

    ctx = AppointmentsService.get_change_request_context(req_id)
    if not ctx or not ctx.get('appointment'):
        return _deny("NOT_FOUND", "Solicitud de cambio no encontrada.", 404)

    denied = _event_manage_denied(ctx.get('event'))
    if denied:
        return denied

    try:
        acr = AppointmentsService.decide_change(
            request_id=req_id,
            status=data.get('status'),
            decided_by=current_user.id,
            new_slot_id=data.get('new_slot_id')
        )

        # Enviar notificación y correo al aspirante
        try:
            from app.models.event import Event, EventSlot, EventWindow
            from app.services.notification_service import NotificationService

            appt = db.session.get(Appointment, acr.appointment_id)
            slot = db.session.get(EventSlot, appt.slot_id)
            window = db.session.get(EventWindow, slot.event_window_id)
            event = db.session.get(Event, window.event_id)

            new_slot_str = slot.starts_at.strftime('%d/%m/%Y %H:%M') if slot else ''
            location = getattr(event, 'location', None)

            if acr.status == 'accepted':
                NotificationService.notify_appointment_reassigned(
                    user_id=appt.applicant_id,
                    event_title=event.title,
                    appointment_id=appt.id,
                    new_slot_datetime=new_slot_str,
                    old_slot_datetime='',
                    event_id=event.id,
                    location=location
                )
            elif acr.status == 'rejected':
                NotificationService.create_notification(
                    user_id=appt.applicant_id,
                    notification_type='appointment_change_rejected',
                    title='Cambio de horario rechazado',
                    message=f'Tu solicitud de cambio de horario para "{event.title}" fue rechazada. Tu cita original se mantiene el {new_slot_str}.',
                    priority='medium',
                    data={'event_id': event.id, 'event_title': event.title, 'slot_datetime': new_slot_str}
                )
        except Exception as notify_err:
            import logging
            logging.error(f"Error enviando notificación de cambio de cita: {notify_err}")

        return jsonify({"ok": True, "id": acr.id, "status": acr.status}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

@api_appointments.route('/by-slot/<int:slot_id>', methods=['GET'])
@login_required
@permission_required('appointments.api.assign')
def get_appointment_by_slot(slot_id: int):
    """
    Obtiene la cita asignada a un slot (vista de coordinador).

    Sólo nombre y estado: el correo institucional y las notas privadas del
    coordinador NO salen por aquí.
    """
    slot_ctx = AppointmentsService.get_slot_context(slot_id)
    if not slot_ctx or not slot_ctx['event']:
        return _deny("NOT_FOUND", "Horario no encontrado.", 404)

    denied = _event_manage_denied(slot_ctx['event'])
    if denied:
        return denied

    appointment = AppointmentsService.get_active_appointment_for_slot(slot_id)

    if not appointment:
        return jsonify({"ok": True, "appointment": None}), 200

    # Obtener datos del estudiante
    from app.models.user import User
    student = db.session.get(User, appointment.applicant_id)

    return jsonify({
        "ok": True,
        "appointment": {
            "id": appointment.id,
            "status": appointment.status,
            "student": {
                "id": str(student.uuid) if student.uuid else None,
                "full_name": f"{student.first_name} {student.last_name}"
            } if student else None,
            "assigned_by": _public_user_id(appointment.assigned_by),
            "created_at": appointment.created_at.isoformat()
        }
    }), 200

@api_appointments.route('/<int:appointment_id>/mark-status', methods=['POST'])
@login_required
@permission_required('appointments.api.assign')
def mark_appointment_status(appointment_id: int):
    """
    Marca el estado de una cita (done, no_show).
    Si se marca como 'done' y es una entrevista, actualiza admission_status.
    """
    from app.models import UserProgram

    data = request.get_json() or {}
    new_status = data.get('status')
    notes = data.get('notes')

    if new_status not in ['done', 'no_show']:
        return jsonify({
            "ok": False,
            "error": "Estado invalido. Usar 'done' o 'no_show'"
        }), 400

    ctx, _as_admin, err = _resolve_manage_context(appointment_id)
    if err:
        return err

    appt = ctx['appointment']
    event = ctx['event']
    if not ctx['slot'] or not event:
        return _deny("NOT_FOUND", "Horario no encontrado.", 404)

    try:
        # Actualizar estado de la cita
        appt.status = new_status
        if notes:
            appt.notes = f"{appt.notes or ''}\n[{new_status.upper()}]: {notes}".strip()

        # Si es entrevista y se marca como 'done', actualizar admission_status
        if new_status == 'done' and event.type == 'interview' and event.program_id:
            user_program = UserProgram.query.filter_by(
                user_id=appt.applicant_id,
                program_id=event.program_id
            ).first()

            if user_program and user_program.admission_status == 'in_progress':
                user_program.admission_status = 'interview_completed'

                # Registrar en historial
                UserHistoryService.log_action(
                    user_id=appt.applicant_id,
                    admin_id=current_user.id,
                    action='interview_completed',
                    details=f'Entrevista completada: {event.title}'
                )

        db.session.commit()

        return jsonify({
            "ok": True,
            "flash": [{"level": "success", "message": f"Cita marcada como {new_status}"}],
            "id": appointment_id,
            "status": new_status
        }), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500


@api_appointments.route('/<int:appointment_id>/cancel', methods=['POST'])
@login_required
@permission_required('appointments.api.assign')
def cancel_appointment_by_coordinator(appointment_id: int):
    """Cancelar cita desde el coordinador con motivo"""
    data = request.get_json() or {}
    reason = data.get('reason', 'Cancelada por coordinador')

    ctx, as_admin, err = _resolve_manage_context(appointment_id)
    if err:
        return err

    try:
        appt = AppointmentsService.cancel_appointment(
            appointment_id,
            reason=reason,
            acting_user_id=current_user.id,
            as_admin=as_admin,
        )

        # Registrar en historial (incluye notificación automática)
        try:
            UserHistoryService.log_appointment_cancellation(
                user_id=appt.applicant_id,
                event_title=ctx['event_title'],
                reason=reason,
                cancelled_by_admin=True,
                admin_id=current_user.id
            )
            db.session.commit()
        except Exception as e:
            from flask import current_app
            current_app.logger.error(f"Error al registrar cancelación: {e}")

        # Mismo reparto que en assign(): dueño de la cita + coordinadores con
        # alcance sobre el programa del evento.
        try:
            from app.sockets.emitters import emit_user_and_coordinators
            emit_user_and_coordinators(
                'appointment:changed',
                {
                    'action': 'cancelled',
                    'appointment_id': appt.id,
                    'event_id': appt.event_id,
                    'slot_id': appt.slot_id,
                    'applicant_id': _public_user_id(appt.applicant_id),
                    'cancelled_by_coordinator': True,
                },
                appt.applicant_id,
                ctx.get('program_id'),
            )
        except Exception:
            pass

        return jsonify({"ok": True, "id": appointment_id}), 200

    except AppointmentAccessDenied:
        return _appointment_not_found()
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
