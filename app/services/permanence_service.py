# app/services/permanence_service.py
"""
Servicio para gestionar la permanencia semestral de estudiantes (Fase 6).

Flujo por semestre:
1. Coordinador confirma que el estudiante se inscribio/pago en el nuevo periodo
2. Se crea un SemesterEnrollment con status='active' y se incrementa current_semester
3. El estudiante ve su estado en su dashboard
4. Al terminar el semestre, el coordinador puede marcarlo como 'completed'
"""

from app import db
from app.models import UserProgram, User, Program, AcademicPeriod
from app.models.semester_enrollment import SemesterEnrollment
from app.services.notification_service import NotificationService
from app.services.user_history_service import UserHistoryService
from app.utils.datetime_utils import now_local
from sqlalchemy import and_
import logging


class PermanenceError(Exception):
    """Error base para operaciones de permanencia."""
    pass


class StudentNotFound(PermanenceError):
    pass


class InvalidStateTransition(PermanenceError):
    pass


# ── Resolución de alcance: ¿a qué programa pertenece este objeto? ─────────────
#
# La REGLA de alcance vive en `app/services/program_scope_service.py`
# (`program_in_scope` / `user_in_scope`); aquí sólo resolvemos el programa
# DUEÑO de cada objeto de permanencia para que la ruta pueda preguntarla.
# Un permiso dice QUÉ puede hacer el usuario, nunca SOBRE QUÉ objeto.
#
# Todos devuelven None cuando el objeto no existe, para que la ruta pueda
# responder 404 sin distinguir "no existe" de "existe pero es de otro programa".

def get_user_program_scope(user_program_id: int):
    """Programa y estudiante dueños de un UserProgram. None si no existe."""
    up = db.session.get(UserProgram, user_program_id)
    if not up:
        return None
    return {
        'user_program_id': up.id,
        'program_id': up.program_id,
        'user_id': up.user_id,
    }


def get_semester_enrollment_scope(semester_enrollment_id: int):
    """Programa y estudiante dueños de una inscripción semestral."""
    se = db.session.get(SemesterEnrollment, semester_enrollment_id)
    if not se:
        return None
    up = se.user_program
    if not up:
        return None
    return {
        'semester_enrollment_id': se.id,
        'user_program_id': up.id,
        'program_id': up.program_id,
        'user_id': up.user_id,
    }


def get_deadline_scope(deadline_id: int):
    """Programa dueño de una ventana de entrega."""
    from app.models.document_deadline import DocumentDeadline

    dl = db.session.get(DocumentDeadline, deadline_id)
    if not dl:
        return None
    return {
        'deadline_id': dl.id,
        'program_id': dl.program_id,
        'user_id': None,
    }


def get_submission_scope(submission_id: int):
    """
    Programa dueño de una submission de permanencia.

    El programa se toma del ProgramStep (fuente que usa el propio servicio para
    localizar el UserProgram) y, si faltara, de la ventana de entrega.
    """
    from app.models.submission import Submission

    sub = db.session.get(Submission, submission_id)
    if not sub:
        return None

    program_id = sub.program_step.program_id if sub.program_step else None
    if program_id is None and sub.document_deadline is not None:
        program_id = sub.document_deadline.program_id

    return {
        'submission_id': sub.id,
        'program_id': program_id,
        'user_id': sub.user_id,
    }


def get_enrolled_students(program_id: int) -> list:
    """
    Obtiene todos los estudiantes inscritos de un programa con su estado
    de permanencia para el periodo activo.

    Returns:
        Lista de dicts con user, user_program, current_enrollment, history
    """
    active_period = AcademicPeriod.get_active_period()

    user_programs = (
        UserProgram.query
        .join(User, UserProgram.user_id == User.id)
        .filter(
            and_(
                UserProgram.program_id == program_id,
                UserProgram.admission_status == 'enrolled',
            )
        )
        .order_by(User.last_name, User.first_name)
        .all()
    )

    all_user_program_ids = [up.id for up in user_programs]

    # Batch load all enrollments in one query
    all_enrollments = (
        SemesterEnrollment.query
        .filter(SemesterEnrollment.user_program_id.in_(all_user_program_ids))
        .join(AcademicPeriod, SemesterEnrollment.academic_period_id == AcademicPeriod.id)
        .order_by(SemesterEnrollment.semester_number)
        .all()
    ) if all_user_program_ids else []

    # Group by user_program_id in Python
    enrollments_by_up: dict = {}
    for se in all_enrollments:
        enrollments_by_up.setdefault(se.user_program_id, []).append(se)

    result = []
    for up in user_programs:
        user = up.user
        up_enrollments = enrollments_by_up.get(up.id, [])

        current_enrollment = None
        if active_period:
            current_enrollment = next(
                (se for se in up_enrollments if se.academic_period_id == active_period.id),
                None,
            )

        result.append({
            'user_program': up.to_dict(),
            'user': {
                'id': user.id,
                'full_name': f"{user.first_name} {user.last_name} {user.mother_last_name or ''}".strip(),
                'email': user.email,
                'control_number': user.control_number,
            },
            'current_enrollment': current_enrollment.to_dict() if current_enrollment else None,
            'current_period': active_period.to_dict() if active_period else None,
            'history': [
                {
                    **se.to_dict(),
                    'period_name': se.academic_period.name,
                    'period_code': se.academic_period.code,
                }
                for se in up_enrollments
            ],
        })

    return result


def get_permanence_stats(program_id: int) -> dict:
    """
    Estadisticas de permanencia para un programa.

    Returns:
        Dict con conteos: total_students, confirmed, pending, on_leave
    """
    active_period = AcademicPeriod.get_active_period()

    total_students = UserProgram.query.filter_by(
        program_id=program_id,
        admission_status='enrolled'
    ).count()

    if not active_period:
        return {
            'total_students': total_students,
            'confirmed': 0,
            'pending': total_students,
            'on_leave': 0,
            'has_active_period': False,
        }

    from sqlalchemy import func as _func

    enrollment_counts = (
        db.session.query(
            SemesterEnrollment.enrollment_confirmed,
            SemesterEnrollment.status,
            _func.count(SemesterEnrollment.id),
        )
        .join(UserProgram, SemesterEnrollment.user_program_id == UserProgram.id)
        .filter(
            UserProgram.program_id == program_id,
            UserProgram.admission_status == 'enrolled',
            SemesterEnrollment.academic_period_id == active_period.id,
        )
        .group_by(SemesterEnrollment.enrollment_confirmed, SemesterEnrollment.status)
        .all()
    )

    confirmed = sum(count for conf, _status, count in enrollment_counts if conf)
    on_leave = sum(
        count for conf, _status, count in enrollment_counts
        if not conf and _status == 'on_leave'
    )

    return {
        'total_students': total_students,
        'confirmed': confirmed,
        'pending': total_students - confirmed - on_leave,
        'on_leave': on_leave,
        'has_active_period': True,
        'active_period_name': active_period.name,
    }


