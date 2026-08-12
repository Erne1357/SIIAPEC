# app/services/semester_transition_service.py
"""
Servicio para el cierre de periodo y avance masivo de semestre (Tarea "Pasar Semestre").

Flujo:
1. postgraduate_admin llama a preview_program() para obtener un resumen de qué ocurrirá.
2. Confirma y llama a execute_program_transition() (o execute_global_transition() para todos
   los programas) — toda la operación ocurre en una única transacción SQL.
3. Por cada estudiante elegible se cierra su SemesterEnrollment activo y se crea uno nuevo
   en el periodo destino (pending, sin confirmar).
4. Los aspirantes se migran o expiran según cuántos periodos llevan de antigüedad.
5. Los diferidos cuyo periodo destino coincide con el nuevo periodo activo se reactivan.

Idempotencia:
- Si ya existe un SE para el periodo destino de un UserProgram, se omite silently.
- Los aspirantes ya expirados no se tocan.
"""

import logging
from typing import Optional

from app import db
from app.services import public_id_service
from app.models import UserProgram, User, Program, AcademicPeriod
from app.models.semester_enrollment import SemesterEnrollment
from app.models.enrollment_deferral import EnrollmentDeferral
from app.services.notification_service import NotificationService
from app.services.user_history_service import UserHistoryService
from app.services.payment_reference_service import PaymentReferenceService
from app.utils.datetime_utils import now_local

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------

class TransitionError(Exception):
    """Error base para operaciones de transición semestral."""


class PeriodNotFound(TransitionError):
    """El periodo académico no existe."""


class ProgramNotFound(TransitionError):
    """El programa no existe."""


