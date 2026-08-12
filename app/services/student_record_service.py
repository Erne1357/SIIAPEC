"""
Student Record (Expediente Completo) Service.

Aggregates everything we know about a student into a single payload:
  - personal info
  - academic info (program, status, control number, scholarships)
  - documents grouped by phase (admission, permanence by semester, conclusion)
  - acceptance documents (carta, tira, dictamen, boleta)
  - semester enrollments history
  - interview (Appointment with type=interview)
  - event participation (attended + upcoming registered)
  - deferrals
  - audit history

Editing personal info validates that the requester has access (program_admin
of student's program OR postgraduate_admin) and produces a UserHistory entry
plus a notification to the student.
"""

from typing import Optional

from app import db
from app.services import public_id_service
from app.models.user import User
from app.models.user_program import UserProgram
from app.models.acceptance_document import AcceptanceDocument
from app.models.semester_enrollment import SemesterEnrollment
from app.models.appointment import Appointment
from app.models.event import Event, EventAttendance
from app.models.enrollment_deferral import EnrollmentDeferral
from app.models.user_history import UserHistory
from app.services.notification_service import NotificationService
from app.services.user_history_service import UserHistoryService
from app.services import file_access_service
from app.services import profile_activity_service
from app.services import program_scope_service
from app.utils.datetime_utils import now_local


# Whitelist of fields editable by the coordinator
EDITABLE_PERSONAL_FIELDS = {
    'phone', 'mobile_phone', 'address',
    'curp', 'rfc', 'nss', 'cedula_profesional',
    'birth_date', 'birth_place',
    'emergency_contact_name', 'emergency_contact_phone',
    'emergency_contact_relationship',
}


#: The ONE message every caller must answer with when a record cannot be
#: served, whatever the reason. `StudentNotFound` and `AccessDenied` stay two
#: distinct exceptions so the OPERATOR can tell them apart in the log, but they
#: must never be distinguishable from outside: answering 404 for "no such user"
#: and 403 for "exists, not yours" turns `/students/<id>/record` into a census
#: of which user ids exist. Every other module collapses the pair the same way
#: (acceptance_api, invitations_api, appointments_api, program_changes_api,
#: files_api) — this one was the odd one out.
RECORD_NOT_FOUND_MESSAGE = 'Expediente no encontrado.'


class StudentRecordError(Exception):
    """Base of the two outcomes that collapse into a single 404 response."""
    pass


class StudentNotFound(StudentRecordError):
    """No user with that id. For the LOG only — never for the response."""
    pass


class AccessDenied(StudentRecordError):
    """The user exists but is outside the requester's scope. LOG only."""
    pass


def _can_view_record(requester: User, target: User) -> bool:
    """
    True if the requester can view target's FULL record:
      - target == requester → always
      - holds 'students.api.view_record' AND the target is inside the
        requester's program scope (postgraduate_admin = every program).

    The scope half is delegated to `program_scope_service.user_in_scope`, the
    single shared predicate — do not re-implement the intersection here.
    """
    if requester.id == target.id:
        return True
    if not requester.has_permission('students.api.view_record'):
        return False
    return program_scope_service.user_in_scope(requester, target, allow_self=False)


def load_record_target(user_id: int, requester: User) -> User:
    """
    Resolve the student whose record is being opened, enforcing access.

    Single gate for both the API and the page route, so neither has to reach
    for `_can_view_record` or query `User` on its own.

    Raises:
        StudentNotFound — no user with that id.
        AccessDenied    — the user exists but is outside the requester's scope.

    The two are for the log ONLY: the caller must answer both with the same
    404 and `RECORD_NOT_FOUND_MESSAGE`.
    """
    # `user_id` puede llegar como None cuando la ruta no pudo resolver el UUID
    # público: es exactamente el mismo caso que "no existe" y sale por la misma
    # excepción, que el llamador traduce al 404 compartido.
    user = db.session.get(User, user_id) if user_id is not None else None
    if not user:
        raise StudentNotFound("Usuario no encontrado")

    if not _can_view_record(requester, user):
        raise AccessDenied(
            f"El usuario {user_id} está fuera del alcance del solicitante."
        )
    return user