def confirm_semester_enrollment(
    user_program_id: int,
    academic_period_id: int,
    coordinator_id: int,
    notes: str = None,
    payment_proof_path: str = None,
    schedule_path: str = None,
    force: bool = False,
) -> SemesterEnrollment:
    # Webhook futuro:
    # Cuando el SII (Sistema Integral de Información) notifique pagos vía webhook,
    # ese handler debe invocar esta misma función pasando coordinator_id=<usuario sistema>
    # y notes='Confirmado por webhook SII'. La marca enrollment_confirmed=True quedará
    # registrada igual; el path de payment_proof puede venir del PDF que el estudiante
    # subió previamente (no se sobreescribe). Hoy todo es manual desde la pestaña
    # Inscripción.
    """
    El coordinador confirma la inscripcion semestral de un estudiante.

    Si no existe un SemesterEnrollment para este user_program + periodo, lo crea.
    Incrementa current_semester en UserProgram solo si es un periodo nuevo.

    Returns:
        SemesterEnrollment actualizado/creado
    """
    up = UserProgram.query.get(user_program_id)
    if not up:
        raise StudentNotFound(f"UserProgram {user_program_id} no encontrado")

    if up.admission_status != 'enrolled':
        raise InvalidStateTransition(
            f"Solo estudiantes inscritos pueden tener permanencia semestral. "
            f"Estado actual: '{up.admission_status}'"
        )

    period = AcademicPeriod.query.get(academic_period_id)
    if not period:
        raise StudentNotFound(f"Periodo academico {academic_period_id} no encontrado")

    # Verificar si ya existe inscripcion para este periodo
    se = SemesterEnrollment.query.filter_by(
        user_program_id=user_program_id,
        academic_period_id=academic_period_id
    ).first()

    is_new = se is None

    if se and se.enrollment_confirmed:
        raise InvalidStateTransition(
            "La inscripcion semestral ya fue confirmada para este periodo"
        )

    if is_new:
        # ── Buscar el último SE del estudiante para validar y cerrarlo ──
        last_se = (
            SemesterEnrollment.query
            .filter_by(user_program_id=user_program_id)
            .order_by(SemesterEnrollment.semester_number.desc())
            .first()
        )

        # Validación: el periodo destino debe ser el inmediato siguiente al último SE
        # Permite saltar solo si force=True (coordinador confirma explícitamente)
        if last_se and last_se.academic_period_id != academic_period_id and not force:
            last_period = AcademicPeriod.query.get(last_se.academic_period_id)
            if last_period and last_period.start_date and period.start_date:
                # Buscar si hay periodos intermedios entre last_period y period
                intermediate = (
                    AcademicPeriod.query
                    .filter(
                        AcademicPeriod.start_date > last_period.start_date,
                        AcademicPeriod.start_date < period.start_date,
                    )
                    .count()
                )
                if intermediate > 0:
                    raise InvalidStateTransition(
                        f"El estudiante tiene rezago de {intermediate} periodo(s). "
                        f"Su último semestre fue en '{last_period.name}' (sem. {last_se.semester_number}). "
                        f"Para avanzarlo directamente al periodo '{period.name}' se requiere "
                        f"confirmación explícita del coordinador (force=true)."
                    )

        # ── Cerrar el SE anterior si está activo/pendiente ──
        if last_se and last_se.status in ('active', 'pending'):
            last_se.status = 'completed'
            last_se.updated_at = now_local()

        # Calcular el numero de semestre: uno mas que el maximo registrado
        max_sem = db.session.query(
            db.func.max(SemesterEnrollment.semester_number)
        ).filter_by(user_program_id=user_program_id).scalar() or 0

        semester_number = max_sem + 1

        # Validar límite de semestres del programa (duration_semesters + 4)
        program = up.program
        if program and program.duration_semesters:
            max_allowed = program.duration_semesters + 4
            if semester_number > max_allowed:
                raise InvalidStateTransition(
                    f"No se puede confirmar: el estudiante excedería el límite de "
                    f"{max_allowed} semestres del programa "
                    f"(duración oficial {program.duration_semesters} + 4 de gracia)."
                )

        se = SemesterEnrollment(
            user_program_id=user_program_id,
            academic_period_id=academic_period_id,
            semester_number=semester_number,
            status='active',
        )
        db.session.add(se)

        # Actualizar el semestre actual en user_program
        up.current_semester = semester_number
    else:
        se.status = 'active'

    se.enrollment_confirmed = True
    se.confirmed_by = coordinator_id
    se.confirmed_at = now_local()
    se.notes = notes
    if payment_proof_path:
        se.payment_proof_path = payment_proof_path
    if schedule_path:
        se.schedule_path = schedule_path

    user = up.user
    program = up.program

    # Notificar al estudiante
    NotificationService.create_notification(
        user_id=user.id,
        notification_type='semester_enrolled',
        title=f'Inscripción confirmada — {period.name}',
        message=(
            f'Tu inscripción para el semestre {se.semester_number} '
            f'({period.name}) ha sido confirmada. '
            f'Puedes ver tu estado en tu panel de permanencia.'
        ),
        priority='medium',
        action_url='/user/dashboard',
    )

    UserHistoryService.log_action(
        user_id=coordinator_id,
        admin_id=coordinator_id,
        action='semester_enrollment_confirmed',
        details=(
            f'Confirmó semestre {se.semester_number} de '
            f'{user.first_name} {user.last_name} en {program.name} '
            f'para el periodo {period.name}'
        )
    )

    db.session.commit()

    # Notificar al estudiante + coordinadores del programa en tiempo real
    from app.sockets.emitters import emit_user_and_coordinators
    emit_user_and_coordinators(
        'permanence:status_changed',
        {
            'user_id': user.id,
            'user_program_id': up.id,
            'program_id': up.program_id,
            'action': 'semester_confirmed',
            'semester_number': se.semester_number,
            'period_name': period.name,
        },
        user_id=user.id,
        program_id=up.program_id,
    )

    return se


def update_enrollment_status(
    semester_enrollment_id: int,
    new_status: str,
    coordinator_id: int,
    notes: str = None
) -> SemesterEnrollment:
    """
    Actualiza el estado de una inscripcion semestral.

    Estados validos: active, completed, on_leave, dropped
    """
    VALID_STATUSES = {'active', 'completed', 'on_leave', 'dropped'}
    if new_status not in VALID_STATUSES:
        raise ValueError(f"Estado invalido: '{new_status}'. Use: {', '.join(VALID_STATUSES)}")

    se = SemesterEnrollment.query.get(semester_enrollment_id)
    if not se:
        raise StudentNotFound(f"Inscripcion semestral {semester_enrollment_id} no encontrada")

    old_status = se.status
    se.status = new_status
    if notes:
        se.notes = notes
    se.updated_at = now_local()

    up = se.user_program
    user = up.user
    program = up.program
    period = se.academic_period

    STATUS_LABELS = {
        'active': 'Activo',
        'completed': 'Completado',
        'on_leave': 'Baja temporal',
        'dropped': 'Baja definitiva',
    }

    UserHistoryService.log_action(
        user_id=coordinator_id,
        admin_id=coordinator_id,
        action='semester_enrollment_status_updated',
        details=(
            f'Cambió estado de semestre {se.semester_number} de '
            f'{user.first_name} {user.last_name} en {program.name} '
            f'({period.name}): {STATUS_LABELS.get(old_status, old_status)} → '
            f'{STATUS_LABELS.get(new_status, new_status)}'
        )
    )

    # Notificar al estudiante solo en estados finales significativos
    NOTIFY_STATUSES = {
        'completed': ('Semestre completado', f'Tu semestre {se.semester_number} ({period.name}) en {program.name} ha sido marcado como completado.'),
        'on_leave': ('Baja temporal registrada', f'Se ha registrado una baja temporal en tu semestre {se.semester_number} ({period.name}) en {program.name}.'),
        'dropped': ('Baja definitiva registrada', f'Se ha registrado una baja definitiva en tu semestre {se.semester_number} ({period.name}) en {program.name}. Contacta al coordinador si crees que esto es un error.'),
    }

    if new_status in NOTIFY_STATUSES:
        title, message = NOTIFY_STATUSES[new_status]
        priority = 'high' if new_status == 'dropped' else 'medium'
        NotificationService.create_notification(
            user_id=user.id,
            notification_type='enrollment_status_changed',
            title=title,
            message=message,
            priority=priority,
            action_url='/user/dashboard',
        )

        # Enviar email para bajas (temporal y definitiva)
        if new_status in ('dropped', 'on_leave'):
            try:
                from app.services.email_service import EmailService
                from app.services.email_templates import EmailTemplates
                from app.utils.urls import external_url
                # Pinned to APP_BASE_URL, never to the request host.
                dashboard_url = external_url('pages_user.dashboard')
                subject, html = EmailTemplates.enrollment_status_changed(
                    user_name=f"{user.first_name} {user.last_name}",
                    program_name=program.name,
                    period_name=period.name,
                    semester_number=se.semester_number,
                    status=new_status,
                    status_label=STATUS_LABELS.get(new_status, new_status),
                    dashboard_url=dashboard_url,
                )
                EmailService.queue_email(user.id, subject, html)
            except Exception as e:
                import logging as _log
                _log.error(f"Error queueing email for enrollment_status_changed: {e}")

    db.session.commit()
    return se


