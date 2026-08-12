from datetime import datetime, date, time, timedelta
from app import db
from app.models.event import Event, EventWindow, EventSlot,EventAttendance
from app.models.academic_period import AcademicPeriod
from sqlalchemy import and_, or_, func
func_coalesce = func.coalesce
from datetime import timezone
from app.utils.datetime_utils import now_local

class EventsService:

    # ============================================================
    # AUTORIDAD SOBRE UN EVENTO — permiso ≠ alcance
    # ============================================================

    @staticmethod
    def user_may_manage_event(user, event) -> bool:
        """
        ¿Puede `user` ADMINISTRAR este evento? Incluye su lista de asistencia.

        La autoridad sobre un evento es la autoridad sobre su programa:

          - evento CON programa  → alcance sobre ese programa;
          - evento SIN programa (institucional) → SÓLO alcance global.

        El caso institucional es el que había que decidir. Un evento con
        `program_id = NULL` lo ve toda la institución y en su lista de
        registrados hay gente de cualquier posgrado, así que "tengo el permiso
        `attendance.api.mark`" no puede bastar: el coordinador de A escribiría
        el estado de asistencia de un alumno de B pasando por ese evento. Como
        el objeto escrito pertenece al evento y no al alumno, la regla correcta
        es "manda quien es dueño del evento", y un evento institucional no
        tiene más dueño posible que la Jefatura de Posgrado. Un coordinador con
        alcance limitado ya no puede crear eventos sin programa
        (`events_api.create_event`), así que la regla no deja huérfano nada que
        él pueda producir hoy.

        Framework-agnostic: el llamador pasa el usuario (nunca `current_user`).
        """
        from app.services import program_scope_service as scope_service

        if user is None or event is None:
            return False
        if scope_service.is_global_scope(user):
            return True
        return (
            event.program_id is not None
            and scope_service.program_in_scope(user, event.program_id)
        )

    @staticmethod
    def user_may_participate_in_event(user, event) -> bool:
        """
        May `user` take part in this event — see it, join it, read its hosts,
        its images and its slots?

        THE participant rule for the whole module. Managing an event is a
        different tier and lives in `user_may_manage_event`; a call site that
        serves both audiences asks for `manage OR participate` and never
        invents a third variant. Four different answers to this one question
        is what let an applicant of program A read a private event of B.

            status != 'published'   -> False. A draft accepts nobody, and a
                                       concluded/archived event is over.
            not visible_to_students -> False. This flag is the organiser's own
                                       switch for "this is live for the
                                       audience", and every participant-facing
                                       endpoint already honours it.
            public + institutional  -> True  (program_id IS NULL).
            public + own program    -> True.
            invited                 -> True, whatever the visibility or the
                                       program: an EventInvitation row can
                                       only be written by someone who MANAGES
                                       the event, so it is a third-party
                                       grant. This is also what keeps
                                       `invite_students(allow_external=True)`
                                       working.
            everything else         -> False.

        Note what is NOT an input: an `EventAttendance` row. Registration is a
        self-service write (`POST /api/v1/attendance/event/<id>/register`), and
        an authorisation the caller can grant themselves is not an
        authorisation — it was the whole exploit: register first, then read the
        private event through every ACL that trusted that row. Registration now
        DEPENDS on this predicate instead of feeding it (`register_to_event`).

        Framework-agnostic: the caller passes the user, never `current_user`.
        """
        from app.models.event import EventInvitation
        from app.services import program_scope_service as scope_service

        if user is None or event is None:
            return False

        user_id = getattr(user, 'id', None)
        if user_id is None:
            return False

        if event.status != 'published' or not event.visible_to_students:
            return False

        if event.visibility == 'public':
            if event.program_id is None:
                return True
            if event.program_id in scope_service.program_ids_of_user(user):
                return True

        # Last, because it costs a query: the organiser's explicit grant.
        # Any status counts (pending / accepted / rejected): a rejected
        # invitation may be reconsidered, so the event must stay readable.
        invited = db.session.query(EventInvitation.id).filter(
            EventInvitation.event_id == event.id,
            EventInvitation.user_id == user_id,
        ).first()
        return invited is not None

    @staticmethod
    def create_event(
        program_id: int | None,
        type_: str,
        title: str,
        description: str | None,
        location: str | None,
        created_by: int,
        visible_to_students: bool = True,
        capacity_type: str = 'single',
        max_capacity: int | None = None,
        requires_registration: bool = True,
        allows_attendance_tracking: bool = False,
        status: str = 'published',
        academic_period_id: int | None = None,
        visibility: str = 'public',
        reminders_enabled: bool = True
    ) -> Event:
        """Crear evento con parámetros de capacidad, visibilidad y recordatorios."""

        if capacity_type not in ('single', 'multiple', 'unlimited'):
            raise ValueError("capacity_type debe ser 'single', 'multiple' o 'unlimited'")

        if capacity_type == 'multiple' and not max_capacity:
            raise ValueError("max_capacity es requerido para eventos de capacidad múltiple")

        if visibility not in ('public', 'private'):
            raise ValueError("visibility debe ser 'public' o 'private'")

        if academic_period_id is None:
            active = AcademicPeriod.get_active_period()
            if active:
                academic_period_id = active.id

        ev = Event(
            program_id=program_id,
            academic_period_id=academic_period_id,
            type=type_ or 'interview',
            title=title,
            description=description,
            location=location,
            created_by=created_by,
            visible_to_students=visible_to_students,
            visibility=visibility,
            capacity_type=capacity_type,
            max_capacity=max_capacity,
            requires_registration=requires_registration,
            allows_attendance_tracking=allows_attendance_tracking,
            reminders_enabled=reminders_enabled,
            status=status
        )
        db.session.add(ev)
        db.session.commit()

        # Fase 6.2: broadcast a usuarios potencialmente interesados cuando el evento
        # se publica directamente como público y de capacidad múltiple/ilimitada.
        if (
            status == 'published'
            and visible_to_students
            and capacity_type != 'single'
            and visibility == 'public'
        ):
            try:
                from app.services.notification_service import NotificationService
                from app.models.user_program import UserProgram
                from app.models.user import User
                from app.models.role import Role

                target_roles = Role.query.filter(Role.name.in_(['student', 'applicant'])).all()
                target_role_ids = [r.id for r in target_roles]

                if program_id:
                    target_user_ids = [
                        u.id for u in db.session.query(User).join(
                            UserProgram, UserProgram.user_id == User.id
                        ).filter(
                            UserProgram.program_id == program_id,
                            User.role_id.in_(target_role_ids),
                            User.is_active == True
                        ).all()
                    ]
                else:
                    target_user_ids = [
                        u.id for u in User.query.filter(
                            User.role_id.in_(target_role_ids),
                            User.is_active == True
                        ).all()
                    ]

                # Excluir creador
                target_user_ids = [uid for uid in target_user_ids if uid != created_by]

                if target_user_ids:
                    NotificationService.notify_event_published(
                        user_ids=target_user_ids,
                        event_title=ev.title,
                        event_id=ev.id
                    )
                    db.session.commit()
            except Exception as e:
                from flask import current_app
                current_app.logger.exception(f"[create_event] Broadcast fallo event_id={ev.id}: {e}")

        return ev

    @staticmethod
    def update_event(event_id: int, data: dict) -> Event:
        """Actualiza campos de un evento con validaciones."""
        event = db.session.get(Event, event_id)
        if not event:
            raise ValueError("Evento no encontrado")

        mutable_fields = (
            'title', 'description', 'location', 'type', 'status',
            'visible_to_students', 'allows_attendance_tracking',
            'max_capacity', 'academic_period_id', 'program_id',
            'requires_registration', 'visibility', 'reminders_enabled'
        )
        for field in mutable_fields:
            if field in data:
                setattr(event, field, data[field])

        if 'capacity_type' in data and data['capacity_type'] != event.capacity_type:
            from app.models.event import EventAttendance as _EA
            has_slots = EventSlot.query.join(EventWindow).filter(
                EventWindow.event_id == event_id
            ).count() > 0
            has_registrations = _EA.query.filter_by(event_id=event_id).count() > 0
            if has_slots or has_registrations:
                raise ValueError(
                    "No se puede cambiar el tipo de capacidad de un evento con slots o registros existentes"
                )
            event.capacity_type = data['capacity_type']

        db.session.commit()
        return event

    @staticmethod
    def list_public_events(user_id: int) -> list[Event]:
        """
        Lista eventos visibles para un usuario en /events:
        - visible_to_students=True, status='published', capacity_type != 'single'
        - academic_period_id = periodo activo OR NULL
        - Público + (programa del usuario OR global)
        - Privado:
            * tiene invitación (cualquier status), OR
            * es el creador (preview), OR
            * es program_admin/postgraduate_admin con acceso al programa (preview)

        An EventAttendance row is deliberately NOT one of the private-event
        clauses: registration is a self-service write, so trusting it here let
        a user list a private event of any program by first POSTing a
        registration to it. See `user_may_participate_in_event`.
        """
        from app.models.user_program import UserProgram
        from app.models.event import EventInvitation
        from app.models.user import User

        active_period = AcademicPeriod.get_active_period()
        active_pid = active_period.id if active_period else None
        user_program = UserProgram.query.filter_by(user_id=user_id).first()
        user_pid = user_program.program_id if user_program else None

        invited_event_ids = [
            row[0] for row in db.session.query(EventInvitation.event_id).filter(
                EventInvitation.user_id == user_id
            ).all()
        ]

        # Programas accesibles para preview de privados (si es admin)
        user = db.session.get(User, user_id)
        accessible_pids = user.get_accessible_program_ids() if user else set()

        base = Event.query.filter(
            Event.visible_to_students == True,
            Event.status == 'published',
            Event.capacity_type != 'single'
        )

        # Excluir eventos ya finalizados (end_date o event_date < ahora)
        now = now_local()
        end_or_start = func_coalesce(Event.event_end_date, Event.event_date)
        base = base.filter(
            or_(end_or_start.is_(None), end_or_start >= now)
        )

        if active_pid is not None:
            base = base.filter(
                or_(
                    Event.academic_period_id == active_pid,
                    Event.academic_period_id.is_(None)
                )
            )
        else:
            base = base.filter(Event.academic_period_id.is_(None))

        # Público: el usuario lo ve si...
        public_subfilters = [Event.program_id.is_(None)]  # eventos generales
        if user_pid:
            public_subfilters.append(Event.program_id == user_pid)
        if accessible_pids is None:
            # Admin global (postgraduate_admin sin scope) ve todos los públicos
            public_subfilters.append(Event.id.isnot(None))
        elif accessible_pids:
            # Coordinador / admin con scope ve públicos de programas accesibles
            public_subfilters.append(Event.program_id.in_(accessible_pids))

        public_clause = and_(
            Event.visibility == 'public',
            or_(*public_subfilters)
        )

        # Privado: invitado OR creador OR admin del programa.
        # "Ya registrado" NO es una cláusula: el registro se lo concede el
        # propio usuario (ver `user_may_participate_in_event`).
        private_filters = [Event.created_by == user_id]
        if invited_event_ids:
            private_filters.append(Event.id.in_(invited_event_ids))
        if accessible_pids is None:
            # Acceso global (postgraduate_admin sin scope)
            private_filters.append(Event.id.isnot(None))  # match all
        elif accessible_pids:
            private_filters.append(Event.program_id.in_(accessible_pids))

        private_clause = and_(
            Event.visibility == 'private',
            or_(*private_filters)
        )

        # Si el organizador pasa a privado un evento donde ya había gente
        # registrada, esa gente deja de verlo en /events salvo que además
        # tenga invitación — que es exactamente lo que significa cerrar el
        # evento. Su propio registro sigue apareciendo en el widget del
        # dashboard y en /api/v1/attendance/my-registrations, que leen SUS
        # filas y no conceden acceso al evento.

        query = base.filter(or_(public_clause, private_clause))

        return query.order_by(
            Event.event_date.desc().nullslast(),
            Event.created_at.desc()
        ).all()

    @staticmethod
    def get_public_events_with_invitation_status(user_id: int) -> list[dict]:
        """
        Retorna list_public_events anotados con my_invitation_status.
        status posibles: None | 'pending' | 'accepted' | 'rejected' | 'cancelled'
        """
        from app.models.event import EventInvitation

        events = EventsService.list_public_events(user_id)
        invs = {
            inv.event_id: inv.status
            for inv in EventInvitation.query.filter(
                EventInvitation.user_id == user_id,
                EventInvitation.event_id.in_([e.id for e in events]) if events else False
            ).all()
        }

        return [
            {'event': ev, 'my_invitation_status': invs.get(ev.id)}
            for ev in events
        ]

    @staticmethod
    def list_admin_events(accessible_pids: set | None, filters: dict | None = None) -> list[Event]:
        """
        Lista eventos administrables. accessible_pids = None significa acceso global.
        filters: academic_period_id, program_id, type, status, capacity_type, search.
        """
        filters = filters or {}
        query = Event.query

        if accessible_pids is not None:
            if not accessible_pids:
                query = query.filter(Event.program_id.is_(None))
            else:
                query = query.filter(
                    or_(
                        Event.program_id.in_(accessible_pids),
                        Event.program_id.is_(None)
                    )
                )

        if filters.get('academic_period_id'):
            query = query.filter(Event.academic_period_id == filters['academic_period_id'])
        if filters.get('program_id'):
            query = query.filter(Event.program_id == filters['program_id'])
        if filters.get('type'):
            query = query.filter(Event.type == filters['type'])
        if filters.get('status'):
            query = query.filter(Event.status == filters['status'])
        else:
            # Por default ocultar archivados; admin debe pedir explícito "archived" para verlos.
            query = query.filter(Event.status != 'archived')
        if filters.get('capacity_type'):
            query = query.filter(Event.capacity_type == filters['capacity_type'])
        if filters.get('search'):
            term = f"%{filters['search']}%"
            query = query.filter(
                or_(Event.title.ilike(term), Event.description.ilike(term))
            )

        return query.order_by(Event.created_at.desc()).all()

    @staticmethod
    def get_admin_dashboard_stats(accessible_pids: set | None) -> dict:
        """KPIs para encabezado de la vista de administración de eventos."""
        from app.models.appointment import AppointmentChangeRequest, Appointment

        today_start = datetime.combine(date.today(), time.min)
        today_end = datetime.combine(date.today(), time.max)
        in_seven_days = today_end + timedelta(days=7)

        def scope_event_query():
            q = Event.query
            if accessible_pids is not None:
                if not accessible_pids:
                    q = q.filter(Event.program_id.is_(None))
                else:
                    q = q.filter(or_(
                        Event.program_id.in_(accessible_pids),
                        Event.program_id.is_(None)
                    ))
            return q.filter(Event.status != 'archived')

        def scope_event_ids():
            return [e.id for e in scope_event_query().with_entities(Event.id).all()]

        scoped_ids = scope_event_ids()

        today_count = 0
        upcoming_count = 0
        if scoped_ids:
            today_appts = db.session.query(Appointment).join(
                EventSlot, EventSlot.id == Appointment.slot_id
            ).filter(
                Appointment.status != 'cancelled',
                Appointment.event_id.in_(scoped_ids),
                EventSlot.starts_at >= today_start,
                EventSlot.starts_at <= today_end
            ).count()
            today_multi = scope_event_query().filter(
                Event.event_date >= today_start,
                Event.event_date <= today_end
            ).count()
            today_count = today_appts + today_multi

            upcoming_appts = db.session.query(Appointment).join(
                EventSlot, EventSlot.id == Appointment.slot_id
            ).filter(
                Appointment.status != 'cancelled',
                Appointment.event_id.in_(scoped_ids),
                EventSlot.starts_at > today_end,
                EventSlot.starts_at <= in_seven_days
            ).count()
            upcoming_multi = scope_event_query().filter(
                Event.event_date > today_end,
                Event.event_date <= in_seven_days
            ).count()
            upcoming_count = upcoming_appts + upcoming_multi

        active_count = scope_event_query().filter(
            Event.status.in_(['published', 'ongoing', 'draft'])
        ).count()

        pending_changes = 0
        free_slots = 0
        if scoped_ids:
            pending_changes = db.session.query(AppointmentChangeRequest).join(
                Appointment, Appointment.id == AppointmentChangeRequest.appointment_id
            ).filter(
                Appointment.event_id.in_(scoped_ids),
                AppointmentChangeRequest.status == 'pending'
            ).count()
            free_slots = db.session.query(EventSlot).join(
                EventWindow, EventWindow.id == EventSlot.event_window_id
            ).filter(
                EventWindow.event_id.in_(scoped_ids),
                EventSlot.status == 'free'
            ).count()

        return {
            'today': today_count,
            'upcoming_7d': upcoming_count,
            'active': active_count,
            'pending_change_requests': pending_changes,
            'free_slots': free_slots
        }

    @staticmethod
    def add_window(
        event_id: int,
        window_date: date,
        start: time,
        end: time,
        slot_minutes: int,
        timezone_str: str = 'America/Ciudad_Juarez'
    ) -> EventWindow:
        """Agregar ventana de horarios"""
        
        if start >= end:
            raise ValueError("start_time debe ser menor a end_time")
        if slot_minutes not in (15, 20, 30, 45, 60):
            raise ValueError("slot_minutes debe ser 15, 20, 30, 45 o 60")

        win = EventWindow(
            event_id=event_id,
            date=window_date,
            start_time=start,
            end_time=end,
            slot_minutes=slot_minutes,
            timezone=timezone_str,
            slots_generated=False
        )
        db.session.add(win)
        db.session.commit()
        return win

    @staticmethod
    def generate_slots(window_id: int, overwrite_free: bool = True) -> dict:
        """
        Genera slots para una ventana.
        
        Args:
            window_id: ID de la ventana
            overwrite_free: Si True, regenera slots libres. Si False, solo crea los faltantes.
        
        Returns:
            dict con 'created', 'skipped', 'total'
        """
        win = db.session.get(EventWindow, window_id)
        if not win:
            raise ValueError("EventWindow no encontrado")

        # Construir timestamps
        starts = datetime.combine(win.date, win.start_time)
        ends = datetime.combine(win.date, win.end_time)

        created = 0
        skipped = 0
        cursor = starts
        
        while cursor < ends:
            slot_end = cursor + timedelta(minutes=win.slot_minutes)
            if slot_end > ends:
                break

            # Verificar si ya existe un slot en este horario
            existing = EventSlot.query.filter(
                and_(
                    EventSlot.event_window_id == win.id,
                    EventSlot.starts_at == cursor
                )
            ).first()

            if existing:
                # Si existe y está libre, actualizar
                if overwrite_free and existing.status == 'free':
                    existing.ends_at = slot_end
                    skipped += 1
                else:
                    # Slot ocupado o no se permite sobrescribir
                    skipped += 1
            else:
                # Crear nuevo slot
                slot = EventSlot(
                    event_window_id=win.id,
                    starts_at=cursor,
                    ends_at=slot_end,
                    status='free'
                )
                db.session.add(slot)
                created += 1

            cursor = slot_end

        # Marcar ventana como generada
        win.slots_generated = True
        db.session.commit()
        
        return {
            'created': created,
            'skipped': skipped,
            'total': created + skipped
        }

    @staticmethod
    def delete_slot(slot_id: int, force: bool = False) -> bool:
        """
        Elimina un slot individual.
        
        Args:
            slot_id: ID del slot
            force: Si True, elimina aunque esté ocupado (cancela la cita)
        
        Returns:
            True si se eliminó exitosamente
        """
        slot = db.session.get(EventSlot, slot_id)
        if not slot:
            raise ValueError("Slot no encontrado")
        
        if slot.status != 'free' and not force:
            raise ValueError("El slot está ocupado. Usa force=True para eliminar de todas formas.")
        
        # Si está ocupado y force=True, cancelar la cita asociada
        if slot.status == 'booked':
            from app.models.appointment import Appointment
            appointment = Appointment.query.filter_by(slot_id=slot_id).first()
            if appointment:
                appointment.status = 'cancelled'
                appointment.notes = f"{appointment.notes or ''}\n[Sistema]: Slot eliminado".strip()
        
        db.session.delete(slot)
        db.session.commit()
        return True

    @staticmethod
    def delete_window(window_id: int, force: bool = False) -> bool:
        """
        Elimina una ventana completa con todos sus slots.
        
        Args:
            window_id: ID de la ventana
            force: Si True, elimina aunque tenga slots ocupados
        
        Returns:
            True si se eliminó exitosamente
        """
        window = db.session.get(EventWindow, window_id)
        if not window:
            raise ValueError("Ventana no encontrada")
        
        # Verificar si tiene slots ocupados
        occupied_slots = EventSlot.query.filter(
            and_(
                EventSlot.event_window_id == window_id,
                EventSlot.status == 'booked'
            )
        ).count()
        
        if occupied_slots > 0 and not force:
            raise ValueError(f"La ventana tiene {occupied_slots} slots ocupados. Usa force=True para eliminar.")
        
        # Si tiene slots ocupados y force=True, cancelar todas las citas
        if occupied_slots > 0 and force:
            from app.models.appointment import Appointment
            booked_slots = EventSlot.query.filter(
                and_(
                    EventSlot.event_window_id == window_id,
                    EventSlot.status == 'booked'
                )
            ).all()
            
            for slot in booked_slots:
                appointment = Appointment.query.filter_by(slot_id=slot.id).first()
                if appointment:
                    appointment.status = 'cancelled'
                    appointment.notes = f"{appointment.notes or ''}\n[Sistema]: Ventana eliminada".strip()
        
        # Cascade eliminará automáticamente los slots
        db.session.delete(window)
        db.session.commit()
        return True

    @staticmethod
    def list_slots(event_id: int = None, status: str = None):
        """Lista slots con filtros opcionales"""
        q = db.session.query(EventSlot).join(
            EventWindow, EventWindow.id == EventSlot.event_window_id
        )
        
        if event_id:
            q = q.join(Event, Event.id == EventWindow.event_id).filter(Event.id == event_id)
        if status:
            q = q.filter(EventSlot.status == status)
        
        return q.order_by(EventSlot.starts_at.asc()).all()
    
    @staticmethod
    def register_to_event(event_id: int, user_id: int, notes: str = None) -> 'EventAttendance':
        """
        Register a user for a multiple/unlimited capacity event.
        A pending invitation of that user for this event is marked 'accepted'.

        AUTHORISATION FIRST. This is a self-service write, and until this guard
        existed it was the module's root defect: the route carried only
        `@login_required` and this method never looked at `status`,
        `visible_to_students`, `visibility` or the event's program. Anybody
        could plant an `EventAttendance` row on any event id in the
        institution — including a draft one — and two ACLs then read that row
        back as proof of access. The row is now only ever created for someone
        who could already participate (or who manages the event), so it can
        never widen anybody's reach.
        """
        from app.models.event import EventAttendance, EventInvitation
        from app.models.user import User

        event = db.session.get(Event, event_id)
        if not event:
            raise ValueError("Evento no encontrado")

        user = db.session.get(User, user_id)
        if not (
            EventsService.user_may_participate_in_event(user, event)
            or EventsService.user_may_manage_event(user, event)
        ):
            raise ValueError("No tienes acceso a este evento")

        if event.capacity_type == 'single':
            raise ValueError("Este evento requiere asignación de slot individual")

        # No permitir registro si el evento ya finalizó.
        # event.event_end_date / event.event_date son naive en DB; now_local() es aware.
        # Normalizamos ambos a naive en la zona local para comparar.
        now = now_local().replace(tzinfo=None)
        end_dt = event.event_end_date or event.event_date
        if end_dt:
            end_naive = end_dt.replace(tzinfo=None) if end_dt.tzinfo else end_dt
            if end_naive < now:
                raise ValueError("El evento ya finalizó")

        existing = EventAttendance.query.filter_by(
            event_id=event_id,
            user_id=user_id
        ).first()

        if existing:
            raise ValueError("El usuario ya está registrado en este evento")

        # Capacidad: contar registered + attended (todos los que ocupan cupo)
        if event.capacity_type == 'multiple' and event.max_capacity:
            current_count = EventAttendance.query.filter(
                EventAttendance.event_id == event_id,
                EventAttendance.status.in_(['registered', 'attended'])
            ).count()

            if current_count >= event.max_capacity:
                raise ValueError(f"El evento ha alcanzado su capacidad máxima ({event.max_capacity})")

        attendance = EventAttendance(
            event_id=event_id,
            user_id=user_id,
            status='registered',
            notes=notes
        )

        db.session.add(attendance)

        # Si hay una invitación pendiente, marcarla como aceptada al registrarse
        pending_inv = EventInvitation.query.filter_by(
            event_id=event_id,
            user_id=user_id,
            status='pending'
        ).first()
        if pending_inv:
            pending_inv.status = 'accepted'
            pending_inv.responded_at = now_local()

        db.session.commit()

        return attendance
    
    @staticmethod
    def unregister_from_event(event_id: int, user_id: int) -> bool:
        """
        Cancela el registro de un usuario a un evento
        """
        from app.models.event import EventAttendance
        
        attendance = EventAttendance.query.filter_by(
            event_id=event_id,
            user_id=user_id
        ).first()
        
        if not attendance:
            raise ValueError("No se encontró el registro")
        
        db.session.delete(attendance)
        db.session.commit()
        
        return True
    
    @staticmethod
    def mark_attendance(event_id: int, user_id: int, attended: bool = True, notes: str = None, reset: bool = False):
        """
        Marca la asistencia de un usuario a un evento.

        Sólo actualiza una fila EXISTENTE de `EventAttendance`: nunca crea una.
        Esa es la mitad del control de alcance de esta operación — el gestor no
        puede fabricar asistencia para alguien que jamás se registró, sólo
        anotar lo que pasó con quien se apuntó a su evento. La otra mitad
        (quién puede tocar la lista de este evento) la resuelve
        `user_may_manage_event`, que la ruta consulta antes de llamar aquí.

        Args:
            event_id: ID del evento
            user_id: ID del usuario
            attended: True=asistió, False=no asistió
            notes: Notas adicionales
            reset: Si True, resetea a 'registered'
        """
        from app.models.event import EventAttendance

        attendance = EventAttendance.query.filter_by(
            event_id=event_id,
            user_id=user_id
        ).first()

        if not attendance:
            raise ValueError("El usuario no está registrado en este evento")

        if reset:
            # Resetear a estado registrado
            attendance.status = 'registered'
            attendance.attended_at = None
        elif attended:
            attendance.status = 'attended'
            attendance.attended_at = now_local()
        else:
            attendance.status = 'no_show'
            attendance.attended_at = None

        if notes:
            attendance.notes = f"{attendance.notes or ''}\n{notes}".strip()

        db.session.commit()

        return attendance
    
    @staticmethod
    def get_event_registrations(event_id: int, include_notes: bool = True):
        """
        Obtiene todos los registros de un evento.

        Args:
            include_notes: pásalo en False cuando quien consulta NO administra
                el evento (`user_may_manage_event`). Nombre, correo y estado de
                asistencia son el nivel resumido que sí cruza entre programas
                (`CROSS_PROGRAM_SUMMARY_FIELDS`); la nota que escribió el
                organizador sobre la persona no lo es, y en un evento
                institucional la lista mezcla alumnos de todos los posgrados.
        """
        from app.models.event import EventAttendance
        from app.models.user import User

        registrations = db.session.query(EventAttendance, User).join(
            User, EventAttendance.user_id == User.id
        ).filter(
            EventAttendance.event_id == event_id
        ).order_by(EventAttendance.registered_at.desc()).all()

        return [{
            'id': attendance.id,
            'user_id': user.id,
            'full_name': f"{user.first_name} {user.last_name}",
            'email': user.email,
            'status': attendance.status,
            'registered_at': attendance.registered_at.isoformat(),
            'attended_at': attendance.attended_at.isoformat() if attendance.attended_at else None,
            'notes': attendance.notes if include_notes else None
        } for attendance, user in registrations]
    
    @staticmethod
    def invite_students(event_id: int, user_ids: list[int], invited_by: int, notes: str = None, allow_external: bool = False):
        """
        Invita múltiples estudiantes a un evento

        Returns:
            dict con 'invited', 'already_invited', 'already_registered', 'wrong_program'
        """
        from app.models.event import EventInvitation, EventAttendance
        from app.models.user_program import UserProgram

        event = db.session.get(Event, event_id)
        if not event:
            raise ValueError("Evento no encontrado")

        if event.capacity_type == 'single':
            raise ValueError("Este evento requiere asignación de slots individuales")

        results = {
            'invited': [],
            'already_invited': [],
            'already_registered': [],
            'wrong_program': []  # NUEVO
        }

        for user_id in user_ids:
            # Si el evento tiene programa específico y no se permite externos, validar
            if event.program_id and not allow_external:
                user_program = UserProgram.query.filter_by(
                    user_id=user_id,
                    program_id=event.program_id
                ).first()

                if not user_program:
                    results['wrong_program'].append(user_id)
                    continue
            
            # Verificar si ya está registrado
            existing_reg = EventAttendance.query.filter_by(
                event_id=event_id,
                user_id=user_id
            ).first()
            
            if existing_reg:
                results['already_registered'].append(user_id)
                continue
            
            # Verificar si ya tiene invitación
            existing_inv = EventInvitation.query.filter_by(
                event_id=event_id,
                user_id=user_id
            ).first()

            if existing_inv:
                # Si rechazó o fue cancelada, reabrir (permite "reconsiderar")
                if existing_inv.status in ('rejected', 'cancelled'):
                    existing_inv.status = 'pending'
                    existing_inv.responded_at = None
                    existing_inv.invited_by = invited_by
                    existing_inv.invited_at = now_local()
                    if notes:
                        existing_inv.notes = notes
                    results['invited'].append(user_id)
                else:
                    results['already_invited'].append(user_id)
                continue

            # Crear invitación
            invitation = EventInvitation(
                event_id=event_id,
                user_id=user_id,
                invited_by=invited_by,
                status='pending',
                notes=notes
            )
            db.session.add(invitation)
            results['invited'].append(user_id)
        
        db.session.commit()

        # Post-commit: notificaciones + historial + email_queue — aislar fallos por usuario
        # para que un error en un user_id no aborte el resto del batch.
        from flask import current_app
        from app.services.user_history_service import UserHistoryService
        from app.services.notification_service import NotificationService
        from app.models.email_queue import EmailQueue

        notified_users = []
        failed_notifications = []

        for user_id in results['invited']:
            try:
                invitation = EventInvitation.query.filter_by(
                    event_id=event_id,
                    user_id=user_id,
                    invited_by=invited_by
                ).first()

                if not invitation:
                    current_app.logger.warning(
                        f"[invite_students] Invitación no encontrada tras commit para user_id={user_id}, event_id={event_id}"
                    )
                    continue

                event_date_str = (
                    event.event_date.strftime('%d/%m/%Y') if event.event_date else 'Por definir'
                )

                UserHistoryService.log_event_invitation(
                    user_id=user_id,
                    event_title=event.title,
                    event_id=event_id,
                    invitation_id=invitation.id,
                    event_date=event_date_str,
                    invited_by=invited_by
                )

                notification = NotificationService.notify_event_invitation(
                    user_id=user_id,
                    event_title=event.title,
                    event_id=event_id,
                    invitation_id=invitation.id,
                    event_date=event_date_str,
                    description=event.description
                )

                # Commit por usuario para que la notificación + email_queue
                # sobrevivan aunque el siguiente usuario falle.
                db.session.commit()

                # Verificación explícita: confirmar que el EmailQueue row se creó
                email_row = EmailQueue.query.filter_by(
                    user_id=user_id,
                    notification_id=notification.id
                ).first()
                if not email_row:
                    current_app.logger.warning(
                        f"[invite_students] EmailQueue NO creado para user_id={user_id}, notification_id={notification.id}. "
                        f"Revisar notify_event_invitation y EmailService.queue_email."
                    )
                else:
                    current_app.logger.info(
                        f"[invite_students] Email encolado id={email_row.id} para user_id={user_id}, event_id={event_id}"
                    )
                notified_users.append(user_id)

                from app.sockets.emitters import emit_to_user
                pending_count = EventInvitation.query.filter_by(user_id=user_id, status='pending').count()
                emit_to_user('invitations:count_changed', {'count': pending_count}, user_id)

            except Exception as e:
                db.session.rollback()
                current_app.logger.exception(
                    f"[invite_students] Fallo al notificar user_id={user_id} en event_id={event_id}: {e}"
                )
                failed_notifications.append({'user_id': user_id, 'error': str(e)})

        results['notified'] = notified_users
        results['failed_notifications'] = failed_notifications
        return results
    
    @staticmethod
    def respond_to_invitation(invitation_id: int, user_id: int, accept: bool):
        """
        Responder a una invitación (aceptar/rechazar).
        Si ya fue rechazada y se acepta ahora → reconsiderar (permitido).
        """
        from app.models.event import EventInvitation

        invitation = db.session.get(EventInvitation, invitation_id)
        if not invitation:
            raise ValueError("Invitación no encontrada")

        if invitation.user_id != user_id:
            raise ValueError("Esta invitación no es para ti")

        if invitation.status == 'cancelled':
            raise ValueError("Esta invitación fue cancelada por el organizador")

        # Permitir reconsiderar: accept sobre 'rejected' → 'accepted'.
        # Si ya está accepted y vuelve a aceptar → noop.
        # Si está accepted y rechaza → cambiar a rejected.
        if invitation.status == 'accepted' and accept:
            return invitation
        
        invitation.status = 'accepted' if accept else 'rejected'
        invitation.responded_at = now_local()
        
        # Si acepta, crear registro automáticamente
        if accept:
            try:
                EventsService.register_to_event(
                    event_id=invitation.event_id,
                    user_id=user_id,
                    notes=f"Registrado mediante invitación #{invitation.id}"
                )
            except ValueError as e:
                # Si ya no hay cupo, marcar invitación como rechazada
                invitation.status = 'rejected'
                invitation.notes = f"{invitation.notes or ''}\nNo se pudo registrar: {str(e)}".strip()
                raise
        
        db.session.commit()

        from app.sockets.emitters import emit_to_user
        from app.models.event import EventInvitation as _EI
        pending_count = _EI.query.filter_by(user_id=user_id, status='pending').count()
        emit_to_user('invitations:count_changed', {'count': pending_count}, user_id)

        return invitation

    @staticmethod
    def get_event_invitations(event_id: int):
        """
        Obtiene todas las invitaciones de un evento con info de usuarios
        """
        from app.models.event import EventInvitation
        from app.models.user import User
        
        invitations = db.session.query(EventInvitation, User).join(
            User, EventInvitation.user_id == User.id
        ).filter(
            EventInvitation.event_id == event_id
        ).order_by(EventInvitation.invited_at.desc()).all()
        
        # Obtener info del invitador
        result = []
        for invitation, user in invitations:
            inviter = db.session.get(User, invitation.invited_by) if invitation.invited_by else None
            result.append({
                'id': invitation.id,
                'user_id': user.id,
                'full_name': f"{user.first_name} {user.last_name}",
                'email': user.email,
                'status': invitation.status,
                'invited_at': invitation.invited_at.isoformat(),
                'responded_at': invitation.responded_at.isoformat() if invitation.responded_at else None,
                'inviter_name': f"{inviter.first_name} {inviter.last_name}" if inviter else "Sistema",
                'notes': invitation.notes
            })
        
        return result
    
    @staticmethod
    def get_my_invitations(user_id: int):
        """
        Obtiene invitaciones pendientes de un usuario
        """
        from app.models.event import EventInvitation
        
        invitations = db.session.query(EventInvitation, Event).join(
            Event, EventInvitation.event_id == Event.id
        ).filter(
            EventInvitation.user_id == user_id,
            EventInvitation.status == 'pending'
        ).order_by(EventInvitation.invited_at.desc()).all()
        
        return [{
            'invitation_id': inv.id,
            'event_id': event.id,
            'event_title': event.title,
            'event_type': event.type,
            'event_location': event.location,
            'event_date': event.event_date.isoformat() if event.event_date else None,
            'invited_at': inv.invited_at.isoformat(),
            'notes': inv.notes
        } for inv, event in invitations]
    
    @staticmethod
    def get_dashboard_widget(user_id: int) -> dict:
        """
        Datos para el widget de Eventos en dashboards (applicant + student).

        Returns:
            {
              'pending_invitations': [{...}],   # status='pending' del usuario
              'accepted_invitations': [{...}],  # status='accepted' del usuario, evento futuro
              'upcoming_events': [{...}],       # max 3 eventos públicos visibles, futuros, no registrado
              'my_registrations': [{...}],      # EventAttendance status='registered'/'attended', evento futuro
            }
        """
        from app.models.event import EventInvitation, EventAttendance, EventImage

        now = now_local()

        # Pending + accepted invitations
        invitations_q = db.session.query(EventInvitation, Event).join(
            Event, EventInvitation.event_id == Event.id
        ).filter(
            EventInvitation.user_id == user_id,
            EventInvitation.status.in_(('pending', 'accepted'))
        ).order_by(Event.event_date.asc().nullslast()).all()

        pending_invitations = []
        accepted_invitations = []
        for inv, ev in invitations_q:
            entry = {
                'invitation_id': inv.id,
                'event_id': ev.id,
                'event_title': ev.title,
                'event_type': ev.type,
                'event_location': ev.location,
                'event_date': ev.event_date.isoformat() if ev.event_date else None,
                'event_status': ev.status,
                'invited_at': inv.invited_at.isoformat() if inv.invited_at else None,
                'notes': inv.notes,
            }
            if inv.status == 'pending':
                pending_invitations.append(entry)
            elif ev.event_date is None or ev.event_date >= now:
                accepted_invitations.append(entry)

        # My registrations (status registered/attended), evento futuro
        registrations_q = db.session.query(EventAttendance, Event).join(
            Event, EventAttendance.event_id == Event.id
        ).filter(
            EventAttendance.user_id == user_id,
            EventAttendance.status.in_(('registered', 'attended')),
            Event.status == 'published'
        ).order_by(Event.event_date.asc().nullslast()).all()

        registered_event_ids = set()
        my_registrations = []
        for att, ev in registrations_q:
            if ev.event_date and ev.event_date < now:
                continue
            registered_event_ids.add(ev.id)
            my_registrations.append({
                'attendance_id': att.id,
                'event_id': ev.id,
                'event_title': ev.title,
                'event_type': ev.type,
                'event_location': ev.location,
                'event_date': ev.event_date.isoformat() if ev.event_date else None,
                'attendance_status': att.status,
            })

        # Upcoming events: visibles para el usuario, futuros, sin registrar
        invited_event_ids = {entry['event_id'] for entry in pending_invitations}
        invited_event_ids.update(entry['event_id'] for entry in accepted_invitations)

        upcoming_events = []
        try:
            visible = EventsService.list_public_events(user_id)
        except Exception:
            visible = []

        for ev in visible:
            if ev.event_date and ev.event_date < now:
                continue
            if ev.id in registered_event_ids or ev.id in invited_event_ids:
                continue
            cover = EventImage.query.filter_by(event_id=ev.id, is_cover=True).first()
            upcoming_events.append({
                'event_id': ev.id,
                'event_title': ev.title,
                'event_type': ev.type,
                'event_location': ev.location,
                'event_date': ev.event_date.isoformat() if ev.event_date else None,
                'cover_path': cover.path if cover else None,
            })
            if len(upcoming_events) >= 3:
                break

        return {
            'pending_invitations': pending_invitations,
            'accepted_invitations': accepted_invitations,
            'upcoming_events': upcoming_events,
            'my_registrations': my_registrations,
        }

    @staticmethod
    def count_new_events(user_id: int) -> int:
        """
        Cuenta eventos públicos creados desde la última vez que el usuario abrió
        la lista de eventos (User.last_events_seen_at). Si nunca ha visto la lista,
        cuenta los publicados en los últimos 7 días.
        """
        from app.models.user import User

        user = db.session.get(User, user_id)
        if not user:
            return 0

        threshold = user.last_events_seen_at
        if threshold is None:
            threshold = now_local() - timedelta(days=7)
        # DB stores naive datetimes; normalizar threshold a naive para comparación.
        if getattr(threshold, 'tzinfo', None) is not None:
            threshold = threshold.replace(tzinfo=None)

        visible_events = EventsService.list_public_events(user_id)
        return sum(
            1 for ev in visible_events
            if ev.created_at and (ev.created_at.replace(tzinfo=None) if ev.created_at.tzinfo else ev.created_at) > threshold
        )

    @staticmethod
    def mark_events_seen(user_id: int) -> None:
        """Actualiza User.last_events_seen_at = now."""
        from app.models.user import User

        user = db.session.get(User, user_id)
        if not user:
            return
        user.last_events_seen_at = now_local()
        db.session.commit()

    @staticmethod
    def cancel_invitation(invitation_id: int):
        """
        Cancela una invitación (solo si está pendiente)
        """
        from app.models.event import EventInvitation

        invitation = db.session.get(EventInvitation, invitation_id)
        if not invitation:
            raise ValueError("Invitación no encontrada")

        if invitation.status != 'pending':
            raise ValueError("Solo se pueden cancelar invitaciones pendientes")

        target_user_id = invitation.user_id
        db.session.delete(invitation)
        db.session.commit()

        from app.sockets.emitters import emit_to_user
        pending_count = EventInvitation.query.filter_by(user_id=target_user_id, status='pending').count()
        emit_to_user('invitations:count_changed', {'count': pending_count}, target_user_id)

        return True
    
    @staticmethod
    def update_event_dates(event_id: int, event_date: datetime = None, event_end_date: datetime = None):
        """
        Actualiza las fechas de un evento
        """
        event = db.session.get(Event, event_id)
        if not event:
            raise ValueError("Evento no encontrado")

        if event.capacity_type == 'single':
            raise ValueError("Los eventos de capacidad individual usan ventanas de horarios")

        event.event_date = event_date
        event.event_end_date = event_end_date

        db.session.commit()
        return event

    # ============================================================
    # STATUS TRANSITIONS (conclude / archive / unarchive)
    # ============================================================

    @staticmethod
    def _cancel_pending_invitations(event_id: int, event_title: str):
        """Cancela invitaciones pending y notifica a los invitados."""
        from app.models.event import EventInvitation
        from app.services.notification_service import NotificationService

        pending = EventInvitation.query.filter_by(
            event_id=event_id, status='pending'
        ).all()

        for inv in pending:
            inv.status = 'cancelled'
            inv.responded_at = now_local()
            try:
                NotificationService.notify_event_cancelled_invitation(
                    user_id=inv.user_id,
                    event_title=event_title,
                    event_id=event_id
                )
            except Exception:
                from flask import current_app
                current_app.logger.exception(
                    f"[cancel_invitation] fallo notif user_id={inv.user_id} event_id={event_id}"
                )

    @staticmethod
    def _notify_registered_archived(event_id: int, event_title: str):
        """Notifica a registrados (status='registered') que el evento fue archivado."""
        from app.models.event import EventAttendance
        from app.services.notification_service import NotificationService

        registrations = EventAttendance.query.filter_by(
            event_id=event_id, status='registered'
        ).all()

        for reg in registrations:
            try:
                NotificationService.notify_event_archived(
                    user_id=reg.user_id,
                    event_title=event_title,
                    event_id=event_id
                )
            except Exception:
                from flask import current_app
                current_app.logger.exception(
                    f"[notify_archived] fallo user_id={reg.user_id} event_id={event_id}"
                )

    @staticmethod
    def conclude_event(event_id: int, acting_user_id: int) -> Event:
        """
        Marca evento como 'completed'. Cancela invitaciones pending (con notif),
        purga imágenes, registra en historial del admin.
        """
        from app.services.user_history_service import UserHistoryService

        event = db.session.get(Event, event_id)
        if not event:
            raise ValueError("Evento no encontrado")

        if event.status in ('completed', 'archived'):
            raise ValueError(f"El evento ya está en estado '{event.status}'")

        event_title = event.title
        event.status = 'completed'
        db.session.commit()

        EventsService._cancel_pending_invitations(event_id, event_title)
        EventsService.purge_event_media(event_id)

        UserHistoryService.log_action(
            user_id=acting_user_id,
            action='event_concluded',
            details={'event_id': event_id, 'event_title': event_title}
        )
        db.session.commit()

        return event

    @staticmethod
    def archive_event(event_id: int, acting_user_id: int) -> Event:
        """
        Archiva evento (oculto del público). Cancela invitaciones pending,
        notifica a registrados, purga imágenes, registra en historial.
        """
        from app.services.user_history_service import UserHistoryService

        event = db.session.get(Event, event_id)
        if not event:
            raise ValueError("Evento no encontrado")

        if event.status == 'archived':
            raise ValueError("El evento ya está archivado")

        event_title = event.title
        event.status = 'archived'
        db.session.commit()

        EventsService._cancel_pending_invitations(event_id, event_title)
        EventsService._notify_registered_archived(event_id, event_title)
        EventsService.purge_event_media(event_id)

        UserHistoryService.log_action(
            user_id=acting_user_id,
            action='event_archived',
            details={'event_id': event_id, 'event_title': event_title}
        )
        db.session.commit()

        return event

    @staticmethod
    def unarchive_event(event_id: int, acting_user_id: int, new_status: str = 'published') -> Event:
        """Reactiva evento archivado. Por defecto lo republica."""
        from app.services.user_history_service import UserHistoryService

        event = db.session.get(Event, event_id)
        if not event:
            raise ValueError("Evento no encontrado")

        if event.status != 'archived':
            raise ValueError("Solo se puede desarchivar un evento archivado")

        if new_status not in ('draft', 'published'):
            raise ValueError("new_status debe ser 'draft' o 'published'")

        event.status = new_status
        db.session.commit()

        UserHistoryService.log_action(
            user_id=acting_user_id,
            action='event_unarchived',
            details={'event_id': event_id, 'event_title': event.title, 'new_status': new_status}
        )
        db.session.commit()

        return event

    # ============================================================
    # HOSTS / PRESENTADORES
    # ============================================================

    @staticmethod
    def set_event_hosts(event_id: int, hosts_data: list[dict]) -> list:
        """
        Reemplaza atómicamente la lista de hosts de un evento.
        hosts_data: [{user_id?, external_name?, external_bio?, external_photo_path?, role_label, display_order?}]
        Cada item debe tener `user_id` O `external_name`.
        """
        from app.models.event import EventHost

        event = db.session.get(Event, event_id)
        if not event:
            raise ValueError("Evento no encontrado")

        # Validar antes de borrar
        for idx, item in enumerate(hosts_data):
            has_user = bool(item.get('user_id'))
            has_external = bool(item.get('external_name'))
            if not (has_user or has_external):
                raise ValueError(f"Host #{idx}: debe tener user_id o external_name")
            if not item.get('role_label'):
                raise ValueError(f"Host #{idx}: role_label es requerido")

        try:
            # Reemplazo total: borrar previos
            EventHost.query.filter_by(event_id=event_id).delete()

            new_hosts = []
            for idx, item in enumerate(hosts_data):
                host = EventHost(
                    event_id=event_id,
                    user_id=item.get('user_id'),
                    external_name=item.get('external_name') if not item.get('user_id') else None,
                    external_bio=item.get('external_bio') if not item.get('user_id') else None,
                    external_photo_path=item.get('external_photo_path') if not item.get('user_id') else None,
                    role_label=item['role_label'],
                    display_order=item.get('display_order', idx)
                )
                db.session.add(host)
                new_hosts.append(host)

            db.session.commit()
            return new_hosts
        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def get_event_hosts(event_id: int) -> list[dict]:
        """
        Lista hosts con info completa para admin y público.
        Para internos resuelve foto vía `User.avatar_url`; para externos
        construye URL servida `/files/event/<id>/hosts/<filename>`.
        """
        from app.models.event import EventHost
        from app.models.user import User
        from flask import url_for

        hosts = EventHost.query.filter_by(event_id=event_id).order_by(
            EventHost.display_order.asc()
        ).all()

        result = []
        for h in hosts:
            if h.user_id:
                user = db.session.get(User, h.user_id)
                if user:
                    name  = f"{user.first_name} {user.last_name}".strip()
                    email = user.email
                    role_display = user.role.name if user.role else None
                    try:
                        photo_url = user.avatar_url
                    except Exception:
                        photo_url = None
                else:
                    name = "Usuario eliminado"
                    email = None
                    role_display = None
                    photo_url = None
                external_name = None
                external_bio = None
                external_photo_path = None
                bio = None
            else:
                name = h.external_name
                email = None
                role_display = None
                bio = h.external_bio
                external_name = h.external_name
                external_bio = h.external_bio
                external_photo_path = h.external_photo_path
                if h.external_photo_path:
                    filename = h.external_photo_path.split('/')[-1]
                    try:
                        photo_url = url_for(
                            'api_files.event_image',
                            event_id=event_id, kind='hosts', filename=filename
                        )
                    except Exception:
                        photo_url = f"/files/event/{event_id}/hosts/{filename}"
                else:
                    photo_url = None

            result.append({
                'id': h.id,
                'user_id': h.user_id,
                'name': name,
                'full_name': name,
                'email': email,
                'role_display': role_display,
                'bio': bio,
                'photo_url': photo_url,
                'avatar_url': photo_url,
                'external_name': external_name,
                'external_bio': external_bio,
                'external_photo_path': external_photo_path,
                'role_label': h.role_label,
                'display_order': h.display_order,
                'is_external': h.user_id is None,
            })
        return result

    # ============================================================
    # IMÁGENES (COVER + GALLERY)
    # ============================================================

    @staticmethod
    def upload_event_cover(event_id: int, file_storage) -> 'EventImage':
        """
        Guarda cover. Reemplaza el cover anterior (unicidad is_cover=True por evento).
        """
        from app.models.event import EventImage
        from app.utils.files import save_event_image, delete_event_image_file

        event = db.session.get(Event, event_id)
        if not event:
            raise ValueError("Evento no encontrado")

        try:
            # Buscar y eliminar cover previo (DB + disco)
            previous = EventImage.query.filter_by(event_id=event_id, is_cover=True).first()
            if previous:
                delete_event_image_file(previous.path)
                db.session.delete(previous)
                db.session.flush()

            path = save_event_image(file_storage, event_id, 'cover')
            image = EventImage(
                event_id=event_id,
                path=path,
                is_cover=True,
                display_order=0
            )
            db.session.add(image)
            db.session.commit()
            return image
        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def upload_event_gallery_image(event_id: int, file_storage, caption: str = None) -> 'EventImage':
        """Agrega imagen a la galería (no cover)."""
        from app.models.event import EventImage
        from app.utils.files import save_event_image

        event = db.session.get(Event, event_id)
        if not event:
            raise ValueError("Evento no encontrado")

        try:
            last_order = db.session.query(db.func.max(EventImage.display_order)).filter_by(
                event_id=event_id, is_cover=False
            ).scalar() or 0

            path = save_event_image(file_storage, event_id, 'gallery')
            image = EventImage(
                event_id=event_id,
                path=path,
                caption=caption,
                is_cover=False,
                display_order=last_order + 1
            )
            db.session.add(image)
            db.session.commit()
            return image
        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def delete_event_image(image_id: int) -> bool:
        """Borra imagen (DB + disco). Si era cover, el evento queda sin cover (fallback a icono)."""
        from app.models.event import EventImage
        from app.utils.files import delete_event_image_file

        image = db.session.get(EventImage, image_id)
        if not image:
            raise ValueError("Imagen no encontrada")

        try:
            delete_event_image_file(image.path)
            db.session.delete(image)
            db.session.commit()
            return True
        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def get_event_images(event_id: int) -> dict:
        """Retorna {cover: {...} | None, gallery: [...]}."""
        from app.models.event import EventImage

        images = EventImage.query.filter_by(event_id=event_id).order_by(
            EventImage.is_cover.desc(), EventImage.display_order.asc()
        ).all()

        cover = None
        gallery = []
        for img in images:
            data = img.to_dict()
            if img.is_cover:
                cover = data
            else:
                gallery.append(data)

        return {'cover': cover, 'gallery': gallery}

    @staticmethod
    def purge_event_media(event_id: int) -> dict:
        """
        Borra TODAS las imágenes de un evento (DB + disco).
        Se usa al concluir/archivar evento o al cambiar de periodo para liberar espacio.
        """
        from app.models.event import EventImage
        from app.utils.files import delete_all_event_files

        deleted_rows = EventImage.query.filter_by(event_id=event_id).delete()
        deleted_files = delete_all_event_files(event_id)
        db.session.commit()
        return {'db_rows_deleted': deleted_rows, 'files_deleted': deleted_files}