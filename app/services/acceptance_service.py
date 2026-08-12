# app/services/acceptance_service.py
"""
Servicio para gestionar los documentos de aceptacion e inscripcion.

Flujo:
1. Coordinador acepta aspirante (deliberation_service.accept_applicant)
2. Coordinador sube carta de aceptacion y tira de materias
3. Aspirante descarga sus documentos
4. Aspirante sube boleta de servicios escolares
5. Coordinador revisa y aprueba/rechaza la boleta
6. Una vez aprobada, se puede proceder a la transicion a estudiante (Fase 5)
"""

from app import db
from app.models import UserProgram, User, Program, ExtensionRequest, Submission, ProgramStep
from app.models.acceptance_document import AcceptanceDocument
from app.services.notification_service import NotificationService
from app.services.user_history_service import UserHistoryService
from app.utils.files import save_user_doc
from app.utils.datetime_utils import now_local, to_local_timezone
from sqlalchemy import and_


VALID_DOC_TYPES = {'acceptance_letter', 'course_schedule', 'enrollment_receipt', 'acceptance_opinion'}
COORDINATOR_DOC_TYPES = {'acceptance_letter', 'course_schedule', 'acceptance_opinion'}
APPLICANT_DOC_TYPES = {'enrollment_receipt'}

DOC_TYPE_LABELS = {
    'acceptance_letter': 'Carta de Aceptacion',
    'course_schedule': 'Tira de Materias',
    'enrollment_receipt': 'Boleta de Servicios Escolares',
    'acceptance_opinion': 'Dictamen de Aceptacion',
}

#: The single wording every control-number rejection uses when the number cannot
#: be assigned because it already exists somewhere in the institution.
#:
#: The uniqueness check itself is global and STAYS global — a control number is
#: institution-wide unique, and scoping the query to the caller's programs would
#: let two coordinators mint the same number. What must not be global is the
#: *answer*: the old message ("ya está asignado a otro usuario") confirmed the
#: existence of a control number the caller may have no right to know about,
#: which is the same secret `CROSS_PROGRAM_SEARCHABLE_FIELDS` keeps out of the
#: student search. Anyone holding an applicant of their own could probe the
#: institution's control numbers one guess per request and read the hits off the
#: message.
#:
#: The wording below states only that this request cannot proceed. It carries no
#: claim about whether the number exists, no owner, and — deliberately — not even
#: the number itself, so that a screenshot or a shared error log leaks nothing.
#: Every site that rejects on collision must use this exact string; a second,
#: subtly different wording anywhere re-opens the oracle.
#:
#: This does not make the two branches indistinguishable — a free number is
#: assigned and a taken one is not, and that difference is inherent to a real
#: write against a unique column. What it removes is the explicit confirmation
#: and the ability to harvest it passively. The remaining probing is a loud,
#: authenticated, attributable act; see the note at the call site in
#: `app/routes/api/admin/users_api.py`.
CONTROL_NUMBER_REJECTED_MESSAGE = (
    'No se pudo asignar el número de control. '
    'Verifica el dato con la jefatura de posgrado e inténtalo de nuevo.'
)


class AcceptanceError(Exception):
    """Error base para operaciones de aceptacion."""
    pass


class ApplicantNotFound(AcceptanceError):
    pass


class InvalidDocumentType(AcceptanceError):
    pass


class InvalidStateTransition(AcceptanceError):
    pass


class DocumentDebtError(AcceptanceError):
    """
    Hay una o más prórrogas vencidas con documentos sin entregar.
    El coordinador debe resolver la deuda antes de asignar el número de control.
    """
    def __init__(self, debts: list):
        self.debts = debts  # lista de {'archive_name': str, 'expired_at': str}
        super().__init__("Existen documentos con prórroga vencida sin entregar.")


