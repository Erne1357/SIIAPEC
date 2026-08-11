# app/routes/api/coordinator_api.py
from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user
from sqlalchemy import select, and_, or_, func
from datetime import datetime, timezone

from app import db
from app.utils.permissions import (
    permission_required,
    any_permission_required,
    program_scope_required,
    guard_user_scope,
)
from app.services import program_scope_service as scope_service
from app.utils.files import save_user_doc  # Importar tu función de archivos
from app.services.user_history_service import UserHistoryService
from app.utils.history_formatter import HistoryFormatter
from app.models.user import User
from app.models.role import Role
from app.models.program import Program
from app.models.user_program import UserProgram
from app.models.submission import Submission
from app.models.archive import Archive
from app.models.step import Step
from app.models.phase import Phase
from app.models.program_step import ProgramStep
from app.models.appointment import Appointment
from app.models.semester_enrollment import SemesterEnrollment
from app.models.academic_period import AcademicPeriod
from app.services.admission_service import get_admission_state

api_coordinator = Blueprint('api_coordinator', __name__, url_prefix='/api/v1/coordinator')


def _scoped_user_program(student_id: int):
    """
    UserProgram del estudiante que cae DENTRO del alcance del llamador.

    Un estudiante puede tener más de un UserProgram (cambio de programa). Tomar
    `.first()` a ciegas permitía que un coordinador escribiera sobre la
    inscripción de un programa ajeno aunque compartiera otro con el estudiante.

    Returns:
        UserProgram | None — None si no tiene ninguno accesible.
    """
    rows = UserProgram.query.filter_by(user_id=student_id).all()
    if not rows:
        return None
    scope = scope_service.accessible_program_ids(current_user)
    if scope is None:
        return rows[0]
    for up in rows:
        if up.program_id in scope:
            return up
    return None


def _is_student_account(user) -> bool:
    """Sólo aspirantes y estudiantes son objetivo de los paneles de coordinación."""
    return getattr(getattr(user, 'role', None), 'name', None) in ('applicant', 'student')


def _summary_of(student, program, user_program=None, admission_state=None) -> dict:
    """
    Nivel reducido entre programas: nombre, correo y progreso/estado.

    Se arma plano y se pasa por `to_cross_program_summary`, que descarta todo lo
    que no esté en la lista blanca — incluido cualquier campo que se agregue
    después. Nunca construyas el payload restringido a mano.
    """
    flat = {
        "id": student.id,
        "full_name": f"{student.first_name} {student.last_name} {student.mother_last_name or ''}".strip(),
        "email": student.email,
        "program_id": program.id if program else None,
        "program_name": program.name if program else None,
        "program_slug": program.slug if program else None,
        "can_manage": False,
    }
    if user_program is not None:
        flat.update({
            "admission_status": user_program.admission_status,
            "current_semester": user_program.current_semester or 1,
        })
    if admission_state is not None:
        counts = admission_state.get('status_count', {})
        flat.update({
            "progress_percentage": admission_state.get('progress_pct', 0),
            "approved_docs": counts.get('approved', 0),
            "pending_docs": counts.get('pending', 0),
            "rejected_docs": counts.get('rejected', 0),
            "extended_docs": admission_state.get('extended_docs', 0),
        })
    return scope_service.to_cross_program_summary(flat)


def _restricted_permanence_payload(student, user_program, program) -> dict:
    """
    Respuesta de `/permanence-details` para un estudiante de otro programa.

    Conserva la forma del payload (el modal la consume tal cual) pero cada
    campo prohibido sale en `None` / vacío: foto, número de control, beca
    SECIHTI, periodo, inscripción semestral e historial de semestres.
    """
    s = _summary_of(student, program, user_program=user_program)
    return {
        "ok": True,
        "restricted": True,
        "student": {
            "id": s.get('id'),
            "full_name": s.get('full_name'),
            "email": s.get('email'),
            "avatar_url": None,
            "control_number": None,
        },
        "user_program": {
            "id": None,
            "current_semester": s.get('current_semester'),
            "has_conacyt_scholarship": None,
            "admission_status": s.get('admission_status'),
        },
        "program": {
            "id": s.get('program_id'),
            "name": s.get('program_name'),
        },
        "active_period": None,
        "current_enrollment": None,
        "pending_admission_count": None,
        "semester_history": [],
        "can_manage": False,
    }


