"""
Review Service — consultas y decisiones sobre entregas de documentos
(`Submission`), SIEMPRE acotadas al alcance de programas del revisor.

Regla central (ver `.claude/rules/backend-patterns.md`):
tener el permiso `admin_review.*` responde QUÉ puede hacer el revisor, nunca
SOBRE QUIÉN. Una entrega pertenece a un programa (`Submission.program_step
-> ProgramStep.program_id`); si ese programa está fuera del alcance del
revisor, la entrega **no existe** para él: los listados no la incluyen y las
consultas puntuales levantan `SubmissionNotFound` (404, no 403, para no
filtrar la existencia del expediente ajeno).

Un documento personal es información prohibida entre programas — el nivel
reducido `to_cross_program_summary` (nombre / correo / progreso) NO cubre
documentos, así que aquí no hay "solo consulta" entre programas.

Servicio agnóstico del framework: no usa `request`, `g` ni `current_user`.
El llamador pasa el objeto `User` del revisor de forma explícita.
"""

import logging

from sqlalchemy.orm import joinedload

from app import db
from app.services import public_id_service
from app.models import Program, ProgramStep, Submission, User
from app.models.archive import Archive
from app.models.phase import Phase
from app.models.step import Step
from app.models.user_program import UserProgram
from app.services import program_scope_service as scope_service
from app.services.notification_service import NotificationService
from app.services.user_history_service import UserHistoryService
from app.utils.datetime_utils import now_local

logger = logging.getLogger(__name__)


#: Fases con documentos revisables.
VALID_PHASES = ('admission', 'permanence', 'conclusion')

#: Acciones aceptadas por `decide_submission`.
VALID_ACTIONS = ('approve', 'reject')

_STATUS_BY_ACTION = {'approve': 'approved', 'reject': 'rejected'}


class SubmissionNotFound(LookupError):
    """
    La entrega no existe **o** pertenece a un programa fuera del alcance del
    revisor. Deliberadamente el mismo error para ambos casos: el revisor no
    debe poder distinguir "no existe" de "no es tuya".
    """

    def __init__(self, message='La entrega no existe o no pertenece a tus programas.'):
        super().__init__(message)
        self.message = message


class InvalidReviewAction(ValueError):
    """Acción de revisión no soportada."""

    def __init__(self, message='Acción inválida.'):
        super().__init__(message)
        self.message = message


# ─── Alcance ─────────────────────────────────────────────────────────────────

def submission_program_id(submission):
    """Programa al que pertenece la entrega (None si el paso no está cargado)."""
    if submission is None or submission.program_step is None:
        return None
    return submission.program_step.program_id


def _apply_scope(query, reviewer):
    """
    Restringe una consulta ya unida a `ProgramStep` a los programas del
    revisor.

    - alcance global (jefe de posgrado) → sin restricción.
    - alcance vacío (p. ej. servicio social sin delegación) → `IN ()`, que
      SQLAlchemy 2.0 resuelve como "ninguna fila". Falla cerrado.
    """
    scope = scope_service.accessible_program_ids(reviewer)
    if scope is None:
        return query
    return query.filter(ProgramStep.program_id.in_(scope))


# ─── Lecturas ────────────────────────────────────────────────────────────────

_LIST_OPTIONS = (
    joinedload(Submission.user),
    joinedload(Submission.program_step).joinedload(ProgramStep.program),
    joinedload(Submission.program_step).joinedload(ProgramStep.step),
    joinedload(Submission.archive),
)

_DETAIL_OPTIONS = _LIST_OPTIONS + (
    joinedload(Submission.document_deadline),
    joinedload(Submission.academic_period),
)


def list_submissions_for_review(
    reviewer,
    status='pending',
    phase=None,
    applicant_id=None,
    program_id=None,
    sort='desc',
):
    """
    Entregas visibles para `reviewer`, siempre dentro de su alcance.

    Los filtros `applicant_id` y `program_id` sólo pueden **reducir** el
    resultado: se aplican encima del filtro de alcance, nunca en su lugar.
    """
    q = (
        Submission.query
        .filter(Submission.status == status)
        .join(ProgramStep, Submission.program_step_id == ProgramStep.id)
    )

    if phase:
        q = (
            q.join(Archive, Submission.archive_id == Archive.id)
             .join(Step, Archive.step_id == Step.id)
             .join(Phase, Step.phase_id == Phase.id)
             .filter(Phase.name == phase)
        )

    q = _apply_scope(q, reviewer)

    if applicant_id:
        q = q.filter(Submission.user_id == applicant_id)
    if program_id:
        q = q.filter(ProgramStep.program_id == program_id)

    q = q.options(*_LIST_OPTIONS)
    q = q.order_by(
        Submission.upload_date.asc() if sort == 'asc'
        else Submission.upload_date.desc()
    )
    return q.all()


def get_submission_for_review(reviewer, submission_id, detailed=False):
    """
    Una entrega concreta, sólo si está dentro del alcance del revisor.

    Raises:
        SubmissionNotFound: si no existe o pertenece a otro programa.
    """
    options = _DETAIL_OPTIONS if detailed else _LIST_OPTIONS
    submission = (
        Submission.query
        .options(*options)
        .filter(Submission.id == submission_id)
        .one_or_none()
    )
    if submission is None:
        raise SubmissionNotFound()

    if not scope_service.program_in_scope(reviewer, submission_program_id(submission)):
        raise SubmissionNotFound()

    return submission


