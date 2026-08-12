# app/services/student_bulk_service.py
"""
Servicio para el alta masiva de estudiantes existentes en SIIAP.

Caso de uso: estudiantes que ya cursan el programa pero nunca pasaron por
el flujo de admisión del sistema (generaciones previas al sistema).

Flujo principal:
  1. Validar payload (email único, control_number único, programa activo,
     periodo de admisión existente, suficientes periodos para backfill).
  2. Crear User con contraseña aleatoria + must_change_password=True.
  3. Crear UserProgram con admission_status='enrolled'.
  4. Generar N SemesterEnrollment sintéticos (1..N-1 completed, N active).
  5. Enviar email de bienvenida con link de set-password.
  6. Registrar en historial.

Para CSV: validar cada fila individualmente + detectar duplicados intra-CSV,
luego ejecutar filas válidas atómicamente por fila (errores aislados).

ALCANCE DE PROGRAMA — `creator_program_ids`
-------------------------------------------
Todas las entradas públicas de este módulo reciben `creator_program_ids`: el
conjunto de programas sobre los que el usuario que ejecuta el alta puede
operar. Sigue el contrato de `program_scope_service.accessible_program_ids`:

    None      → TODOS los programas (jefe de posgrado)
    set()     → NINGUNO (p. ej. servicio social sin delegación)
    {1, 4}    → sólo esos

Nunca confundas `None` con `set()`. Sin este hilo, un coordinador podía
fabricar estudiantes con número de control, estatus `enrolled` y semestres
confirmados hacia atrás DENTRO DEL PROGRAMA DE OTRO coordinador, masivamente
por CSV: quedaba auditado pero no impedido.

La comprobación vive en `validate_individual`, que es por donde pasan las tres
rutas (individual, preview CSV y ejecución CSV), de modo que unas filas
manipuladas con `valid: true` en el cuerpo de `/csv/execute` tampoco la evitan.

UNICIDAD DEL NÚMERO DE CONTROL — ORDEN Y MENSAJE
------------------------------------------------
El número de control es único en TODA la institución, así que comprobarlo es
forzosamente una consulta global. Eso lo convertía en un oráculo de existencia,
y aquí era el peor de todos: `/validate` y `/csv/preview` son ensayos en seco
que devuelven los errores tal cual, y un solo CSV sondea N números de golpe.
Además la consulta corría ANTES del alcance de programa, así que contestaba
incluso en la fila que se iba a rechazar por apuntar al programa de otro.

Dos reglas lo cierran, y ninguna toca la restricción de unicidad (que es
correcta y debe quedarse):

  1. ORDEN — el alcance de programa se resuelve primero. Una fila fuera de
     alcance se rechaza sin que la consulta global llegue a ejecutarse.
  2. MENSAJE — una colisión responde con
     `acceptance_service.CONTROL_NUMBER_REJECTED_MESSAGE`, literalmente el mismo
     texto que emiten `acceptance_service.assign_control_number`,
     `acceptance_service.assign_control_number_admin` y
     `admin/users_api.assign_control_number`. No nombra el número ni afirma que
     exista. Cuatro sitios, una sola redacción: dos textos distintos volverían a
     distinguir la colisión del resto de los rechazos.

Queda un residuo inevitable —una fila con número libre valida y una con número
tomado no—, idéntico al de los otros tres sitios y propio de cualquier columna
única escrita con un valor que elige quien llama. Lo que se elimina es la
confirmación explícita y la cosecha pasiva; el sondeo que resta queda escrito en
el log de la aplicación con el id de quien lo hace (`actor_id`), que es la
señal de volumen sobre la que se puede actuar.
"""

import csv
import io
import logging