def reinstate_from_leave(
    user_program_id: int,
    academic_period_id: int,
    coordinator_id: int,
    notes: str = None,
    payment_proof_path: str = None,
) -> SemesterEnrollment:
    """
    Reincorpora a un estudiante que estaba en baja temporal.

    Crea un nuevo SemesterEnrollment activo+confirmado en el periodo dado,
    incrementando el semester_number a max+1. El SE 'on_leave' previo
    permanece sin cambios para preservar el historial.

    Reglas:
    - El último SE del estudiante debe estar en estado 'on_leave'.
    - No debe existir aún un SE para el periodo destino (evita doble inscripción).
    """
    up = UserProgram.query.get(user_program_id)
    if not up:
        raise StudentNotFound(f"UserProgram {user_program_id} no encontrado")

    if up.admission_status != 'enrolled':
        raise InvalidStateTransition(
            f"Sólo estudiantes inscritos pueden reincorporarse. Estado: '{up.admission_status}'"
        )

    period = AcademicPeriod.query.get(academic_period_id)
    if not period:
        raise StudentNotFound(f"Periodo académico {academic_period_id} no encontrado")

    last_se = (
        SemesterEnrollment.query
        .filter_by(user_program_id=user_program_id)
        .order_by(SemesterEnrollment.semester_number.desc())
        .first()
    )
    if not last_se or last_se.status != 'on_leave':
        raise InvalidStateTransition(
            "El estudiante no está en baja temporal — no aplica reincorporación."
        )

    existing = SemesterEnrollment.query.filter_by(
        user_program_id=user_program_id,
        academic_period_id=academic_period_id,
    ).first()
    if existing:
        raise InvalidStateTransition(
            f"Ya existe inscripción para el periodo {period.name}."
        )

    new_se = SemesterEnrollment(
        user_program_id=user_program_id,
        academic_period_id=academic_period_id,
        semester_number=last_se.semester_number + 1,
        status='active',
        enrollment_confirmed=True,
        confirmed_by=coordinator_id,
        confirmed_at=now_local(),
        notes=notes,
        payment_proof_path=payment_proof_path,
    )
    db.session.add(new_se)
    up.current_semester = new_se.semester_number

    user = up.user
    program = up.program

    NotificationService.create_notification(
        user_id=user.id,
        notification_type='semester_reinstated',
        title='Reincorporación confirmada',
        message=(
            f'Has sido reincorporado al programa {program.name} en el semestre '
            f'{new_se.semester_number} ({period.name}).'
        ),
        priority='high',
        action_url='/user/dashboard',
    )

    UserHistoryService.log_action(
        user_id=coordinator_id,
        admin_id=coordinator_id,
        action='semester_enrollment_reinstated',
        details=(
            f'Reincorporó a {user.first_name} {user.last_name} en {program.name} '
            f'al semestre {new_se.semester_number} ({period.name}). Notas: {notes or "—"}'
        ),
    )

    db.session.commit()

    from app.sockets.emitters import emit_user_and_coordinators
    emit_user_and_coordinators(
        'permanence:status_changed',
        {
            'user_id': user.id,
            'user_program_id': up.id,
            'program_id': up.program_id,
            'action': 'reinstated',
            'semester_number': new_se.semester_number,
            'period_name': period.name,
        },
        user_id=user.id,
        program_id=up.program_id,
    )

    return new_se


def get_enrollment_overview(program_id: int) -> dict:
    """
    Vista consolidada para la pestaña de Inscripción del coordinador.

    Devuelve listas categorizadas de UserProgram según su situación de
    inscripción semestral en el periodo activo.

    Categorías:
      - to_confirm:       enrolled sin SE confirmado en el periodo activo
      - on_leave:         último SE.status='on_leave' (candidatos a reincorporar)
      - behind:           último SE en periodo NO-activo y status='active'/'pending'
                          (estudiante rezagado — coordinador puede avanzar manualmente)
      - recently_confirmed: últimos N SE confirmados en el periodo activo
    """
    active_period = AcademicPeriod.get_active_period()

    enrolled = (
        UserProgram.query
        .filter_by(program_id=program_id, admission_status='enrolled')
        .join(User, UserProgram.user_id == User.id)
        .filter(User.is_active == True)  # noqa: E712 — excluir cuentas desactivadas
        .order_by(User.last_name, User.first_name)
        .all()
    )
    up_ids = [up.id for up in enrolled]

    last_se_by_up = {}
    se_for_active_by_up = {}
    if up_ids:
        all_se = (
            SemesterEnrollment.query
            .filter(SemesterEnrollment.user_program_id.in_(up_ids))
            .order_by(SemesterEnrollment.semester_number.desc())
            .all()
        )
        for se in all_se:
            if se.user_program_id not in last_se_by_up:
                last_se_by_up[se.user_program_id] = se
            if active_period and se.academic_period_id == active_period.id:
                se_for_active_by_up[se.user_program_id] = se

    def _row(up, last_se=None, current_se=None):
        u = up.user
        return {
            'user_program': up.to_dict(),
            'user': {
                'id': u.id,
                'full_name': f"{u.first_name} {u.last_name} {u.mother_last_name or ''}".strip(),
                'email': u.email,
                'control_number': u.control_number,
            },
            'last_enrollment': (
                {
                    **last_se.to_dict(),
                    'period_name': last_se.academic_period.name,
                    'period_code': last_se.academic_period.code,
                } if last_se else None
            ),
            'current_enrollment': (
                {
                    **current_se.to_dict(),
                    'period_name': current_se.academic_period.name,
                } if current_se else None
            ),
        }

    to_confirm = []
    on_leave = []
    behind = []
    recently_confirmed = []

    for up in enrolled:
        last = last_se_by_up.get(up.id)
        current = se_for_active_by_up.get(up.id)

        # Sin periodo activo: todos quedan en "to_confirm" como referencia
        if not active_period:
            to_confirm.append(_row(up, last, None))
            continue

        if last and last.status == 'on_leave':
            on_leave.append(_row(up, last, current))
            continue

        if current and current.enrollment_confirmed:
            recently_confirmed.append(_row(up, last, current))
            continue

        if current and not current.enrollment_confirmed:
            to_confirm.append(_row(up, last, current))
            continue

        # No hay SE en periodo activo
        if last and last.academic_period_id != active_period.id and last.status in ('active', 'pending'):
            behind.append(_row(up, last, None))
        else:
            to_confirm.append(_row(up, last, None))

    return {
        'active_period': active_period.to_dict() if active_period else None,
        'to_confirm': to_confirm,
        'on_leave': on_leave,
        'behind': behind,
        'recently_confirmed': recently_confirmed[:20],
        'counts': {
            'to_confirm': len(to_confirm),
            'on_leave': len(on_leave),
            'behind': len(behind),
            'recently_confirmed': len(recently_confirmed),
        },
    }


