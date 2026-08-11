# app/services/programs_service.py
import logging

from sqlalchemy.orm import joinedload, selectinload
from app import db
from app.models.program import Program
from app.models.step import Step
from app.models.program_step import ProgramStep
from app.models.user_program import UserProgram
from app.utils.datetime_utils import now_local

class AlreadyEnrolledError(Exception): ...
class ProgramNotFound(Exception): ...


class AdmissionClosedError(Exception):
    """Se lanza cuando no hay un periodo de admisión activo abierto."""
    def __init__(self, next_period=None):
        self.next_period = next_period
        super().__init__("El periodo de admisión no está activo.")

def list_programs():
    return Program.query.order_by(Program.name).all()

def get_program_by_slug(slug: str):
    program = (Program.query.filter_by(slug=slug)
        .options(
            joinedload(Program.program_steps)
              .joinedload(ProgramStep.step)
              .joinedload(Step.phase),
            joinedload(Program.program_steps)
              .joinedload(ProgramStep.step)
              .selectinload(Step.archives)
        )
        .first())
    if not program:
        raise ProgramNotFound()
    return program

def get_open_admission_period():
    """
    Retorna el periodo cuya ventana de admisión está abierta hoy.
    Un periodo puede tener admisiones abiertas aunque no sea el periodo académico activo
    (p.ej. el siguiente semestre abre inscripciones mientras el actual está en clases).
    """
    from app.models.academic_period import AcademicPeriod
    today = now_local().date()
    return (
        AcademicPeriod.query
        .filter(
            AcademicPeriod.admission_start_date <= today,
            AcademicPeriod.admission_end_date >= today,
        )
        .first()
    )


def get_next_upcoming_period():
    """Retorna el próximo periodo cuya ventana de admisión aún no ha iniciado."""
    from app.models.academic_period import AcademicPeriod
    today = now_local().date()
    return (
        AcademicPeriod.query
        .filter(AcademicPeriod.admission_start_date > today)
        .order_by(AcademicPeriod.admission_start_date.asc())
        .first()
    )


def get_user_program(user_id: int):
    """The user's current UserProgram, or None. A user has at most one."""
    return UserProgram.query.filter_by(user_id=user_id).first()


def enroll_user_once(program_id: int, user_id: int):
    program = Program.query.get(program_id)
    if not program:
        raise ProgramNotFound()

    already = UserProgram.query.filter_by(user_id=user_id).first()
    if already:
        raise AlreadyEnrolledError("Ya estás inscrito en un programa.")

    # Verificar que haya un periodo con ventana de admisión abierta hoy
    open_period = get_open_admission_period()
    if not open_period:
        raise AdmissionClosedError(next_period=get_next_upcoming_period())

    db.session.add(UserProgram(
        user_id=user_id,
        program_id=program.id,
        admission_period_id=open_period.id,
    ))
    db.session.commit()
    return program


def update_program_config(program_id: int, data: dict):
    """
    Actualiza la configuración extendida de un programa.

    Args:
        program_id: ID del programa
        data: Diccionario con los campos a actualizar

    Returns:
        Program: Programa actualizado
    """
    program = Program.query.get(program_id)
    if not program:
        raise ProgramNotFound()

    # Lista de campos permitidos para actualizar
    allowed_fields = [
        # Información general
        'program_level', 'academic_area', 'image_filename', 'is_active',
        # Duración y modalidad
        'duration_semesters', 'duration_years', 'modality', 'schedule_info',
        # Información académica
        'introduction_text', 'recognition_text', 'scholarship_info', 'admission_requirements',
        # Objetivos y perfil (JSON)
        'objectives', 'graduate_profile_intro', 'graduate_competencies',
        # Líneas de investigación (JSON)
        'research_lines',
        # Mapa curricular (JSON)
        'curriculum_structure', 'show_curriculum',
        # Contacto
        'contact_email', 'contact_email_secondary', 'contact_phone', 'contact_phone_secondary',
        'contact_address', 'contact_office', 'contact_hours',
        # Configuración de visualización
        'show_hero_cards', 'show_objectives', 'show_graduate_profile',
        'show_research_lines', 'show_contact_section', 'show_contact_form',
        # SEO
        'meta_title', 'meta_description', 'meta_keywords'
    ]

    # Actualizar solo los campos permitidos que vengan en data
    for field in allowed_fields:
        if field in data:
            setattr(program, field, data[field])

    db.session.commit()
    return program


# ---------------------------------------------------------------------------
# Interés en una futura convocatoria de admisión
# ---------------------------------------------------------------------------

class AdmissionAlreadyOpenError(Exception):
    """Raised when a user registers interest while admissions are already open."""


class AlreadyInterestedError(Exception):
    """Raised when the user already asked to be notified for the same period."""


def register_admission_interest(program_id: int, user_id: int):
    """
    Record that `user_id` wants to be told when `program_id` reopens admissions.

    Returns the ProgramAdmissionInterest row. Raises AdmissionAlreadyOpenError
    when there is nothing to wait for, and AlreadyInterestedError when the same
    user already registered for the same upcoming period.
    """
    from app.models.program_admission_interest import ProgramAdmissionInterest
    from app.services.notification_service import NotificationService
    from app.services.user_history_service import UserHistoryService
    from app.utils.datetime_utils import format_date_es

    program = Program.query.get(program_id)
    if not program:
        raise ProgramNotFound()

    if get_open_admission_period() is not None:
        raise AdmissionAlreadyOpenError()

    next_period = get_next_upcoming_period()
    period_id = next_period.id if next_period else None

    existing = ProgramAdmissionInterest.query.filter_by(
        user_id=user_id,
        program_id=program_id,
        period_id=period_id,
    ).first()
    if existing:
        raise AlreadyInterestedError()

    interest = ProgramAdmissionInterest(
        user_id=user_id,
        program_id=program_id,
        period_id=period_id,
    )

    try:
        db.session.add(interest)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    if next_period:
        when = format_date_es(next_period.admission_start_date)
        message = (
            f'Te avisaremos en cuanto abran las inscripciones de '
            f'{program.name}. El proceso inicia el {when}.'
        )
    else:
        message = (
            f'Te avisaremos en cuanto se publique la próxima convocatoria de '
            f'{program.name}.'
        )

    # El interés ya está guardado. Historial y notificación son efectos
    # secundarios: si fallan, se registra el error pero no se le devuelve un
    # 500 a alguien cuyo aviso SÍ quedó activado.
    try:
        UserHistoryService.log_action(
            user_id=user_id,
            admin_id=user_id,
            action='admission_interest_registered',
            details={
                'program_id': program_id,
                'program_name': program.name,
                'period_id': period_id,
            },
        )

        NotificationService.create_notification(
            user_id=user_id,
            notification_type='admission_interest',
            title='Aviso de convocatoria activado',
            message=message,
            priority='low',
            action_url=f'/programs/{program.slug}',
            data={'program_id': program_id, 'period_id': period_id},
        )
        # Ninguno de los dos hace commit: log_action sólo add() y
        # create_notification add() + flush(). Sin esto se quedaban en la
        # sesión y se perdían al cerrar la petición.
        db.session.commit()
    except Exception:
        db.session.rollback()
        # logging estándar, no current_app: los servicios no dependen de Flask.
        logging.getLogger(__name__).exception(
            'Interés de convocatoria guardado (id=%s) pero fallaron los '
            'efectos secundarios (historial/notificación).', interest.id
        )

    return interest