def _restricted_details_payload(student, program, admission_state) -> dict:
    """
    Respuesta de `/details` para un estudiante de otro programa.

    Mismas claves de siempre, pero cada dato prohibido sale en `None`: los
    personales (CURP, RFC, NSS, domicilio, fecha y lugar de nacimiento, contacto
    de emergencia), la foto, los documentos con sus URLs y la elegibilidad de
    entrevista.

    `profile_data` conserva su FORMA con los valores en `None` en lugar de venir
    como `None` entero. El modal del coordinador la recorre campo por campo, y
    un `null` en la raíz reventaba el render con un TypeError que se pintaba
    como «Error al cargar información», dejando inalcanzable el nivel reducido
    que esta función existe para servir. Vaciar los valores comunica lo mismo
    —no hay dato disponible— sin romper al consumidor.
    """
    s = _summary_of(student, program, admission_state=admission_state)
    return {
        "ok": True,
        "restricted": True,
        "student": {
            "id": s.get('id'),
            "full_name": s.get('full_name'),
            "email": s.get('email'),
            "avatar_url": None,
            "profile_completed": None,
            "registration_date": None,
            "program": {
                "id": s.get('program_id'),
                "name": s.get('program_name'),
                "slug": s.get('program_slug'),
            },
            "profile_data": {
                "phone": None,
                "mobile_phone": None,
                "address": None,
                "curp": None,
                "rfc": None,
                "birth_date": None,
                "birth_place": None,
                "nss": None,
                "emergency_contact": {
                    "name": None,
                    "phone": None,
                    "relationship": None,
                },
            },
        },
        "documents": [],
        "interview": {
            "has_interview": False,
            "appointment": None,
            "eligibility": {"eligible": None, "missing_items": []},
        },
        "metrics": {
            "total_documents": None,
            "approved": s.get('approved_docs'),
            "pending": s.get('pending_docs'),
            "rejected": s.get('rejected_docs'),
            "extended": s.get('extended_docs'),
            "in_review": None,
            "progress_percentage": s.get('progress_percentage'),
        },
        "missing_documents": [],
        "can_manage": False,
    }

@api_coordinator.route('/students', methods=['GET'])
@login_required
@permission_required('coordinator.api.list_students')
def list_students():
    """
    Lista estudiantes que el coordinador puede ver/gestionar.
    Filtros: program_id, phase, status, search, show_other

    Alcance: las filas de programas ajenos existen por decisión del dueño, pero
    se proyectan al nivel reducido (nombre / correo / progreso) con
    `to_cross_program_summary`. Ningún dato personal, foto ni documento sale de
    aquí para un programa fuera del alcance del llamador.
    """
    program_id = request.args.get('program_id', type=int)
    phase = request.args.get('phase')  # admission, permanence, conclusion
    status = request.args.get('status')  # pending, review, approved, rejected
    search = request.args.get('search', '').strip()
    show_other = request.args.get('show_other') == 'true'

    # El toggle "ver otros programas" es exactamente el nivel reducido.
    if show_other and not scope_service.may_view_cross_program_summary(current_user):
        show_other = False

    # Base query: usuarios con programas (aspirantes y estudiantes ya inscritos)
    query = db.session.query(User, UserProgram, Program).join(
        UserProgram, User.id == UserProgram.user_id
    ).join(
        Program, UserProgram.program_id == Program.id
    ).filter(
        User.role.has(name='applicant') | User.role.has(name='student'),
        User.is_active == True,
    )
    
    # Programas que puede gestionar el coordinador (propios + delegados).
    # None = alcance global (jefe de posgrado); set() vacío = sin alcance.
    scope = scope_service.accessible_program_ids(current_user)

    if scope is not None and not show_other:
        if not scope:
            return jsonify({"students": []}), 200
        query = query.filter(Program.id.in_(scope))

    # Filtros adicionales
    if program_id:
        query = query.filter(Program.id == program_id)
    
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                User.first_name.ilike(search_term),
                User.last_name.ilike(search_term),
                User.email.ilike(search_term)
            )
        )
    
    results = query.all()
    students = []

    active_period = AcademicPeriod.get_active_period()
    active_period_id = active_period.id if active_period else None

    # Una sola resolución de alcance para toda la página.
    manageable_pids = scope_service.programs_in_scope(
        current_user, {program.id for _, _, program in results}
    )

    for user, user_program, program in results:
        # Calcular estado actual del estudiante
        admission_state = get_admission_state(user.id, program.id, user_program)
        # Métricas de permanencia basadas en SemesterEnrollment + duración del programa
        perm = _compute_permanence_metrics(user_program, program, active_period_id)
        # Determinar fase actual basada en estado
        current_phase = _determine_current_phase(admission_state, user_program, perm)
        
        # Filtro por fase
        if phase and current_phase != phase:
            continue
        
        # ¿Puede gestionar este estudiante? (el programa de la fila está dentro
        # del alcance del llamador — el jefe de posgrado siempre pasa)
        can_manage = program.id in manageable_pids

        # Calcular métricas
        student_data = {
            "id": user.id,
            "full_name": f"{user.first_name} {user.last_name}",
            "email": user.email,
            "avatar_url": user.avatar_url,
            "program_id": program.id,
            "program_name": program.name,
            "current_phase": current_phase,
            "can_manage": can_manage,
            
            # Métricas de admisión
            "progress_percentage": admission_state.get("progress_pct", 0),
            "approved_docs": admission_state["status_count"].get("approved", 0),
            "pending_docs": admission_state["status_count"].get("pending", 0),
            "rejected_docs": admission_state["status_count"].get("rejected", 0),
            "extended_docs": admission_state.get("extended_docs", 0),
            "overall_status": _determine_overall_status(admission_state),
            "ready_for_interview": _check_ready_for_interview(admission_state),
            
            # Métricas de permanencia (basadas en SemesterEnrollment real)
            "current_semester": perm["current_semester"],
            "completed_semesters": perm["completed_semesters"],
            "total_semesters": perm["total_semesters"],
            "academic_progress": perm["academic_progress"],
            "in_progress_segment": perm["in_progress_segment"],
            "academic_status": perm["academic_status"],
            # Métricas de conclusión (placeholder)
            "conclusion_stage": "inicial",
            "conclusion_progress": 0,
            "conclusion_status": "pending"
        }

        # Fuera de alcance: sólo nombre, correo y progreso/estado. La proyección
        # elimina la foto de perfil y cualquier campo que se añada después.
        if not can_manage:
            student_data = scope_service.to_cross_program_summary(student_data)

        # Filtro por status
        if status and student_data.get("overall_status") != status:
            continue

        students.append(student_data)
    
    return jsonify({"students": students}), 200

