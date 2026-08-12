from datetime import date, time
from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user
from app.utils.permissions import permission_required, guard_program_scope
from app.services import program_scope_service as scope_service
from app.services.events_service import EventsService
from app.models.event import Event, EventWindow, EventSlot
from app.models.program import Program
from app.models.appointment import Appointment
from sqlalchemy import select, or_
from app import db
import logging

api_events = Blueprint('api_events', __name__, url_prefix='/api/v1/events')


# ============================================================================
# Alcance por programa — permiso ≠ alcance
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


def _event_managed_by_current_user(event) -> bool:
    """
    True si `current_user` puede GESTIONAR este evento. Regla única para todo
    el módulo; vive en `EventsService.user_may_manage_event`.

    - Evento CON programa: exige alcance sobre ese programa. Aquí se cierra el
      paso de un coordinador al calendario de otro.
    - Evento SIN programa (institucional): sólo alcance global.

    La excepción institucional que había antes ("sin programa = de todos los
    gestores") era la puerta de atrás del módulo: convertía cada evento con
    `program_id = NULL` en un objeto que cualquier coordinador podía editar,
    concluir, archivar, rellenar de ventanas y — por `update_event` — reclamar
    como suyo poniéndole su propio program_id. Con `create_event` ya cerrado,
    ningún usuario con alcance limitado puede fabricar uno de esos eventos, así
    que la regla estricta no le quita nada que él pueda producir hoy.
    """
    return EventsService.user_may_manage_event(current_user, event)


def _event_participable_by_current_user(event) -> bool:
    """
    True si `current_user` puede PARTICIPAR en este evento: verlo, apuntarse,
    ver sus ponentes, sus imágenes y sus horarios.

    Regla única para todo el módulo; vive en
    `EventsService.user_may_participate_in_event`. Los sitios que sirven a las
    dos audiencias preguntan `gestión OR participación` — nunca inventan una
    tercera variante. Tres de ellos (hosts, imágenes y el servidor de bytes)
    usaban un predicado que ignoraba `visibility` y el programa, así que
    cualquier cuenta autenticada leía el cartel de ponentes y las imágenes de
    un evento PRIVADO de cualquier posgrado.
    """
    return EventsService.user_may_participate_in_event(current_user, event)


def _check_event_access(event_id: int):
    """
    Carga el evento y exige autoridad de GESTIÓN sobre él.

    404 EN LAS DOS RAMAS, a propósito. Estas rutas se dirigen por un id crudo
    que el llamante puede teclear, y el listado de gestión
    (`GET /api/v1/events`) ya está acotado a su alcance: un evento que no
    gestiona nunca le llegó en una lista, así que su existencia no es
    información que él tenga. Contestar 403 aquí y 404 veinte líneas más allá
    convertía el par en un oráculo: un `program_admin` recorría los ids y sabía
    cuáles existen —cuántos eventos hay en la institución y en qué rango de
    ids— sin ver ninguno. `invitations_api.cancel_invitation` ya había elegido
    404 por este motivo; ahora el módulo entero dice lo mismo.

    El bucket contrario —403— es para las rutas de PARTICIPACIÓN
    (`/slots`, `/hosts`, `/images`, `/public/<id>`): allí el evento sí llegó en
    una lista que el llamante recibió legítimamente, su existencia ya la
    conoce, y el 403 le dice algo útil ("existe pero no es para ti") en vez de
    mandarlo a buscar un id que tiene delante.

    Returns:
        (event, None) si procede; (None, respuesta_error) si no.
    """
    event = db.session.get(Event, event_id)
    if not event or not _event_managed_by_current_user(event):
        return None, _deny("NOT_FOUND", "Evento no encontrado.", 404)
    return event, None


@api_events.route('', methods=['POST'])
@login_required
@permission_required('events.api.create')
def create_event():
    data = request.get_json() or {}

    # Un usuario con alcance limitado DEBE anclar el evento a uno de sus
    # programas: sin program_id el evento sería institucional y se saltaría
    # todos los controles de alcance posteriores.
    program_id = data.get('program_id')
    if program_id not in (None, ''):
        try:
            program_id = int(program_id)
        except (TypeError, ValueError):
            return _deny("VALIDATION_ERROR", "program_id inválido.", 400)
    else:
        program_id = None

    if not scope_service.is_global_scope(current_user):
        if not program_id:
            return _deny(
                "VALIDATION_ERROR",
                "Debes indicar el programa al que pertenece el evento.",
                400,
            )
        denied = guard_program_scope(program_id)
        if denied:
            return denied

    ev = EventsService.create_event(
        program_id=program_id,
        type_=data.get('type', 'interview'),
        title=data.get('title'),
        description=data.get('description'),
        location=data.get('location'),
        created_by=current_user.id,
        visible_to_students=bool(data.get('visible_to_students', True)),
        capacity_type=data.get('capacity_type', 'single'),
        max_capacity=data.get('max_capacity'),
        requires_registration=bool(data.get('requires_registration', True)),
        allows_attendance_tracking=bool(data.get('allows_attendance_tracking', False)),
        status=data.get('status', 'published'),
        academic_period_id=data.get('academic_period_id'),
        visibility=data.get('visibility', 'public'),
        reminders_enabled=bool(data.get('reminders_enabled', True))
    )

    from app.sockets.emitters import emit_event_change
    emit_event_change({
        'action': 'created',
        'event_id': ev.id,
        'program_id': ev.program_id,
        'title': ev.title,
    }, event=ev)

    return jsonify({"ok": True, "id": ev.id}), 201