def _get_user_program(user_id: int, program_id: int) -> UserProgram:
    up = UserProgram.query.filter_by(user_id=user_id, program_id=program_id).first()
    if not up:
        raise ApplicantNotFound(f"No se encontro al aspirante {user_id} en el programa {program_id}")
    return up


def _get_user_program_by_id(user_program_id: int) -> UserProgram:
    up = UserProgram.query.get(user_program_id)
    if not up:
        raise ApplicantNotFound(f"No se encontro UserProgram {user_program_id}")
    return up


def _get_or_create_doc(user_program_id: int, document_type: str) -> AcceptanceDocument:
    """Obtiene o crea un AcceptanceDocument para un user_program y tipo de documento."""
    doc = AcceptanceDocument.query.filter_by(
        user_program_id=user_program_id,
        document_type=document_type
    ).first()

    if not doc:
        doc = AcceptanceDocument(
            user_program_id=user_program_id,
            document_type=document_type,
            status='pending'
        )
        db.session.add(doc)

    return doc


def get_acceptance_status(user_program_id: int) -> dict:
    """
    Retorna el estado de los 3 documentos de aceptacion para un UserProgram.

    Returns:
        Dict con doc_type -> {doc_id, status, file_path, uploaded_at, review_notes}
    """
    up = _get_user_program_by_id(user_program_id)

    result = {}
    for doc_type in VALID_DOC_TYPES:
        doc = AcceptanceDocument.query.filter_by(
            user_program_id=user_program_id,
            document_type=doc_type
        ).first()

        if doc:
            result[doc_type] = doc.to_dict()
        else:
            result[doc_type] = {
                'id': None,
                'document_type': doc_type,
                'status': 'pending',
                'file_path': None,
                'uploaded_at': None,
                'review_notes': None,
            }

    return result


def get_accepted_applicants(program_id: int):
    """
    Obtiene todos los aspirantes aceptados de un programa con su estado de documentos.

    Returns:
        Lista de dicts con user, user_program y acceptance_docs
    """
    user_programs = UserProgram.query.join(
        User, UserProgram.user_id == User.id
    ).filter(
        and_(
            UserProgram.program_id == program_id,
            UserProgram.admission_status == 'accepted',
            User.is_active == True,  # noqa: E712 — excluir cuentas desactivadas
        )
    ).order_by(UserProgram.decision_at.desc()).all()

    result = []
    for up in user_programs:
        user = up.user
        docs = {}
        for doc_type in VALID_DOC_TYPES:
            doc = AcceptanceDocument.query.filter_by(
                user_program_id=up.id,
                document_type=doc_type
            ).first()
            docs[doc_type] = doc.to_dict() if doc else {
                'id': None, 'document_type': doc_type, 'status': 'pending',
                'file_path': None, 'uploaded_at': None, 'review_notes': None
            }

        result.append({
            'user_program': up.to_dict(include_deliberation=True),
            'user': {
                'id': user.id,
                'full_name': f"{user.first_name} {user.last_name} {user.mother_last_name or ''}".strip(),
                'email': user.email,
                'curp': user.curp,
                'control_number': user.control_number,
            },
            'acceptance_docs': docs,
        })

    return result


def get_acceptance_stats(program_id: int) -> dict:
    """
    Estadisticas de documentos de aceptacion para un programa.

    Returns:
        Dict con conteos: pending_docs, receipt_submitted, completed
    """
    accepted = UserProgram.query.filter_by(
        program_id=program_id,
        admission_status='accepted'
    ).all()

    pending_docs = 0
    receipt_submitted = 0
    completed = 0

    for up in accepted:
        letter = AcceptanceDocument.query.filter_by(
            user_program_id=up.id, document_type='acceptance_letter'
        ).first()
        schedule = AcceptanceDocument.query.filter_by(
            user_program_id=up.id, document_type='course_schedule'
        ).first()
        receipt = AcceptanceDocument.query.filter_by(
            user_program_id=up.id, document_type='enrollment_receipt'
        ).first()

        letter_ok = letter and letter.status in ('uploaded', 'approved')
        schedule_ok = schedule and schedule.status in ('uploaded', 'approved')
        receipt_uploaded = receipt and receipt.status in ('uploaded',)
        receipt_approved = receipt and receipt.status == 'approved'

        if not letter_ok or not schedule_ok:
            pending_docs += 1
        elif receipt_uploaded:
            receipt_submitted += 1
        elif receipt_approved:
            completed += 1
        else:
            # Tiene carta y tira pero no ha subido boleta
            pending_docs += 1

    return {
        'total_accepted': len(accepted),
        'pending_docs': pending_docs,
        'receipt_submitted': receipt_submitted,
        'completed': completed,
    }