@api_coordinator.route('/manageable-students', methods=['GET'])
@login_required
@permission_required('coordinator.api.list_students')
def manageable_students():
    """
    Lista solo estudiantes que el coordinador puede gestionar (para selects)
    """
    scope = scope_service.accessible_program_ids(current_user)
    if scope is None:
        program_filter = True  # acceso global
    else:
        if not scope:
            return jsonify({"students": []}), 200
        program_filter = Program.id.in_(scope)
    
    query = db.session.query(User, Program).join(
        UserProgram, User.id == UserProgram.user_id
    ).join(
        Program, UserProgram.program_id == Program.id
    ).filter(
        User.role.has(name='applicant'),
        User.is_active == True,
        program_filter
    ).order_by(User.first_name, User.last_name)
    
    results = query.all()
    students = []
    
    for user, program in results:
        students.append({
            "id": user.id,
            "full_name": f"{user.first_name} {user.last_name}",
            "email": user.email,
            "program_name": program.name
        })
    
    return jsonify({"students": students}), 200

@api_coordinator.route('/student/<int:student_id>/uploadable-archives', methods=['GET'])
@login_required
@permission_required('coordinator.api.upload_for_student')
@program_scope_required(user_id_kwarg='student_id', allow_self=False)
def student_uploadable_archives(student_id: int):
    """
    Lista archivos que el coordinador puede subir para un estudiante específico.

    El alcance lo resuelve `@program_scope_required`: un aspirante de otro
    programa (o un id inexistente) nunca llega al cuerpo de la vista.
    """
    student = db.session.get(User, student_id)
    if not student or not _is_student_account(student):
        return jsonify({"error": "Estudiante no encontrado"}), 404

    user_program = _scoped_user_program(student_id)
    if not user_program:
        return jsonify({"error": "Estudiante no inscrito en programa"}), 404

    # Obtener archivos que permiten subida por coordinador
    query = db.session.query(Archive, Step, Phase).join(
        Step, Archive.step_id == Step.id
    ).join(
        Phase, Step.phase_id == Phase.id
    ).join(
        ProgramStep, and_(
            ProgramStep.step_id == Step.id,
            ProgramStep.program_id == user_program.program_id
        )
    ).filter(
        Archive.allow_coordinator_upload == True,
        Archive.is_uploadable == True
    ).order_by(Phase.id, ProgramStep.sequence)
    
    archives = []
    for archive, step, phase in query:
        # Verificar si ya existe submission
        existing = Submission.query.filter_by(
            user_id=student_id,
            archive_id=archive.id
        ).first()
        
        archives.append({
            "id": archive.id,
            "name": archive.name,
            "description": archive.description,
            "step_name": step.name,
            "phase_name": phase.name,
            "has_existing": bool(existing),
            "existing_status": existing.status if existing else None
        })
    
    return jsonify({"archives": archives}), 200