def get_deadlines_for_program(program_id: int, academic_period_id: int = None,
                               include_archived: bool = False) -> list:
    """
    Obtiene las ventanas de entrega de un programa para el periodo dado
    (por defecto el activo). Por defecto excluye archivadas; pasar
    ``include_archived=True`` para incluirlas (vista del coordinador con
    toggle "Mostrar archivadas").
    """
    from app.models.document_deadline import DocumentDeadline
    from app.models.archive import Archive
    from app.models.submission import Submission

    if not academic_period_id:
        period = AcademicPeriod.get_active_period()
        if not period:
            return []
        academic_period_id = period.id

    q = (
        DocumentDeadline.query
        .filter_by(program_id=program_id, academic_period_id=academic_period_id)
    )
    if not include_archived:
        q = q.filter(DocumentDeadline.is_archived == False)  # noqa: E712

    deadlines = (
        q
        .join(Archive, DocumentDeadline.archive_id == Archive.id)
        .filter(Archive.is_active == True)
        .order_by(DocumentDeadline.sequence)
        .all()
    )

    result = []
    for dl in deadlines:
        total = Submission.query.filter_by(document_deadline_id=dl.id).count()
        pending = Submission.query.filter_by(document_deadline_id=dl.id, status='review').count()
        approved = Submission.query.filter_by(document_deadline_id=dl.id, status='approved').count()
        result.append({
            **dl.to_dict(),
            'stats': {'total': total, 'pending': pending, 'approved': approved},
        })
    return result


def create_document_deadline(
    program_id: int,
    archive_id: int,
    academic_period_id: int,
    label: str,
    sequence: int = 1,
    opens_at=None,
    closes_at=None,
    coordinator_id: int = None,
):
    """El coordinador crea una ventana de entrega para un documento en un periodo."""
    from app.models.document_deadline import DocumentDeadline
    from app.models.archive import Archive

    archive = Archive.query.get(archive_id)
    if not archive or not archive.is_active:
        raise ValueError(f"Archive {archive_id} no encontrado o inactivo")

    dl = DocumentDeadline(
        archive_id=archive_id,
        program_id=program_id,
        academic_period_id=academic_period_id,
        sequence=sequence,
        label=label,
        opens_at=opens_at,
        closes_at=closes_at,
        is_open=True,
        created_by=coordinator_id,
    )
    db.session.add(dl)
    db.session.flush()

    if coordinator_id:
        UserHistoryService.log_action(
            user_id=coordinator_id,
            admin_id=coordinator_id,
            action='document_deadline_created',
            details=(
                f'Creó ventana "{label}" (archive={archive.name}, '
                f'program={program_id}, period={academic_period_id}, '
                f'sequence={sequence}, opens_at={opens_at}, closes_at={closes_at})'
            ),
        )

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    # Notificar a los estudiantes inscritos del programa (vía Celery)
    try:
        student_ids = [
            up.user_id for up in
            UserProgram.query.filter_by(program_id=program_id, admission_status='enrolled').all()
        ]
        if student_ids:
            NotificationService.send_bulk(
                user_ids=student_ids,
                notification_type='deadline_created',
                title=f'Nueva ventana de entrega: {label}',
                message=f'Se ha abierto la ventana de entrega "{label}". Revisa tu panel de permanencia para subir tu documento.',
                priority='medium',
                action_url='/user/dashboard',
            )
    except Exception as e:
        logging.error(f"Error sending bulk notification for deadline: {e}")

    return dl


def toggle_document_deadline(deadline_id: int, is_open: bool, coordinator_id: int):
    """El coordinador abre o cierra manualmente una ventana de entrega."""
    from app.models.document_deadline import DocumentDeadline

    dl = DocumentDeadline.query.get(deadline_id)
    if not dl:
        raise StudentNotFound(f"Ventana {deadline_id} no encontrada")

    dl.is_open = is_open
    UserHistoryService.log_action(
        user_id=coordinator_id,
        admin_id=coordinator_id,
        action='deadline_opened' if is_open else 'deadline_closed',
        details=f'{"Abrió" if is_open else "Cerró"} ventana "{dl.label}"',
    )
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    # Notificar a estudiantes solo cuando se ABRE la ventana
    if is_open:
        try:
            student_ids = [
                up.user_id for up in
                UserProgram.query.filter_by(program_id=dl.program_id, admission_status='enrolled').all()
            ]
            if student_ids:
                NotificationService.send_bulk(
                    user_ids=student_ids,
                    notification_type='deadline_opened',
                    title=f'Ventana de entrega abierta: {dl.label}',
                    message=f'La ventana de entrega "{dl.label}" se ha abierto. Sube tu documento antes de que cierre.',
                    priority='medium',
                    action_url='/user/dashboard',
                )
        except Exception as e:
            logging.error(f"Error sending bulk notification for deadline toggle: {e}")

    return dl


def update_document_deadline(deadline_id: int, coordinator_id: int,
                              label: str = None,
                              opens_at=None,
                              closes_at=None,
                              is_open: bool = None) -> "DocumentDeadline":
    """
    Coordinador edita una ventana de entrega: cambiar label, mover fechas,
    reabrir/cerrar. Cualquier campo None se ignora.

    Si la ventana se REABRE (transición False→True), notifica a los estudiantes.
    """
    from app.models.document_deadline import DocumentDeadline

    dl = DocumentDeadline.query.get(deadline_id)
    if not dl:
        raise StudentNotFound(f"Ventana {deadline_id} no encontrada")

    changed = []

    if label is not None and label.strip() and label != dl.label:
        dl.label = label.strip()
        changed.append('label')

    if opens_at is not None and opens_at != dl.opens_at:
        dl.opens_at = opens_at
        changed.append('opens_at')

    if closes_at is not None and closes_at != dl.closes_at:
        dl.closes_at = closes_at
        changed.append('closes_at')

    reopened = False
    if is_open is not None and bool(is_open) != bool(dl.is_open):
        if is_open and not dl.is_open:
            reopened = True
        dl.is_open = bool(is_open)
        changed.append('is_open')

    if not changed:
        return dl

    UserHistoryService.log_action(
        user_id=coordinator_id,
        admin_id=coordinator_id,
        action='deadline_updated',
        details=f'Actualizó ventana "{dl.label}" — campos: {", ".join(changed)}',
    )

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    # Notificar a los estudiantes si la ventana se REABRE explícitamente
    if reopened:
        try:
            student_ids = [
                up.user_id for up in
                UserProgram.query.filter_by(
                    program_id=dl.program_id, admission_status='enrolled'
                ).all()
            ]
            if student_ids:
                NotificationService.send_bulk(
                    user_ids=student_ids,
                    notification_type='deadline_reopened',
                    title=f'Ventana reabierta: {dl.label}',
                    message=(
                        f'La ventana "{dl.label}" se ha reabierto. '
                        f'Cierra: {dl.closes_at.strftime("%d/%m/%Y") if dl.closes_at else "sin fecha"}.'
                    ),
                    priority='medium',
                    action_url='/user/dashboard',
                )
        except Exception as e:
            logging.error(f"Error notifying students after deadline reopen: {e}")

    return dl