@api_events.route('/<int:event_id>/windows', methods=['POST'])
@login_required
@permission_required('events.api.create_window')
def add_window(event_id:int):
    event, err = _check_event_access(event_id)
    if err:
        return err

    data = request.get_json() or {}
    current_app.logger.warning(f"Received add_window request with data: {data}")
    try:
        current_app.logger.warning(f"Adding window with data: {data}")
        win = EventsService.add_window(
            event_id=event_id,
            window_date=date.fromisoformat(str(data.get('date'))),
            start=time.fromisoformat(str(data.get('start_time'))),
            end=time.fromisoformat(str(data.get('end_time'))),
            slot_minutes=int(data.get('slot_minutes')),
            timezone_str=data.get('timezone','America/Ciudad_Juarez')
        )
        return jsonify({"ok": True, "id": win.id}), 201
    except Exception as e:
        current_app.logger.warning(str(e))
        return jsonify({"ok": False, "error": str(e)}), 400

@api_events.route('/windows/<int:window_id>/generate-slots', methods=['POST'])
@login_required
@permission_required('events.api.generate_slots')
def generate_slots(window_id:int):
    window = db.session.get(EventWindow, window_id)
    # Un solo 404 para "no existe" y para "no es de un evento que gestionas":
    # dos mensajes distintos reconstruyen el oráculo que `_check_event_access`
    # acaba de cerrar, sólo que sobre ids de ventana.
    if not window or _check_event_access(window.event_id)[1]:
        return _deny("NOT_FOUND", "Ventana no encontrada.", 404)

    try:
        result = EventsService.generate_slots(window_id)
        # `generate_slots` devuelve {'created', 'skipped', 'total'}: antes se
        # publicaba len(dict) — siempre 3.
        return jsonify({
            "ok": True,
            "created": result.get('created', 0),
            "skipped": result.get('skipped', 0),
            "total": result.get('total', 0),
        }), 201
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

@api_events.route('/<int:event_id>/slots', methods=['GET'])
@login_required
def list_slots(event_id:int):
    event = db.session.get(Event, event_id)
    if not event:
        return _deny("NOT_FOUND", "Evento no encontrado.", 404)

    # Gestores del programa, o quien pueda participar en el evento (mismo
    # criterio que el detalle público).
    if not (_event_managed_by_current_user(event) or _event_participable_by_current_user(event)):
        return _deny("FORBIDDEN", "No tienes acceso a este evento.", 403)

    status = request.args.get('status')
    items = EventsService.list_slots(event_id=event_id, status=status)
    payload = [{"id": s.id, "starts_at": s.starts_at.isoformat(), "ends_at": s.ends_at.isoformat(), "status": s.status} for s in items]
    return jsonify({"ok": True, "items": payload}), 200

