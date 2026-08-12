# app/services/dashboard_service.py

from sqlalchemy import func, and_, or_
from app import db
from app.models import User, Program, Submission, UserProgram, Event, EventSlot, ProgramStep, Step, Phase
from datetime import datetime, timedelta
from app.utils.datetime_utils import now_local


class DashboardService:
    """Servicio para obtener métricas y datos de los dashboards"""

    @staticmethod
    def build_dashboard_context(user, program_id_param=None):
        """
        Construye el contexto para el template del dashboard según el tipo de usuario.

        El dispatch por role.name aquí selecciona la variante de template/contexto,
        no controla acceso (eso lo hace @login_required y los permisos a nivel de datos).
        """
        role_name = user.role.name if user.role else None

        if role_name == 'applicant':
            return DashboardService._applicant_context(user)
        if role_name == 'student':
            return DashboardService._student_context(user)
        if role_name == 'program_admin':
            return DashboardService._program_admin_context(user, program_id_param)
        if role_name == 'postgraduate_admin':
            return {"metrics": DashboardService.get_postgraduate_admin_metrics()}
        return {}

    @staticmethod
    def _applicant_context(user):
        from app.services.admission_service import get_admission_state

        up = user.user_program[0] if user.user_program else None
        program = up.program if up else None

        if program:
            adm_state = get_admission_state(user.id, program.id, up)
        else:
            adm_state = {
                "progress_segments": [],
                "status_count": {},
                "progress_pct": 0,
                "pending_items": [],
                "timeline": []
            }

        deferral_status = None
        if up and up.admission_status == 'deferred':
            from app.services.deferral_service import get_deferral_status
            try:
                deferral_status = get_deferral_status(up.id)
            except Exception:
                deferral_status = None

        from app.services.events_service import EventsService
        try:
            events_widget = EventsService.get_dashboard_widget(user.id)
        except Exception:
            events_widget = None

        return {
            "program": program,
            **adm_state,
            "admission_status": up.admission_status if up else None,
            "user_program_id": up.id if up else None,
            "deferral_status": deferral_status,
            "events_widget": events_widget,
        }

    @staticmethod
    def _student_context(user):
        up = user.user_program[0] if user.user_program else None
        program = up.program if up else None

        permanence_data = None
        if up:
            from app.services.permanence_service import get_student_permanence
            try:
                permanence_data = get_student_permanence(up.id)
            except Exception:
                permanence_data = None

        pending_admission_docs = []
        pending_permanence_docs = []
        if up and program:
            pending = DashboardService.get_student_pending_docs(
                user_id=user.id,
                program_id=program.id,
            )
            pending_admission_docs = pending['admission']
            pending_permanence_docs = pending['permanence']

        from app.services.events_service import EventsService
        try:
            events_widget = EventsService.get_dashboard_widget(user.id)
        except Exception:
            events_widget = None

        return {
            'program': program,
            'up': up,
            'permanence_data': permanence_data,
            'pending_admission_docs': pending_admission_docs,
            'pending_permanence_docs': pending_permanence_docs,
            'events_widget': events_widget,
        }

    @staticmethod
    def _program_admin_context(user, program_id_param):
        coordinated_programs = user.coordinated_programs

        if not coordinated_programs:
            return {
                "program": None,
                "coordinated_programs": [],
                "selected_program_id": None,
                "selected_program": None,
                "show_program_selector": False,
                "show_all_programs": False,
                "metrics": None,
                "recent_submissions": []
            }

        show_all = program_id_param == 'all'
        selected_program_id = None if show_all else (
            int(program_id_param) if program_id_param and program_id_param.isdigit() else None
        )

        if show_all:
            program_ids = [p.id for p in coordinated_programs]
            return {
                "program": None,
                "coordinated_programs": coordinated_programs,
                "selected_program_id": "all",
                "selected_program": None,
                "show_program_selector": len(coordinated_programs) > 1,
                "show_all_programs": True,
                "metrics": DashboardService.get_combined_program_metrics(program_ids),
                "recent_submissions": DashboardService.get_recent_submissions_multiple(program_ids, limit=5),
            }

        selected_program = (
            next((p for p in coordinated_programs if p.id == selected_program_id), coordinated_programs[0])
            if selected_program_id else coordinated_programs[0]
        )

        return {
            "program": selected_program,
            "coordinated_programs": coordinated_programs,
            "selected_program_id": selected_program.id,
            "selected_program": selected_program,
            "show_program_selector": len(coordinated_programs) > 1,
            "show_all_programs": False,
            "metrics": DashboardService.get_program_admin_metrics(selected_program.id),
            "recent_submissions": DashboardService.get_recent_submissions(selected_program.id, limit=5),
        }

    @staticmethod
    def get_program_admin_metrics(program_id):
        """
        Obtiene métricas para el dashboard del coordinador/admin de programa

        Args:
            program_id: ID del programa del cual obtener métricas

        Returns:
            dict con métricas de admisión
        """
        # Total de solicitantes activos en este programa (no rechazados ni expirados,
        # cuentas desactivadas se excluyen)
        total_applicants = db.session.query(func.count(UserProgram.user_id)).join(
            User, UserProgram.user_id == User.id
        ).filter(
            UserProgram.program_id == program_id,
            UserProgram.admission_status.notin_(['rejected', 'expired']),
            User.is_active == True,  # noqa: E712
        ).scalar() or 0

        # Documentos pendientes de revisión (submissions con status 'review')
        pending_reviews = db.session.query(func.count(Submission.id)).join(
            ProgramStep, Submission.program_step_id == ProgramStep.id
        ).filter(
            ProgramStep.program_id == program_id,
            Submission.status == 'review'
        ).scalar() or 0

        # Solicitudes aprobadas (todos los documentos en 'approved')
        # Obtener usuarios con todas sus submissions aprobadas
        approved_count = 0
        rejected_count = 0

        applicants = db.session.query(UserProgram).join(
            User, UserProgram.user_id == User.id
        ).filter(
            UserProgram.program_id == program_id,
            UserProgram.admission_status.in_(['in_progress', 'interview_completed', 'deliberation']),
            User.is_active == True,  # noqa: E712
        ).all()

        for applicant in applicants:
            # Obtener submissions del usuario para este programa
            submissions = db.session.query(Submission).join(
                ProgramStep, Submission.program_step_id == ProgramStep.id
            ).filter(
                Submission.user_id == applicant.user_id,
                ProgramStep.program_id == program_id
            ).all()

            if submissions:
                # Verificar si todos están aprobados
                all_approved = all(s.status == 'approved' for s in submissions)
                has_rejected = any(s.status == 'rejected' for s in submissions)

                if all_approved:
                    approved_count += 1
                elif has_rejected:
                    rejected_count += 1

        # Entrevistas programadas (EventSlots con status 'booked' para este programa)
        from app.models.event import EventWindow
        interviews_scheduled = db.session.query(func.count(EventSlot.id)).join(
            EventWindow, EventSlot.event_window_id == EventWindow.id
        ).join(
            Event, EventWindow.event_id == Event.id
        ).filter(
            Event.program_id == program_id,
            Event.type == 'interview',
            EventSlot.status == 'booked'
        ).scalar() or 0

        # Solicitudes rechazadas
        # Ya calculado arriba

        # Documentos totales subidos
        total_submissions = db.session.query(func.count(Submission.id)).join(
            ProgramStep, Submission.program_step_id == ProgramStep.id
        ).filter(
            ProgramStep.program_id == program_id
        ).scalar() or 0

        return {
            'total_applicants': total_applicants,
            'pending_reviews': pending_reviews,
            'approved_applications': approved_count,
            'rejected_applications': rejected_count,
            'interviews_scheduled': interviews_scheduled,
            'total_submissions': total_submissions,
            'in_process': total_applicants - approved_count - rejected_count
        }

    @staticmethod
    def get_postgraduate_admin_metrics():
        """
        Obtiene métricas globales para el dashboard del administrador de posgrado

        Returns:
            dict con métricas globales de todos los programas
        """
        # Total de programas activos
        total_programs = db.session.query(func.count(Program.id)).scalar() or 0

        # Total de solicitantes activos en todos los programas (no rechazados ni expirados)
        total_applicants = db.session.query(func.count(UserProgram.id)).filter(
            UserProgram.admission_status.notin_(['rejected', 'expired'])
        ).scalar() or 0

        # Total de estudiantes inscritos
        total_students = db.session.query(func.count(UserProgram.id)).filter(
            UserProgram.admission_status == 'enrolled'
        ).scalar() or 0

        # Documentos pendientes de revisión globalmente
        pending_reviews = db.session.query(func.count(Submission.id)).filter(
            Submission.status == 'review'
        ).scalar() or 0

        # Entrevistas pendientes de programar
        # (usuarios con documentos aprobados pero sin entrevista)
        interviews_pending = 0  # Simplificado por ahora

        # Métricas por programa
        programs = db.session.query(Program).all()
        programs_stats = []

        for program in programs:
            applicants_count = db.session.query(func.count(UserProgram.user_id)).filter(
                UserProgram.program_id == program.id,
                UserProgram.admission_status.notin_(['rejected', 'expired'])
            ).scalar() or 0

            pending_docs = db.session.query(func.count(Submission.id)).join(
                ProgramStep, Submission.program_step_id == ProgramStep.id
            ).filter(
                ProgramStep.program_id == program.id,
                Submission.status == 'review'
            ).scalar() or 0

            programs_stats.append({
                'id': program.id,
                'name': program.name,
                'slug': program.slug,
                'applicants': applicants_count,
                'pending_docs': pending_docs
            })

        return {
            'total_programs': total_programs,
            'total_applicants': total_applicants,
            'total_students': total_students,
            'pending_reviews': pending_reviews,
            'interviews_pending': interviews_pending,
            'programs_stats': programs_stats
        }

    @staticmethod
    def get_recent_submissions(program_id, limit=5):
        """
        Obtiene las últimas submissions para un programa

        Args:
            program_id: ID del programa
            limit: Número máximo de submissions a retornar

        Returns:
            list de submissions recientes
        """
        submissions = db.session.query(Submission).join(
            ProgramStep, Submission.program_step_id == ProgramStep.id
        ).join(
            User, Submission.user_id == User.id
        ).filter(
            ProgramStep.program_id == program_id,
            Submission.status.in_(['review', 'pending'])
        ).order_by(
            Submission.upload_date.desc()
        ).limit(limit).all()

        result = []
        for sub in submissions:
            result.append({
                # Public handle of the submission.
                'id': str(sub.uuid) if sub.uuid else None,
                'user_name': f"{sub.user.first_name} {sub.user.last_name}",
                'archive_name': sub.archive.name if sub.archive else 'N/A',
                'status': sub.status,
                'upload_date': sub.upload_date.strftime('%d/%m/%Y %H:%M') if sub.upload_date else 'N/A'
            })

        return result

    @staticmethod
    def get_combined_program_metrics(program_ids):
        """
        Obtiene métricas combinadas para múltiples programas

        Args:
            program_ids: Lista de IDs de programas

        Returns:
            dict con métricas combinadas
        """
        if not program_ids:
            return {
                'total_applicants': 0,
                'pending_reviews': 0,
                'approved_applications': 0,
                'rejected_applications': 0,
                'interviews_scheduled': 0,
                'total_submissions': 0,
                'in_process': 0
            }

        # Total de solicitantes activos en los programas seleccionados (no rechazados ni expirados)
        total_applicants = db.session.query(func.count(UserProgram.user_id)).filter(
            UserProgram.program_id.in_(program_ids),
            UserProgram.admission_status.notin_(['rejected', 'expired'])
        ).scalar() or 0

        # Documentos pendientes de revisión en todos los programas
        pending_reviews = db.session.query(func.count(Submission.id)).join(
            ProgramStep, Submission.program_step_id == ProgramStep.id
        ).filter(
            ProgramStep.program_id.in_(program_ids),
            Submission.status == 'review'
        ).scalar() or 0

        # Total de submissions en todos los programas
        total_submissions = db.session.query(func.count(Submission.id)).join(
            ProgramStep, Submission.program_step_id == ProgramStep.id
        ).filter(
            ProgramStep.program_id.in_(program_ids)
        ).scalar() or 0

        # Calcular aprobados y rechazados
        approved_count = 0
        rejected_count = 0

        for program_id in program_ids:
            applicants = db.session.query(UserProgram).filter(
                UserProgram.program_id == program_id,
                UserProgram.admission_status.in_(['in_progress', 'interview_completed', 'deliberation'])
            ).all()

            for applicant in applicants:
                submissions = db.session.query(Submission).join(
                    ProgramStep, Submission.program_step_id == ProgramStep.id
                ).filter(
                    Submission.user_id == applicant.user_id,
                    ProgramStep.program_id == program_id
                ).all()

                if submissions:
                    all_approved = all(s.status == 'approved' for s in submissions)
                    has_rejected = any(s.status == 'rejected' for s in submissions)

                    if all_approved:
                        approved_count += 1
                    elif has_rejected:
                        rejected_count += 1

        # Entrevistas programadas en todos los programas
        from app.models.event import EventWindow
        interviews_scheduled = db.session.query(func.count(EventSlot.id)).join(
            EventWindow, EventSlot.event_window_id == EventWindow.id
        ).join(
            Event, EventWindow.event_id == Event.id
        ).filter(
            Event.program_id.in_(program_ids),
            Event.type == 'interview',
            EventSlot.status == 'booked'
        ).scalar() or 0

        return {
            'total_applicants': total_applicants,
            'pending_reviews': pending_reviews,
            'approved_applications': approved_count,
            'rejected_applications': rejected_count,
            'interviews_scheduled': interviews_scheduled,
            'total_submissions': total_submissions,
            'in_process': total_applicants - approved_count - rejected_count
        }

    @staticmethod
    def get_recent_submissions_multiple(program_ids, limit=5):
        """
        Obtiene las últimas submissions para múltiples programas

        Args:
            program_ids: Lista de IDs de programas
            limit: Número máximo de submissions a retornar

        Returns:
            list de submissions recientes con nombre del programa
        """
        if not program_ids:
            return []

        submissions = db.session.query(Submission).join(
            ProgramStep, Submission.program_step_id == ProgramStep.id
        ).join(
            Program, ProgramStep.program_id == Program.id
        ).join(
            User, Submission.user_id == User.id
        ).filter(
            ProgramStep.program_id.in_(program_ids),
            Submission.status.in_(['review', 'pending'])
        ).order_by(
            Submission.upload_date.desc()
        ).limit(limit).all()

        result = []
        for sub in submissions:
            result.append({
                # Public handle of the submission.
                'id': str(sub.uuid) if sub.uuid else None,
                'user_name': f"{sub.user.first_name} {sub.user.last_name}",
                'archive_name': sub.archive.name if sub.archive else 'N/A',
                'program_name': sub.program_step.program.name,
                'status': sub.status,
                'upload_date': sub.upload_date.strftime('%d/%m/%Y %H:%M') if sub.upload_date else 'N/A'
            })

        return result

    # ─────────────────────────────────────────────────────────────────────────
    # Dashboard del estudiante
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def get_student_pending_docs(user_id, program_id):
        """
        Documentos de admisión y permanencia que el estudiante no ha aprobado aún.

        Reglas:
        - Admisión: si el usuario ya está enrolled (admission_status='enrolled'),
          no se devuelven pendientes — la admisión ya quedó atrás. Esto cubre el
          caso de altas masivas: a estos estudiantes se les pondrá enrolled y los
          archives de admisión se ignoran (sin necesidad de submissions sintéticas
          ni nuevo estado 'waived'). Si en el futuro se necesita rastrear cuáles
          archives tenían registro histórico, se puede cambiar la lógica aquí.
        - Permanencia: SÓLO se consideran pendientes los archives que tienen una
          DocumentDeadline en el periodo activo. Archives sin ventana de entrega
          NO bloquean ni aparecen pendientes (no es responsabilidad del estudiante
          sin haberse abierto la ventana).

        Returns:
            dict con claves 'admission' y 'permanence', cada una lista de
            {'name': str, 'status': 'pending'|'rejected'|'review'|...}.
        """
        from app.models.archive import Archive
        from app.models.user_program import UserProgram
        from app.models.document_deadline import DocumentDeadline
        from app.models.academic_period import AcademicPeriod

        # Saltar admisión si está enrolled (alta masiva o flujo normal terminado)
        up = UserProgram.query.filter_by(user_id=user_id, program_id=program_id).first()
        skip_admission = bool(up and up.admission_status == 'enrolled')

        def _pending_admission():
            if skip_admission:
                return []
            archives = (
                Archive.query
                .join(Step, Archive.step_id == Step.id)
                .join(ProgramStep, Step.id == ProgramStep.step_id)
                .join(Phase, Step.phase_id == Phase.id)
                .filter(
                    and_(
                        ProgramStep.program_id == program_id,
                        Phase.name == 'admission',
                        Archive.is_uploadable.is_(True),
                    )
                )
                .all()
            )
            pending = []
            for arch in archives:
                sub = (
                    Submission.query
                    .filter_by(user_id=user_id, archive_id=arch.id)
                    .order_by(Submission.upload_date.desc())
                    .first()
                )
                if not sub or sub.status not in ('approved', 'review'):
                    pending.append({
                        'name': arch.name,
                        'status': sub.status if sub else 'pending',
                    })
            return pending

        def _pending_permanence():
            # Sólo archives que tienen ventana de entrega abierta en el periodo
            # activo. Sin DocumentDeadline → no es pendiente del estudiante.
            active_period = AcademicPeriod.get_active_period()
            if not active_period:
                return []
            deadlines = (
                DocumentDeadline.query
                .filter_by(program_id=program_id, academic_period_id=active_period.id)
                .join(Archive, DocumentDeadline.archive_id == Archive.id)
                .filter(Archive.is_active == True)
                .all()
            )
            pending = []
            for dl in deadlines:
                # Filtrar SECIHTI mensual si el estudiante no es becario
                if dl.archive.step_id == 12 and not (up and up.has_conacyt_scholarship):
                    continue
                sub = (
                    Submission.query
                    .filter_by(user_id=user_id, document_deadline_id=dl.id)
                    .order_by(Submission.upload_date.desc())
                    .first()
                )
                if not sub or sub.status not in ('approved', 'review'):
                    pending.append({
                        'name': dl.label or dl.archive.name,
                        'status': sub.status if sub else 'pending',
                    })
            return pending

        return {
            'admission':  _pending_admission(),
            'permanence': _pending_permanence(),
        }
