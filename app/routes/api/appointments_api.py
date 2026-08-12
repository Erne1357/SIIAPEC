from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from app.utils.permissions import (
    permission_required,
    any_permission_required,
    guard_user_scope,
)
from app.services import program_scope_service as scope_service
from app.services.appointments_service import AppointmentsService, AppointmentAccessDenied
from app.services.events_service import EventsService
from app.services.user_history_service import UserHistoryService
from app.models.appointment import Appointment
from app import db

api_appointments = Blueprint('api_appointments', __name__, url_prefix='/api/v1/appointments')

#: Permiso que distingue al coordinador (gestiona citas ajenas) del aspirante
#: (sólo gestiona la suya).
_STAFF_PERMISSION = 'appointments.api.assign'


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


def _applicant_may_book(event) -> bool:
    """
    Un aspirante sólo puede agendarse en un evento publicado de su programa.

    OJO: aquí un evento sin programa SÍ vale, y es correcto — es el nivel de
    PARTICIPANTE (el evento institucional se publica para toda la institución,
    igual que en `events_api._event_is_public_for_current_user`), y el aspirante
    sólo se agenda a sí mismo. No copies esta forma a una guarda de GESTIÓN:
    para eso está `_event_manage_denied`.
    """
    if not event or not event.visible_to_students or event.status != 'published':
        return False
    if event.program_id is None:
        return True
    return event.program_id in scope_service.program_ids_of_user(current_user.id)


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
        try:
            applicant_id = int(data.get('applicant_id') or current_user.id)
        except (TypeError, ValueError):
            return _deny("VALIDATION_ERROR", "applicant_id inválido.", 400)
        # …y sólo sobre aspirantes de esos mismos programas.
        denied = guard_user_scope(applicant_id)
        if denied:
            return denied
    else:
        # Un aspirante sólo puede agendarse a sí mismo. El applicant_id del
        # cuerpo se ignora deliberadamente.
        applicant_id = current_user.id
        if not _applicant_may_book(event):
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

        # Broadcast a coordinadores
        try:
            from app.extensions import socketio
            socketio.emit(
                'appointment:changed',
                {
                    'action': 'booked',
                    'appointment_id': appt.id,
                    'event_id': appt.event_id,
                    'slot_id': appt.slot_id,
                    'applicant_id': appt.applicant_id,
                },
                room='role:coordinator',
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
    # listado simple; ajusta filtros si quieres por evento
    appts = Appointment.query.filter_by(applicant_id=current_user.id).all()
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

        # Broadcast a coordinadores
        try:
            from app.extensions import socketio
            socketio.emit(
                'appointment:changed',
                {
                    'action': 'cancelled',
                    'appointment_id': appt.id,
                    'event_id': appt.event_id,
                    'slot_id': appt.slot_id,
                    'applicant_id': appt.applicant_id,
                },
                room='role:coordinator',
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
        # Broadcast a coordinadores
        try:
            from app.extensions import socketio
            socketio.emit(
                'appointment:change_requested',
                {
                    'change_request_id': acr.id,
                    'appointment_id': appointment_id,
                    'requested_by': current_user.id,
                },
                room='role:coordinator',
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
                "id": assigner.id,
                "name": f"{assigner.first_name} {assigner.last_name}"
            } if assigner else None
        }
    }), 200

@api_appointments.route('/mine/active', methods=['GET'])
@login_required
def my_active_appointments():
    """Obtiene todas las citas activas del usuario actual con detalles completos"""
    from app.models.event import Event, EventSlot, EventWindow

    appointments = Appointment.query.filter_by(
        applicant_id=current_user.id,
        status='scheduled'
    ).all()

    items = []
    for appt in appointments:
        slot = db.session.get(EventSlot, appt.slot_id)
        if not slot:
            continue

        window = db.session.get(EventWindow, slot.event_window_id)
        if not window:
            continue

        event = db.session.get(Event, window.event_id)
        if not event:
            continue

        # Verificar si hay solicitud de cambio pendiente
        from app.models.appointment import AppointmentChangeRequest
        pending_change = AppointmentChangeRequest.query.filter_by(
            appointment_id=appt.id,
            status='pending'
        ).first()

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
                    "id": user.id,
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
                "id": student.id,
                "full_name": f"{student.first_name} {student.last_name}"
            } if student else None,
            "assigned_by": appointment.assigned_by,
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

        # Broadcast a coordinadores
        try:
            from app.extensions import socketio
            socketio.emit(
                'appointment:changed',
                {
                    'action': 'cancelled',
                    'appointment_id': appt.id,
                    'event_id': appt.event_id,
                    'slot_id': appt.slot_id,
                    'applicant_id': appt.applicant_id,
                    'cancelled_by_coordinator': True,
                },
                room='role:coordinator',
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