@api_events.route('', methods=['GET'])
@login_required
@permission_required('events.api.list')
def list_events():
    """
    Lista eventos para el PANEL DE ADMINISTRACIÓN — no es la lista pública.

    La lista del aspirante y del estudiante es `GET /api/v1/events/public`, que
    pasa por `user_may_participate_in_event`. Esta trae borradores, eventos
    privados, los ocultos al alumnado y los contadores de registros e
    invitaciones pendientes: es material de gestión y `events.api.list` es un
    permiso de gestión. Los roles applicant y student lo tenían por semilla, así
    que un aspirante leía el calendario institucional completo —incluidos los
    borradores— desde aquí; ver el parche
    `database/DML/patches/2026_08_12_01_events_list_revoke_applicant_student.sql`.

    Sigue incluyendo los institucionales (sin programa) para que un coordinador
    vea el calendario general, pero sólo los ya publicados: el borrador
    institucional es de la Jefatura y nadie sin alcance global puede tocarlo
    (`EventsService._institutional_readable_clause`). Cada fila trae
    `can_manage`; el front debe ocultar/deshabilitar las acciones cuando venga
    en False.
    """
    from app.models.academic_period import AcademicPeriod

    filters = {
        'academic_period_id': request.args.get('academic_period_id', type=int),
        'program_id': request.args.get('program_id', type=int),
        'type': request.args.get('type'),
        'status': request.args.get('status'),
        'capacity_type': request.args.get('capacity_type'),
        'search': request.args.get('search'),
    }

    from app.models.event import EventAttendance, EventInvitation

    accessible_pids = current_user.get_accessible_program_ids()
    events = EventsService.list_admin_events(accessible_pids, filters)

    items = []
    for event in events:
        windows_count = EventWindow.query.filter_by(event_id=event.id).count()

        slots_query = db.session.query(EventSlot).join(
            EventWindow, EventWindow.id == EventSlot.event_window_id
        ).filter(EventWindow.event_id == event.id)

        slots_total = slots_query.count()
        slots_booked = slots_query.filter(EventSlot.status == 'booked').count()

        registrations_count = EventAttendance.query.filter_by(event_id=event.id).count()
        invitations_pending = EventInvitation.query.filter_by(event_id=event.id, status='pending').count()

        program = db.session.get(Program, event.program_id) if event.program_id else None
        period = db.session.get(AcademicPeriod, event.academic_period_id) if event.academic_period_id else None

        items.append({
            "id": event.id,
            "title": event.title,
            "type": event.type,
            "status": event.status,
            "location": event.location,
            "description": event.description,
            "program_id": event.program_id,
            "program_name": program.name if program else None,
            "academic_period_id": event.academic_period_id,
            "academic_period_code": period.code if period else None,
            "visible_to_students": event.visible_to_students,
            "visibility": event.visibility,
            "reminders_enabled": event.reminders_enabled,
            "capacity_type": event.capacity_type,
            "max_capacity": event.max_capacity,
            "event_date": event.event_date.isoformat() if event.event_date else None,
            "event_end_date": event.event_end_date.isoformat() if event.event_end_date else None,
            "windows_count": windows_count,
            "slots_total": slots_total,
            "slots_booked": slots_booked,
            "registrations_count": registrations_count,
            "invitations_pending": invitations_pending,
            "can_manage": _event_managed_by_current_user(event),
            "created_at": event.created_at.isoformat()
        })

    return jsonify({"ok": True, "items": items}), 200

