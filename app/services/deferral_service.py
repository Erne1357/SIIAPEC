# app/services/deferral_service.py
"""
Servicio para gestionar diferimientos de inscripción (Fase 7).

Reglas de negocio:
- Máximo 2 diferimientos por aspirante/programa.
- Al diferir, la acceptance_letter se conserva; course_schedule y
  enrollment_receipt se resetean (el coordinador sube nueva tira en el
  periodo diferido; el aspirante vuelve a subir su boleta).
- El siguiente periodo se asigna automáticamente (siguiente por start_date).
- Si el aspirante solicita el diferimiento, queda en estado 'pending' hasta
  que el coordinador aprueba o rechaza.
- Si el coordinador lo inicia directamente, se aprueba de inmediato.
- Al reactivar en el nuevo periodo, admission_status vuelve a 'accepted' y
  admission_period_id se actualiza al periodo diferido.
"""

import logging
import os
from typing import Optional

from flask import current_app

from app import db
from app.services import public_id_service
from app.models import UserProgram, User, AcademicPeriod
from app.models.acceptance_document import AcceptanceDocument
from app.models.enrollment_deferral import EnrollmentDeferral
from app.services.notification_service import NotificationService
from app.services.user_history_service import UserHistoryService
from app.sockets.emitters import emit_user_and_coordinators, emit_to_coordinators
from app.utils.datetime_utils import now_local
from app.utils.files import abs_path_from_db

logger = logging.getLogger(__name__)

MAX_DEFERRALS = 2


# ─────────────────────────────────────────────────────────────────────────────
# Excepciones
# ─────────────────────────────────────────────────────────────────────────────

class DeferralError(Exception):
    """Error base para operaciones de diferimiento."""
    pass


class DeferralNotAllowed(DeferralError):
    pass


class DeferralNotFound(DeferralError):
    pass


#: Denial text for -that applicant/deferral is not here-. Carries no id: the
#: routes speak UUIDs now, so echoing the internal integer would hand it back
#: out, and a message that varies with the cause is an enumeration oracle.
DEFERRAL_NOT_FOUND_MESSAGE = 'No se encontró el proceso del aspirante.'
DEFERRAL_ROW_NOT_FOUND_MESSAGE = 'Diferimiento no encontrado.' 


# ─────────────────────────────────────────────────────────────────────────────
# Helpers privados
# ─────────────────────────────────────────────────────────────────────────────

def _get_user_program(user_id: int, program_id: int) -> UserProgram:
    up = UserProgram.query.filter_by(user_id=user_id, program_id=program_id).first()
    if not up:
        raise DeferralNotFound(DEFERRAL_NOT_FOUND_MESSAGE)
    return up


def _count_active_or_used_deferrals(user_program_id: int) -> int:
    """Cuenta diferimientos activos, usados o expirados (excluye rechazados)."""
    return EnrollmentDeferral.query.filter(
        EnrollmentDeferral.user_program_id == user_program_id,
        EnrollmentDeferral.status.in_(['pending', 'active', 'used', 'expired'])
    ).count()


def _get_next_period(current_period_id: int) -> Optional[AcademicPeriod]:
    """
    Retorna el siguiente periodo académico disponible (por start_date)
    posterior al periodo actual.
    """
    current = AcademicPeriod.query.get(current_period_id)
    if not current:
        return None

    return (
        AcademicPeriod.query
        .filter(AcademicPeriod.start_date > current.start_date)
        .order_by(AcademicPeriod.start_date.asc())
        .first()
    )


def _absolute_doc_path(relative_path: str) -> Optional[str]:
    """
    Traduce la ruta que guarda `save_user_doc` ('<user_id>/acceptance/<archivo>')
    a la ruta absoluta bajo USER_DOCS_FOLDER.

    Lo que hay en `AcceptanceDocument.file_path` es relativo a esa carpeta —así
    lo sirve `/files/doc/...`—, así que resolverlo contra el CWD del proceso
    (lo que hacía este módulo) nunca encontraba el archivo y el borrado físico
    jamás ocurría. Se usa `abs_path_from_db` (safe_join) igual que
    `app/routes/api/files_api.py`, que además corta cualquier '../'.

    Returns:
        str | None — None si la ruta viene vacía o es insegura.
    """
    if not relative_path:
        return None
    try:
        base = current_app.config['USER_DOCS_FOLDER']
        return abs_path_from_db(relative_path, base)
    except (KeyError, RuntimeError) as e:
        # Sin contexto de aplicación o sin configuración: no borrar a ciegas.
        logger.warning(f"No se pudo resolver la ruta de {relative_path}: {e}")
        return None