def archive_document_deadline(deadline_id: int, coordinator_id: int):
    """
    Archiva (soft-delete) una ventana de entrega. No la borra de BD: las
    submissions ligadas conservan su FK y el historial queda intacto. La
    ventana deja de aparecer en panels activos pero puede restaurarse.
    """
    from app.models.document_deadline import DocumentDeadline

    dl = DocumentDeadline.query.get(deadline_id)
    if not dl:
        raise StudentNotFound(f"Ventana {deadline_id} no encontrada")

    if dl.is_archived:
        raise InvalidStateTransition(f"La ventana '{dl.label}' ya está archivada.")

    dl.is_archived = True
    dl.archived_at = now_local()
    dl.archived_by = coordinator_id
    dl.is_open = False  # Cerrar al archivar para evitar nuevas entregas

    UserHistoryService.log_action(
        user_id=coordinator_id,
        admin_id=coordinator_id,
        action='deadline_archived',
        details=f'Archivó ventana "{dl.label}"',
    )
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


def restore_document_deadline(deadline_id: int, coordinator_id: int):
    """Desarchiva una ventana. Queda cerrada por seguridad — coordinador decide reabrir."""
    from app.models.document_deadline import DocumentDeadline

    dl = DocumentDeadline.query.get(deadline_id)
    if not dl:
        raise StudentNotFound(f"Ventana {deadline_id} no encontrada")
    if not dl.is_archived:
        raise InvalidStateTransition(f"La ventana '{dl.label}' no está archivada.")

    dl.is_archived = False
    dl.archived_at = None
    dl.archived_by = None
    dl.is_open = False

    UserHistoryService.log_action(
        user_id=coordinator_id,
        admin_id=coordinator_id,
        action='deadline_restored',
        details=f'Restauró ventana "{dl.label}"',
    )
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


# Alias retro-compat: endpoint DELETE existente y cualquier llamador antiguo
# ahora archiva en vez de borrar. El borrado físico ya no se expone.
delete_document_deadline = archive_document_deadline


def get_student_documents_for_period(user_program_id: int) -> list:
    """
    Obtiene las ventanas de entrega del periodo activo para el estudiante,
    con el estado de su última submission en cada una.
    Omite documentos de Step 12 (SECIHTI) si el estudiante no es becario.
    """
    from app.models.document_deadline import DocumentDeadline
    from app.models.archive import Archive
    from app.models.submission import Submission

    up = UserProgram.query.get(user_program_id)
    if not up:
        raise StudentNotFound(f"UserProgram {user_program_id} no encontrado")

    active_period = AcademicPeriod.get_active_period()
    if not active_period:
        return []

    deadlines = (
        DocumentDeadline.query
        .filter_by(program_id=up.program_id, academic_period_id=active_period.id)
        .filter(DocumentDeadline.is_archived == False)  # noqa: E712
        .join(Archive, DocumentDeadline.archive_id == Archive.id)
        .filter(Archive.is_active == True)
        .order_by(DocumentDeadline.sequence)
        .all()
    )

    result = []
    for dl in deadlines:
        # Filtrar Step 12 (Becarios SECIHTI) si el estudiante no es becario
        if dl.archive.step_id == 12 and not up.has_conacyt_scholarship:
            continue
        sub = (
            Submission.query
            .filter_by(user_id=up.user_id, document_deadline_id=dl.id)
            .order_by(Submission.upload_date.desc())
            .first()
        )
        result.append({
            'deadline': dl.to_dict(),
            'archive': dl.archive.to_dict(),
            'submission': sub.to_dict() if sub else None,
        })
    return result


def submit_permanence_document(
    user_program_id: int,
    document_deadline_id: int,
    file_storage,
    student_id: int,
) -> dict:
    """El estudiante sube un documento para una ventana de entrega activa."""
    from app.models.document_deadline import DocumentDeadline
    from app.models.submission import Submission
    from app.models.program_step import ProgramStep
    from app.utils.files import save_user_doc

    up = UserProgram.query.get(user_program_id)
    if not up or up.user_id != student_id:
        raise StudentNotFound("UserProgram no encontrado o no pertenece al estudiante")

    dl = DocumentDeadline.query.get(document_deadline_id)
    if not dl:
        raise StudentNotFound(f"Ventana {document_deadline_id} no encontrada")

    if not dl.is_currently_open:
        raise InvalidStateTransition(
            f"La ventana '{dl.label}' está cerrada. No se puede subir el documento."
        )

    if dl.program_id != up.program_id:
        raise InvalidStateTransition("Esta ventana no corresponde a tu programa")

    program_step = ProgramStep.query.filter_by(
        program_id=up.program_id,
        step_id=dl.archive.step_id,
    ).first()
    if not program_step:
        raise InvalidStateTransition("El documento no está configurado para este programa")

    existing = Submission.query.filter_by(
        user_id=up.user_id,
        document_deadline_id=dl.id,
        status='review',
    ).first()
    if existing:
        raise InvalidStateTransition(
            "Ya tienes un documento en revisión para esta ventana. "
            "Espera a que sea revisado antes de volver a subir."
        )

    file_path = save_user_doc(file_storage, up.user_id, 'permanence', dl.archive.name)

    sub = Submission(
        file_path=file_path,
        status='review',
        user_id=up.user_id,
        archive_id=dl.archive_id,
        program_step_id=program_step.id,
        semester=up.current_semester,
        uploaded_by=student_id,
        uploaded_by_role='student',
        deadline_at=dl.closes_at,
    )
    sub.document_deadline_id = dl.id
    sub.academic_period_id = dl.academic_period_id
    db.session.add(sub)

    UserHistoryService.log_action(
        user_id=student_id,
        admin_id=student_id,
        action='permanence_document_submitted',
        details=f'Subió "{dl.archive.name}" ({dl.label}) — Semestre {up.current_semester}',
    )

    # Notificar al coordinador del programa
    try:
        program = Program.query.get(up.program_id)
        if program and program.coordinator_id:
            user = User.query.get(student_id)
            student_name = f"{user.first_name} {user.last_name}" if user else f"Usuario {student_id}"
            NotificationService.create_notification(
                user_id=program.coordinator_id,
                notification_type='permanence_doc_submitted',
                title='Documento de permanencia recibido',
                message=f'{student_name} ha subido "{dl.archive.name}" ({dl.label}).',
                priority='low',
                action_url='/coordinator/permanence',
                data={'student_id': student_id, 'program_id': up.program_id, 'deadline_id': dl.id},
            )
    except Exception as e:
        logging.error(f"Error notifying coordinator of permanence doc: {e}")

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    # WebSocket: fire-and-forget DESPUÉS del commit
    try:
        from app.extensions import socketio
        socketio.emit('submission:new', {
            'user_id': student_id,
            'submission_id': sub.id,
            'archive_name': dl.archive.name,
            'program_id': up.program_id,
            'context': 'permanence',
        }, room='role:coordinator')
    except Exception:
        pass

    return sub.to_dict()