@api_coordinator.route('/upload-for-student', methods=['POST'])
@login_required
@permission_required('coordinator.api.upload_for_student')
def upload_for_student():
    """
    Permite al coordinador subir un archivo por un estudiante usando el sistema de archivos
    """
    student_id = request.form.get('student_id', type=int)
    archive_id = request.form.get('archive_id', type=int)
    notes = request.form.get('notes', '').strip()
    decision = (request.form.get('decision') or 'approve').strip().lower()
    if decision not in ('approve', 'reject'):
        decision = 'approve'

    if not student_id or not archive_id:
        return jsonify({"error": "student_id y archive_id son requeridos"}), 400

    # Alcance ANTES de tocar nada: subir un documento es una escritura y una
    # escritura nunca cruza programas. El id viene en el form, no en el URL,
    # así que se usa la guarda imperativa.
    denied = guard_user_scope(student_id, allow_self=False)
    if denied:
        return denied

    student = db.session.get(User, student_id)
    if not student or not _is_student_account(student):
        return jsonify({"error": "Estudiante no encontrado"}), 404

    # Archivo opcional: si no se proporciona, el coordinador valida sin documento
    # (caso típico: examen presencial). Aspirantes/estudiantes siempre suben file.
    file = request.files.get('file') if 'file' in request.files else None
    if file and not file.filename:
        file = None
    if not file and not notes:
        return jsonify({
            "error": "Si no subes un archivo debes proporcionar al menos un comentario justificativo"
        }), 400
    
    # Inscripción sobre la que se escribe: siempre una que el llamador gestiona.
    user_program = _scoped_user_program(student_id)
    if not user_program:
        return jsonify({"error": "Estudiante no inscrito"}), 404

    # Verificar que el archivo permite subida por coordinador
    archive = db.session.get(Archive, archive_id)
    if not archive or not archive.allow_coordinator_upload:
        return jsonify({"error": "Este archivo no permite subida por coordinador"}), 403
    
    try:
        # Si hay archivo, guardarlo. Si no, file_relative_path queda en None.
        file_relative_path = None
        if file:
            file_relative_path = save_user_doc(
                file_storage=file,
                user_id=student_id,
                phase='admission',
                name=archive.name,
            )

        program_step = ProgramStep.query.filter_by(
            program_id=user_program.program_id,
            step_id=archive.step_id
        ).first()

        if not program_step:
            return jsonify({"error": "Configuración de programa incompleta"}), 400

        # Eliminar submission anterior si existe
        existing = Submission.query.filter_by(
            user_id=student_id,
            archive_id=archive_id
        ).first()
        if existing:
            db.session.delete(existing)

        new_status = 'approved' if decision == 'approve' else 'rejected'
        prefix = '[Coordinador]'
        if not file:
            prefix = '[Coordinador · validación sin archivo]'
        comment_body = notes if notes else (
            'Documento subido y aprobado' if decision == 'approve' else 'Documento rechazado'
        )

        submission = Submission(
            file_path=file_relative_path,
            status=new_status,
            review_date=datetime.now(),
            reviewer_comment=f"{prefix} {comment_body}",
            user_id=student_id,
            archive_id=archive_id,
            program_step_id=program_step.id,
            semester=None,
            uploaded_by=current_user.id,
            uploaded_by_role='program_admin'
        )

        # Marcar reviewer_id si la decisión es revisar (no solo subir)
        submission.reviewer_id = current_user.id

        db.session.add(submission)
        db.session.commit()

        try:
            UserHistoryService.log_document_upload(
                user_id=student_id,
                archive_name=archive.name,
                program_name=user_program.program.name,
                uploaded_by_admin=True,
                admin_id=current_user.id
            )
            db.session.commit()
        except Exception as e:
            current_app.logger.error(f"Error al registrar subida por coordinador en historial: {e}")

        msg = (
            f"Documento {'aprobado' if decision == 'approve' else 'rechazado'} exitosamente por coordinador"
            + ('' if file else ' (sin archivo adjunto)')
        )
        return jsonify({
            "ok": True,
            "submission_id": submission.id,
            "message": msg,
        }), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Error al guardar: {str(e)}"}), 500