def upload_coordinator_doc(user_id: int, program_id: int, document_type: str,
                           file_storage, coordinator_id: int) -> AcceptanceDocument:
    """
    El coordinador sube un documento de aceptacion (carta o tira de materias) para el aspirante.

    Args:
        user_id: ID del aspirante
        program_id: ID del programa
        document_type: 'acceptance_letter' o 'course_schedule'
        file_storage: Objeto FileStorage de Flask
        coordinator_id: ID del coordinador que sube el documento

    Returns:
        AcceptanceDocument actualizado
    """
    if document_type not in COORDINATOR_DOC_TYPES:
        raise InvalidDocumentType(
            f"Tipo invalido para coordinador: {document_type}. "
            f"Use: {', '.join(COORDINATOR_DOC_TYPES)}"
        )

    up = _get_user_program(user_id, program_id)

    if up.admission_status != 'accepted':
        raise InvalidStateTransition(
            f"El aspirante debe estar en estado 'accepted', estado actual: '{up.admission_status}'"
        )

    # Guardar archivo
    doc_label = DOC_TYPE_LABELS[document_type].replace(' ', '_').lower()
    file_path = save_user_doc(file_storage, user_id, 'acceptance', doc_label)

    # Crear o actualizar el registro del documento
    doc = _get_or_create_doc(up.id, document_type)
    was_pending = doc.status != 'uploaded'  # False si ya estaba subido (re-subida)
    doc.file_path = file_path
    doc.uploaded_by_id = coordinator_id
    doc.uploaded_at = now_local()
    doc.status = 'uploaded'

    program = Program.query.get(program_id)
    # Pinned to APP_BASE_URL: this URL is embedded in an e-mail body, so it must
    # not be rebuilt from the live request host (the requester controls `Host`).
    from app.utils.urls import external_url, get_base_url
    try:
        dashboard_url = external_url('pages_user.dashboard')
    except Exception:
        # Only reachable without an application context, which neither a route
        # nor a Celery ContextTask can be. Stay absolute anyway.
        dashboard_url = f"{get_base_url()}/user/dashboard"

    # Notificación individual por documento (solo en upload nuevo, no en re-subida)
    if was_pending:
        NotificationService.notify_acceptance_doc_uploaded(
            user_id=user_id,
            program_name=program.name,
            document_type=document_type,
            document_label=DOC_TYPE_LABELS[document_type],
            dashboard_url=dashboard_url,
        )
        UserHistoryService.log_action(
            user_id=user_id,
            admin_id=coordinator_id,
            action=f'acceptance_{document_type}_uploaded',
            details=f'{DOC_TYPE_LABELS[document_type]} subida para {program.name}'
        )

    # Verificar si con este documento se completaron ambos (carta + tira)
    letter = AcceptanceDocument.query.filter_by(user_program_id=up.id, document_type='acceptance_letter').first()
    schedule = AcceptanceDocument.query.filter_by(user_program_id=up.id, document_type='course_schedule').first()
    letter_ok = (letter and letter.status in ('uploaded', 'approved')) or document_type == 'acceptance_letter'
    schedule_ok = (schedule and schedule.status in ('uploaded', 'approved')) or document_type == 'course_schedule'

    # Notificación de "documentos completos" solo cuando AMBOS recién están listos
    if letter_ok and schedule_ok and was_pending:
        NotificationService.notify_acceptance_docs_ready(
            user_id=user_id,
            program_name=program.name,
            dashboard_url=dashboard_url,
        )
        UserHistoryService.log_action(
            user_id=user_id,
            admin_id=coordinator_id,
            action='acceptance_docs_uploaded',
            details=f'Carta de aceptación y tira de materias subidas para {program.name}'
        )

    # WebSocket: actualizar pestaña de aceptación del aspirante en tiempo real
    try:
        from app.extensions import socketio
        socketio.emit('acceptance:updated', {
            'user_id': user_id,
            'program_id': program_id,
            'action': f'{document_type}_uploaded',
            'document_type': document_type,
        }, room=f'user:{user_id}')
        # También notificar a coordinadores del programa para que refresquen sus tablas
        socketio.emit('acceptance:updated', {
            'user_id': user_id,
            'program_id': program_id,
            'action': f'{document_type}_uploaded',
            'document_type': document_type,
        }, room=f'coordinator:program:{program_id}')
    except Exception:
        pass

    db.session.commit()

    return doc