def get_pending_documents(program_id: int) -> list:
    """Lista submissions de permanencia en estado 'review' para el programa activo."""
    from app.models.document_deadline import DocumentDeadline
    from app.models.submission import Submission

    active_period = AcademicPeriod.get_active_period()
    deadline_subq = (
        db.session.query(DocumentDeadline.id)
        .filter(DocumentDeadline.program_id == program_id)
    )
    if active_period:
        deadline_subq = deadline_subq.filter(
            DocumentDeadline.academic_period_id == active_period.id
        )

    subs = (
        Submission.query
        .filter(
            Submission.document_deadline_id.in_(deadline_subq),
            Submission.status == 'review',
        )
        .join(User, Submission.user_id == User.id)
        .order_by(Submission.upload_date.desc())
        .all()
    )

    result = []
    for sub in subs:
        user = sub.user
        result.append({
            'submission': sub.to_dict(),
            'user': {
                'id': user.id,
                'full_name': (
                    f"{user.first_name} {user.last_name} "
                    f"{user.mother_last_name or ''}".strip()
                ),
                'control_number': user.control_number,
            },
            'deadline_label': (
                sub.document_deadline.label if sub.document_deadline else sub.archive.name
            ),
            'archive_name': sub.archive.name if sub.archive else None,
            'file_url': f'/files/doc/{sub.file_path}' if sub.file_path else None,
        })
    return result


def review_permanence_document(
    submission_id: int,
    coordinator_id: int,
    status: str,
    notes: str = None,
) -> dict:
    """El coordinador aprueba o rechaza un documento de permanencia."""
    from app.models.submission import Submission

    if status not in ('approved', 'rejected'):
        raise ValueError("status debe ser 'approved' o 'rejected'")

    sub = Submission.query.get(submission_id)
    if not sub:
        raise StudentNotFound(f"Submission {submission_id} no encontrada")

    if sub.status != 'review':
        raise InvalidStateTransition(
            f"Solo submissions en estado 'review' pueden revisarse. "
            f"Estado actual: '{sub.status}'"
        )

    sub.status = status
    sub.reviewer_id = coordinator_id
    sub.review_date = now_local()
    sub.reviewer_comment = notes

    dl_label = sub.document_deadline.label if sub.document_deadline else sub.archive.name

    if status == 'approved':
        NotificationService.create_notification(
            user_id=sub.user_id,
            notification_type='permanence_doc_approved',
            title=f'Documento aprobado — {dl_label}',
            message=f'Tu documento "{dl_label}" fue aprobado para el periodo actual.',
            priority='medium',
            action_url='/user/dashboard',
        )
    else:
        NotificationService.create_notification(
            user_id=sub.user_id,
            notification_type='permanence_doc_rejected',
            title=f'Documento rechazado — {dl_label}',
            message=(
                f'Tu documento "{dl_label}" fue rechazado. '
                f'Motivo: {notes or "Sin especificar"}. '
                'Vuelve a subirlo cuando la ventana esté abierta.'
            ),
            priority='high',
            action_url='/user/dashboard',
        )
        # Enviar email para documentos rechazados
        try:
            from app.services.email_service import EmailService
            from app.services.email_templates import EmailTemplates
            from app.utils.urls import external_url
            user = User.query.get(sub.user_id)
            if user:
                # Pinned to APP_BASE_URL, never to the request host.
                dashboard_url = external_url('pages_user.dashboard')
                subject, html = EmailTemplates.permanence_doc_rejected(
                    user_name=f"{user.first_name} {user.last_name}",
                    document_label=dl_label,
                    reason=notes or "Sin especificar",
                    dashboard_url=dashboard_url,
                )
                EmailService.queue_email(sub.user_id, subject, html)
        except Exception as e:
            logging.error(f"Error queueing email for permanence_doc_rejected: {e}")

    UserHistoryService.log_action(
        user_id=sub.user_id,
        admin_id=coordinator_id,
        action=f'permanence_document_{status}',
        details=f'{"Aprobó" if status == "approved" else "Rechazó"} "{dl_label}". {notes or ""}',
    )
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    # Notificar al estudiante + coordinadores del programa en tiempo real
    from app.sockets.emitters import emit_user_and_coordinators
    program_id = sub.program_step.program_id if sub.program_step else None
    emit_user_and_coordinators(
        'permanence:status_changed',
        {
            'user_id': sub.user_id,
            'submission_id': sub.id,
            'program_id': program_id,
            'action': 'doc_reviewed',
            'status': status,
            'document_label': dl_label,
        },
        user_id=sub.user_id,
        program_id=program_id,
    )

    return sub.to_dict()


def _get_leave_request_archive():
    """Retorna el archive de baja temporal identificado por archive_key='leave_request'."""
    from app.models.archive import Archive
    return Archive.query.filter_by(
        archive_key='leave_request',
        is_active=True,
    ).first()


def get_student_leave_request(user_program_id: int) -> dict:
    """
    Retorna el estado de la solicitud de baja temporal más reciente del estudiante,
    o None si no existe ninguna.
    """
    from app.models.submission import Submission

    up = UserProgram.query.get(user_program_id)
    if not up:
        raise StudentNotFound(f"UserProgram {user_program_id} no encontrado")

    archive = _get_leave_request_archive()
    if not archive:
        return {'archive_available': False, 'submission': None}

    sub = (
        Submission.query
        .filter_by(user_id=up.user_id, archive_id=archive.id)
        .order_by(Submission.upload_date.desc())
        .first()
    )
    return {
        'archive_available': True,
        'archive_id': archive.id,
        'submission': sub.to_dict() if sub else None,
    }


def submit_leave_request(
    user_program_id: int,
    file_storage,
    student_id: int,
) -> dict:
    """
    El estudiante sube la Solicitud de Baja Temporal.
    No requiere DocumentDeadline: siempre disponible si el archive está activo.
    """
    from app.models.submission import Submission
    from app.models.program_step import ProgramStep
    from app.utils.files import save_user_doc

    up = UserProgram.query.get(user_program_id)
    if not up or up.user_id != student_id:
        raise StudentNotFound("UserProgram no encontrado o no pertenece al estudiante")

    active_period = AcademicPeriod.get_active_period()
    if not active_period:
        raise InvalidStateTransition("No hay periodo académico activo")

    se = SemesterEnrollment.query.filter_by(
        user_program_id=up.id,
        academic_period_id=active_period.id,
    ).first()
    if not se or se.status not in ('active',):
        raise InvalidStateTransition(
            "Solo puedes solicitar baja temporal con una inscripción activa en el periodo actual."
        )

    archive = _get_leave_request_archive()
    if not archive:
        raise ValueError("El archive 'Solicitud de Baja Temporal' no está disponible")

    program_step = ProgramStep.query.filter_by(
        program_id=up.program_id,
        step_id=archive.step_id,
    ).first()
    if not program_step:
        raise InvalidStateTransition("El documento no está configurado para este programa")

    existing = Submission.query.filter_by(
        user_id=up.user_id,
        archive_id=archive.id,
        status='review',
    ).first()
    if existing:
        raise InvalidStateTransition(
            "Ya tienes una solicitud de baja en revisión. Espera a que el coordinador la procese."
        )

    file_path = save_user_doc(file_storage, up.user_id, 'permanence', archive.name)

    sub = Submission(
        file_path=file_path,
        status='review',
        user_id=up.user_id,
        archive_id=archive.id,
        program_step_id=program_step.id,
        semester=up.current_semester,
        uploaded_by=student_id,
        uploaded_by_role='student',
    )
    sub.academic_period_id = active_period.id
    db.session.add(sub)

    UserHistoryService.log_action(
        user_id=student_id,
        admin_id=student_id,
        action='leave_request_submitted',
        details=f'Subió solicitud de baja temporal — Semestre {up.current_semester}',
    )

    # Notificar al coordinador del programa
    try:
        program = Program.query.get(up.program_id)
        if program and program.coordinator_id:
            user = User.query.get(student_id)
            student_name = f"{user.first_name} {user.last_name}" if user else f"Usuario {student_id}"
            NotificationService.create_notification(
                user_id=program.coordinator_id,
                notification_type='leave_request_submitted',
                title='Solicitud de baja temporal recibida',
                message=f'{student_name} ha solicitado baja temporal en {program.name} (Semestre {up.current_semester}).',
                priority='high',
                action_url='/coordinator/permanence',
                data={'student_id': student_id, 'program_id': up.program_id},
            )
    except Exception as e:
        logging.error(f"Error notifying coordinator of leave request: {e}")

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    # WebSocket: fire-and-forget DESPUÉS del commit
    try:
        from app.extensions import socketio
        socketio.emit('submission:new', {
            'user_id': student_id,
            'submission_id': sub.id,
            'archive_name': archive.name,
            'program_id': up.program_id,
            'context': 'leave_request',
        }, room='role:coordinator')
    except Exception:
        pass

    return sub.to_dict()