@api_coordinator.route('/programs', methods=['GET'])
@login_required
@permission_required('coordinator.api.list_students')
def list_coordinator_programs():
    """Lista programas que el coordinador puede gestionar"""
    scope = scope_service.accessible_program_ids(current_user)
    if scope is None:
        programs = Program.query.all()
    elif not scope:
        programs = []
    else:
        programs = Program.query.filter(Program.id.in_(scope)).all()
    
    items = [{
        "id": p.id,
        "name": p.name,
        "slug": p.slug,
        "description": p.description
    } for p in programs]
    
    return jsonify({"ok": True, "programs": items}), 200

@api_coordinator.route('/student/<int:student_id>/permanence-details', methods=['GET'])
@login_required
@permission_required('coordinator.api.list_students')
def get_student_permanence_details(student_id: int):
    """
    Detalles de permanencia de un estudiante inscrito:
    semestre actual, periodo activo, confirmación semestral,
    beca CONACyT, documentos de admisión pendientes e historial semestral.
    """
    from app.models.semester_enrollment import SemesterEnrollment
    from app.models.academic_period import AcademicPeriod

    student = db.session.get(User, student_id)
    if not student or not _is_student_account(student):
        return jsonify({"ok": False, "error": "Estudiante no encontrado"}), 404

    # La inscripción que se muestra es la del programa que el llamador gestiona;
    # sólo si no gestiona ninguna se cae al nivel reducido.
    user_program = _scoped_user_program(student_id)
    can_manage = user_program is not None
    if user_program is None:
        user_program = UserProgram.query.filter_by(user_id=student_id).first()
    if not user_program:
        return jsonify({"ok": False, "error": "Sin programa"}), 404

    program = db.session.get(Program, user_program.program_id)

    # Fuera de alcance: nivel reducido (nombre / correo / progreso). Ni número
    # de control, ni foto, ni beca, ni historial semestral cruzan de programa.
    if not can_manage:
        if not scope_service.may_view_cross_program_summary(current_user):
            return jsonify({
                "ok": False,
                "error": "No tienes acceso a la información de este estudiante"
            }), 403
        return jsonify(_restricted_permanence_payload(student, user_program, program)), 200

    # Periodo activo y enrollment del periodo actual
    active_period = AcademicPeriod.get_active_period()
    current_enrollment = None
    if active_period:
        current_enrollment = SemesterEnrollment.query.filter_by(
            user_program_id=user_program.id,
            academic_period_id=active_period.id
        ).first()

    # Historial semestral (todos los periodos)
    history = SemesterEnrollment.query.filter_by(
        user_program_id=user_program.id
    ).order_by(SemesterEnrollment.semester_number.asc()).all()

    # Documentos de admisión pendientes/rechazados
    admission_state = get_admission_state(student_id, program.id, user_program)
    pending_admission = (
        admission_state['status_count'].get('pending', 0) +
        admission_state['status_count'].get('rejected', 0)
    )

    return jsonify({
        "ok": True,
        "student": {
            "id": student.id,
            "full_name": f"{student.first_name} {student.last_name} {student.mother_last_name or ''}".strip(),
            "email": student.email,
            "avatar_url": student.avatar_url,
            "control_number": student.control_number,
        },
        "user_program": {
            "id": user_program.id,
            "current_semester": user_program.current_semester or 1,
            "has_conacyt_scholarship": user_program.has_conacyt_scholarship,
            "admission_status": user_program.admission_status,
        },
        "program": {
            "id": program.id,
            "name": program.name,
        },
        "active_period": {
            "id": active_period.id,
            "name": active_period.name,
            "code": active_period.code,
        } if active_period else None,
        "current_enrollment": {
            "id": current_enrollment.id,
            "semester_number": current_enrollment.semester_number,
            "status": current_enrollment.status,
            "enrollment_confirmed": current_enrollment.enrollment_confirmed,
            "confirmed_at": current_enrollment.confirmed_at.isoformat() if current_enrollment.confirmed_at else None,
            "notes": current_enrollment.notes,
        } if current_enrollment else None,
        "pending_admission_count": pending_admission,
        "semester_history": [
            {
                "id": se.id,
                "semester_number": se.semester_number,
                "period_name": se.academic_period.name if se.academic_period else "—",
                "period_code": se.academic_period.code if se.academic_period else "—",
                "status": se.status,
                "enrollment_confirmed": se.enrollment_confirmed,
                "confirmed_at": se.confirmed_at.isoformat() if se.confirmed_at else None,
                "notes": se.notes,
            }
            for se in history
        ],
        "can_manage": can_manage,
    }), 200