def _reset_docs_for_deferral(user_program_id: int) -> None:
    """
    Al diferir:
    - course_schedule  → se elimina el registro (el coordinador subirá uno nuevo)
    - enrollment_receipt → se elimina el registro (el aspirante lo subirá de nuevo)
    - acceptance_letter → se conserva tal cual
    """
    for doc_type in ('course_schedule', 'enrollment_receipt'):
        doc = AcceptanceDocument.query.filter_by(
            user_program_id=user_program_id,
            document_type=doc_type
        ).first()
        if doc:
            # Eliminar archivo físico si existe
            abs_path = _absolute_doc_path(doc.file_path)
            if abs_path and os.path.exists(abs_path):
                try:
                    os.remove(abs_path)
                except OSError as e:
                    logger.warning(f"No se pudo eliminar {abs_path}: {e}")
            db.session.delete(doc)


def _apply_deferral(up: UserProgram, deferral: EnrollmentDeferral,
                    coordinator_id: int) -> None:
    """
    Aplica el diferimiento: cambia estados y resetea documentos.
    No hace commit; el llamador es responsable.
    """
    deferral.status = 'active'
    deferral.reviewed_by_id = coordinator_id
    deferral.reviewed_at = now_local()

    # Conservar admission_period_id original hasta la reactivación
    up.admission_status = 'deferred'

    _reset_docs_for_deferral(up.id)

    program = up.program
    user_id = up.user_id

    original_period_name = (
        deferral.original_period.name if deferral.original_period else 'periodo original'
    )
    next_period_name = (
        deferral.deferred_to_period.name
        if deferral.deferred_to_period else 'próximo periodo'
    )

    NotificationService.create_notification(
        user_id=user_id,
        notification_type='deferral_applied',
        title='Tu inscripción ha sido diferida',
        message=(
            f'Tu inscripción en {program.name} ha sido diferida del periodo '
            f'{original_period_name} al {next_period_name}. '
            f'Tu carta de aceptación sigue vigente. '
            f'Cuando el nuevo periodo esté activo, deberás completar los '
            f'documentos de inscripción.'
        ),
        priority='high',
        action_url='/user/dashboard',
    )

    UserHistoryService.log_action(
        user_id=user_id,
        admin_id=coordinator_id,
        action='enrollment_deferred',
        details=(
            f'Inscripción diferida #{deferral.deferral_number} en {program.name}. '
            f'De: {original_period_name} → A: {next_period_name}. '
            f'Iniciado por: {deferral.requested_by}.'
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# API pública
# ─────────────────────────────────────────────────────────────────────────────

def defer_applicant(user_id: int, program_id: int,
                    coordinator_id: int, reason: str = None) -> EnrollmentDeferral:
    """
    El coordinador difiere directamente a un aspirante aceptado.

    Crea un EnrollmentDeferral con status='active' y aplica el diferimiento
    de inmediato (sin requerir aprobación).
    """
    up = _get_user_program(user_id, program_id)

    if up.admission_status != 'accepted':
        raise DeferralNotAllowed(
            f"Solo se puede diferir aspirantes en estado 'accepted'. "
            f"Estado actual: '{up.admission_status}'"
        )

    count = _count_active_or_used_deferrals(up.id)
    if count >= MAX_DEFERRALS:
        raise DeferralNotAllowed(
            f"El aspirante ya utilizó el máximo de {MAX_DEFERRALS} diferimientos."
        )

    original_period_id = up.admission_period_id
    if not original_period_id:
        raise DeferralNotAllowed(
            "El aspirante no tiene un periodo de admisión asignado."
        )

    next_period = _get_next_period(original_period_id)
    if not next_period:
        raise DeferralNotAllowed(
            "No hay periodo académico futuro disponible para diferir la inscripción. "
            "Crea el siguiente periodo académico en Configuración antes de continuar."
        )

    deferral = EnrollmentDeferral(
        user_program_id=up.id,
        original_period_id=original_period_id,
        deferred_to_period_id=next_period.id,
        deferral_number=count + 1,
        status='pending',           # _apply_deferral lo pondrá en 'active'
        requested_by='coordinator',
        reason=reason,
    )
    db.session.add(deferral)
    db.session.flush()  # obtener deferral.id antes de cargar relaciones

    # Recargar relaciones FK para que _apply_deferral pueda leer nombres
    db.session.refresh(deferral)

    _apply_deferral(up, deferral, coordinator_id)

    db.session.commit()

    emit_user_and_coordinators(
        'deferral:applied',
        {
            'user_id': public_id_service.user_uuid(user_id),
            'program_id': program_id,
            'deferral_id': deferral.id,
            'admission_status': 'deferred',
        },
        user_id=user_id,
        program_id=program_id,
    )
    return deferral


def request_deferral(user_id: int, program_id: int,
                     reason: str = None) -> EnrollmentDeferral:
    """
    El aspirante solicita un diferimiento (queda pendiente de aprobación).
    """
    up = _get_user_program(user_id, program_id)

    if up.admission_status != 'accepted':
        raise DeferralNotAllowed(
            f"Solo puedes solicitar diferimiento cuando estás en estado 'accepted'. "
            f"Estado actual: '{up.admission_status}'"
        )

    count = _count_active_or_used_deferrals(up.id)
    if count >= MAX_DEFERRALS:
        raise DeferralNotAllowed(
            f"Ya utilizaste el máximo de {MAX_DEFERRALS} diferimientos permitidos."
        )

    # Verificar que no haya ya una solicitud pendiente
    existing_pending = EnrollmentDeferral.query.filter_by(
        user_program_id=up.id,
        status='pending',
    ).first()
    if existing_pending:
        raise DeferralNotAllowed(
            "Ya tienes una solicitud de diferimiento pendiente de revisión."
        )

    original_period_id = up.admission_period_id
    if not original_period_id:
        raise DeferralNotAllowed(
            "No tienes un periodo de admisión asignado."
        )

    next_period = _get_next_period(original_period_id)
    if not next_period:
        raise DeferralNotAllowed(
            "No hay periodo académico futuro disponible para diferir la inscripción. "
            "Contacta al coordinador para que configure el siguiente periodo."
        )

    deferral = EnrollmentDeferral(
        user_program_id=up.id,
        original_period_id=original_period_id,
        deferred_to_period_id=next_period.id,
        deferral_number=count + 1,
        status='pending',
        requested_by='applicant',
        reason=reason,
    )
    db.session.add(deferral)

    program = up.program

    # Notificar al coordinador (si el programa tiene coordinador asignado)
    if program.coordinator_id:
        NotificationService.create_notification(
            user_id=program.coordinator_id,
            notification_type='deferral_request_received',
            title='Solicitud de diferimiento recibida',
            message=(
                f'{up.user.first_name} {up.user.last_name} '
                f'ha solicitado diferir su inscripción en {program.name}.'
            ),
            priority='medium',
            action_url=f'/coordinator/acceptance/{program.id}',
        )

    UserHistoryService.log_action(
        user_id=user_id,
        admin_id=user_id,
        action='deferral_requested',
        details=f'Aspirante solicitó diferimiento en {program.name}. Motivo: {reason or "Sin especificar"}',
    )

    db.session.commit()

    emit_user_and_coordinators(
        'deferral:requested',
        {
            'user_id': public_id_service.user_uuid(user_id),
            'program_id': program_id,
            'deferral_id': deferral.id,
            'requested_by': 'applicant',
        },
        user_id=user_id,
        program_id=program_id,
    )
    return deferral


def approve_deferral(deferral_id: int, coordinator_id: int,
                     notes: str = None) -> EnrollmentDeferral:
    """
    El coordinador aprueba una solicitud de diferimiento del aspirante.
    """
    deferral = EnrollmentDeferral.query.get(deferral_id)
    if not deferral:
        raise DeferralNotFound(DEFERRAL_ROW_NOT_FOUND_MESSAGE)

    if deferral.status != 'pending':
        raise DeferralNotAllowed(
            f"Solo se pueden aprobar diferimientos en estado 'pending'. "
            f"Estado actual: '{deferral.status}'"
        )

    deferral.review_notes = notes
    up = deferral.user_program

    _apply_deferral(up, deferral, coordinator_id)

    db.session.commit()

    emit_user_and_coordinators(
        'deferral:approved',
        {
            'user_id': public_id_service.user_uuid(up.user_id),
            'program_id': up.program_id,
            'deferral_id': deferral.id,
            'admission_status': 'deferred',
        },
        user_id=up.user_id,
        program_id=up.program_id,
    )
    return deferral


def reject_deferral(deferral_id: int, coordinator_id: int,
                    notes: str = None) -> EnrollmentDeferral:
    """
    El coordinador rechaza una solicitud de diferimiento del aspirante.
    """
    deferral = EnrollmentDeferral.query.get(deferral_id)
    if not deferral:
        raise DeferralNotFound(DEFERRAL_ROW_NOT_FOUND_MESSAGE)

    if deferral.status != 'pending':
        raise DeferralNotAllowed(
            f"Solo se pueden rechazar diferimientos en estado 'pending'. "
            f"Estado actual: '{deferral.status}'"
        )

    deferral.status = 'rejected'
    deferral.reviewed_by_id = coordinator_id
    deferral.reviewed_at = now_local()
    deferral.review_notes = notes

    up = deferral.user_program
    program = up.program

    NotificationService.create_notification(
        user_id=up.user_id,
        notification_type='deferral_rejected',
        title='Solicitud de diferimiento rechazada',
        message=(
            f'Tu solicitud de diferimiento en {program.name} fue rechazada. '
            f'Motivo: {notes or "Sin especificar"}.'
        ),
        priority='high',
        action_url='/user/dashboard',
    )

    UserHistoryService.log_action(
        user_id=up.user_id,
        admin_id=coordinator_id,
        action='deferral_rejected',
        details=f'Diferimiento rechazado en {program.name}. {notes or ""}',
    )

    db.session.commit()

    emit_user_and_coordinators(
        'deferral:rejected',
        {
            'user_id': public_id_service.user_uuid(up.user_id),
            'program_id': up.program_id,
            'deferral_id': deferral.id,
        },
        user_id=up.user_id,
        program_id=up.program_id,
    )
    return deferral


def reactivate_deferred(user_id: int, program_id: int,
                        coordinator_id: int) -> UserProgram:
    """
    El coordinador reactiva a un aspirante diferido en el nuevo periodo.

    - admission_status vuelve a 'accepted'
    - admission_period_id se actualiza al periodo diferido
    - El EnrollmentDeferral activo se marca como 'used'
    - La acceptance_letter se conserva
    - course_schedule y enrollment_receipt ya están reseteados desde el diferimiento
    """
    up = _get_user_program(user_id, program_id)

    if up.admission_status != 'deferred':
        raise DeferralNotAllowed(
            f"Solo se puede reactivar aspirantes en estado 'deferred'. "
            f"Estado actual: '{up.admission_status}'"
        )

    active_deferral = EnrollmentDeferral.query.filter_by(
        user_program_id=up.id,
        status='active',
    ).order_by(EnrollmentDeferral.deferral_number.desc()).first()

    if not active_deferral:
        raise DeferralNotFound(
            "No se encontró un diferimiento activo para este aspirante."
        )

    # Actualizar periodo de admisión al periodo diferido
    if active_deferral.deferred_to_period_id:
        up.admission_period_id = active_deferral.deferred_to_period_id

    up.admission_status = 'accepted'

    active_deferral.status = 'used'

    program = up.program
    new_period_name = (
        active_deferral.deferred_to_period.name
        if active_deferral.deferred_to_period else 'el nuevo periodo'
    )

    NotificationService.create_notification(
        user_id=user_id,
        notification_type='deferral_reactivated',
        title='Tu inscripción ha sido reactivada',
        message=(
            f'Tu proceso de inscripción en {program.name} ha sido reactivado '
            f'para {new_period_name}. '
            f'Ingresa al portal para completar los documentos pendientes.'
        ),
        priority='high',
        action_url='/user/dashboard',
    )

    UserHistoryService.log_action(
        user_id=user_id,
        admin_id=coordinator_id,
        action='deferral_reactivated',
        details=(
            f'Aspirante reactivado en {program.name} para {new_period_name}.'
        ),
    )

    db.session.commit()

    emit_user_and_coordinators(
        'deferral:reactivated',
        {
            'user_id': public_id_service.user_uuid(user_id),
            'program_id': program_id,
            'admission_status': 'accepted',
            'admission_period_id': up.admission_period_id,
        },
        user_id=user_id,
        program_id=program_id,
    )
    return up


def get_deferral_status(user_program_id: int) -> dict:
    """
    Retorna el estado de diferimiento de un UserProgram.

    Returns:
        {
          'can_defer': bool,         # si aún puede diferir
          'deferrals_used': int,     # cuántos diferimientos lleva
          'max_deferrals': int,      # máximo permitido
          'active_deferral': dict|None,
          'all_deferrals': [dict],
          'pending_request': dict|None,  # solicitud pendiente del aspirante
        }
    """
    deferrals = (
        EnrollmentDeferral.query
        .filter_by(user_program_id=user_program_id)
        .order_by(EnrollmentDeferral.deferral_number.asc())
        .all()
    )

    used_count = sum(
        1 for d in deferrals
        if d.status in ('pending', 'active', 'used', 'expired')
    )
    active = next((d for d in deferrals if d.status == 'active'), None)
    pending = next(
        (d for d in deferrals if d.status == 'pending' and d.requested_by == 'applicant'),
        None
    )

    return {
        'can_defer': used_count < MAX_DEFERRALS,
        'deferrals_used': used_count,
        'max_deferrals': MAX_DEFERRALS,
        'active_deferral': active.to_dict() if active else None,
        'pending_request': pending.to_dict() if pending else None,
        'all_deferrals': [d.to_dict() for d in deferrals],
    }


def get_deferred_applicants(program_id: int) -> list:
    """
    Obtiene todos los aspirantes diferidos de un programa
    (admission_status='deferred') junto con su diferimiento activo.

    Returns:
        Lista de dicts con user, user_program y deferral info.
    """
    user_programs = (
        UserProgram.query
        .join(User, UserProgram.user_id == User.id)
        .filter(
            UserProgram.program_id == program_id,
            UserProgram.admission_status == 'deferred',
            User.is_active == True,  # noqa: E712
        )
        .order_by(User.last_name.asc())
        .all()
    )

    result = []
    for up in user_programs:
        active_deferral = EnrollmentDeferral.query.filter_by(
            user_program_id=up.id,
            status='active',
        ).first()

        pending_request = EnrollmentDeferral.query.filter_by(
            user_program_id=up.id,
            status='pending',
            requested_by='applicant',
        ).first()

        result.append({
            'user_program': up.to_dict(include_deliberation=True),
            'user': {
                # Handle público: la consola de aceptación lo devuelve en el
                # URL de `.../user/<uuid>/program/<id>/reactivate`.
                'id': public_id_service.public_id(up.user),
                'full_name': (
                    f"{up.user.first_name} {up.user.last_name} "
                    f"{up.user.mother_last_name or ''}"
                ).strip(),
                'email': up.user.email,
            },
            'deferral': active_deferral.to_dict() if active_deferral else None,
            'deferrals_used': _count_active_or_used_deferrals(up.id),
            'can_defer_again': _count_active_or_used_deferrals(up.id) < MAX_DEFERRALS,
        })

    return result


def get_pending_deferral_requests(program_id: int) -> list:
    """
    Obtiene solicitudes de diferimiento pendientes de aprobación
    (iniciadas por el aspirante) para un programa.
    """
    from sqlalchemy import and_

    deferrals = (
        EnrollmentDeferral.query
        .join(UserProgram, EnrollmentDeferral.user_program_id == UserProgram.id)
        .filter(
            and_(
                UserProgram.program_id == program_id,
                EnrollmentDeferral.status == 'pending',
                EnrollmentDeferral.requested_by == 'applicant',
            )
        )
        .order_by(EnrollmentDeferral.created_at.asc())
        .all()
    )

    result = []
    for d in deferrals:
        up = d.user_program
        result.append({
            'deferral': d.to_dict(),
            'user_program': up.to_dict(),
            'user': {
                # Handle público: la consola de aceptación lo devuelve en el
                # URL de `.../user/<uuid>/program/<id>/reactivate`.
                'id': public_id_service.public_id(up.user),
                'full_name': (
                    f"{up.user.first_name} {up.user.last_name} "
                    f"{up.user.mother_last_name or ''}"
                ).strip(),
                'email': up.user.email,
            },
        })

    return result


def check_and_expire_deferrals() -> dict:
    """
    Tarea de mantenimiento: marca como 'expired' los diferimientos cuyo
    periodo diferido ya finalizó y el aspirante sigue en estado 'deferred'.

    Returns:
        {'expired': int, 'notified': int}
    """
    from app.utils.datetime_utils import now_local
    today = now_local().date()

    # Diferimientos activos cuyo periodo ya terminó
    expired_deferrals = (
        EnrollmentDeferral.query
        .join(
            AcademicPeriod,
            EnrollmentDeferral.deferred_to_period_id == AcademicPeriod.id,
        )
        .filter(
            EnrollmentDeferral.status == 'active',
            AcademicPeriod.end_date < today,
        )
        .all()
    )

    expired_count = 0
    for deferral in expired_deferrals:
        deferral.status = 'expired'
        up = deferral.user_program

        # Si aún está en deferred y ya no puede diferir más → expirar proceso
        total_used = _count_active_or_used_deferrals(up.id)
        if total_used >= MAX_DEFERRALS:
            up.admission_status = 'expired'

        NotificationService.create_notification(
            user_id=up.user_id,
            notification_type='deferral_expired',
            title='Tu periodo de diferimiento ha expirado',
            message=(
                f'El periodo {deferral.deferred_to_period.name if deferral.deferred_to_period else ""} '
                f'ha finalizado sin que completaras tu inscripción en {up.program.name}. '
                + (
                    'Tu proceso de admisión ha sido cerrado.'
                    if total_used >= MAX_DEFERRALS
                    else 'Contacta al coordinador para más información.'
                )
            ),
            priority='high',
            action_url='/user/dashboard',
        )

        expired_count += 1

    # Notificaciones de vencimiento próximo (30 días antes)
    from datetime import timedelta
    thirty_days_later = today + timedelta(days=30)

    upcoming_deferrals = (
        EnrollmentDeferral.query
        .join(
            AcademicPeriod,
            EnrollmentDeferral.deferred_to_period_id == AcademicPeriod.id,
        )
        .filter(
            EnrollmentDeferral.status == 'active',
            AcademicPeriod.end_date <= thirty_days_later,
            AcademicPeriod.end_date > today,
            EnrollmentDeferral.expiry_notified_at.is_(None),
        )
        .all()
    )

    notified_count = 0
    for deferral in upcoming_deferrals:
        up = deferral.user_program
        NotificationService.create_notification(
            user_id=up.user_id,
            notification_type='deferral_expiring',
            title='Recuerda completar tu inscripción',
            message=(
                f'Tu periodo diferido ({deferral.deferred_to_period.name if deferral.deferred_to_period else ""}) '
                f'vence pronto. Completa tu inscripción en {up.program.name} '
                f'antes de que expire.'
            ),
            priority='high',
            action_url='/user/dashboard',
        )
        deferral.expiry_notified_at = now_local()
        notified_count += 1

    db.session.commit()
    return {'expired': expired_count, 'notified': notified_count}