def get_pending_leave_requests(program_id: int) -> list:
    """
    Lista las solicitudes de baja temporal en estado 'review' para un programa.
    """
    from app.models.submission import Submission
    from app.models.program_step import ProgramStep

    archive = _get_leave_request_archive()
    if not archive:
        return []

    # Subquery: user_ids del programa (sólo cuentas activas)
    user_ids_subq = (
        db.session.query(UserProgram.user_id)
        .join(User, UserProgram.user_id == User.id)
        .filter(
            UserProgram.program_id == program_id,
            UserProgram.admission_status == 'enrolled',
            User.is_active == True,  # noqa: E712
        )
    )

    subs = (
        Submission.query
        .filter(
            Submission.archive_id == archive.id,
            Submission.status == 'review',
            Submission.user_id.in_(user_ids_subq),
        )
        .join(User, Submission.user_id == User.id)
        .order_by(Submission.upload_date.asc())
        .all()
    )

    # Batch load user_programs to avoid N+1
    user_ids = [sub.user_id for sub in subs]
    ups_by_user_id = {
        up.user_id: up for up in
        UserProgram.query.filter(
            UserProgram.user_id.in_(user_ids),
            UserProgram.program_id == program_id,
        ).all()
    } if user_ids else {}

    result = []
    for sub in subs:
        user = sub.user
        up = ups_by_user_id.get(user.id)
        result.append({
            'submission': sub.to_dict(),
            'file_url': f'/files/doc/{sub.file_path}' if sub.file_path else None,
            'user': {
                'id': user.id,
                'full_name': f"{user.first_name} {user.last_name} {user.mother_last_name or ''}".strip(),
                'control_number': user.control_number,
            },
            'current_semester': up.current_semester if up else None,
        })
    return result


def process_leave_request(
    submission_id: int,
    coordinator_id: int,
    approve: bool,
    notes: str = None,
) -> dict:
    """
    El coordinador aprueba o rechaza una solicitud de baja temporal.
    Si aprueba: el SemesterEnrollment activo pasa a 'on_leave'.
    """
    from app.models.submission import Submission
    from app.models.program_step import ProgramStep

    sub = Submission.query.get(submission_id)
    if not sub:
        raise StudentNotFound(f"Submission {submission_id} no encontrada")

    if not sub.archive or sub.archive.archive_key != 'leave_request':
        raise InvalidStateTransition("Este documento no es una solicitud de baja temporal")

    if sub.status != 'review':
        raise InvalidStateTransition(
            f"Solo solicitudes en estado 'review' pueden procesarse. Estado: '{sub.status}'"
        )

    sub.status = 'approved' if approve else 'rejected'
    sub.reviewer_id = coordinator_id
    sub.review_date = now_local()
    sub.reviewer_comment = notes

    if approve:
        # Buscar el SemesterEnrollment activo del estudiante en el programa correcto
        program_step = ProgramStep.query.get(sub.program_step_id)
        if program_step:
            up = UserProgram.query.filter_by(
                user_id=sub.user_id,
                program_id=program_step.program_id,
            ).first()
            if up:
                active_se = SemesterEnrollment.query.filter_by(
                    user_program_id=up.id,
                    status='active',
                ).first()
                if active_se:
                    active_se.status = 'on_leave'

        NotificationService.create_notification(
            user_id=sub.user_id,
            notification_type='leave_request_approved',
            title='Baja temporal aprobada',
            message=(
                'Tu solicitud de baja temporal fue aprobada. '
                'Tu estado se actualizó a baja temporal. '
                'Para reincorporarte, contacta al coordinador.'
            ),
            priority='high',
            action_url='/user/dashboard',
        )
    else:
        NotificationService.create_notification(
            user_id=sub.user_id,
            notification_type='leave_request_rejected',
            title='Baja temporal rechazada',
            message=(
                f'Tu solicitud de baja temporal fue rechazada. '
                f'Motivo: {notes or "Sin especificar"}.'
            ),
            priority='high',
            action_url='/user/dashboard',
        )

    UserHistoryService.log_action(
        user_id=sub.user_id,
        admin_id=coordinator_id,
        action='leave_request_approved' if approve else 'leave_request_rejected',
        details=f'{"Aprobó" if approve else "Rechazó"} solicitud de baja temporal. {notes or ""}',
    )

    # Enviar email al estudiante
    try:
        from app.services.email_service import EmailService
        from app.services.email_templates import EmailTemplates
        from app.utils.urls import external_url
        user = User.query.get(sub.user_id)
        if user:
            # Obtener nombre del programa
            up_for_email = UserProgram.query.filter_by(user_id=sub.user_id).first()
            program_name = up_for_email.program.name if up_for_email and up_for_email.program else 'tu programa'
            # Pinned to APP_BASE_URL, never to the request host.
            dashboard_url = external_url('pages_user.dashboard')
            subject, html = EmailTemplates.leave_request_result(
                user_name=f"{user.first_name} {user.last_name}",
                program_name=program_name,
                approved=approve,
                reason=notes or "",
                dashboard_url=dashboard_url,
            )
            EmailService.queue_email(sub.user_id, subject, html)
    except Exception as e:
        logging.error(f"Error queueing email for leave_request_result: {e}")

    db.session.commit()

    # Notificar al estudiante + coordinadores del programa en tiempo real
    from app.sockets.emitters import emit_user_and_coordinators
    program_id = sub.program_step.program_id if sub.program_step else None
    emit_user_and_coordinators(
        'permanence:status_changed',
        {
            'user_id': sub.user_id,
            'submission_id': sub.id,
            'program_id': program_id,
            'action': 'leave_decided',
            'approved': bool(approve),
            'new_enrollment_status': 'on_leave' if approve else None,
        },
        user_id=sub.user_id,
        program_id=program_id,
    )

    return {
        'submission': sub.to_dict(),
        'new_enrollment_status': 'on_leave' if approve else None,
    }