class InvalidTransition(TransitionError):
    """Transición inválida entre periodos."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_period(period_id: int) -> AcademicPeriod:
    period = AcademicPeriod.query.get(period_id)
    if not period:
        raise PeriodNotFound(f"Periodo académico {period_id} no encontrado")
    return period


def _get_program(program_id: int) -> Program:
    program = Program.query.get(program_id)
    if not program:
        raise ProgramNotFound(f"Programa {program_id} no encontrado")
    return program


def _ordered_period_ids() -> list:
    """
    Devuelve todos los IDs de periodos ordenados cronológicamente por
    ``start_date``. NO usar ``id`` como proxy porque los periodos pueden
    crearse en cualquier orden (ej: migración que rellena periodos
    históricos faltantes después de que ya existían los recientes).
    """
    rows = (
        db.session.query(AcademicPeriod.id)
        .order_by(AcademicPeriod.start_date)
        .all()
    )
    return [r[0] for r in rows]


def _delta_periods(admission_period_id: Optional[int], target_period_id: int) -> Optional[int]:
    """
    Calcula cuántos periodos de diferencia hay entre admission_period_id
    y target_period_id en la lista ordenada.

    Retorna None si admission_period_id es None o no existe en la lista.
    Retorna el número de posiciones de distancia (≥ 0).
    """
    if admission_period_id is None:
        return None
    period_ids = _ordered_period_ids()
    try:
        idx_target = period_ids.index(target_period_id)
        idx_admission = period_ids.index(admission_period_id)
    except ValueError:
        return None
    return idx_target - idx_admission


def _get_conacyt_archive_step_id() -> Optional[int]:
    """Devuelve el step_id del archive de SECIHTI (step 12), o None si no existe."""
    # El archive SECIHTI está en Step 12 por convención del proyecto.
    return 12


def _is_conacyt_deadline(deadline) -> bool:
    """
    Determina si un DocumentDeadline es de tipo SECIHTI mensual
    (i.e., su archive pertenece al step 12 / Formato de Desempeño).
    """
    return deadline.archive and deadline.archive.step_id == 12


# ---------------------------------------------------------------------------
# Core evaluation
# ---------------------------------------------------------------------------

def evaluate_student(
    user_program_id: int,
    source_period_id: int,
    target_period_id: int,
) -> dict:
    """
    Evalúa si un estudiante puede avanzar al siguiente semestre.

    Returns:
        {
            'can_advance': bool,
            'blockers': [
                {'code': 'not_enrolled', 'message': str},
                {'code': 'enrollment_not_confirmed', 'message': str},
                {'code': 'not_enrolled', 'message': str},
                {'code': 'on_leave', 'message': str},
                {'code': 'dropped', 'message': str},
                {
                    'code': 'missing_documents',
                    'message': str,
                    'deadlines': [{'id': int, 'label': str}]
                },
                {
                    'code': 'missing_conacyt_months',
                    'message': str,
                    'months': [{'id': int, 'label': str, 'sequence': int}]
                },
            ]
        }
    """
    from app.models.document_deadline import DocumentDeadline
    from app.models.submission import Submission

    up = UserProgram.query.get(user_program_id)
    if not up:
        return {
            'can_advance': False,
            'blockers': [{'code': 'not_enrolled', 'message': 'UserProgram no encontrado'}],
        }

    blockers = []

    # ── Regla 1: Debe existir SE en periodo origen con enrollment_confirmed=True ──
    source_se = SemesterEnrollment.query.filter_by(
        user_program_id=user_program_id,
        academic_period_id=source_period_id,
    ).first()

    if not source_se:
        blockers.append({
            'code': 'not_enrolled',
            'message': 'No tiene inscripción semestral en el periodo origen',
        })
        return {'can_advance': False, 'blockers': blockers}

    # ── Regla nueva: Límite máximo de semestres (duration_semesters + 4) ──
    # Maestrías: 4 normal → 8 máx. Doctorado: 6 normal → 10 máx.
    # Bajas temporales NO descuentan: el contador sigue creciendo.
    program = up.program
    if program and program.duration_semesters:
        max_allowed = program.duration_semesters + 4
        next_sem = (source_se.semester_number or 0) + 1
        if next_sem > max_allowed:
            blockers.append({
                'code': 'semester_limit_exceeded',
                'message': (
                    f'Excede el límite de semestres del programa '
                    f'(máx {max_allowed}: duración {program.duration_semesters} + 4). '
                    f'Avanzaría al semestre {next_sem}.'
                ),
            })

    if not source_se.enrollment_confirmed:
        blockers.append({
            'code': 'enrollment_not_confirmed',
            'message': 'La inscripción semestral no fue confirmada por el coordinador',
        })

    # ── Regla 2: SE en periodo origen debe estar en status='active' ──
    if source_se.status == 'on_leave':
        blockers.append({
            'code': 'on_leave',
            'message': 'El estudiante está en baja temporal',
        })
    elif source_se.status == 'dropped':
        blockers.append({
            'code': 'dropped',
            'message': 'El estudiante tiene baja definitiva registrada',
        })
    elif source_se.status == 'pending':
        blockers.append({
            'code': 'enrollment_not_confirmed',
            'message': 'La inscripción semestral está en estado pendiente (no activa)',
        })

    # ── Regla 3: Documentos de permanencia cerrados sin submission aprobada ──
    now = now_local().replace(tzinfo=None)

    from app.models.archive import Archive

    deadlines = (
        DocumentDeadline.query
        .filter_by(
            program_id=up.program_id,
            academic_period_id=source_period_id,
        )
        .join(Archive, DocumentDeadline.archive_id == Archive.id)
        .filter(Archive.is_active == True)
        .all()
    )

    missing_docs = []
    missing_conacyt = []

    for dl in deadlines:
        # Sólo ventanas ya cerradas bloquean
        if dl.closes_at is None:
            continue
        if dl.closes_at > now:
            continue

        has_approved = Submission.query.filter_by(
            user_id=up.user_id,
            document_deadline_id=dl.id,
            status='approved',
        ).first() is not None

        if has_approved:
            continue

        if _is_conacyt_deadline(dl):
            # Las ventanas SECIHTI mensual se acumulan por separado
            missing_conacyt.append({
                'id': dl.id,
                'label': dl.label,
                'sequence': dl.sequence,
            })
        else:
            missing_docs.append({
                'id': dl.id,
                'label': dl.label,
            })

    if missing_docs:
        blockers.append({
            'code': 'missing_documents',
            'message': (
                f'{len(missing_docs)} ventana(s) de entrega cerrada(s) sin documento aprobado'
            ),
            'deadlines': missing_docs,
        })

    # ── Regla 4: SECIHTI mensual (sólo becarios) ──
    if up.has_conacyt_scholarship and missing_conacyt:
        blockers.append({
            'code': 'missing_conacyt_months',
            'message': (
                f'{len(missing_conacyt)} entrega(s) mensual(es) SECIHTI cerrada(s) sin aprobación'
            ),
            'months': missing_conacyt,
        })
    # Si no es becario, ignorar ventanas SECIHTI (ya se omitieron de missing_docs)

    return {
        'can_advance': len(blockers) == 0,
        'blockers': blockers,
    }


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

def _build_student_row(up: UserProgram, source_period_id: int, target_period_id: int) -> dict:
    """Construye un dict de fila para la respuesta del preview."""
    user = up.user
    evaluation = evaluate_student(up.id, source_period_id, target_period_id)

    source_se = SemesterEnrollment.query.filter_by(
        user_program_id=up.id,
        academic_period_id=source_period_id,
    ).first()

    # Calcular el siguiente número de semestre
    max_sem = (
        db.session.query(db.func.max(SemesterEnrollment.semester_number))
        .filter_by(user_program_id=up.id)
        .scalar()
    ) or 0
    next_semester_number = max_sem + 1

    program = up.program
    return {
        'user_program': up.to_dict(),
        'user': {
            'id': public_id_service.user_uuid(user.id),
            'full_name': f"{user.first_name} {user.last_name} {user.mother_last_name or ''}".strip(),
            'email': user.email,
            'control_number': getattr(user, 'control_number', None),
        },
        'program': {
            'id': program.id if program else None,
            'name': program.name if program else None,
            'slug': program.slug if program else None,
        },
        'current_se': source_se.to_dict() if source_se else None,
        'next_semester_number': next_semester_number,
        'can_advance': evaluation['can_advance'],
        'blockers': evaluation['blockers'],
    }


def _build_admission_rows(
    program_id: int,
    target_period_id: int,
) -> tuple:
    """
    Clasifica los aspirantes del programa según antigüedad de admisión.

    Returns:
        (migrate_list, expire_list, cleanup_list, deferred_reactivate_list,
         already_aligned_list)

    ``already_aligned`` son aspirantes cuyo ``admission_period_id`` ya coincide
    con el periodo destino (delta=0) — su admisión sigue activa en el nuevo
    periodo activo, no requieren acción. Antes se omitían silenciosamente y
    parecían "en limbo" en la UI.
    """
    period_ids = _ordered_period_ids()

    try:
        idx_target = period_ids.index(target_period_id)
    except ValueError:
        return [], [], [], [], []

    # Todos los UserProgram del programa que NO son estudiantes enrolled
    applicants = (
        UserProgram.query
        .filter(
            UserProgram.program_id == program_id,
            UserProgram.admission_status.notin_(['enrolled', 'expired']),
        )
        .all()
    )

    # Diferidos cuyo target coincide con el nuevo periodo
    deferred_reactivate = []

    # Pre-cargar diferidos activos en el periodo destino para este programa
    active_deferrals = {
        d.user_program_id: d
        for d in EnrollmentDeferral.query.filter(
            EnrollmentDeferral.deferred_to_period_id == target_period_id,
            EnrollmentDeferral.status == 'active',
        ).all()
    }

    migrate = []
    expire = []
    cleanup = []
    already_aligned = []

    for up in applicants:
        user = up.user
        program = up.program
        base_row = {
            'user_program': up.to_dict(),
            'user': {
                'id': public_id_service.user_uuid(user.id),
                'full_name': f"{user.first_name} {user.last_name} {user.mother_last_name or ''}".strip(),
                'email': user.email,
                'control_number': getattr(user, 'control_number', None),
            },
            'program': {
                'id': program.id if program else None,
                'name': program.name if program else None,
                'slug': program.slug if program else None,
            },
            'admission_period_id': up.admission_period_id,
        }

        # Excepción diferidos: si tienen deferral activo que apunta al target_period
        if up.id in active_deferrals:
            deferred_reactivate.append(base_row)
            continue

        if up.admission_period_id is None:
            # Sin periodo de admisión registrado: ignorar
            continue

        try:
            idx_admission = period_ids.index(up.admission_period_id)
        except ValueError:
            continue

        delta = idx_target - idx_admission

        if delta < 0:
            # Periodo de admisión es FUTURO al destino: ignorar (no aplica)
            continue
        elif delta == 0:
            # Ya pertenece al periodo destino: su admisión sigue allí
            already_aligned.append(base_row)
        elif delta == 1:
            migrate.append(base_row)
        elif delta == 2:
            expire.append(base_row)
        else:  # delta >= 3
            cleanup.append(base_row)

    return migrate, expire, cleanup, deferred_reactivate, already_aligned


def preview_program(
    program_id: int,
    source_period_id: int,
    target_period_id: int,
) -> dict:
    """
    Genera un resumen de lo que ocurrirá al ejecutar la transición para un programa.
    No muta ningún dato.

    Returns:
        {
            'will_advance': [{user_program_dict, user_dict, current_se_dict, next_semester_number}],
            'will_block': [{user_program_dict, user_dict, blockers}],
            'on_leave': [{user_program_dict, user_dict, current_se_dict}],
            'admission_migrate': [{user_program_dict, user_dict, admission_period_id}],
            'admission_expire': [...],
            'admission_to_cleanup': [...],
            'deferred_reactivate': [...],
            'stats': {
                'will_advance': int,
                'will_block': int,
                'on_leave': int,
                'admission_migrate': int,
                'admission_expire': int,
                'admission_to_cleanup': int,
                'deferred_reactivate': int,
            }
        }
    """
    _get_period(source_period_id)
    _get_period(target_period_id)
    _get_program(program_id)

    # ── Estudiantes enrolled ──
    enrolled_ups = (
        UserProgram.query
        .filter_by(program_id=program_id, admission_status='enrolled')
        .all()
    )

    will_advance = []
    will_block = []
    on_leave = []

    for up in enrolled_ups:
        source_se = SemesterEnrollment.query.filter_by(
            user_program_id=up.id,
            academic_period_id=source_period_id,
        ).first()

        if source_se and source_se.status == 'on_leave':
            user = up.user
            program = up.program
            on_leave.append({
                'user_program': up.to_dict(),
                'user': {
                    'id': public_id_service.user_uuid(user.id),
                    'full_name': f"{user.first_name} {user.last_name} {user.mother_last_name or ''}".strip(),
                    'email': user.email,
                    'control_number': getattr(user, 'control_number', None),
                },
                'program': {
                    'id': program.id if program else None,
                    'name': program.name if program else None,
                    'slug': program.slug if program else None,
                },
                'current_se': source_se.to_dict() if source_se else None,
            })
            continue

        row = _build_student_row(up, source_period_id, target_period_id)
        if row['can_advance']:
            will_advance.append(row)
        else:
            will_block.append(row)

    # ── Aspirantes ──
    migrate, expire, cleanup, deferred, already_aligned = _build_admission_rows(
        program_id, target_period_id
    )

    stats = {
        'will_advance': len(will_advance),
        'will_block': len(will_block),
        'on_leave': len(on_leave),
        'admission_migrate': len(migrate),
        'admission_expire': len(expire),
        'admission_to_cleanup': len(cleanup),
        'deferred_reactivate': len(deferred),
        'admission_already_aligned': len(already_aligned),
    }

    return {
        'will_advance': will_advance,
        'will_block': will_block,
        'on_leave': on_leave,
        'admission_migrate': migrate,
        'admission_expire': expire,
        'admission_to_cleanup': cleanup,
        'deferred_reactivate': deferred,
        'admission_already_aligned': already_aligned,
        'stats': stats,
    }


def preview_global(source_period_id: int, target_period_id: int) -> dict:
    """
    Genera un preview global para todos los programas.

    Returns dict con las mismas claves que preview_program pero acumuladas,
    más 'programs' (lista de program_id → stats individuales).
    """
    _get_period(source_period_id)
    _get_period(target_period_id)

    programs = Program.query.filter_by(is_active=True).all()

    global_result = {
        'will_advance': [],
        'will_block': [],
        'on_leave': [],
        'admission_migrate': [],
        'admission_expire': [],
        'admission_to_cleanup': [],
        'deferred_reactivate': [],
        'admission_already_aligned': [],
        'programs': [],
        'stats': {
            'will_advance': 0,
            'will_block': 0,
            'on_leave': 0,
            'admission_migrate': 0,
            'admission_expire': 0,
            'admission_to_cleanup': 0,
            'deferred_reactivate': 0,
            'admission_already_aligned': 0,
        },
    }

    for program in programs:
        try:
            p_result = preview_program(program.id, source_period_id, target_period_id)
        except Exception as e:
            logger.error(
                f"Error en preview_program(program_id={program.id}): {e}"
            )
            continue

        for key in ('will_advance', 'will_block', 'on_leave', 'admission_migrate',
                    'admission_expire', 'admission_to_cleanup', 'deferred_reactivate',
                    'admission_already_aligned'):
            global_result[key].extend(p_result[key])
            global_result['stats'][key] += p_result['stats'][key]

        global_result['programs'].append({
            'program_id': program.id,
            'program_name': program.name,
            'stats': p_result['stats'],
        })

    return global_result


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def execute_program_transition(
    program_id: int,
    source_period_id: int,
    target_period_id: int,
    coordinator_id: int,
) -> dict:
    """
    Aplica la transición semestral completa para un programa en una sola transacción.

    - Estudiantes elegibles: cierra SE actual, crea SE nuevo en pending.
    - Aspirantes Δ=1: migra admission_period_id al target_period_id.
    - Aspirantes Δ=2: admission_status='expired'.
    - Diferidos con deferred_to_period_id == target_period_id: reactiva (in_progress).
    - Idempotente: si ya existe SE para target_period, se omite.

    Returns:
        {
            'advanced': int,
            'blocked': int,
            'on_leave': int,
            'admission_migrated': int,
            'admission_expired': int,
            'deferred_reactivated': int,
            'errors': [str],
        }
    """
    source_period = _get_period(source_period_id)
    target_period = _get_period(target_period_id)
    program = _get_program(program_id)

    stats = {
        'advanced': 0,
        'blocked': 0,
        'on_leave': 0,
        'admission_migrated': 0,
        'admission_expired': 0,
        'deferred_reactivated': 0,
        'errors': [],
        'expired_user_program_ids': [],
    }

    try:
        now = now_local()

        # ── Diferidos que se reactivan ──────────────────────────────────────
        active_deferrals = {
            d.user_program_id: d
            for d in EnrollmentDeferral.query.filter(
                EnrollmentDeferral.deferred_to_period_id == target_period_id,
                EnrollmentDeferral.status == 'active',
            ).join(
                UserProgram,
                EnrollmentDeferral.user_program_id == UserProgram.id,
            ).filter(
                UserProgram.program_id == program_id,
            ).all()
        }

        # ── Aspirantes ──────────────────────────────────────────────────────
        period_ids = _ordered_period_ids()
        try:
            idx_target = period_ids.index(target_period_id)
        except ValueError:
            idx_target = None

        if idx_target is not None:
            applicants = (
                UserProgram.query
                .filter(
                    UserProgram.program_id == program_id,
                    UserProgram.admission_status.notin_(['enrolled', 'expired']),
                )
                .all()
            )

            for up in applicants:
                try:
                    # Diferidos con deferred_to_period_id == target → reactivar
                    if up.id in active_deferrals:
                        deferral = active_deferrals[up.id]
                        up.admission_period_id = target_period_id
                        up.admission_status = 'in_progress'
                        deferral.status = 'used'
                        UserHistoryService.log_action(
                            user_id=up.user_id,
                            admin_id=coordinator_id,
                            action='deferral_reactivated_by_transition',
                            details=(
                                f'Diferido reactivado al periodo {target_period.name} '
                                f'durante transición semestral del programa {program.name}'
                            ),
                        )
                        NotificationService.create_notification(
                            user_id=up.user_id,
                            notification_type='deferral_reactivated',
                            title='Tu diferimiento ha sido activado',
                            message=(
                                f'Tu solicitud de admisión ha sido reactivada para el periodo '
                                f'{target_period.name} en {program.name}. '
                                f'Revisa tu portal para continuar el proceso.'
                            ),
                            priority='high',
                            action_url='/user/dashboard',
                        )
                        stats['deferred_reactivated'] += 1
                        continue

                    if up.admission_period_id is None:
                        continue

                    try:
                        idx_admission = period_ids.index(up.admission_period_id)
                    except ValueError:
                        continue

                    delta = idx_target - idx_admission

                    if delta <= 0:
                        continue
                    elif delta == 1:
                        up.admission_period_id = target_period_id
                        up.updated_at = now
                        UserHistoryService.log_action(
                            user_id=up.user_id,
                            admin_id=coordinator_id,
                            action='admission_period_migrated',
                            details=(
                                f'Periodo de admisión migrado a {target_period.name} '
                                f'durante transición del programa {program.name}'
                            ),
                        )
                        stats['admission_migrated'] += 1
                    elif delta == 2:
                        up.admission_status = 'expired'
                        up.updated_at = now
                        UserHistoryService.log_action(
                            user_id=up.user_id,
                            admin_id=coordinator_id,
                            action='admission_expired',
                            details=(
                                f'Admisión expirada durante transición semestral '
                                f'del programa {program.name} (Δ=2 periodos)'
                            ),
                        )
                        NotificationService.create_notification(
                            user_id=up.user_id,
                            notification_type='admission_expired',
                            title='Tu proceso de admisión ha expirado',
                            message=(
                                f'Tu proceso de admisión al programa {program.name} ha expirado '
                                f'porque ha transcurrido más de un periodo sin completarse. '
                                f'Contacta al coordinador si tienes dudas.'
                            ),
                            priority='high',
                            action_url='/user/dashboard',
                        )
                        stats['admission_expired'] += 1
                        stats['expired_user_program_ids'].append(up.id)
                    # delta >= 3: lo gestiona purga manual desde Configuración → Limpieza

                except Exception as e:
                    err_msg = f"Error procesando aspirante up_id={up.id}: {e}"
                    logger.error(err_msg)
                    stats['errors'].append(err_msg)

        # ── Estudiantes enrolled ────────────────────────────────────────────
        enrolled_ups = (
            UserProgram.query
            .filter_by(program_id=program_id, admission_status='enrolled')
            .all()
        )

        for up in enrolled_ups:
            try:
                source_se = SemesterEnrollment.query.filter_by(
                    user_program_id=up.id,
                    academic_period_id=source_period_id,
                ).first()

                if source_se and source_se.status == 'on_leave':
                    stats['on_leave'] += 1
                    continue

                evaluation = evaluate_student(up.id, source_period_id, target_period_id)

                if not evaluation['can_advance']:
                    stats['blocked'] += 1
                    logger.info(
                        f"Estudiante up_id={up.id} bloqueado: "
                        f"{[b['code'] for b in evaluation['blockers']]}"
                    )
                    continue

                # ── Idempotencia: verificar si ya existe SE para target_period ──
                existing_target_se = SemesterEnrollment.query.filter_by(
                    user_program_id=up.id,
                    academic_period_id=target_period_id,
                ).first()

                if existing_target_se:
                    logger.info(
                        f"SE ya existe para up_id={up.id} en period_id={target_period_id} — omitido"
                    )
                    stats['advanced'] += 1
                    continue

                # ── Marcar SE actual como completed ──
                if source_se:
                    source_se.status = 'completed'
                    source_se.updated_at = now

                # ── Calcular nuevo número de semestre ──
                max_sem = (
                    db.session.query(db.func.max(SemesterEnrollment.semester_number))
                    .filter_by(user_program_id=up.id)
                    .scalar()
                ) or 0
                new_semester_number = max_sem + 1

                # ── Crear nuevo SE en target_period ──
                new_se = SemesterEnrollment(
                    user_program_id=up.id,
                    academic_period_id=target_period_id,
                    semester_number=new_semester_number,
                    status='pending',
                    enrollment_confirmed=False,
                )
                db.session.add(new_se)

                # ── Actualizar current_semester en UserProgram ──
                up.current_semester = new_semester_number
                up.updated_at = now

                # ── Stub de referencia bancaria (loggear, no guardar) ──
                payment_ref = PaymentReferenceService.generate(up.id, target_period_id)
                if payment_ref.get('reference'):
                    logger.info(
                        f"Referencia bancaria generada para up_id={up.id}: "
                        f"ref={payment_ref['reference']} monto={payment_ref['amount']}"
                    )
                else:
                    logger.debug(
                        f"PaymentReferenceService.generate(up_id={up.id}): stub retornó None"
                    )

                # ── Historial ──
                UserHistoryService.log_action(
                    user_id=up.user_id,
                    admin_id=coordinator_id,
                    action='semester_advanced',
                    details=(
                        f'Avanzó al semestre {new_semester_number} en {program.name} '
                        f'(periodo {target_period.name})'
                    ),
                )

                # ── Notificación al estudiante ──
                NotificationService.create_notification(
                    user_id=up.user_id,
                    notification_type='semester_advanced',
                    title=f'Avanzaste al semestre {new_semester_number}',
                    message=(
                        f'Tu avance al semestre {new_semester_number} en {program.name} '
                        f'({target_period.name}) ha sido registrado. '
                        f'Sube tu comprobante de pago para confirmar tu inscripción.'
                    ),
                    priority='normal',
                    action_url='/user/dashboard',
                )

                stats['advanced'] += 1

            except Exception as e:
                err_msg = f"Error procesando estudiante up_id={up.id}: {e}"
                logger.error(err_msg)
                stats['errors'].append(err_msg)

        # ── Notificar coordinador si hay bloqueados ──────────────────────────
        if stats['blocked'] > 0:
            try:
                if program.coordinator_id:
                    NotificationService.create_notification(
                        user_id=program.coordinator_id,
                        notification_type='semester_transition_blocked',
                        title='Estudiantes no avanzaron en la transición',
                        message=(
                            f'{stats["blocked"]} estudiante(s) de {program.name} no pudieron '
                            f'avanzar al semestre siguiente. Revisa la pestaña '
                            f'"Inscripción → Rezagados" para gestionar los pendientes.'
                        ),
                        priority='high',
                        action_url='/coordinator/permanence',
                    )
            except Exception as e:
                logger.error(f"Error notificando coordinador de bloqueados: {e}")

        db.session.commit()

    except Exception:
        db.session.rollback()
        raise

    # Activar destino + cerrar origen tras transición exitosa.
    # Si esto falla, el avance ya está commiteado — sólo se loggea.
    try:
        _activate_target_period(source_period_id, target_period_id)
    except Exception as e:
        logger.error(f"Error activando periodo destino tras transición: {e}")
        stats['errors'].append(f"No se pudo activar el periodo destino: {e}")

    return stats


def _activate_target_period(source_period_id: int, target_period_id: int) -> None:
    """
    Marca el periodo destino como activo (is_active=True) y el origen como
    inactivo (is_active=False). Idempotente: si ya están así, no hace nada.
    Commit independiente — se llama después de la transición exitosa.
    """
    source = _get_period(source_period_id)
    target = _get_period(target_period_id)
    changed = False
    if source.is_active:
        source.is_active = False
        if hasattr(source, 'status'):
            source.status = 'completed'
        changed = True
    if not target.is_active:
        target.is_active = True
        if hasattr(target, 'status'):
            target.status = 'active'
        changed = True
    if changed:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise


def execute_global_transition(
    source_period_id: int,
    target_period_id: int,
    coordinator_id: int,
) -> dict:
    """
    Ejecuta la transición semestral para todos los programas activos.
    Acumula estadísticas de cada programa. Al finalizar, activa el periodo
    destino y cierra el origen.

    Returns:
        {
            'total': {stats acumuladas},
            'programs': [{program_id, program_name, stats}],
            'errors': [str]
        }
    """
    _get_period(source_period_id)
    _get_period(target_period_id)

    programs = Program.query.filter_by(is_active=True).all()

    total_stats = {
        'advanced': 0,
        'blocked': 0,
        'on_leave': 0,
        'admission_migrated': 0,
        'admission_expired': 0,
        'deferred_reactivated': 0,
        'errors': [],
        'expired_user_program_ids': [],
    }
    per_program = []

    for program in programs:
        try:
            p_stats = execute_program_transition(
                program_id=program.id,
                source_period_id=source_period_id,
                target_period_id=target_period_id,
                coordinator_id=coordinator_id,
            )
        except Exception as e:
            err_msg = f"Error fatal en programa {program.id} ({program.name}): {e}"
            logger.error(err_msg)
            total_stats['errors'].append(err_msg)
            per_program.append({
                'program_id': program.id,
                'program_name': program.name,
                'stats': None,
                'error': str(e),
            })
            continue

        for key in ('advanced', 'blocked', 'on_leave', 'admission_migrated',
                    'admission_expired', 'deferred_reactivated'):
            total_stats[key] += p_stats.get(key, 0)
        total_stats['errors'].extend(p_stats.get('errors', []))
        total_stats['expired_user_program_ids'].extend(
            p_stats.get('expired_user_program_ids', [])
        )

        per_program.append({
            'program_id': program.id,
            'program_name': program.name,
            'stats': p_stats,
        })

    # Activar el periodo destino + cerrar origen al final
    try:
        _activate_target_period(source_period_id, target_period_id)
    except Exception as e:
        logger.error(f"Error activando periodo destino: {e}")
        total_stats['errors'].append(f"No se pudo activar el periodo destino: {e}")

    return {
        'total': total_stats,
        'programs': per_program,
    }