from app import db
from app.services import public_id_service
from app.models import (
    User, UserProgram, Program, AcademicPeriod, Role
)
from app.models.semester_enrollment import SemesterEnrollment
from app.services.notification_service import NotificationService
from app.services.user_history_service import UserHistoryService
from app.utils.datetime_utils import now_local
from app.utils.validators import (
    EMAIL_MAX_LENGTH,
    InputValidationError,
    validate_person_name,
    validate_short_text,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Excepciones del dominio
# ---------------------------------------------------------------------------

class StudentBulkError(Exception):
    """Error base para operaciones de alta masiva."""


class ValidationError(StudentBulkError):
    """Payload inválido (reglas de negocio)."""


class StudentCreationError(StudentBulkError):
    """Error al crear el estudiante en la base de datos."""


class ProgramScopeError(StudentBulkError):
    """
    El programa destino del alta está fuera del alcance de quien la ejecuta.

    Se separa de `ValidationError` porque no es un payload mal formado sino una
    denegación: la ruta la traduce a 403, no a 400.
    """


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

CSV_HEADERS = [
    'first_name', 'last_name', 'mother_last_name', 'email',
    'control_number', 'program_slug', 'current_semester',
    'admission_period_code', 'has_conacyt',
]

# Columnas opcionales del CSV. Si están presentes y no vacías se aplican al
# perfil del estudiante. Si faltan o vienen vacías se ignoran (no falla la
# validación). Cualquier columna extra fuera de esta lista se descarta.
CSV_OPTIONAL_HEADERS = [
    'phone', 'mobile_phone', 'address',
    'curp', 'rfc', 'nss', 'cedula_profesional',
    'birth_date',          # 'YYYY-MM-DD'
    'birth_place',
    'emergency_contact_name', 'emergency_contact_phone',
    'emergency_contact_relationship',
]

CSV_EXAMPLE_ROW = [
    'Juan', 'García', 'López', 'jgarcia@ejemplo.com',
    'M21111001', 'maestria-ingenieria', '3',
    '20221', 'no',
]

# Spanish labels used in the per-row validation messages of the optional
# profile columns, plus the length each one is bounded to. Every limit mirrors
# its `user` column except `address`, a TEXT column bounded here for sanity.
CSV_OPTIONAL_FIELD_RULES = {
    'phone':                          ('Teléfono', 20),
    'mobile_phone':                   ('Teléfono móvil', 20),
    'address':                        ('Dirección', 500),
    'curp':                           ('CURP', 18),
    'rfc':                            ('RFC', 13),
    'nss':                            ('NSS', 15),
    'cedula_profesional':             ('Cédula profesional', 20),
    'birth_place':                    ('Lugar de nacimiento', 200),
    'emergency_contact_name':         ('Nombre del contacto de emergencia', 200),
    'emergency_contact_phone':        ('Teléfono del contacto de emergencia', 20),
    'emergency_contact_relationship': ('Parentesco del contacto de emergencia', 50),
}

# Mirrors user.control_number -> String(20).
CONTROL_NUMBER_MAX_LENGTH = 20


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _random_password(length: int = 24) -> str:
    """Genera una contraseña aleatoria segura no usable directamente."""
    from app.services.password_reset_service import random_password
    return random_password(length)


def _get_all_periods_ordered() -> list:
    """Devuelve todos los AcademicPeriod ordenados por id ascendente (orden cronológico)."""
    return AcademicPeriod.query.order_by(AcademicPeriod.id.asc()).all()


def _get_active_period() -> AcademicPeriod | None:
    """Devuelve el periodo activo, o None si no existe."""
    return AcademicPeriod.query.filter_by(is_active=True).first()


# `_build_login_url()` used to live here. It built the URL from the live request
# (`url_for(_external=True)`, falling back to `request.host_url`), i.e. from a
# host the requester chooses, and it had no callers at all. Removed rather than
# left as a ready-made hole for the next caller to step in. Anything needing an
# absolute login URL must use `app.utils.urls.external_url('pages_auth.login_page')`.


def _apply_validator(validator, value, errors: list, **kwargs):
    """
    Run one validator from app/utils/validators.py folding its rejection into
    the per-row `errors` list instead of letting it raise.

    Bulk import reports every problem of a row at once and keeps processing the
    remaining rows, so a rejected value must never abort the pass.

    Returns the normalized value, or None when the value was rejected.
    """
    try:
        return validator(value, **kwargs)
    except InputValidationError as exc:
        errors.append(exc.message)
        return None


def _program_in_creator_scope(program, creator_program_ids) -> bool:
    """
    True si el usuario que ejecuta el alta puede operar sobre ese programa.

    `creator_program_ids is None` significa alcance global (jefe de posgrado);
    un conjunto vacío significa SIN alcance y niega todo. Ver la nota de
    alcance en el encabezado del módulo.
    """
    if creator_program_ids is None:
        return True
    if program is None:
        return False
    return program.id in creator_program_ids


def _resolve_program_for_scope(program_slug: str):
    """Programa por slug (sin validar nada más), o None si no existe."""
    if not program_slug:
        return None
    return Program.query.filter_by(slug=program_slug).first()


def _require_program_scope(payload: dict, creator_program_ids) -> None:
    """
    Denegación temprana y explícita para las altas individuales.

    `validate_individual` también registra el problema como error de fila (lo
    necesita el preview del CSV), pero la ruta individual debe responder 403 y
    no 400, así que aquí se lanza `ProgramScopeError` antes de validar nada más.

    Un slug inexistente NO se denega aquí: eso es un error de datos y lo
    reporta la validación normal con su mensaje ("No existe un programa…").
    """
    program = _resolve_program_for_scope((payload.get('program_slug') or '').strip())
    if program is not None and not _program_in_creator_scope(program, creator_program_ids):
        raise ProgramScopeError(
            f'No tienes acceso al programa {program.slug!r}. Sólo puedes dar de '
            f'alta estudiantes en los programas que administras.'
        )


def _build_reset_password_url(token: str) -> str:
    """
    Construye la URL absoluta de la página de configuración de contraseña
    con el token incrustado.

    Única implementación en `password_reset_service.build_reset_password_url`,
    compartida con el alta de servicio social y el reset administrativo: la
    regla de anclar la URL a APP_BASE_URL y nunca a la petición debe valer
    igual para todos los correos que llevan un token.
    """
    from app.services.password_reset_service import build_reset_password_url
    return build_reset_password_url(token)


# ---------------------------------------------------------------------------
# Validación individual
# ---------------------------------------------------------------------------

def validate_individual(payload: dict, creator_program_ids=None,
                        actor_id=None) -> dict:
    """
    Valida un payload de alta individual sin crear registros en DB.

    Args:
        payload: dict con campos mínimos del estudiante.
        creator_program_ids: alcance de programas de quien ejecuta el alta.
            **None significa TODOS** (jefe de posgrado); `set()` significa
            NINGUNO. Ver la nota de alcance del encabezado del módulo.
        actor_id: id de quien ejecuta la validación. Sólo se usa para atribuir
            en el log una colisión de número de control; el servicio sigue sin
            leer `current_user` (la ruta lo pasa explícitamente).

    Returns:
        {'valid': bool, 'errors': list[str], 'normalized': dict}
    """
    errors = []
    normalized = {}

    # Person names and bounded free text go through the shared validators
    # (app/utils/validators.py) — the same rules as self-registration, because
    # these columns end up in the staff consoles either way. A rejection is
    # recorded as a row error and the row is simply skipped at execution time.

    # --- first_name ---
    normalized['first_name'] = _apply_validator(
        validate_person_name, payload.get('first_name'), errors, label='Nombre'
    )

    # --- last_name ---
    normalized['last_name'] = _apply_validator(
        validate_person_name, payload.get('last_name'), errors,
        label='Apellido paterno',
    )

    # --- mother_last_name ---
    normalized['mother_last_name'] = _apply_validator(
        validate_person_name, payload.get('mother_last_name'), errors,
        label='Apellido materno', required=False,
    )

    # --- email ---
    email = _apply_validator(
        validate_short_text, payload.get('email'), errors,
        label='Correo electrónico', required=True, max_length=EMAIL_MAX_LENGTH,
    )
    email = email.lower() if email else ''
    if email and User.query.filter_by(email=email).first():
        errors.append(f'El correo {email!r} ya está registrado en el sistema.')
    normalized['email'] = email

    # --- program_slug ---
    # Se resuelve ANTES del número de control a propósito. La unicidad del
    # número de control es institucional y por tanto se consulta contra toda la
    # base; esa consulta no debe llegar a ejecutarse para una fila que apunta al
    # programa de otro. Ver "UNICIDAD DEL NÚMERO DE CONTROL" en el encabezado.
    program_slug = (payload.get('program_slug') or '').strip()
    program = None
    program_in_scope = False
    if not program_slug:
        errors.append('El slug del programa es requerido.')
    else:
        program = Program.query.filter_by(slug=program_slug).first()
        if not program:
            errors.append(f'No existe un programa con slug {program_slug!r}.')
        elif not _program_in_creator_scope(program, creator_program_ids):
            # El alta masiva escribe número de control, estatus 'enrolled' y
            # semestres confirmados: sólo en los programas propios.
            errors.append(
                f'No tienes acceso al programa {program_slug!r}. Sólo puedes '
                f'dar de alta estudiantes en los programas que administras.'
            )
            program = None   # no sigas derivando límites de un programa ajeno
        else:
            program_in_scope = True
            if not program.is_active:
                errors.append(f'El programa {program_slug!r} no está activo.')
    normalized['program_slug'] = program_slug

    # --- control_number ---
    # La consulta de unicidad se ejecuta SÓLO si la fila ya pasó el alcance.
    # Antes corría siempre y respondía con el motivo exacto, así que un
    # coordinador podía pedir `/validate` —o un CSV entero contra
    # `/csv/preview`— con números de control de programas ajenos y leer en la
    # respuesta cuáles existen, sin escribir nada y sin dejar rastro.
    #
    # El texto del rechazo no vive aquí: es el mismo objeto que usan los otros
    # tres puntos que rechazan por colisión. Si esta cadena se reescribiera
    # "para que diga algo más útil", el oráculo se reabre.
    control_number = _apply_validator(
        validate_short_text, payload.get('control_number'), errors,
        label='Número de control', required=True,
        max_length=CONTROL_NUMBER_MAX_LENGTH,
    ) or ''
    if control_number and program_in_scope:
        if User.query.filter_by(control_number=control_number).first():
            from app.services.acceptance_service import CONTROL_NUMBER_REJECTED_MESSAGE
            logger.warning(
                '[control_number] Colisión en alta masiva de estudiantes '
                f'(actor_id={actor_id}, program_slug={program_slug!r}, '
                f'control_number={control_number})'
            )
            errors.append(CONTROL_NUMBER_REJECTED_MESSAGE)
    normalized['control_number'] = control_number

    # --- current_semester ---
    raw_semester = payload.get('current_semester')
    current_semester = None
    try:
        current_semester = int(str(raw_semester).strip())
    except (ValueError, TypeError):
        errors.append('El semestre actual debe ser un número entero.')

    if current_semester is not None:
        max_sem = (program.duration_semesters * 2) if (program and program.duration_semesters) else 20
        if current_semester < 1:
            errors.append('El semestre actual debe ser al menos 1.')
        elif current_semester > max_sem:
            errors.append(
                f'El semestre actual ({current_semester}) excede el máximo permitido ({max_sem}) '
                f'para el programa.'
            )
    normalized['current_semester'] = current_semester

    # --- admission_period_code ---
    admission_period_code = (payload.get('admission_period_code') or '').strip()
    admission_period = None
    if not admission_period_code:
        errors.append('El código de periodo de admisión es requerido.')
    else:
        admission_period = AcademicPeriod.query.filter_by(code=admission_period_code).first()
        if not admission_period:
            errors.append(f'No existe un periodo académico con código {admission_period_code!r}.')
    normalized['admission_period_code'] = admission_period_code

    # --- has_conacyt ---
    raw_conacyt = payload.get('has_conacyt')
    if isinstance(raw_conacyt, bool):
        has_conacyt = raw_conacyt
    elif isinstance(raw_conacyt, str):
        has_conacyt = raw_conacyt.strip().lower() in ('1', 'true', 'yes', 'si', 'sí')
    else:
        has_conacyt = bool(raw_conacyt)
    normalized['has_conacyt'] = has_conacyt

    # --- Campos opcionales del perfil ---
    # Un campo opcional vacío se ignora silenciosamente; uno con contenido pasa
    # por el validador correspondiente (charset y longitud). Sólo birth_date se
    # valida aparte porque debe parsearse a date.
    for field, (label, max_length) in CSV_OPTIONAL_FIELD_RULES.items():
        raw = payload.get(field)
        if raw is None:
            normalized[field] = None
            continue
        # Every optional column, including emergency_contact_name, goes through
        # the permissive validator. That field is filled in freely by whoever
        # prepares the CSV — "Juan Perez (madre)", "Maria, mama" — so the strict
        # person-name charset would reject ordinary rows. It is bounded and
        # markup-free, which is what the render sites need.
        normalized[field] = _apply_validator(
            validate_short_text, str(raw), errors,
            label=label, required=False, max_length=max_length,
        )

    raw_birth = payload.get('birth_date')
    if raw_birth is None or (isinstance(raw_birth, str) and not raw_birth.strip()):
        normalized['birth_date'] = None
    else:
        from datetime import date as _date
        try:
            if isinstance(raw_birth, _date):
                normalized['birth_date'] = raw_birth
            else:
                # Acepta 'YYYY-MM-DD' o 'YYYY-MM-DD HH:MM[:SS]'
                date_str = str(raw_birth).strip().split(' ')[0]
                y, m, d = date_str.split('-')
                normalized['birth_date'] = _date(int(y), int(m), int(d))
        except (ValueError, AttributeError):
            errors.append(
                f'birth_date inválido ({raw_birth!r}). Formato esperado: YYYY-MM-DD.'
            )
            normalized['birth_date'] = None

    # --- Validaciones cronológicas (solo si no hubo errores previos críticos) ---
    if admission_period and current_semester and current_semester >= 1:
        all_periods = _get_all_periods_ordered()
        period_ids = [p.id for p in all_periods]

        if admission_period.id not in period_ids:
            errors.append('El periodo de admisión no se encontró en la lista cronológica de periodos.')
        else:
            admission_idx = period_ids.index(admission_period.id)
            active_period = _get_active_period()

            # El periodo de admisión debe ser <= el periodo activo cronológicamente
            if active_period:
                active_idx = period_ids.index(active_period.id) if active_period.id in period_ids else -1
                if admission_idx > active_idx:
                    errors.append(
                        f'El periodo de admisión ({admission_period_code}) es posterior al '
                        f'periodo activo actual. El estudiante no puede haber ingresado en un '
                        f'periodo futuro.'
                    )

            # Verificar que haya suficientes periodos cronológicos para backfill
            # Se necesitan current_semester periodos: admission_idx .. admission_idx + N - 1
            needed_end_idx = admission_idx + current_semester - 1
            if needed_end_idx >= len(all_periods):
                available = len(all_periods) - admission_idx
                errors.append(
                    f'No hay suficientes periodos académicos para el backfill. '
                    f'Se necesitan {current_semester} periodos desde {admission_period_code}, '
                    f'pero solo hay {available} periodos disponibles a partir de ese punto.'
                )

    return {
        'valid': len(errors) == 0,
        'errors': errors,
        'normalized': normalized,
    }


# ---------------------------------------------------------------------------
# Backfill de historial semestral
# ---------------------------------------------------------------------------

def _backfill_history(
    up: UserProgram,
    admission_period_id: int,
    current_semester: int,
    created_by_id: int,
) -> int:
    """
    Crea N SemesterEnrollment sintéticos para los semestres 1..current_semester.

    Lógica de periodos:
      - Obtiene todos los AcademicPeriod ordenados por id asc (cronológico).
      - Localiza el periodo de admisión por id.
      - Para semestre k (1-based): usa el periodo en index (admission_idx + k - 1).
      - Semestres 1..N-1 → status='completed', enrollment_confirmed=True.
      - Semestre N → status='active', enrollment_confirmed=True.

    Args:
        up: UserProgram ya persistido (con id).
        admission_period_id: ID del AcademicPeriod de admisión.
        current_semester: Número del semestre actual (1-based).
        created_by_id: ID del usuario que realiza la operación (confirmed_by).

    Returns:
        Número de SemesterEnrollment creados.
    """
    all_periods = _get_all_periods_ordered()
    period_ids = [p.id for p in all_periods]
    admission_idx = period_ids.index(admission_period_id)

    now = now_local()
    created_count = 0

    for sem_num in range(1, current_semester + 1):
        period_idx = admission_idx + sem_num - 1
        period = all_periods[period_idx]
        is_last = (sem_num == current_semester)

        se = SemesterEnrollment(
            user_program_id=up.id,
            academic_period_id=period.id,
            semester_number=sem_num,
            status='active' if is_last else 'completed',
            enrollment_confirmed=True,
            confirmed_by=created_by_id,
            confirmed_at=now,
        )
        db.session.add(se)
        created_count += 1

    # Sincronizar current_semester en UserProgram
    up.current_semester = current_semester

    return created_count


# ---------------------------------------------------------------------------
# Creación individual
# ---------------------------------------------------------------------------

def create_student_individual(payload: dict, created_by_id: int,
                              creator_program_ids=None) -> dict:
    """
    Crea un estudiante completo (User + UserProgram + SemesterEnrollments)
    en una única transacción de base de datos.

    Pasos:
      1. Validar payload.
      2. Crear User con contraseña aleatoria e inutilizable.
      3. Crear UserProgram con admission_status='enrolled'.
      4. Crear N SemesterEnrollments (backfill).
      5. Generar token de bienvenida y encolar email.
      6. Registrar en historial.
      7. Commit.

    Args:
        payload: dict con campos del estudiante (ver validate_individual).
        created_by_id: ID del usuario que realiza la operación.
        creator_program_ids: alcance de programas de quien ejecuta el alta.
            **None significa TODOS** (jefe de posgrado); `set()` significa
            NINGUNO. Ver la nota de alcance del encabezado del módulo.

    Returns:
        dict: {user_id, user_program_id, sems_created, email}

    Raises:
        ProgramScopeError: Si el programa destino está fuera del alcance.
        ValidationError: Si el payload no es válido.
        StudentCreationError: Si hay un error de base de datos.
    """
    # 0. Alcance de programa. Se comprueba aquí, en el servicio, para que valga
    #    también cuando las filas llegan desde /csv/execute con 'valid' puesto
    #    a mano en el cuerpo de la petición.
    _require_program_scope(payload, creator_program_ids)

    # 1. Validar
    result = validate_individual(
        payload,
        creator_program_ids=creator_program_ids,
        actor_id=created_by_id,
    )
    if not result['valid']:
        raise ValidationError('; '.join(result['errors']))

    normalized = result['normalized']

    try:
        # Resolver objetos de DB
        program = Program.query.filter_by(slug=normalized['program_slug']).first()
        admission_period = AcademicPeriod.query.filter_by(
            code=normalized['admission_period_code']
        ).first()
        student_role = Role.query.filter_by(name='student').first()
        if not student_role:
            raise StudentCreationError('No se encontró el rol "student" en el sistema.')

        # 2. Crear User
        random_pw = _random_password()
        user = User(
            first_name=normalized['first_name'],
            last_name=normalized['last_name'],
            mother_last_name=normalized['mother_last_name'],
            username=normalized['control_number'],   # username = control_number
            password=random_pw,
            email=normalized['email'],
            is_internal=False,
            role_id=student_role.id,
            must_change_password=True,
        )
        user.control_number = normalized['control_number']
        user.control_number_assigned_at = now_local()
        user.is_active = True
        user.profile_completed = False
        user.registration_date = now_local()

        # Aplicar campos opcionales del perfil si vinieron en el payload
        for field in ('phone', 'mobile_phone', 'address', 'curp', 'rfc', 'nss',
                      'cedula_profesional', 'birth_date', 'birth_place',
                      'emergency_contact_name', 'emergency_contact_phone',
                      'emergency_contact_relationship'):
            value = normalized.get(field)
            if value is not None:
                setattr(user, field, value)

        # Si vino suficiente info, marcar perfil como completo
        user.update_profile_completion_status()

        db.session.add(user)
        db.session.flush()  # obtener user.id

        # 3. Crear UserProgram
        up = UserProgram(
            user_id=user.id,
            program_id=program.id,
            admission_status='enrolled',
            admission_period_id=admission_period.id,
            current_semester=normalized['current_semester'],
            has_conacyt_scholarship=normalized['has_conacyt'],
            enrollment_date=now_local(),
        )
        db.session.add(up)
        db.session.flush()  # obtener up.id

        # 4. Backfill semestral
        sems_created = _backfill_history(
            up=up,
            admission_period_id=admission_period.id,
            current_semester=normalized['current_semester'],
            created_by_id=created_by_id,
        )

        # 5. Generar token de set-password (TTL 7 días) y encolar email
        from app.services import password_reset_service as prs

        prt = prs.generate_token(
            user_id=user.id,
            purpose='set_password',
            ttl_days=7,
            created_by_id=created_by_id,
        )
        token_link = _build_reset_password_url(prt.token)

        email_sent = False
        try:
            from app.services.email_templates import EmailTemplates
            from app.services.email_service import EmailService

            full_name = f'{user.first_name} {user.last_name}'.strip()
            subject, html = EmailTemplates.student_welcome_set_password(
                user_name=full_name,
                control_number=normalized['control_number'],
                program_name=program.name,
                token_link=token_link,
                expires_at=prt.expires_at,
            )
            EmailService.queue_email(
                user_id=user.id,
                subject=subject,
                html_content=html,
            )
            email_sent = True
        except Exception as email_exc:
            logger.warning(
                f'[student_bulk] No se pudo encolar email de bienvenida para '
                f'usuario {user.id}: {email_exc}'
            )

        # 6. Historial
        UserHistoryService.log_action(
            user_id=user.id,
            admin_id=created_by_id,
            action='enrolled_via_bulk_import',
            details={
                'program': program.name,
                'program_slug': program.slug,
                'control_number': normalized['control_number'],
                'admission_period_code': normalized['admission_period_code'],
                'current_semester': normalized['current_semester'],
                'sems_created': sems_created,
                'has_conacyt': normalized['has_conacyt'],
                'set_password_token_sent': email_sent,
            },
        )

        # 7. Commit
        db.session.commit()

        return {
            # Public handle: the bulk console echoes this back in its report.
            'user_id': public_id_service.user_uuid(user.id),
            'user_program_id': up.id,
            'sems_created': sems_created,
            'email': user.email,
            'email_sent': email_sent,
            'token_link': token_link if not email_sent else None,
        }

    except (ValidationError, StudentCreationError):
        db.session.rollback()
        raise
    except Exception as exc:
        db.session.rollback()
        raise StudentCreationError(f'Error al crear el estudiante: {exc}') from exc


# ---------------------------------------------------------------------------
# Validación CSV
# ---------------------------------------------------------------------------

def validate_csv(csv_text: str, creator_program_ids=None, actor_id=None) -> dict:
    """
    Parsea y valida un CSV de alta masiva de estudiantes.

    Formato esperado (primera fila = headers):
        first_name, last_name, mother_last_name, email, control_number,
        program_slug, current_semester, admission_period_code, has_conacyt

    Valida cada fila con validate_individual y además detecta duplicados
    intra-CSV (emails y control_numbers repetidos dentro del propio archivo).

    Una fila cuyo `program_slug` cae fuera del alcance del usuario se marca
    inválida con su motivo, igual que cualquier otro error de fila: el resto
    del archivo se sigue validando.

    Args:
        csv_text: Contenido del archivo CSV como string UTF-8.
        creator_program_ids: alcance de programas de quien ejecuta el alta.
            **None significa TODOS**; `set()` significa NINGUNO.
        actor_id: id de quien ejecuta la validación, sólo para atribuir en el
            log una colisión de número de control (ver `validate_individual`).
            Un CSV sondea N números por petición: sin este dato el log no
            distingue quién lo mandó.

    Returns:
        {
          'rows': [{'index': int, 'data': dict, 'valid': bool, 'errors': list}],
          'summary': {'total': int, 'valid': int, 'invalid': int}
        }
    """
    rows = []
    reader = csv.DictReader(io.StringIO(csv_text.strip()))

    # Verificar headers
    if not reader.fieldnames:
        return {
            'rows': [],
            'summary': {'total': 0, 'valid': 0, 'invalid': 0},
            'error': 'El archivo CSV está vacío o no tiene encabezados.',
        }

    missing_headers = [h for h in CSV_HEADERS if h not in reader.fieldnames]
    if missing_headers:
        return {
            'rows': [],
            'summary': {'total': 0, 'valid': 0, 'invalid': 0},
            'error': f'Encabezados faltantes en el CSV: {", ".join(missing_headers)}',
        }

    raw_rows = list(reader)

    # Recopilar emails y control_numbers para detección de duplicados intra-CSV
    intra_emails: dict[str, list[int]] = {}
    intra_controls: dict[str, list[int]] = {}

    for i, row in enumerate(raw_rows, start=1):
        email = (row.get('email') or '').strip().lower()
        ctrl = (row.get('control_number') or '').strip()
        if email:
            intra_emails.setdefault(email, []).append(i)
        if ctrl:
            intra_controls.setdefault(ctrl, []).append(i)

    duplicate_emails = {e for e, indices in intra_emails.items() if len(indices) > 1}
    duplicate_controls = {c for c, indices in intra_controls.items() if len(indices) > 1}

    # Validar cada fila — pasar también campos opcionales si están en el CSV
    available_optional = [h for h in CSV_OPTIONAL_HEADERS if h in (reader.fieldnames or [])]
    for i, row in enumerate(raw_rows, start=1):
        payload = {k: (row.get(k) or '').strip() for k in CSV_HEADERS}
        for k in available_optional:
            payload[k] = (row.get(k) or '').strip()
        result = validate_individual(
            payload,
            creator_program_ids=creator_program_ids,
            actor_id=actor_id,
        )

        extra_errors = []
        email = payload.get('email', '').lower()
        ctrl = payload.get('control_number', '')

        if email in duplicate_emails:
            extra_errors.append(
                f'El correo {email!r} aparece duplicado en el archivo CSV.'
            )
        if ctrl in duplicate_controls:
            extra_errors.append(
                f'El número de control {ctrl!r} aparece duplicado en el archivo CSV.'
            )

        all_errors = result['errors'] + extra_errors
        valid = result['valid'] and len(extra_errors) == 0

        rows.append({
            'index': i,
            'data': result['normalized'],
            'valid': valid,
            'errors': all_errors,
        })

    total = len(rows)
    valid_count = sum(1 for r in rows if r['valid'])

    return {
        'rows': rows,
        'summary': {
            'total': total,
            'valid': valid_count,
            'invalid': total - valid_count,
        },
    }


# ---------------------------------------------------------------------------
# Ejecución CSV
# ---------------------------------------------------------------------------

def execute_csv(rows: list, created_by_id: int, creator_program_ids=None) -> dict:
    """
    Aplica las filas válidas de un preview CSV.

    Cada estudiante se crea en su propia transacción atómica. Si una fila
    falla, las demás continúan procesándose (fallas aisladas).

    `rows` llega del cliente, así que su bandera `valid` NO es de fiar: quien
    llama puede ponerla a True sobre una fila que apunta al programa de otro
    coordinador. Por eso el alcance se revalida dentro de
    `create_student_individual`, y la fila rechazada aparece en `failed`.

    Args:
        rows: Lista de dicts con estructura {index, data, valid, errors}.
              Solo se procesan las filas con valid=True.
        created_by_id: ID del usuario que ejecuta la operación.
        creator_program_ids: alcance de programas de quien ejecuta el alta.
            **None significa TODOS**; `set()` significa NINGUNO.

    Returns:
        {
          'created': int,
          'failed': [{'index': int, 'email': str, 'error': str}],
          'created_users': [{'user_id': int, 'email': str, 'control_number': str}]
        }
    """
    created = 0
    failed = []
    created_users = []

    valid_rows = [r for r in rows if r.get('valid')]

    for row in valid_rows:
        index = row.get('index', '?')
        data = row.get('data', {})
        email = data.get('email', '')

        try:
            result = create_student_individual(
                data, created_by_id, creator_program_ids=creator_program_ids
            )
            created += 1
            created_users.append({
                'user_id': result['user_id'],
                'email': result['email'],
                'control_number': data.get('control_number', ''),
            })
        except (ProgramScopeError, ValidationError, StudentCreationError) as exc:
            failed.append({
                'index': index,
                'email': email,
                'error': str(exc),
            })
        except Exception as exc:
            failed.append({
                'index': index,
                'email': email,
                'error': f'Error inesperado: {exc}',
            })

    return {
        'created': created,
        'failed': failed,
        'created_users': created_users,
    }


# ---------------------------------------------------------------------------
# Plantilla CSV
# ---------------------------------------------------------------------------

def get_csv_template() -> str:
    """
    Devuelve el contenido de la plantilla CSV con headers y una fila de ejemplo.

    Returns:
        String con contenido CSV listo para descargar.
    """
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(CSV_HEADERS)
    writer.writerow(CSV_EXAMPLE_ROW)
    return output.getvalue()