@api_coordinator.route('/student/<int:student_id>/details', methods=['GET'])
@login_required
@permission_required('coordinator.api.list_students')
def get_student_details(student_id: int):
    """
    Obtiene detalles completos de un estudiante para el modal del coordinador.
    Incluye: perfil, documentos, entrevista, métricas
    """
    from app.models.event import Event, EventSlot, EventWindow
    from app.services.interview_service import InterviewEligibilityService
    
    # 1. Obtener estudiante
    student = db.session.get(User, student_id)
    if not student or not _is_student_account(student):
        return jsonify({"ok": False, "error": "Estudiante no encontrado"}), 404

    # 2. Resolver la inscripción dentro del alcance del llamador
    user_program = _scoped_user_program(student_id)
    can_manage = user_program is not None
    if user_program is None:
        user_program = UserProgram.query.filter_by(user_id=student_id).first()
    if not user_program:
        return jsonify({"ok": False, "error": "Estudiante no inscrito"}), 404

    program = db.session.get(Program, user_program.program_id)

    # 3. Obtener estado de admisión completo
    admission_state = get_admission_state(student_id, program.id, user_program)

    # Fuera de alcance: nivel reducido. Los datos personales, los documentos y
    # sus URLs nunca cruzan de programa, aunque el llamador tenga el permiso.
    if not can_manage:
        if not scope_service.may_view_cross_program_summary(current_user):
            return jsonify({
                "ok": False,
                "error": "No tienes acceso a la información de este estudiante"
            }), 403
        return jsonify(_restricted_details_payload(student, program, admission_state)), 200

    # 4. Organizar documentos por paso
    documents_by_step = []
    for item in admission_state['processed_steps']:
        if item['sequence'] == 0:
            continue
        if item['is_combined']:
            # Paso combinado
            step_data = {
                "step_id": item['id'],
                "step_name": item['name'],
                "sequence": item['sequence'],
                "is_combined": True,
                "locked": item['locked'],
                "state": item['state'],
                "archives": []
            }
            
            # Archivos del step1
            for arch in item['step1'].archives:
                step_data['archives'].append(_format_archive_status(
                    arch, 
                    admission_state['subs'], 
                    admission_state['all_extensions']
                ))
            
            # Archivos del step2
            for arch in item['step2'].archives:
                step_data['archives'].append(_format_archive_status(
                    arch, 
                    admission_state['subs'], 
                    admission_state['all_extensions']
                ))
            
            documents_by_step.append(step_data)
        else:
            # Paso normal
            step = item['step']
            step_data = {
                "step_id": step.id,
                "step_name": step.name,
                "sequence": item['sequence'],
                "is_combined": False,
                "locked": item['locked'],
                "state": item['state'],
                "archives": []
            }
            
            for arch in step.archives:
                step_data['archives'].append(_format_archive_status(
                    arch, 
                    admission_state['subs'], 
                    admission_state['all_extensions']
                ))
            
            documents_by_step.append(step_data)
    
    # 5. Estado de entrevista
    interview_status = _get_interview_status(student_id, program.id)
    
    # 6. Verificar elegibilidad
    eligibility = InterviewEligibilityService.check_student_eligibility(student_id, program.id)
    
    # 7. Construir respuesta
    return jsonify({
        "ok": True,
        "student": {
            "id": student.id,
            "full_name": f"{student.first_name} {student.last_name} {student.mother_last_name or ''}".strip(),
            "email": student.email,
            "avatar_url": student.avatar_url,
            "profile_completed": student.profile_completed,
            "registration_date": student.registration_date.isoformat() if student.registration_date else None,
            "program": {
                "id": program.id,
                "name": program.name,
                "slug": program.slug
            },
            "profile_data": {
                "phone": student.phone,
                "mobile_phone": student.mobile_phone,
                "address": student.address,
                "curp": student.curp,
                "rfc": student.rfc,
                "birth_date": student.birth_date.isoformat() if student.birth_date else None,
                "birth_place": student.birth_place,
                "nss": student.nss,
                "emergency_contact": {
                    "name": student.emergency_contact_name,
                    "phone": student.emergency_contact_phone,
                    "relationship": student.emergency_contact_relationship
                }
            }
        },
        "documents": documents_by_step,
        "interview": {
            **interview_status,
            "eligibility": eligibility
        },
        "metrics": {
            "total_documents": len([a for step in documents_by_step for a in step['archives']]),
            "approved": admission_state['status_count'].get('approved', 0),
            "pending": admission_state['status_count'].get('pending', 0),
            "rejected": admission_state['status_count'].get('rejected', 0),
            "extended": admission_state['status_count'].get('extended', 0),
            "in_review": admission_state['status_count'].get('review', 0),
            "progress_percentage": admission_state['progress_pct']
        },
        "missing_documents": _get_missing_documents(documents_by_step),
        "can_manage": can_manage
    }), 200