def submit_enrollment_receipt(user_id: int, program_id: int,
                               file_storage, aspirant_id: int) -> AcceptanceDocument:
    """
    El aspirante sube su boleta de servicios escolares.

    Args:
        user_id: ID del aspirante (debe coincidir con aspirant_id)
        program_id: ID del programa
        file_storage: Objeto FileStorage de Flask
        aspirant_id: ID del usuario que hace la peticion (debe ser el mismo aspirante)

    Returns:
        AcceptanceDocument actualizado
    """
    up = _get_user_program(user_id, program_id)

    if up.admission_status != 'accepted':
        raise InvalidStateTransition(
            f"Solo aspirantes aceptados pueden subir boleta, estado actual: '{up.admission_status}'"
        )

    # Verificar que el coordinador ya subio los documentos previos
    letter = AcceptanceDocument.query.filter_by(user_program_id=up.id, document_type='acceptance_letter').first()
    schedule = AcceptanceDocument.query.filter_by(user_program_id=up.id, document_type='course_schedule').first()

    if not (letter and letter.status == 'uploaded') or not (schedule and schedule.status == 'uploaded'):
        raise InvalidStateTransition(
            "El coordinador aun no ha subido la carta de aceptacion y tira de materias"
        )

    # Guardar archivo
    file_path = save_user_doc(file_storage, user_id, 'acceptance', 'boleta_servicios_escolares')

    doc = _get_or_create_doc(up.id, 'enrollment_receipt')

    # Si ya habia una boleta rechazada, permite resubir
    if doc.status not in ('pending', 'rejected'):
        raise InvalidStateTransition(
            f"Ya hay una boleta en estado '{doc.status}'. No se puede reemplazar."
        )

    doc.file_path = file_path
    doc.uploaded_by_id = aspirant_id
    doc.uploaded_at = now_local()
    doc.status = 'uploaded'
    doc.reviewed_by_id = None
    doc.reviewed_at = None
    doc.review_notes = None

    program = Program.query.get(program_id)

    UserHistoryService.log_action(
        user_id=user_id,
        admin_id=aspirant_id,
        action='enrollment_receipt_submitted',
        details=f'Carta de asignación de número de control subida para {program.name}'
    )

    # Notificar al coordinador del programa
    if program.coordinator_id:
        user = User.query.get(user_id)
        student_name = f"{user.first_name} {user.last_name}" if user else f"Usuario {user_id}"
        NotificationService.create_notification(
            user_id=program.coordinator_id,
            notification_type='enrollment_receipt_submitted',
            title='Carta de número de control recibida',
            message=f'{student_name} ha subido su carta de asignación de número de control para {program.name}.',
            priority='medium',
            action_url='/coordinator/acceptance',
            data={'student_id': user_id, 'program_id': program_id},
        )

    # WebSocket: actualizar pestaña de aceptación del coordinador
    try:
        # Igual que los otros emits de aceptación de este servicio: la sala
        # program-scoped, no `role:coordinator` (que es toda la institución).
        from app.sockets.emitters import emit_to_coordinators
        emit_to_coordinators('acceptance:updated', {
            'user_id': user_id,
            'program_id': program_id,
            'action': 'receipt_submitted',
        }, program_id)
    except Exception:
        pass

    db.session.commit()

    return doc