def create_monthly_conacyt_deadlines(
    program_id: int,
    academic_period_id: int,
    coordinator_id: int,
) -> dict:
    """
    Crea las ventanas de entrega mensuales para becarios SECIHTI del semestre.

    Busca el archive activo de Step 12 (Formato de Desempeño) y genera un
    DocumentDeadline por cada mes dentro del rango del periodo académico,
    usando el número de mes como sequence (1-12).

    Es idempotente: omite los meses que ya tienen ventana creada.

    Returns:
        {'created': int, 'skipped': int, 'deadlines': [...]}
    """
    import calendar
    from datetime import datetime
    from app.models.document_deadline import DocumentDeadline
    from app.models.archive import Archive

    period = AcademicPeriod.query.get(academic_period_id)
    if not period:
        raise StudentNotFound(f"Periodo académico {academic_period_id} no encontrado")

    conacyt_archive = (
        Archive.query
        .filter(Archive.step_id == 12, Archive.is_active == True)
        .first()
    )
    if not conacyt_archive:
        raise ValueError(
            "No se encontró el archive de SECIHTI (Step 12) o está inactivo. "
            "Verifica que el archive 'Formato de Desempeño' exista y esté activo."
        )

    # Calcular meses entre start_date y end_date del periodo
    start = period.start_date
    end = period.end_date
    months = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append((y, m))
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1

    MONTH_NAMES = {
        1: 'Enero', 2: 'Febrero', 3: 'Marzo', 4: 'Abril',
        5: 'Mayo', 6: 'Junio', 7: 'Julio', 8: 'Agosto',
        9: 'Septiembre', 10: 'Octubre', 11: 'Noviembre', 12: 'Diciembre',
    }

    created = []
    skipped = 0

    for year, month in months:
        existing = DocumentDeadline.query.filter_by(
            program_id=program_id,
            academic_period_id=academic_period_id,
            archive_id=conacyt_archive.id,
            sequence=month,
        ).first()

        if existing:
            skipped += 1
            continue

        last_day = calendar.monthrange(year, month)[1]
        opens_at = datetime(year, month, 1, 0, 0, 0)
        closes_at = datetime(year, month, last_day, 23, 59, 59)

        dl = DocumentDeadline(
            archive_id=conacyt_archive.id,
            program_id=program_id,
            academic_period_id=academic_period_id,
            sequence=month,
            label=f"Formato SECIHTI — {MONTH_NAMES[month]} {year}",
            opens_at=opens_at,
            closes_at=closes_at,
            is_open=True,
            created_by=coordinator_id,
        )
        db.session.add(dl)
        created.append(dl)

    if created:
        UserHistoryService.log_action(
            user_id=coordinator_id,
            admin_id=coordinator_id,
            action='conacyt_deadlines_created',
            details=(
                f'Creó {len(created)} ventana(s) mensual(es) SECIHTI '
                f'para el periodo {period.name}'
            ),
        )

    db.session.commit()

    # Notificar solo a becarios SECIHTI del programa (vía Celery)
    if created:
        try:
            conacyt_student_ids = [
                up.user_id for up in
                UserProgram.query.filter_by(
                    program_id=program_id,
                    admission_status='enrolled',
                    has_conacyt_scholarship=True,
                ).all()
            ]
            if conacyt_student_ids:
                NotificationService.send_bulk(
                    user_ids=conacyt_student_ids,
                    notification_type='conacyt_deadlines_created',
                    title='Ventanas mensuales SECIHTI creadas',
                    message=f'Se crearon {len(created)} ventana(s) de entrega SECIHTI para {period.name}. Revisa tu panel.',
                    priority='medium',
                    action_url='/user/dashboard',
                )
        except Exception as e:
            logging.error(f"Error sending bulk notification for SECIHTI deadlines: {e}")

    return {
        'created': len(created),
        'skipped': skipped,
        'deadlines': [dl.to_dict() for dl in created],
    }


def set_conacyt_scholarship(
    user_program_id: int,
    coordinator_id: int,
    value: bool = None,
) -> dict:
    """
    Activa o desactiva la beca SECIHTI/CONACyT de un estudiante.

    Si `value` es None, alterna el valor actual (comportamiento histórico del
    endpoint). Registra historial, notifica al estudiante y emite el evento de
    socket; el historial y la notificación no rompen la operación si fallan.

    Returns:
        {'has_conacyt_scholarship': bool, 'user_id': int, 'program_id': int}
    """
    up = db.session.get(UserProgram, user_program_id)
    if not up:
        raise StudentNotFound(f"UserProgram {user_program_id} no encontrado")

    new_value = (not up.has_conacyt_scholarship) if value is None else bool(value)
    up.has_conacyt_scholarship = new_value
    label = 'activada' if new_value else 'desactivada'

    # Historial
    try:
        UserHistoryService.log_action(
            user_id=up.user_id,
            admin_id=coordinator_id,
            action='conacyt_scholarship_changed',
            details=(
                f'Beca CONACyT {label} por coordinador en '
                f'{up.program.name if up.program else "programa"}'
            ),
        )
    except Exception:
        pass

    # Notificar al estudiante
    try:
        NotificationService.create_notification(
            user_id=up.user_id,
            notification_type='conacyt_scholarship_changed',
            title=f'Beca CONACyT {label}',
            message=(
                f'Tu beca CONACyT ha sido {label} por el coordinador. '
                f'{"Ahora verás las ventanas de entrega CONACyT en tu panel." if new_value else "Ya no verás las ventanas CONACyT."}'
            ),
            priority='medium',
            action_url='/user/dashboard',
        )
    except Exception:
        pass

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    # Socket: coordinadores y estudiante refrescan en tiempo real
    try:
        from app.sockets.emitters import emit_user_and_coordinators
        emit_user_and_coordinators(
            'permanence:scholarship_changed',
            {
                'user_id': up.user_id,
                'program_id': up.program_id,
                'has_conacyt_scholarship': new_value,
            },
            user_id=up.user_id,
            program_id=up.program_id,
        )
    except Exception:
        pass

    return {
        'has_conacyt_scholarship': new_value,
        'user_id': up.user_id,
        'program_id': up.program_id,
    }


def get_student_permanence(user_program_id: int) -> dict:
    """
    Obtiene el estado de permanencia de un estudiante para mostrarlo en su dashboard.

    Returns:
        Dict con current_enrollment, current_period, history
    """
    up = UserProgram.query.get(user_program_id)
    if not up:
        raise StudentNotFound(f"UserProgram {user_program_id} no encontrado")

    active_period = AcademicPeriod.get_active_period()

    current_enrollment = None
    if active_period:
        current_enrollment = SemesterEnrollment.query.filter_by(
            user_program_id=user_program_id,
            academic_period_id=active_period.id
        ).first()

    history = (
        SemesterEnrollment.query
        .filter_by(user_program_id=user_program_id)
        .join(AcademicPeriod, SemesterEnrollment.academic_period_id == AcademicPeriod.id)
        .order_by(SemesterEnrollment.semester_number)
        .all()
    )

    return {
        'user_program': up.to_dict(),
        'current_semester': max((se.semester_number for se in history), default=up.current_semester),
        'current_enrollment': current_enrollment.to_dict() if current_enrollment else None,
        'current_period': active_period.to_dict() if active_period else None,
        'history': [
            {
                **se.to_dict(),
                'period_name': se.academic_period.name,
                'period_code': se.academic_period.code,
            }
            for se in history
        ],
    }