def submission_version_history(reviewer, submission):
    """
    Versiones anteriores del MISMO archive del MISMO usuario, dentro del
    alcance del revisor.

    El alcance importa aquí: un usuario puede estar inscrito en dos programas
    y haber entregado el mismo archive en ambos. La plantilla del detalle
    publica la ruta del archivo de cada versión, así que las versiones de un
    programa ajeno no deben aparecer.
    """
    if submission is None:
        return []
    q = (
        Submission.query
        .join(ProgramStep, Submission.program_step_id == ProgramStep.id)
        .filter(
            Submission.user_id == submission.user_id,
            Submission.archive_id == submission.archive_id,
            Submission.id != submission.id,
        )
    )
    q = _apply_scope(q, reviewer)
    return q.order_by(Submission.upload_date.desc()).all()


def user_program_of_submission(submission):
    """`UserProgram` del autor de la entrega en el programa de la entrega."""
    program_id = submission_program_id(submission)
    if submission is None or program_id is None:
        return None
    return (
        UserProgram.query
        .filter_by(user_id=submission.user_id, program_id=program_id)
        .first()
    )


def pending_counts_by_phase(reviewer, program_id=None):
    """Pendientes por fase dentro del alcance (para los badges de las tabs)."""
    q = (
        db.session.query(Phase.name, db.func.count(Submission.id))
        .select_from(Submission)
        .join(ProgramStep, Submission.program_step_id == ProgramStep.id)
        .join(Archive, Submission.archive_id == Archive.id)
        .join(Step, Archive.step_id == Step.id)
        .join(Phase, Step.phase_id == Phase.id)
        .filter(Submission.status == 'pending')
    )
    q = _apply_scope(q, reviewer)
    if program_id:
        q = q.filter(ProgramStep.program_id == program_id)

    counts = {phase: 0 for phase in VALID_PHASES}
    for phase_name, total in q.group_by(Phase.name).all():
        if phase_name in counts:
            counts[phase_name] = total
    return counts


def reviewable_users(reviewer, phase):
    """
    Aspirantes (fase admisión) o estudiantes (permanencia / conclusión) del
    alcance del revisor, para el desplegable de filtro.
    """
    role_name = 'applicant' if phase == 'admission' else 'student'
    q = User.query.filter(User.role.has(name=role_name))

    scope = scope_service.accessible_program_ids(reviewer)
    if scope is None:
        q = q.filter(User.user_program.any())
    else:
        q = (
            q.join(UserProgram, User.id == UserProgram.user_id)
             .filter(UserProgram.program_id.in_(scope))
             .distinct()
        )
    return q.order_by(User.first_name).all()


def reviewable_programs(reviewer):
    """Programas del alcance del revisor, para el desplegable de filtro."""
    q = Program.query
    scope = scope_service.accessible_program_ids(reviewer)
    if scope is not None:
        q = q.filter(Program.id.in_(scope))
    return q.order_by(Program.name).all()


# ─── Escritura ───────────────────────────────────────────────────────────────

def decide_submission(reviewer, submission_id, action, comment=''):
    """
    Aprueba o rechaza una entrega. Verifica el alcance ANTES de escribir.

    Args:
        reviewer: `User` que dictamina (el llamador lo extrae de la sesión).
        submission_id: id de la entrega.
        action: 'approve' | 'reject'.
        comment: comentario para el aspirante.

    Returns:
        La `Submission` actualizada.

    Raises:
        InvalidReviewAction: acción no soportada.
        SubmissionNotFound: no existe o está fuera del alcance del revisor.
    """
    action = (action or '').strip().lower()
    if action not in VALID_ACTIONS:
        raise InvalidReviewAction()

    submission = get_submission_for_review(reviewer, submission_id)
    comment = (comment or '').strip()

    submission.status = _STATUS_BY_ACTION[action]
    submission.reviewer_id = reviewer.id
    submission.review_date = now_local()
    submission.reviewer_comment = comment
    db.session.commit()

    archive_name = (
        submission.archive.name if submission.archive
        else f'Documento ID {submission.archive_id}'
    )

    # Historial del revisor
    try:
        UserHistoryService.log_document_review(
            user_id=submission.user_id,
            archive_name=archive_name,
            status=submission.status,
            reviewer_comment=comment,
            admin_id=reviewer.id,
        )
        db.session.commit()
    except Exception as exc:
        logger.error('Error al registrar revisión de documento en historial: %s', exc)

    # Notificación al aspirante (incluye correo automático)
    try:
        program = submission.program_step.program if submission.program_step else None
        program_slug = program.slug if program else None
        if action == 'approve':
            NotificationService.notify_document_approved(
                submission.user_id, archive_name, submission.id,
                program_slug=program_slug,
            )
        else:
            NotificationService.notify_document_rejected(
                submission.user_id, archive_name, submission.id, comment,
                program_slug=program_slug,
            )
        db.session.commit()
    except Exception as exc:
        logger.error('Error al enviar notificación de revisión: %s', exc)

    # WebSocket: dashboard del aspirante + coordinadores del programa
    from app.sockets.emitters import emit_user_and_coordinators

    program_id = submission_program_id(submission)
    emit_user_and_coordinators(
        'submission:reviewed',
        {
            'user_id': public_id_service.user_uuid(submission.user_id),
            'submission_id': public_id_service.submission_uuid(submission.id),
            'archive_id': public_id_service.archive_uuid(submission.archive_id),
            'program_id': program_id,
            'status': submission.status,
        },
        user_id=submission.user_id,
        program_id=program_id,
    )

    return submission