def review_enrollment_receipt(doc_id: int, coordinator_id: int,
                               status: str, notes: str = None) -> AcceptanceDocument:
    """
    El coordinador revisa y aprueba o rechaza la boleta del aspirante.

    Args:
        doc_id: ID del AcceptanceDocument de tipo enrollment_receipt
        coordinator_id: ID del coordinador
        status: 'approved' o 'rejected'
        notes: Notas de revision (requerido si es rejected)

    Returns:
        AcceptanceDocument actualizado
    """
    if status not in ('approved', 'rejected'):
        raise ValueError("status debe ser 'approved' o 'rejected'")

    doc = AcceptanceDocument.query.get(doc_id)
    if not doc:
        raise ApplicantNotFound(f"Documento {doc_id} no encontrado")

    if doc.document_type != 'enrollment_receipt':
        raise InvalidDocumentType("Solo se puede revisar documentos de tipo enrollment_receipt")

    if doc.status != 'uploaded':
        raise InvalidStateTransition(
            f"Solo se pueden revisar documentos en estado 'uploaded', estado actual: '{doc.status}'"
        )

    doc.status = status
    doc.reviewed_by_id = coordinator_id
    doc.reviewed_at = now_local()
    doc.review_notes = notes

    up = doc.user_program
    user_id = up.user_id
    program = up.program

    # Notificar e historiar (en la misma transaccion)
    if status == 'approved':
        NotificationService.create_notification(
            user_id=user_id,
            notification_type='enrollment_receipt_approved',
            title='Carta de número de control aprobada',
            message=f'Tu carta de asignación de número de control para {program.name} fue aprobada. '
                    f'El coordinador te asignará tu número de control próximamente.',
            priority='high',
            action_url='/user/dashboard',
        )
        UserHistoryService.log_action(
            user_id=user_id,
            admin_id=coordinator_id,
            action='enrollment_receipt_approved',
            details=f'Carta aprobada para {program.name}. {notes or ""}'
        )
    else:
        NotificationService.create_notification(
            user_id=user_id,
            notification_type='enrollment_receipt_rejected',
            title='Carta de número de control rechazada',
            message=f'Tu carta de asignación de número de control para {program.name} fue rechazada. '
                    f'Motivo: {notes or "Sin especificar"}. Por favor vuelve a subirla.',
            priority='high',
            action_url='/user/dashboard',
        )
        UserHistoryService.log_action(
            user_id=user_id,
            admin_id=coordinator_id,
            action='enrollment_receipt_rejected',
            details=f'Carta rechazada para {program.name}. {notes or ""}'
        )

    # Enviar email al aspirante
    try:
        from app.services.email_service import EmailService
        from app.services.email_templates import EmailTemplates
        from app.utils.urls import external_url
        user = User.query.get(user_id)
        if user:
            # Pinned to APP_BASE_URL, never to the request host.
            dashboard_url = external_url('pages_user.dashboard')
            if status == 'approved':
                subject, html = EmailTemplates.enrollment_receipt_approved(
                    user_name=f"{user.first_name} {user.last_name}",
                    program_name=program.name,
                    dashboard_url=dashboard_url,
                )
            else:
                subject, html = EmailTemplates.enrollment_receipt_rejected(
                    user_name=f"{user.first_name} {user.last_name}",
                    program_name=program.name,
                    reason=notes or "Sin especificar",
                    dashboard_url=dashboard_url,
                )
            EmailService.queue_email(user_id, subject, html)
    except Exception as e:
        import logging
        logging.error(f"Error queueing email for enrollment_receipt_{status}: {e}")

    # WebSocket: actualizar pestaña de aceptación del aspirante
    try:
        from app.extensions import socketio
        socketio.emit('acceptance:updated', {
            'user_id': user_id,
            'program_id': up.program_id,
            'action': f'receipt_{status}',
        }, room=f'user:{user_id}')
    except Exception:
        pass

    db.session.commit()

    return doc