def get_full_record(user_id: int, requester: User) -> dict:
    user = load_record_target(user_id, requester)

    user_programs = list(user.user_program or [])
    primary_up: Optional[UserProgram] = user_programs[0] if user_programs else None

    return {
        'user': _user_dict(user),
        'programs': [_program_dict(up) for up in user_programs],
        'primary_program_id': primary_up.program_id if primary_up else None,
        'acceptance_documents': _acceptance_docs(user_programs),
        'documents_by_phase': profile_activity_service.get_user_documents_grouped(user.id),
        'semester_enrollments': _semester_enrollments(user_programs),
        'interview': _interview_info(user.id),
        'events_attended': _events_attended(user.id),
        'upcoming_events': profile_activity_service.get_upcoming_events(user.id, limit=10),
        'deferrals': _deferrals(user_programs),
        'history': _history(user.id, limit=100),
        'editable_fields': sorted(EDITABLE_PERSONAL_FIELDS),
    }


def update_personal_info(user_id: int, coordinator_id: int, data: dict) -> User:
    """
    Coordinator updates whitelisted personal fields of a student.
    Logs every changed field and notifies the student once.
    """
    requester = db.session.get(User, coordinator_id)
    if not requester:
        raise AccessDenied(f"Solicitante {coordinator_id} no encontrado.")

    user = load_record_target(user_id, requester)

    changed = {}
    for field, new_value in (data or {}).items():
        if field not in EDITABLE_PERSONAL_FIELDS:
            continue
        old_value = getattr(user, field)

        if field == 'birth_date' and isinstance(new_value, str) and new_value:
            from datetime import date
            try:
                y, m, d = new_value.split('-')
                new_value = date(int(y), int(m), int(d))
            except (ValueError, AttributeError):
                continue

        if isinstance(new_value, str):
            new_value = new_value.strip() or None

        if old_value != new_value:
            setattr(user, field, new_value)
            changed[field] = {'from': str(old_value) if old_value else None,
                              'to': str(new_value) if new_value else None}

    if not changed:
        return user

    user.update_profile_completion_status()

    UserHistoryService.log_action(
        user_id=user.id,
        admin_id=coordinator_id,
        action='personal_info_updated',
        details=f'Coordinador actualizó {len(changed)} campo(s): {", ".join(changed.keys())}',
    )

    NotificationService.create_notification(
        user_id=user.id,
        notification_type='personal_info_updated',
        title='Tu información personal fue actualizada',
        message=(
            f'Un coordinador actualizó {len(changed)} campo(s) de tu información personal: '
            f'{", ".join(sorted(changed.keys()))}.'
        ),
        priority='normal',
        action_url='/user/profile',
    )

    db.session.commit()
    return user


# ─── Internal helpers ────────────────────────────────────────────────────────

def _user_dict(user: User) -> dict:
    return {
        # Public handle, never the integer primary key.
        'id': str(user.uuid) if user.uuid else None,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'mother_last_name': user.mother_last_name,
        'username': user.username,
        'email': user.email,
        'avatar_url': user.avatar_url,
        'role': user.role.name if user.role else None,
        'is_internal': user.is_internal,
        'is_active': user.is_active,
        'control_number': user.control_number,
        'control_number_assigned_at': (
            user.control_number_assigned_at.isoformat()
            if user.control_number_assigned_at else None
        ),
        'registration_date': user.registration_date.isoformat() if user.registration_date else None,
        'last_login': user.last_login.isoformat() if user.last_login else None,
        'profile_completed': user.profile_completed,
        # Personal
        'phone': user.phone,
        'mobile_phone': user.mobile_phone,
        'address': user.address,
        'curp': user.curp,
        'rfc': user.rfc,
        'birth_date': user.birth_date.isoformat() if user.birth_date else None,
        'birth_place': user.birth_place,
        'cedula_profesional': user.cedula_profesional,
        'nss': user.nss,
        'emergency_contact_name': user.emergency_contact_name,
        'emergency_contact_phone': user.emergency_contact_phone,
        'emergency_contact_relationship': user.emergency_contact_relationship,
        # Photo flags
        'photo_change_allowed': user.photo_change_allowed,
        'photo_change_requested_at': (
            user.photo_change_requested_at.isoformat()
            if user.photo_change_requested_at else None
        ),
    }


def _program_dict(up: UserProgram) -> dict:
    p = up.program
    return {
        'user_program_id': up.id,
        'program_id': up.program_id,
        'program_name': p.name if p else None,
        'program_slug': p.slug if p else None,
        'admission_status': up.admission_status,
        'admission_period_id': up.admission_period_id,
        'admission_period_name': (
            up.admission_period.name if getattr(up, 'admission_period', None) else None
        ),
        'current_semester': up.current_semester,
        'enrollment_date': up.enrollment_date.isoformat() if up.enrollment_date else None,
        'has_conacyt_scholarship': getattr(up, 'has_conacyt_scholarship', False),
    }