def _format_archive_status(archive, subs, all_extensions):
    """Formatea el estado de un archivo para el modal"""
    sub = subs.get(archive.id)
    ext = all_extensions.get(archive.id)
    
    return {
        "id": archive.id,
        "name": archive.name,
        "description": archive.description,
        "has_submission": bool(sub),
        "status": sub.status if sub else "pending",
        "uploaded_at": sub.upload_date.isoformat() if sub and sub.upload_date else None,
        "uploaded_by_role": sub.uploaded_by_role if sub else None,
        "reviewer_comment": sub.reviewer_comment if sub else None,
        "review_date": sub.review_date.isoformat() if sub and sub.review_date else None,
        "file_url": f"/files/doc/{sub.user_id}/admission/{sub.file_path.split('/')[-1]}" if sub and sub.file_path else None,
        "has_extension": bool(ext),
        "extension_status": ext.status if ext else None,
        "extension_until": ext.granted_until.isoformat() if ext and ext.granted_until else None,
        "is_uploadable": archive.is_uploadable,
        "allow_coordinator_upload": archive.allow_coordinator_upload
    }

def _get_interview_status(student_id, program_id):
    """Obtiene el estado de entrevista del estudiante"""
    from app.models.event import Event, EventSlot, EventWindow

    # Buscar cualquier cita no cancelada: scheduled (pendiente), done (realizada), no_show
    appointment = db.session.execute(
        select(Appointment)
        .join(EventSlot, Appointment.slot_id == EventSlot.id)
        .join(EventWindow, EventSlot.event_window_id == EventWindow.id)
        .join(Event, EventWindow.event_id == Event.id)
        .where(
            Appointment.applicant_id == student_id,
            Appointment.status.in_(['scheduled', 'done', 'no_show']),
            or_(Event.program_id == program_id, Event.program_id.is_(None)),
            Event.type == 'interview'
        )
    ).scalar_one_or_none()

    if not appointment:
        return {
            "has_interview": False,
            "appointment": None
        }
    
    # Obtener detalles completos
    slot = db.session.get(EventSlot, appointment.slot_id)
    window = db.session.get(EventWindow, slot.event_window_id)
    event = db.session.get(Event, window.event_id)
    
    return {
        "has_interview": True,
        "appointment": {
            "id": appointment.id,
            "status": appointment.status,
            "notes": appointment.notes,
            "created_at": appointment.created_at.isoformat(),
            "event": {
                "id": event.id,
                "title": event.title,
                "location": event.location,
                "description": event.description
            },
            "slot": {
                "starts_at": slot.starts_at.isoformat(),
                "ends_at": slot.ends_at.isoformat()
            }
        }
    }

def _get_missing_documents(documents_by_step):
    """Lista de documentos faltantes o rechazados"""
    missing = []
    for step in documents_by_step:
        for arch in step['archives']:
            if arch['status'] in ['pending', 'rejected']:
                missing.append({
                    "step": step['step_name'],
                    "archive": arch['name'],
                    "status": arch['status']
                })
    return missing
# ==================== FUNCIONES AUXILIARES ====================