def delete_coordinator_doc(doc_id: int, coordinator_id: int) -> None:
    """
    Elimina un documento de aceptacion subido por el coordinador (carta o tira).
    Util para reemplazar un documento incorrecto.
    """
    doc = AcceptanceDocument.query.get(doc_id)
    if not doc:
        raise ApplicantNotFound(f"Documento {doc_id} no encontrado")

    if doc.document_type not in COORDINATOR_DOC_TYPES:
        raise InvalidDocumentType("Solo se pueden eliminar carta de aceptacion o tira de materias")

    user_id = doc.user_program.user_id
    program = doc.user_program.program
    doc_label = DOC_TYPE_LABELS[doc.document_type]

    # Registrar antes de eliminar (en la misma transaccion)
    UserHistoryService.log_action(
        user_id=user_id,
        admin_id=coordinator_id,
        action='acceptance_doc_deleted',
        details=f'{doc_label} eliminado de {program.name}'
    )

    db.session.delete(doc)
    db.session.commit()


def _check_document_debt(user_id: int, program_id: int) -> None:
    """
    Detecta prórrogas vencidas cuyo documento nunca fue entregado ni aprobado.
    Si encuentra alguna, lanza DocumentDebtError con el detalle de cada deuda.
    """
    now = now_local()

    # Extensiones granted para este usuario en este programa
    granted_exts = (
        ExtensionRequest.query
        .join(ProgramStep, ExtensionRequest.program_step_id == ProgramStep.id)
        .filter(
            ExtensionRequest.user_id == user_id,
            ExtensionRequest.status == 'granted',
            ExtensionRequest.granted_until.isnot(None),
            ProgramStep.program_id == program_id,
        )
        .all()
    )

    debts = []
    for ext in granted_exts:
        granted_until = to_local_timezone(ext.granted_until)
        if granted_until >= now:
            continue  # prórroga todavía vigente

        # Verificar si hay submission válida para este archivo (aprobada o en revisión)
        sub = Submission.query.filter(
            Submission.user_id == user_id,
            Submission.archive_id == ext.archive_id,
            Submission.program_step_id == ext.program_step_id,
            Submission.status.in_(['approved', 'review']),
        ).first()

        if not sub:
            debts.append({
                'archive_name': ext.archive.name if ext.archive else f'Archivo #{ext.archive_id}',
                'expired_at': granted_until.strftime('%d/%m/%Y %H:%M'),
            })

    if debts:
        raise DocumentDebtError(debts=debts)