def _acceptance_docs(user_programs: list) -> list:
    if not user_programs:
        return []
    up_ids = [up.id for up in user_programs]
    docs = (
        AcceptanceDocument.query
        .filter(AcceptanceDocument.user_program_id.in_(up_ids))
        .order_by(AcceptanceDocument.uploaded_at.desc())
        .all()
    )
    # `to_dict()` ya trae `file_url` opaco; se repite la clave aquí sólo para
    # que este proyector siga siendo explícito sobre lo que publica.
    return [
        {**d.to_dict(),
         'file_url': file_access_service.acceptance_document_url(d)}
        for d in docs
    ]


def _semester_enrollments(user_programs: list) -> list:
    if not user_programs:
        return []
    up_ids = [up.id for up in user_programs]
    enrollments = (
        SemesterEnrollment.query
        .filter(SemesterEnrollment.user_program_id.in_(up_ids))
        .order_by(SemesterEnrollment.semester_number.asc())
        .all()
    )
    out = []
    for se in enrollments:
        item = {
            'id': se.id,
            'user_program_id': se.user_program_id,
            'academic_period_id': se.academic_period_id,
            'academic_period_name': (
                se.academic_period.name if getattr(se, 'academic_period', None) else None
            ),
            'semester_number': se.semester_number,
            'status': se.status,
            'enrollment_confirmed': se.enrollment_confirmed,
            'confirmed_at': se.confirmed_at.isoformat() if se.confirmed_at else None,
            'confirmed_by': public_id_service.uuid_for(User, se.confirmed_by),
            'notes': se.notes,
            # Sólo el basename humano; la ruta almacenada empieza por
            # `<user_id>/`. Los `_url` nombran la fila + el slot.
            'payment_proof_path': file_access_service.document_download_name(
                getattr(se, 'payment_proof_path', None)),
            'payment_proof_url': file_access_service.enrollment_payment_proof_url(se),
            'schedule_path': file_access_service.document_download_name(
                getattr(se, 'schedule_path', None)),
            'schedule_url': file_access_service.enrollment_schedule_url(se),
        }
        out.append(item)
    return out


def _interview_info(user_id: int) -> dict | None:
    appt = (
        db.session.query(Appointment, Event)
        .join(Event, Appointment.event_id == Event.id)
        .filter(
            Appointment.applicant_id == user_id,
            Event.type == 'interview',
        )
        .order_by(Appointment.created_at.desc())
        .first()
    )
    if not appt:
        return None
    a, ev = appt
    interviewer = User.query.get(ev.created_by) if ev else None
    return {
        'appointment_id': a.id,
        'event_id': ev.id if ev else None,
        'event_title': ev.title if ev else None,
        'event_date': ev.event_date.isoformat() if ev and ev.event_date else None,
        'status': a.status,
        'notes': a.notes,
        'created_at': a.created_at.isoformat() if a.created_at else None,
        'interviewer': (
            {
                # Public handle, never the integer primary key.
                'id': str(interviewer.uuid) if interviewer.uuid else None,
                'name': f"{interviewer.first_name} {interviewer.last_name}",
                'email': interviewer.email,
            } if interviewer else None
        ),
    }


def _events_attended(user_id: int) -> list:
    rows = (
        db.session.query(EventAttendance, Event)
        .join(Event, EventAttendance.event_id == Event.id)
        .filter(EventAttendance.user_id == user_id)
        .order_by(EventAttendance.registered_at.desc())
        .all()
    )
    return [
        {
            'event_id': ev.id,
            'title': ev.title,
            'type': ev.type,
            'event_date': ev.event_date.isoformat() if ev.event_date else None,
            'status': att.status,
            'registered_at': att.registered_at.isoformat() if att.registered_at else None,
            'attended_at': att.attended_at.isoformat() if att.attended_at else None,
        }
        for att, ev in rows
    ]


def _deferrals(user_programs: list) -> list:
    if not user_programs:
        return []
    up_ids = [up.id for up in user_programs]
    items = (
        EnrollmentDeferral.query
        .filter(EnrollmentDeferral.user_program_id.in_(up_ids))
        .order_by(EnrollmentDeferral.created_at.desc())
        .all()
    )
    return [d.to_dict() for d in items]


def _history(user_id: int, limit: int = 100) -> list:
    items = (
        UserHistory.query
        .filter_by(user_id=user_id)
        .order_by(UserHistory.timestamp.desc())
        .limit(limit)
        .all()
    )
    return [h.to_dict() for h in items]
