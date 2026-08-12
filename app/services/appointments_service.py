from datetime import datetime, timezone
from app.utils.datetime_utils import now_local
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from app import db
from app.models.event import EventSlot, EventWindow
from app.models.appointment import Appointment, AppointmentChangeRequest
from app.models.event import Event
from app.services.events_service import EventsService


class AppointmentAccessDenied(PermissionError):
    """
    La cita existe pero el usuario que actúa no es su dueño ni un
    coordinador autorizado. Las rutas la traducen a 404/403 según a quién
    deban ocultarle la existencia del objeto.
    """

    def __init__(self, message: str = 'No tienes acceso a esta cita.'):
        super().__init__(message)
        self.message = message


class AppointmentsService:

    # ─── Contexto (slot → ventana → evento) ──────────────────────────────
    #
    # Toda ruta que toca una cita necesita el evento (y con él el programa)
    # para poder validar el alcance. Antes cada handler repetía la cadena
    # de tres `db.session.get`; ahora vive aquí una sola vez.
    #
    # QUIÉN PUEDE GESTIONAR LA CITA se decide SIEMPRE con
    # `EventsService.user_may_manage_event(user, ctx['event'])`. El
    # `program_id` que devuelven estas funciones es informativo: un
    # `program_id` nulo significa "evento institucional", que exige alcance
    # GLOBAL, jamás "no hay programa que comprobar, pasa cualquiera".

    @staticmethod
    def get_appointment_context(appointment_id: int) -> dict | None:
        """
        Resuelve cita → slot → ventana → evento → programa.

        Returns:
            dict con 'appointment', 'slot', 'window', 'event', 'program_id' y
            'event_title'; None si la cita no existe.
        """
        appt = db.session.get(Appointment, appointment_id)
        if not appt:
            return None

        slot = db.session.get(EventSlot, appt.slot_id)
        window = db.session.get(EventWindow, slot.event_window_id) if slot else None
        event = db.session.get(Event, window.event_id) if window else db.session.get(Event, appt.event_id)

        return {
            'appointment': appt,
            'slot': slot,
            'window': window,
            'event': event,
            'program_id': event.program_id if event else None,
            'event_title': event.title if event else 'Desconocido',
        }

    @staticmethod
    def get_change_request_context(request_id: int) -> dict | None:
        """
        Igual que `get_appointment_context` pero partiendo de una solicitud de
        cambio. Añade la clave 'change_request'. None si la solicitud no existe.
        """
        acr = db.session.get(AppointmentChangeRequest, request_id)
        if not acr:
            return None
        ctx = AppointmentsService.get_appointment_context(acr.appointment_id) or {}
        ctx['change_request'] = acr
        return ctx

    @staticmethod
    def get_slot_context(slot_id: int) -> dict | None:
        """Resuelve slot → ventana → evento. None si el slot no existe."""
        slot = db.session.get(EventSlot, slot_id)
        if not slot:
            return None
        window = db.session.get(EventWindow, slot.event_window_id)
        event = db.session.get(Event, window.event_id) if window else None
        return {
            'slot': slot,
            'window': window,
            'event': event,
            'program_id': event.program_id if event else None,
        }

    # ─── ACL — leer el EVENTO que hay detrás de una cita ──────────────────
    #
    # TENER LA CITA NO ES PERMISO PARA LEER EL EVENTO. Es literalmente la
    # lección de `EventAttendance`: una fila que el propio sujeto puede
    # escribirse no autoriza nada, y en cuanto un ACL la aceptaba como prueba
    # bastaba con crearla para leer un evento privado de cualquier posgrado.
    # `Appointment` tenía la misma forma: `POST /api/v1/appointments` la crea
    # el aspirante para sí mismo, y luego `/details` y `/mine/active` servían
    # título, tipo, sede y descripción del evento con la sola prueba de que la
    # cita era suya.
    #
    # La diferencia — y la única razón por la que una cita SÍ puede abrir la
    # lectura — es quién la escribió: ver `_assignment_is_third_party`.

    @staticmethod
    def _assignment_is_third_party(appointment) -> bool:
        """
        ¿Se la puso OTRA persona al aspirante, o se la puso él mismo?

        `assign()` escribe SIEMPRE `assigned_by = <quien llama>`, y sólo deja
        elegir el `applicant_id` a quien tiene 'appointments.api.assign' y
        además pasa `EventsService.user_may_manage_event` sobre el evento y
        `guard_user_scope` sobre la persona. De ahí que
        `assigned_by != applicant_id` implique: un gestor con autoridad sobre
        el evento Y sobre el aspirante creó esta cita. Esa es exactamente la
        forma de una `EventInvitation` — una concesión de TERCERO — y por eso
        vale como llave de lectura del evento, aunque el evento sea privado o
        se haya cerrado después (el aspirante tiene que poder seguir viendo
        dónde y a qué hora es su entrevista).

        `assigned_by == applicant_id` es una AUTO-reserva: no concede nada.

        `assigned_by` es NULL-able (`ondelete='SET NULL'`): si se borra la
        cuenta del coordinador que asignó, la concesión deja de constar y la
        lectura vuelve al predicado compartido. Falla cerrado a propósito.
        """
        if appointment is None:
            return False
        return (
            appointment.assigned_by is not None
            and appointment.assigned_by != appointment.applicant_id
        )

    @staticmethod
    def user_may_read_event_of_appointment(user, appointment, event) -> bool:
        """
        ¿Puede `user` leer los CAMPOS DEL EVENTO (título, tipo, sede,
        descripción, horarios) que cuelgan de esta cita?

        Composición explícita, no una copia. Tres vías, en este orden:

          1. gestiona el evento   → `EventsService.user_may_manage_event`;
          2. es el dueño de la cita Y se la asignó un tercero con autoridad
             → `_assignment_is_third_party` (misma lógica que una invitación);
          3. puede participar en el evento AHORA
             → `EventsService.user_may_participate_in_event`, releído en cada
               lectura, nunca cacheado en la fila de la cita.

        La vía 3 es la que hay que releer: un evento que se vuelve privado, se
        despublica o se oculta a estudiantes deja de ser legible por esa vía
        aunque la auto-reserva siga existiendo — igual que un registro de
        asistencia deja de listar el evento en /events cuando el organizador
        lo cierra.

        Framework-agnostic: el llamador pasa el usuario, nunca `current_user`.
        """
        if user is None or appointment is None or event is None:
            return False

        if EventsService.user_may_manage_event(user, event):
            return True

        user_id = getattr(user, 'id', None)
        if user_id is None:
            return False

        if (
            appointment.applicant_id == user_id
            and AppointmentsService._assignment_is_third_party(appointment)
        ):
            return True

        return EventsService.user_may_participate_in_event(user, event)

    # ─── Listados propios del aspirante ──────────────────────────────────

    @staticmethod
    def list_appointments_of_user(user_id: int) -> list[Appointment]:
        """Citas de un usuario (todas, cualquier estado)."""
        return db.session.execute(
            select(Appointment)
            .where(Appointment.applicant_id == user_id)
            .order_by(Appointment.created_at.desc())
        ).scalars().all()

    @staticmethod
    def list_active_appointment_contexts(user_id: int) -> list[dict]:
        """
        Citas vigentes ('scheduled') de un usuario, ya resueltas a
        slot → ventana → evento y con su solicitud de cambio pendiente.

        El filtro por `applicant_id` es la única forma de entrar aquí: la ruta
        no acepta un id de usuario del cliente. Aun así, el que la cita sea
        suya NO decide si puede leer el evento — eso lo contesta
        `user_may_read_event_of_appointment` sobre cada fila.

        Returns:
            lista de dicts con 'appointment', 'slot', 'event' y 'pending_change'.
        """
        rows = db.session.execute(
            select(Appointment, EventSlot, Event)
            .join(EventSlot, EventSlot.id == Appointment.slot_id)
            .join(EventWindow, EventWindow.id == EventSlot.event_window_id)
            .join(Event, Event.id == EventWindow.event_id)
            .where(
                Appointment.applicant_id == user_id,
                Appointment.status == 'scheduled',
            )
            .order_by(EventSlot.starts_at.asc())
        ).all()

        appointment_ids = [appt.id for appt, _slot, _event in rows]
        pending_by_appointment: dict[int, AppointmentChangeRequest] = {}
        if appointment_ids:
            pending = db.session.execute(
                select(AppointmentChangeRequest)
                .where(
                    AppointmentChangeRequest.appointment_id.in_(appointment_ids),
                    AppointmentChangeRequest.status == 'pending',
                )
                .order_by(AppointmentChangeRequest.created_at.desc())
            ).scalars().all()
            for acr in pending:
                pending_by_appointment.setdefault(acr.appointment_id, acr)

        return [{
            'appointment': appt,
            'slot': slot,
            'event': event,
            'pending_change': pending_by_appointment.get(appt.id),
        } for appt, slot, event in rows]

    @staticmethod
    def get_active_appointment_for_slot(slot_id: int) -> Appointment | None:
        """
        Cita vigente (no cancelada) de un slot. Las canceladas conservan el
        `slot_id` hasta que el slot se vuelve a reservar (ver
        `_release_stale_reservation`), así que hay que excluirlas.
        """
        return db.session.execute(
            select(Appointment).where(
                Appointment.slot_id == slot_id,
                Appointment.status != 'cancelled',
            )
        ).scalars().first()

    # ─── Liberación real del slot ────────────────────────────────────────
    #
    # DECISIÓN (bug de disponibilidad, independiente del IDOR):
    # `Appointment.slot_id` es UNIQUE y NOT NULL. Al cancelar se marcaba el
    # slot como 'free' pero la fila de la cita seguía ocupando el UNIQUE, así
    # que el slot quedaba libre en pantalla e IMPOSIBLE de reservar de nuevo
    # (IntegrityError silencioso convertido en "el slot ya fue tomado").
    #
    # Poner slot_id a NULL exigiría una migración (columna NOT NULL), así que
    # la reserva muerta se limpia de forma perezosa al volver a reservar el
    # slot: se borra la fila cancelada que lo bloquea. El rastro de auditoría
    # no se pierde: la cancelación queda registrada en UserHistory
    # (`log_appointment_cancellation`) y en la notificación enviada.

    @staticmethod
    def _release_stale_reservation(slot_id: int, keep_appointment_id: int | None = None) -> int:
        """
        Borra las citas CANCELADAS que siguen ocupando el UNIQUE de `slot_id`.

        Returns:
            número de reservas muertas liberadas.
        """
        stale = db.session.execute(
            select(Appointment).where(
                Appointment.slot_id == slot_id,
                Appointment.status == 'cancelled',
            )
        ).scalars().all()

        released = 0
        for old in stale:
            if keep_appointment_id and old.id == keep_appointment_id:
                continue
            db.session.delete(old)
            released += 1

        if released:
            db.session.flush()
        return released

    # ─── Asignación ──────────────────────────────────────────────────────

    @staticmethod
    def assign_slot(event_id: int, slot_id: int, applicant_id: int, assigned_by: int, notes: str | None = None) -> Appointment:
        # Lock del slot para evitar carrera
        slot = db.session.execute(
            select(EventSlot).where(EventSlot.id == slot_id).with_for_update()
        ).scalar_one_or_none()
        if not slot:
            raise ValueError("Slot no encontrado")

        # validar que el slot pertenece al event
        win = db.session.get(EventWindow, slot.event_window_id)
        ev = db.session.get(Event, win.event_id) if win else None
        if not ev or ev.id != event_id:
            raise ValueError("El slot no pertenece al evento")

        if slot.status != 'free':
            raise ValueError("El slot no está disponible")

        # El slot está libre: si quedó una reserva muerta de una cita
        # cancelada, liberarla antes de insertar (UNIQUE en slot_id).
        AppointmentsService._release_stale_reservation(slot_id)

        slot.status = 'booked'
        slot.held_by = applicant_id
        appt = Appointment(
            event_id=event_id,
            slot_id=slot_id,
            applicant_id=applicant_id,
            assigned_by=assigned_by,
            status='scheduled',
            notes=notes,
            created_at=now_local()
        )
        db.session.add(appt)
        try:
            db.session.commit()
        except IntegrityError as e:
            db.session.rollback()
            raise ValueError("El alumno ya tiene una cita para este evento o el slot ya fue tomado") from e
        return appt

    # ─── Cancelación ─────────────────────────────────────────────────────

    @staticmethod
    def cancel_appointment(appointment_id: int, reason: str | None = None,
                           acting_user_id: int | None = None, as_admin: bool = False):
        """
        Cancela una cita.

        Args:
            acting_user_id: id de quien ejecuta la acción. Obligatorio cuando
                `as_admin` es False: sin él NO se puede comprobar la propiedad
                de la cita y la operación se rechaza (falla cerrado).
            as_admin: True sólo cuando la ruta ya verificó que el usuario tiene
                'appointments.api.assign' Y que puede gestionar el evento del
                que cuelga la cita (`EventsService.user_may_manage_event`:
                alcance sobre su programa, o alcance global si el evento es
                institucional).

        Raises:
            ValueError: la cita no existe o ya estaba cancelada.
            AppointmentAccessDenied: la cita no es del usuario que actúa.
        """
        appt = db.session.get(Appointment, appointment_id)
        if not appt:
            raise ValueError("Cita no encontrada")

        if not as_admin:
            if acting_user_id is None or appt.applicant_id != acting_user_id:
                raise AppointmentAccessDenied(
                    'No puedes cancelar una cita que no te pertenece.'
                )

        # Idempotencia: sin esto cada DELETE repetido volvía a registrar
        # historial y a enviar el correo de cancelación.
        if appt.status == 'cancelled':
            raise ValueError("La cita ya estaba cancelada")

        slot = db.session.get(EventSlot, appt.slot_id)
        if slot:
            slot.status = 'free'
            slot.held_by = None
            slot.hold_expires_at = None
        appt.status = 'cancelled'
        if reason:
            appt.notes = (appt.notes + "\n" if appt.notes else "") + f"[CANCEL]: {reason}"
        db.session.commit()
        return appt

    # ─── Solicitudes de cambio ───────────────────────────────────────────

    @staticmethod
    def request_change(appointment_id: int, requested_by: int, reason: str | None = None,
                       suggestions: str | None = None, as_admin: bool = False) -> AppointmentChangeRequest:
        """
        Crea (o actualiza) la solicitud de cambio de una cita.

        Sólo el dueño de la cita puede pedir el cambio; antes cualquiera con el
        permiso podía sobrescribir la solicitud pendiente de otro aspirante.
        """
        appt = db.session.get(Appointment, appointment_id)
        if not appt:
            raise ValueError("Cita no encontrada")

        if not as_admin and appt.applicant_id != requested_by:
            raise AppointmentAccessDenied(
                'No puedes solicitar cambios sobre una cita que no te pertenece.'
            )

        if appt.status != 'scheduled':
            raise ValueError("Solo puedes solicitar cambios de una cita vigente")

        # Si ya hay una solicitud pendiente, actualizar en lugar de crear una nueva
        existing = db.session.execute(
            select(AppointmentChangeRequest).where(
                AppointmentChangeRequest.appointment_id == appointment_id,
                AppointmentChangeRequest.status == 'pending',
            )
        ).scalars().first()

        if existing:
            existing.reason = reason
            existing.suggestions = suggestions
            existing.requested_by = requested_by
            existing.created_at = now_local()
            db.session.commit()
            return existing

        acr = AppointmentChangeRequest(
            appointment_id=appointment_id,
            requested_by=requested_by,
            reason=reason,
            suggestions=suggestions,
            status='pending',
            created_at=now_local()
        )
        db.session.add(acr)
        db.session.commit()
        return acr

    @staticmethod
    def decide_change(request_id: int, status: str, decided_by: int, new_slot_id: int | None = None):
        if status not in ('accepted', 'rejected', 'cancelled'):
            raise ValueError("status inválido")

        acr = db.session.get(AppointmentChangeRequest, request_id)
        if not acr:
            raise ValueError("Solicitud de cambio no encontrada")

        appt = db.session.get(Appointment, acr.appointment_id)
        if not appt:
            raise ValueError("Appointment no encontrado")

        acr.status = status
        acr.decided_by = decided_by
        acr.decided_at = now_local()

        if status == 'accepted':
            if not new_slot_id:
                raise ValueError("Se requiere new_slot_id para aceptar")
            # liberar slot previo y asignar nuevo con lock
            old_slot = db.session.get(EventSlot, appt.slot_id)
            new_slot = db.session.execute(
                select(EventSlot).where(EventSlot.id == new_slot_id).with_for_update()
            ).scalar_one_or_none()
            if not new_slot or new_slot.status != 'free':
                raise ValueError("El nuevo slot no está disponible")

            # El nuevo slot debe pertenecer AL MISMO evento: sin esto un
            # coordinador podía mover la cita al calendario de otro programa.
            new_window = db.session.get(EventWindow, new_slot.event_window_id)
            if not new_window or new_window.event_id != appt.event_id:
                raise ValueError("El nuevo slot no pertenece a este evento")

            # Liberar la reserva muerta que pudiera bloquear el UNIQUE del
            # nuevo slot (cita cancelada previa sobre ese horario).
            AppointmentsService._release_stale_reservation(
                new_slot_id, keep_appointment_id=appt.id
            )

            # reasignar
            if old_slot:
                old_slot.status = 'free'
                old_slot.held_by = None
                old_slot.hold_expires_at = None
            new_slot.status = 'booked'
            new_slot.held_by = appt.applicant_id
            appt.slot_id = new_slot_id
            appt.status = 'scheduled'

        db.session.commit()
        return acr