def assign_control_number(user_id: int, program_id: int,
                           control_number: str, coordinator_id: int) -> UserProgram:
    """
    El coordinador asigna un número de control al aspirante aceptado.
    Completa la transición: número de control + rol 'student' + admission_status='enrolled'.
    Requiere que la boleta/carta de asignación ya esté aprobada.

    Args:
        user_id: ID del aspirante
        program_id: ID del programa
        control_number: Número de control a asignar (ej: M21111182)
        coordinator_id: ID del coordinador que asigna

    Returns:
        UserProgram actualizado
    """
    from app.models.user import User as UserModel
    from app.models.role import Role
    from app.models.academic_period import AcademicPeriod
    from app.models.semester_enrollment import SemesterEnrollment

    if not control_number or not control_number.strip():
        raise ValueError("El número de control no puede estar vacío")

    ctrl = control_number.strip()

    # Unicidad institucional: la consulta es global a propósito (un número de
    # control es único en toda la institución). El mensaje NO revela el motivo
    # —ver CONTROL_NUMBER_REJECTED_MESSAGE—, porque el número colisionado puede
    # pertenecer a un programa fuera del alcance de quien llama.
    existing = UserModel.query.filter_by(control_number=ctrl).first()
    if existing and existing.id != user_id:
        raise ValueError(CONTROL_NUMBER_REJECTED_MESSAGE)

    up = _get_user_program(user_id, program_id)

    if up.admission_status != 'accepted':
        raise InvalidStateTransition(
            f"El aspirante debe estar en estado 'accepted', estado actual: '{up.admission_status}'"
        )

    receipt = AcceptanceDocument.query.filter_by(
        user_program_id=up.id, document_type='enrollment_receipt'
    ).first()

    if not receipt or receipt.status != 'approved':
        raise InvalidStateTransition(
            "La carta de asignación de número de control debe estar aprobada antes de asignar el número de control"
        )

    # Verificar deuda documental: prórrogas vencidas con doc sin entregar/aprobar
    _check_document_debt(user_id, program_id)

    student_role = Role.query.filter_by(name='student').first()
    if not student_role:
        raise ValueError("El rol 'student' no está configurado en el sistema. Ejecuta las migraciones.")

    user = UserModel.query.get(user_id)
    if not user:
        raise ApplicantNotFound(f"Usuario {user_id} no encontrado")

    program = Program.query.get(program_id)

    # 1. Asignar número de control (también actualiza username)
    user.assign_control_number(ctrl)

    # 2. Cambiar rol a 'student'
    user.role_id = student_role.id

    # 3. Actualizar estado de inscripción
    up.admission_status = 'enrolled'
    up.current_semester = 1

    # 3b. Crear SemesterEnrollment para semestre 1 ya confirmado.
    #     El pago del primer semestre se verifica implícitamente al aprobar la
    #     carta de asignación de número de control, por lo que no se requiere
    #     una confirmación adicional por parte del coordinador.
    
    # Usar el periodo de admisión original, o fallback al activo si no existe
    enrollment_period_id = up.admission_period_id
    if not enrollment_period_id:
        active_period = AcademicPeriod.get_active_period()
        if active_period:
            enrollment_period_id = active_period.id

    if enrollment_period_id:
        first_semester_enrollment = SemesterEnrollment(
            user_program_id=up.id,
            academic_period_id=enrollment_period_id,
            semester_number=1,
            status='active',
            enrollment_confirmed=True,
            confirmed_by=coordinator_id,
            confirmed_at=now_local(),
        )
        db.session.add(first_semester_enrollment)

    # 4. Historial y notificación (antes del commit)
    UserHistoryService.log_action(
        user_id=user_id,
        admin_id=coordinator_id,
        action='control_number_assigned',
        details=f'Número de control {ctrl} asignado en {program.name}. Transición a estudiante completada.'
    )

    NotificationService.notify_control_number_assigned(
        user_id=user_id,
        control_number=ctrl,
    )

    # WebSocket: actualizar pestaña de aceptación del aspirante
    try:
        from app.extensions import socketio
        socketio.emit('acceptance:updated', {
            'user_id': user_id,
            'program_id': program_id,
            'action': 'control_number_assigned',
        }, room=f'user:{user_id}')
    except Exception:
        pass

    db.session.commit()

    return up