def _compute_permanence_metrics(user_program, program, active_period_id):
    """
    Calcula métricas reales de permanencia para un UserProgram.

    Returns dict con:
      - current_semester: número de semestre actual del UserProgram
      - completed_semesters: cantidad de SemesterEnrollment con status='completed'
      - total_semesters: duración del programa (Program.duration_semesters; default 4)
      - academic_progress: % de semestres completados sobre el total
      - in_progress_segment: % adicional que corresponde al semestre en curso
        (sólo cuando hay enrollment en estado 'active' en el periodo activo)
      - academic_status: estado funcional para el coordinador
        ('active' | 'on_leave' | 'completed' | 'dropped' | 'pending')
    """
    total = max(int(program.duration_semesters or 4), 1)
    current_semester = user_program.current_semester or 1

    completed = (
        SemesterEnrollment.query
        .filter_by(user_program_id=user_program.id, status='completed')
        .count()
    )
    completed = min(completed, total)

    # Enrollment del periodo activo (si existe) define el estado funcional + segmento parpadeante
    current_enrollment = None
    if active_period_id is not None:
        current_enrollment = (
            SemesterEnrollment.query
            .filter_by(user_program_id=user_program.id, academic_period_id=active_period_id)
            .first()
        )

    if current_enrollment is not None:
        academic_status = current_enrollment.status
    elif completed >= total:
        academic_status = 'completed'
    else:
        academic_status = 'pending'

    progress_pct = round((completed / total) * 100, 2)
    segment_pct = round((1 / total) * 100, 2)

    # Sólo parpadea si hay un semestre activamente en curso y queda espacio en la barra
    in_progress_segment = 0
    if academic_status == 'active' and (progress_pct + segment_pct) <= 100.001:
        in_progress_segment = segment_pct

    return {
        "current_semester": current_semester,
        "completed_semesters": completed,
        "total_semesters": total,
        "academic_progress": progress_pct,
        "in_progress_segment": in_progress_segment,
        "academic_status": academic_status,
    }


def _determine_current_phase(admission_state, user_program, perm):
    """Determina la fase actual del estudiante."""
    # Estudiantes con número de control = ya están en permanencia o conclusión
    if user_program.admission_status == 'enrolled':
        if perm["completed_semesters"] >= perm["total_semesters"]:
            return "conclusion"
        return "permanence"

    # Si no ha completado admisión, está en admisión
    return "admission"

def _determine_overall_status(admission_state):
    """Determina el estado general del estudiante"""
    status_count = admission_state.get("status_count", {})
    
    if status_count.get("rejected", 0) > 0:
        return "rejected"
    elif status_count.get("review", 0) > 0:
        return "review"
    elif status_count.get("pending", 0) > 0:
        return "pending"
    elif status_count.get("approved", 0) > 0:
        return "approved"
    
    return "pending"


@api_coordinator.route('/students/<int:student_id>/history', methods=['GET'])
@login_required
@permission_required('coordinator.api.list_students')
def get_student_history(student_id):
    """
    Obtiene el historial formateado de un estudiante específico.
    Solo coordinadores y administradores pueden ver el historial de estudiantes.
    """
    try:
        # Verificar que el estudiante existe y el coordinador tiene acceso
        student = User.query.filter_by(id=student_id).first()
        if not student or student.role.name not in ('applicant', 'student'):
            return jsonify({
                'success': False,
                'message': 'Estudiante no encontrado'
            }), 404
        
        # El historial es información prohibida entre programas: aquí no hay
        # nivel reducido, o el estudiante está en tu alcance o no lo ves.
        if not scope_service.user_in_scope(current_user, student, allow_self=False):
            return jsonify({
                'success': False,
                'message': 'No tienes permisos para ver el historial de este estudiante'
            }), 403


        # Parámetros de consulta
        format_type = request.args.get('format', 'formatted')
        limit = min(int(request.args.get('limit', 50)), 100)
        
        # Obtener el historial del estudiante
        history_entries = UserHistoryService.get_user_history(
            user_id=student_id,
            limit=limit,
            order_by='desc'
        )
        
        # Formatear las entradas si se solicita
        formatted_history = []
        formatter = HistoryFormatter()
        
        for entry in history_entries:
            entry_dict = entry.to_dict()
            
            # Agregar información del coordinador que realizó la acción
            if entry.performed_by_id:
                entry_dict['performed_by_name'] = f"{entry.performed_by.first_name} {entry.performed_by.last_name}"
                entry_dict['performed_by_role'] = entry.performed_by.role.name if entry.performed_by.role else None
            
            # Agregar descripción formateada si se solicita
            if format_type == 'formatted':
                entry_dict['formatted_description'] = formatter.format_history_entry(entry)
            
            formatted_history.append(entry_dict)
        
        return jsonify({
            'success': True,
            'data': {
                'student': {
                    'id': student.id,
                    'name': f"{student.first_name} {student.last_name}",
                    'control_number': student.control_number,
                    'email': student.email
                },
                'history': formatted_history,
                'total_count': len(history_entries),
                'format_type': format_type
            },
            'meta': {
                'viewed_by': current_user.id,
                'ordered_by': 'timestamp_desc',
                'limit_applied': limit
            }
        }), 200
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error al obtener historial del estudiante: {str(e)}'
        }), 500

def _check_ready_for_interview(admission_state):
    """Verifica si el estudiante está listo para entrevista"""
    # Lógica simplificada: si tiene >80% de progreso
    return admission_state.get("progress_pct", 0) >= 80