@api_events.route('/<int:event_id>', methods=['DELETE'])
@login_required
@permission_required('events.api.manage')
def delete_event(event_id: int):
    """Elimina un evento y todos sus slots/appointments asociados"""
    # Misma regla —y misma respuesta— que el resto de la gestión: borrar
    # arrastra ventanas, horarios y citas, y un evento institucional sólo lo
    # borra el alcance global. El 403 propio que había aquí delataba qué ids
    # existen; ver `_check_event_access`.
    event, err = _check_event_access(event_id)
    if err:
        return err

    # Verificar si hay appointments activas
    appointments_count = db.session.query(Appointment).join(
        EventSlot, EventSlot.id == Appointment.slot_id
    ).join(
        EventWindow, EventWindow.id == EventSlot.event_window_id
    ).filter(
        EventWindow.event_id == event_id,
        Appointment.status == 'scheduled'
    ).count()
    
    if appointments_count > 0:
        force = request.args.get('force') in ('true', '1', 'yes')
        if not force:
            return jsonify({
                "ok": False, 
                "requires_force": True,
                "message": f"El evento tiene {appointments_count} citas activas. Usa ?force=true para eliminar."
            }), 409
    
    try:
        # Cascade debería manejar la eliminación de windows, slots y appointments
        program_id = event.program_id
        title = event.title
        db.session.delete(event)
        db.session.commit()

        # Sin `event=`: la fila ya no existe y no se puede releer su status ni
        # su visibility, así que sólo se avisa a la gestión. Es el lado seguro.
        from app.sockets.emitters import emit_event_change
        emit_event_change({
            'action': 'deleted',
            'event_id': event_id,
            'program_id': program_id,
            'title': title,
        })

        return jsonify({"ok": True, "deleted": event_id}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500

@api_events.route('/<int:event_id>', methods=['GET'])
@login_required
@permission_required('events.api.manage')
def get_event_details(event_id: int):
    """Obtiene detalles completos de un evento incluyendo sus ventanas"""
    from app.models.academic_period import AcademicPeriod
    from app.models.event import EventAttendance, EventInvitation

    event, err = _check_event_access(event_id)
    if err:
        return err

    windows = EventWindow.query.filter_by(event_id=event_id).all()

    program = db.session.get(Program, event.program_id) if event.program_id else None
    period = db.session.get(AcademicPeriod, event.academic_period_id) if event.academic_period_id else None

    slots_query = db.session.query(EventSlot).join(
        EventWindow, EventWindow.id == EventSlot.event_window_id
    ).filter(EventWindow.event_id == event_id)
    slots_total = slots_query.count()
    slots_booked = slots_query.filter(EventSlot.status == 'booked').count()

    registrations_count = EventAttendance.query.filter_by(event_id=event_id).count()
    invitations_pending = EventInvitation.query.filter_by(event_id=event_id, status='pending').count()

    payload = event.to_dict()
    payload.update({
        "ok": True,
        "program_name": program.name if program else None,
        "academic_period_code": period.code if period else None,
        "windows_count": len(windows),
        "slots_total": slots_total,
        "slots_booked": slots_booked,
        "registrations_count": registrations_count,
        "invitations_pending": invitations_pending,
        "windows": [{
            "id": w.id,
            "date": w.date.isoformat(),
            "start_time": w.start_time.isoformat(),
            "end_time": w.end_time.isoformat(),
            "slot_minutes": w.slot_minutes
        } for w in windows]
    })
    return jsonify(payload), 200
@api_events.route('/windows/<int:window_id>', methods=['DELETE'])
@login_required
@permission_required('events.api.manage')
def delete_window(window_id: int):
    """Elimina una ventana y todos sus slots"""
    from app.services.events_service import EventsService
    
    window = db.session.get(EventWindow, window_id)
    # Un solo 404 para las dos razones — ver `generate_slots`.
    if not window or _check_event_access(window.event_id)[1]:
        return _deny("NOT_FOUND", "Ventana no encontrada.", 404)

    force = request.args.get('force') in ('true', '1', 'yes')

    try:
        EventsService.delete_window(window_id, force=force)
        return jsonify({"ok": True, "deleted": window_id}), 200
    except ValueError as e:
        if "force=True" in str(e):
            return jsonify({
                "ok": False,
                "requires_force": True,
                "message": str(e)
            }), 409
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500


@api_events.route('/slots/<int:slot_id>', methods=['DELETE'])
@login_required
@permission_required('events.api.manage')
def delete_slot(slot_id: int):
    """Elimina un slot individual"""
    from app.services.events_service import EventsService
    
    slot = db.session.get(EventSlot, slot_id)
    window = db.session.get(EventWindow, slot.event_window_id) if slot else None
    # Un solo 404 para las tres razones — ver `generate_slots`.
    if not slot or not window or _check_event_access(window.event_id)[1]:
        return _deny("NOT_FOUND", "Horario no encontrado.", 404)

    force = request.args.get('force') in ('true', '1', 'yes')

    try:
        EventsService.delete_slot(slot_id, force=force)
        return jsonify({"ok": True, "deleted": slot_id}), 200
    except ValueError as e:
        if "force=True" in str(e):
            return jsonify({
                "ok": False,
                "requires_force": True,
                "message": str(e)
            }), 409
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500


@api_events.route('/<int:event_id>/windows-list', methods=['GET'])
@login_required
@permission_required('events.api.manage')
def list_event_windows(event_id: int):
    """Lista todas las ventanas de un evento con estadísticas"""
    event, err = _check_event_access(event_id)
    if err:
        return err

    windows = EventWindow.query.filter_by(event_id=event_id).order_by(
        EventWindow.date.asc(), EventWindow.start_time.asc()
    ).all()
    
    items = []
    for window in windows:
        # Contar slots
        slots_total = EventSlot.query.filter_by(event_window_id=window.id).count()
        slots_free = EventSlot.query.filter_by(
            event_window_id=window.id, status='free'
        ).count()
        slots_booked = EventSlot.query.filter_by(
            event_window_id=window.id, status='booked'
        ).count()
        
        items.append({
            "id": window.id,
            "date": window.date.isoformat(),
            "start_time": window.start_time.isoformat(),
            "end_time": window.end_time.isoformat(),
            "slot_minutes": window.slot_minutes,
            "slots_generated": window.slots_generated,
            "slots_total": slots_total,
            "slots_free": slots_free,
            "slots_booked": slots_booked
        })
    
    return jsonify({"ok": True, "windows": items}), 200

@api_events.route('/<int:event_id>', methods=['PUT'])
@login_required
@permission_required('events.api.manage')
def update_event(event_id: int):
    """
    Actualizar información de un evento.

    `program_id` es INMUTABLE para quien no tiene alcance global. El programa
    no es un campo más: decide quién manda sobre el evento, y dejarlo editable
    convertía la ruta en una herramienta de apropiación — el coordinador de A
    reclamaba un evento institucional poniéndole su program_id, o soltaba el
    suyo a NULL para volverlo institución-wide y sacarlo del alcance de
    cualquiera menos el global. Mover un evento además arrastra sus ventanas,
    horarios, citas, invitaciones y registros, que son de alumnos del programa
    de origen. Reasignar es un acto de la Jefatura de Posgrado; para el resto,
    el camino es borrar y volver a crear.

    Reenviar el mismo program_id que ya tiene el evento es un no-op aceptado:
    el formulario manda el objeto completo.
    """
    event, err = _check_event_access(event_id)
    if err:
        return err

    data = request.get_json() or {}

    if 'program_id' in data:
        new_pid = data.get('program_id')
        if new_pid in (None, ''):
            new_pid = None
        else:
            try:
                new_pid = int(new_pid)
            except (TypeError, ValueError):
                return _deny("VALIDATION_ERROR", "program_id inválido.", 400)

        if new_pid == event.program_id:
            data.pop('program_id')          # no-op: no lo toques
        elif not scope_service.is_global_scope(current_user):
            return _deny(
                "FORBIDDEN",
                "No puedes cambiar el programa de un evento. Pide a la "
                "Jefatura de Posgrado que lo reasigne.",
                403,
            )
        else:
            # Alcance global: puede reasignar, incluso a institucional.
            data['program_id'] = new_pid

    try:
        event = EventsService.update_event(event_id, data)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500

    from app.sockets.emitters import emit_event_change
    emit_event_change({
        'action': 'updated',
        'event_id': event.id,
        'program_id': event.program_id,
        'title': event.title,
    }, event=event)

    return jsonify({"ok": True, "id": event.id}), 200

@api_events.route('/public', methods=['GET'])
@login_required
def list_public_events():
    """Lista eventos visibles para estudiantes (filtrados por periodo activo + visibilidad)."""
    from app.models.event import EventAttendance, EventImage, EventHost
    from app.models.user import User

    annotated = EventsService.get_public_events_with_invitation_status(current_user.id)

    items = []
    for entry in annotated:
        event = entry['event']
        program = db.session.get(Program, event.program_id) if event.program_id else None
        current_registrations = EventAttendance.query.filter_by(
            event_id=event.id,
            status='registered'
        ).count()

        # Cover path (elimina N+1 en frontend)
        cover = EventImage.query.filter_by(event_id=event.id, is_cover=True).first()
        cover_path = cover.path if cover else None

        # Hosts summary: máximo 3 con URL de foto ya construida (sin N+1 frontend)
        from flask import url_for as _url_for
        hosts_summary = []
        hosts = EventHost.query.filter_by(event_id=event.id).order_by(
            EventHost.display_order.asc()
        ).limit(3).all()
        for h in hosts:
            if h.user_id:
                u = db.session.get(User, h.user_id)
                photo_url = None
                if u:
                    try:
                        photo_url = u.avatar_url
                    except Exception:
                        photo_url = None
                hosts_summary.append({
                    'name': f"{u.first_name} {u.last_name}" if u else 'Ponente',
                    'photo_url': photo_url,
                    'is_external': False,
                    'role_label': h.role_label,
                })
            else:
                photo_url = None
                if h.external_photo_path:
                    filename = h.external_photo_path.split('/')[-1]
                    photo_url = f"/files/event/{event.id}/hosts/{filename}"
                hosts_summary.append({
                    'name': h.external_name,
                    'photo_url': photo_url,
                    'is_external': True,
                    'role_label': h.role_label,
                })

        hosts_total = EventHost.query.filter_by(event_id=event.id).count()

        # Es preview si es privado y NO tiene invitación (lo ve por ser creador/admin)
        is_preview = (
            event.visibility == 'private'
            and not entry['my_invitation_status']
        )
        is_creator = event.created_by == current_user.id

        items.append({
            "id": event.id,
            "title": event.title,
            "type": event.type,
            "description": event.description,
            "location": event.location,
            "capacity_type": event.capacity_type,
            "max_capacity": event.max_capacity,
            "current_registrations": current_registrations,
            "program_id": event.program_id,
            "program_name": program.name if program else None,
            "academic_period_id": event.academic_period_id,
            "visibility": event.visibility,
            "my_invitation_status": entry['my_invitation_status'],
            "is_preview": is_preview,
            "is_creator": is_creator,
            "event_date": event.event_date.isoformat() if event.event_date else None,
            "event_end_date": event.event_end_date.isoformat() if event.event_end_date else None,
            "created_at": event.created_at.isoformat(),
            "cover_path": cover_path,
            "hosts_summary": hosts_summary,
            "hosts_total": hosts_total,
        })

    return jsonify({"ok": True, "items": items}), 200


@api_events.route('/public/<int:event_id>', methods=['GET'])
@login_required
def get_public_event_detail(event_id: int):
    """
    Detalle público de un evento visible para estudiantes.

    Incluye:
      - datos del evento + programa
      - registro actual del usuario (si aplica)
      - cita actual del usuario (si aplica, para capacity_type='single')
      - ventanas con slots (para eventos 1:1)
      - conteo de registros actuales (para multiple/unlimited)
    """
    from app.models.event import EventAttendance
    from app.models.appointment import Appointment

    event = db.session.get(Event, event_id)
    if not event:
        return jsonify({"ok": False, "error": "Evento no encontrado"}), 404

    if not event.visible_to_students or event.status != 'published':
        return jsonify({"ok": False, "error": "Evento no disponible"}), 403

    # ACL — el creador, quien gestiona el evento, o quien puede participar en
    # él (`EventsService.user_may_participate_in_event`: público de su programa
    # o institucional, o invitado).
    #
    # Ya NO cuenta estar registrado. El registro es una escritura que el propio
    # usuario se concede (`POST /api/v1/attendance/event/<id>/register`), así
    # que aceptarlo aquí era regalar la llave: bastaba con apuntarse a un evento
    # privado de otro posgrado para que este endpoint devolviera su
    # descripción, su sede, su cupo y sus horarios. Una autorización que el
    # llamante puede firmarse a sí mismo no es una autorización.
    #
    # La rama de programa tampoco se resuelve ya a mano: `UserProgram...first()`
    # leía UNA sola fila, de modo que un usuario con dos programas quedaba
    # fuera del evento de su segundo programa.
    is_creator = event.created_by == current_user.id
    is_admin = _event_managed_by_current_user(event)

    from app.models.event import EventInvitation as _EI
    has_invitation = _EI.query.filter_by(
        event_id=event.id, user_id=current_user.id
    ).first() is not None

    if not (is_creator or is_admin or _event_participable_by_current_user(event)):
        return jsonify({"ok": False, "error": "Sin acceso a este evento"}), 403

    program = db.session.get(Program, event.program_id) if event.program_id else None

    # Registro actual del usuario en eventos multiple/unlimited
    my_registration = None
    if event.capacity_type in ('multiple', 'unlimited'):
        att = EventAttendance.query.filter_by(
            event_id=event.id, user_id=current_user.id
        ).first()
        if att:
            my_registration = {
                'id': att.id,
                'status': att.status,
                'registered_at': att.registered_at.isoformat(),
                'attended_at': att.attended_at.isoformat() if att.attended_at else None,
            }

    # Invitación actual del usuario a este evento
    from app.models.event import EventInvitation
    my_invitation = None
    inv = EventInvitation.query.filter_by(
        event_id=event.id, user_id=current_user.id
    ).first()
    if inv:
        my_invitation = {
            'id': inv.id,
            'status': inv.status,
            'invited_at': inv.invited_at.isoformat() if inv.invited_at else None,
            'responded_at': inv.responded_at.isoformat() if inv.responded_at else None,
        }

    # Cita actual del usuario en eventos single (1:1)
    my_appointment = None
    if event.capacity_type == 'single':
        appt = Appointment.query.filter(
            Appointment.event_id == event.id,
            Appointment.applicant_id == current_user.id,
            Appointment.status == 'scheduled',
        ).first()
        if appt:
            slot = db.session.get(EventSlot, appt.slot_id)
            my_appointment = {
                'id': appt.id,
                'slot_id': appt.slot_id,
                'status': appt.status,
                'starts_at': slot.starts_at.isoformat() if slot else None,
                'ends_at': slot.ends_at.isoformat() if slot else None,
            }

    # Ventanas con slots (solo para eventos 1:1 o cuando aplique)
    windows = []
    if event.capacity_type == 'single':
        wins = EventWindow.query.filter_by(event_id=event.id).order_by(
            EventWindow.date.asc(), EventWindow.start_time.asc()
        ).all()
        for w in wins:
            slots = EventSlot.query.filter_by(event_window_id=w.id).order_by(
                EventSlot.starts_at.asc()
            ).all()
            windows.append({
                'id': w.id,
                'date': w.date.isoformat() if w.date else None,
                'start_time': w.start_time.isoformat() if w.start_time else None,
                'end_time': w.end_time.isoformat() if w.end_time else None,
                'slot_minutes': w.slot_minutes,
                'slots': [{
                    'id': s.id,
                    'starts_at': s.starts_at.isoformat(),
                    'ends_at': s.ends_at.isoformat(),
                    'status': s.status,
                } for s in slots],
            })

    # Conteo de registros (multiple/unlimited)
    current_registrations = 0
    if event.capacity_type in ('multiple', 'unlimited'):
        current_registrations = EventAttendance.query.filter_by(
            event_id=event.id, status='registered'
        ).count()

    return jsonify({
        "ok": True,
        "event": {
            "id": event.id,
            "title": event.title,
            "type": event.type,
            "description": event.description,
            "location": event.location,
            "capacity_type": event.capacity_type,
            "max_capacity": event.max_capacity,
            "current_registrations": current_registrations,
            "requires_registration": event.requires_registration,
            "status": event.status,
            "visibility": event.visibility,
            "event_date": event.event_date.isoformat() if event.event_date else None,
            "event_end_date": event.event_end_date.isoformat() if event.event_end_date else None,
            "program_id": event.program_id,
            "program_name": program.name if program else None,
            "is_creator": is_creator,
            "is_preview": event.visibility == 'private' and not has_invitation,
            "created_at": event.created_at.isoformat(),
        },
        "my_registration": my_registration,
        "my_appointment": my_appointment,
        "my_invitation": my_invitation,
        "windows": windows,
    }), 200


# ============================================================================
# STATUS TRANSITIONS (conclude / archive / unarchive)
# ============================================================================

@api_events.route('/<int:event_id>/conclude', methods=['POST'])
@login_required
@permission_required('events.api.conclude')
def conclude_event(event_id: int):
    """Concluye evento: status='completed', cancela pending invitations, purga media."""
    event, err = _check_event_access(event_id)
    if err:
        return err
    try:
        event = EventsService.conclude_event(event_id, current_user.id)

        from app.sockets.emitters import emit_event_change
        emit_event_change({
            'action': 'concluded',
            'event_id': event_id,
            'program_id': event.program_id,
            'title': event.title,
        }, event=event)
        return jsonify({"ok": True, "status": event.status}), 200
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500


@api_events.route('/<int:event_id>/archive', methods=['POST'])
@login_required
@permission_required('events.api.archive')
def archive_event(event_id: int):
    """Archiva evento: oculto del público, cancela invitaciones, notifica registrados, purga media."""
    event, err = _check_event_access(event_id)
    if err:
        return err
    try:
        event = EventsService.archive_event(event_id, current_user.id)

        from app.sockets.emitters import emit_event_change
        emit_event_change({
            'action': 'archived',
            'event_id': event_id,
            'program_id': event.program_id,
            'title': event.title,
        }, event=event)
        return jsonify({"ok": True, "status": event.status}), 200
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500


@api_events.route('/<int:event_id>/unarchive', methods=['POST'])
@login_required
@permission_required('events.api.archive')
def unarchive_event(event_id: int):
    """Reactiva evento archivado. Body opcional: {new_status: 'published'|'draft'}."""
    event, err = _check_event_access(event_id)
    if err:
        return err
    data = request.get_json() or {}
    new_status = data.get('new_status', 'published')
    try:
        event = EventsService.unarchive_event(event_id, current_user.id, new_status)

        from app.sockets.emitters import emit_event_change
        emit_event_change({
            'action': 'unarchived',
            'event_id': event_id,
            'program_id': event.program_id,
            'title': event.title,
        }, event=event)
        return jsonify({"ok": True, "status": event.status}), 200
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500


# ============================================================================
# HOSTS / PRESENTADORES
# ============================================================================

@api_events.route('/<int:event_id>/hosts', methods=['GET'])
@login_required
def list_event_hosts(event_id: int):
    """
    Lista hosts de un evento. Visible para quien lo gestiona o participa en él.

    El cartel de ponentes lleva nombres internos y la URL de su fotografía, y
    la ACL anterior (`visible_to_students and published and capacity_type !=
    'single'`) no miraba ni la `visibility` ni el programa: cualquier cuenta
    autenticada leía los ponentes de un evento PRIVADO de cualquier posgrado.

    El lector va al servicio porque la foto de un ponente interno sólo se
    publica a quien puede pedir esos bytes (`may_view_avatar`): el índice y el
    servidor de archivos tienen que contestar lo mismo.
    """
    event = db.session.get(Event, event_id)
    if not event:
        return _deny("NOT_FOUND", "Evento no encontrado.", 404)

    if not (_event_managed_by_current_user(event) or _event_participable_by_current_user(event)):
        return _deny("FORBIDDEN", "No tienes acceso a este evento.", 403)

    hosts = EventsService.get_event_hosts(event_id, viewer=current_user)
    return jsonify({"ok": True, "hosts": hosts}), 200


@api_events.route('/<int:event_id>/hosts/photo', methods=['POST'])
@login_required
@permission_required('events.api.manage_hosts')
def upload_host_photo(event_id: int):
    """
    Sube foto para un host externo. Retorna el path relativo para usar en
    `external_photo_path` al llamar PUT /hosts.
    """
    event, err = _check_event_access(event_id)
    if err:
        return err

    file_storage = request.files.get('file')
    if not file_storage:
        return jsonify({"ok": False, "error": "Archivo 'file' es requerido"}), 400

    try:
        from app.utils.files import save_event_image
        path = save_event_image(file_storage, event_id, 'host')
        return jsonify({"ok": True, "path": path}), 201
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@api_events.route('/<int:event_id>/hosts', methods=['PUT'])
@login_required
@permission_required('events.api.manage_hosts')
def set_event_hosts(event_id: int):
    """
    Reemplaza la lista completa de hosts de un evento.

    Gestionar el evento autoriza a EDITAR SU CARTEL, no a nombrar ponente a
    cualquier cuenta de la institución: cada `user_id` interno lo valida
    `EventsService.user_may_be_named_host` contra el actor. Sin eso, la fila
    que el propio gestor escribe era la prueba con la que
    `file_access_service` le servía el rostro de la víctima.
    """
    event, err = _check_event_access(event_id)
    if err:
        return err

    data = request.get_json() or {}
    hosts_data = data.get('hosts', [])
    if not isinstance(hosts_data, list):
        return jsonify({"ok": False, "error": "'hosts' debe ser una lista"}), 400

    try:
        hosts = EventsService.set_event_hosts(
            event_id, hosts_data, acting_user=current_user
        )
        return jsonify({"ok": True, "count": len(hosts)}), 200
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ============================================================================
# IMÁGENES (COVER + GALLERY)
# ============================================================================

@api_events.route('/<int:event_id>/images', methods=['GET'])
@login_required
def list_event_images(event_id: int):
    """
    Lista imágenes de un evento (cover + gallery).

    Mismo criterio que los ponentes: gestión o participación. Estas rutas son
    el índice de los bytes que sirve `api_files.event_image`, y las dos deben
    contestar lo mismo o el índice delata lo que el servidor niega.
    """
    event = db.session.get(Event, event_id)
    if not event:
        return _deny("NOT_FOUND", "Evento no encontrado.", 404)

    if not (_event_managed_by_current_user(event) or _event_participable_by_current_user(event)):
        return _deny("FORBIDDEN", "No tienes acceso a este evento.", 403)

    data = EventsService.get_event_images(event_id)
    return jsonify({"ok": True, **data}), 200


@api_events.route('/<int:event_id>/cover', methods=['POST'])
@login_required
@permission_required('events.api.manage_images')
def upload_event_cover(event_id: int):
    """Sube (o reemplaza) la imagen de portada del evento."""
    event, err = _check_event_access(event_id)
    if err:
        return err

    file_storage = request.files.get('file')
    if not file_storage:
        return jsonify({"ok": False, "error": "Archivo 'file' es requerido"}), 400

    try:
        image = EventsService.upload_event_cover(event_id, file_storage)
        return jsonify({"ok": True, "image": image.to_dict()}), 201
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@api_events.route('/<int:event_id>/images', methods=['POST'])
@login_required
@permission_required('events.api.manage_images')
def upload_event_gallery_image(event_id: int):
    """Agrega imagen a la galería del evento."""
    event, err = _check_event_access(event_id)
    if err:
        return err

    file_storage = request.files.get('file')
    if not file_storage:
        return jsonify({"ok": False, "error": "Archivo 'file' es requerido"}), 400

    caption = request.form.get('caption')
    try:
        image = EventsService.upload_event_gallery_image(event_id, file_storage, caption)
        return jsonify({"ok": True, "image": image.to_dict()}), 201
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@api_events.route('/images/<int:image_id>', methods=['DELETE'])
@login_required
@permission_required('events.api.manage_images')
def delete_event_image(image_id: int):
    """Elimina imagen (cover o galería) + archivo físico."""
    from app.models.event import EventImage
    image = db.session.get(EventImage, image_id)
    # Un solo 404 para las dos razones — ver `generate_slots`.
    if not image or _check_event_access(image.event_id)[1]:
        return _deny("NOT_FOUND", "Imagen no encontrada.", 404)

    try:
        EventsService.delete_event_image(image_id)
        return jsonify({"ok": True, "deleted": image_id}), 200
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ============================================================================
# FASE 6 — PROMOCIÓN & DESCUBRIMIENTO
# ============================================================================

@api_events.route('/admin-stats', methods=['GET'])
@login_required
@permission_required('events.api.list')
def get_admin_dashboard_stats():
    """
    KPIs para el dashboard de administración de eventos.

    Mismo permiso y mismo universo de filas que `list_events`: un contador
    también es una lectura. Cuenta sólo lo que ese llamador podría listar.
    """
    accessible_pids = current_user.get_accessible_program_ids()
    stats = EventsService.get_admin_dashboard_stats(accessible_pids)
    return jsonify({"ok": True, **stats}), 200


@api_events.route('/new-count', methods=['GET'])
@login_required
def get_new_events_count():
    """Cuenta eventos públicos creados desde la última visita del usuario."""
    try:
        count = EventsService.count_new_events(current_user.id)
        return jsonify({"data": {"count": count}, "error": None, "meta": {}}), 200
    except Exception as e:
        from flask import current_app
        current_app.logger.exception(f"new-count failed for user {current_user.id}")
        return jsonify({"data": None, "error": {"code": "SERVER_ERROR", "message": str(e)}, "meta": {}}), 500


@api_events.route('/mark-seen', methods=['POST'])
@login_required
def mark_events_seen():
    """Marca los eventos como vistos por el usuario actual."""
    try:
        EventsService.mark_events_seen(current_user.id)
        return jsonify({"data": {"ok": True}, "error": None, "meta": {}}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"data": None, "error": {"code": "SERVER_ERROR", "message": str(e)}, "meta": {}}), 500