def assign_control_number_admin(user_id: int, program_id: int,
                                 control_number: str, admin_id: int) -> UserProgram:
    """
    Variante administrativa de assign_control_number sin los chequeos rigurosos
    de flujo (admission_status='accepted', enrollment_receipt approved, deuda
    documental). Sirve para casos legacy donde el estudiante ya está cursando
    pero nunca pasó por el flujo formal de aceptación.

    SÍ hace toda la transición:
      - control_number + username
      - role -> 'student'
      - admission_status -> 'enrolled'
      - current_semester = 1 (si no tenía)
      - SemesterEnrollment(semester=1, active, confirmed) — idempotente: si
        ya existe SE para sem 1 no lo duplica.
      - Notificación + email + socket emit

    Args:
        user_id: ID del usuario.
        program_id: ID del programa.
        control_number: Número de control a asignar.
        admin_id: ID del administrador que ejecuta la operación.

    Returns:
        UserProgram actualizado.
    """
    from app.models.user import User as UserModel
    from app.models.role import Role
    from app.models.academic_period import AcademicPeriod
    from app.models.semester_enrollment import SemesterEnrollment

    if not control_number or not control_number.strip():
        raise ValueError("El número de control no puede estar vacío")

    ctrl = control_number.strip()

    # Unicidad institucional: consulta global a propósito, mensaje sin motivo.
    # Mismo texto exacto que en assign_control_number() — dos redacciones
    # distintas volverían a distinguir la colisión del resto de los rechazos.
    existing = UserModel.query.filter_by(control_number=ctrl).first()
    if existing and existing.id != user_id:
        raise ValueError(CONTROL_NUMBER_REJECTED_MESSAGE)

    up = _get_user_program(user_id, program_id)

    student_role = Role.query.filter_by(name='student').first()
    if not student_role:
        raise ValueError("El rol 'student' no está configurado en el sistema. Ejecuta las migraciones.")

    user = UserModel.query.get(user_id)
    if not user:
        raise ApplicantNotFound(f"Usuario {user_id} no encontrado")

    program = Program.query.get(program_id)

    # 1. Asignar número de control (también actualiza username)
    user.assign_control_number(ctrl)

    # 2. Cambiar rol a 'student'
    user.role_id = student_role.id

    # 3. Actualizar estado de inscripción (forzado, sin verificar transición previa)
    up.admission_status = 'enrolled'
    if not up.current_semester or up.current_semester < 1:
        up.current_semester = 1

    # 4. Crear SemesterEnrollment idempotente: solo si no existe SE alguno.
    #    Si el usuario ya tenía SE históricos (ej. legacy con backfill), no
    #    crear uno nuevo: respetar lo existente.
    has_any_se = SemesterEnrollment.query.filter_by(user_program_id=up.id).first() is not None
    if not has_any_se:
        enrollment_period_id = up.admission_period_id
        if not enrollment_period_id:
            active_period = AcademicPeriod.get_active_period()
            if active_period:
                enrollment_period_id = active_period.id

        if enrollment_period_id:
            db.session.add(SemesterEnrollment(
                user_program_id=up.id,
                academic_period_id=enrollment_period_id,
                semester_number=up.current_semester or 1,
                status='active',
                enrollment_confirmed=True,
                confirmed_by=admin_id,
                confirmed_at=now_local(),
            ))

    # 5. Historial + notificación con email + socket
    UserHistoryService.log_action(
        user_id=user_id,
        admin_id=admin_id,
        action='control_number_assigned_admin',
        details=(
            f'Número de control {ctrl} asignado por administrador (sin chequeos '
            f'de flujo) en {program.name if program else f"program {program_id}"}. '
            f'Transición a estudiante completada.'
        )
    )

    NotificationService.notify_control_number_assigned(
        user_id=user_id,
        control_number=ctrl,
    )

    try:
        from app.extensions import socketio
        socketio.emit('acceptance:updated', {
            'user_id': user_id,
            'program_id': program_id,
            'action': 'control_number_assigned_admin',
        }, room=f'user:{user_id}')
    except Exception:
        pass

    db.session.commit()

    return up
