# app/routes/api/interviews_api.py
"""
Interview eligibility endpoints.

Permiso ≠ alcance. `interviews.api.check_eligibility`, `list_eligible` and
`manage` are ROLE grants — every program_admin holds them institution-wide —
so they only answer "may this account work on interviews at all". Every route
below that names a concrete program or applicant must ALSO declare the scope
with `@program_scope_required(...)`; the payloads carry per-document
compliance detail and one of them writes a state that feeds eligibility.
"""

from flask import Blueprint, current_app, jsonify
from flask_login import login_required, current_user

from app.models.user import User
from app.routes._public_id import resolved_id
from app.utils.permissions import (
    permission_required,
    program_scope_required,
    guard_user_scope,
    current_accessible_program_ids,
)
from app.services.interview_service import InterviewEligibilityService

api_interviews = Blueprint('api_interviews', __name__, url_prefix='/api/v1/interviews')

_SERVER_ERROR_MESSAGE = 'Ocurrió un error al procesar la solicitud'


@api_interviews.route('/eligibility/<uuid:student_uuid>/<int:program_id>', methods=['GET'])
@login_required
@permission_required('interviews.api.check_eligibility')
@program_scope_required(program_id_kwarg='program_id')
def check_eligibility(student_uuid, program_id: int):
    """
    Verifica si un estudiante específico es elegible para entrevista.

    Both ids arrive from the URL and are attacker-controlled, and the payload
    carries per-document detail (archive names, submission status, missing
    items), so BOTH are scoped: the program must be one of the caller's and the
    applicant must belong to one of the caller's programs. That also closes the
    enumeration oracle — an unknown student_id and an out-of-scope one both
    answer 403, so ids are no longer walkable 1..N.

    The applicant half of that guard is now imperative because the URL carries
    a UUID: `guard_user_scope(None)` fails closed, so an unknown identifier and
    an out-of-scope one still produce the byte-identical 403.
    """
    student = User.by_uuid(student_uuid)
    denied = guard_user_scope(resolved_id(student))
    if denied:
        return denied
    student_id = student.id

    try:
        eligibility = InterviewEligibilityService.check_student_eligibility(student_id, program_id)
        return jsonify({
            "ok": True,
            "student_id": str(student.uuid) if student.uuid else None,
            "program_id": program_id,
            "eligibility": eligibility
        }), 200
    except Exception:
        current_app.logger.exception(
            "Error verificando elegibilidad (student_id=%s, program_id=%s)",
            student_id, program_id
        )
        return jsonify({
            "ok": False,
            "error": _SERVER_ERROR_MESSAGE
        }), 500


@api_interviews.route('/eligible-students/<int:program_id>', methods=['GET'])
@login_required
@permission_required('interviews.api.list_eligible')
@program_scope_required(program_id_kwarg='program_id')
def list_eligible_students(program_id: int):
    """
    Lista todos los estudiantes elegibles para entrevista en un programa.
    """
    try:
        eligible_students = InterviewEligibilityService.get_eligible_students(program_id)
        # Only the count is logged. The list carries names, e-mails and
        # per-applicant document compliance; the application log is not a
        # place for applicant PII.
        current_app.logger.info(
            "Usuario %s listó estudiantes elegibles del programa %s - Total: %s",
            current_user.id, program_id, len(eligible_students)
        )
        return jsonify({
            "ok": True,
            "program_id": program_id,
            "eligible_students": eligible_students,
            "count": len(eligible_students)
        }), 200
    except Exception:
        current_app.logger.exception(
            "Error listando estudiantes elegibles del programa %s", program_id
        )
        return jsonify({
            "ok": False,
            "error": _SERVER_ERROR_MESSAGE
        }), 500


@api_interviews.route('/eligible-students', methods=['GET'])
@login_required
@permission_required('interviews.api.list_eligible')
def list_all_eligible_students():
    """
    Lista los estudiantes elegibles para entrevista en los programas del usuario.

    No `@program_scope_required` here because there is no target id in the
    request: the route derives the program set FROM the caller's scope, which
    is the strongest form of the guard.
    """
    try:
        scope = current_accessible_program_ids()

        if scope is None:
            # Alcance global (jefe de posgrado): se expande aquí, de forma
            # explícita, para que el servicio nunca reciba un valor que
            # signifique "todos los programas".
            program_ids = InterviewEligibilityService.all_program_ids()
        elif not scope:
            # Conjunto vacío = SIN alcance. Nunca "todos".
            return jsonify({
                "ok": True,
                "programs": [],
                "total_programs": 0,
                "total_eligible_students": 0,
                "message": "No tienes programas asignados"
            }), 200
        else:
            program_ids = sorted(scope)

        programs_data = InterviewEligibilityService.get_eligible_students_by_programs(program_ids)
        total_eligible = sum(p["eligible_count"] for p in programs_data)

        current_app.logger.info(
            "Usuario %s listó estudiantes elegibles de %s programas - Total: %s",
            current_user.id, len(programs_data), total_eligible
        )

        return jsonify({
            "ok": True,
            "programs": programs_data,
            "total_programs": len(programs_data),
            "total_eligible_students": total_eligible,
            "user_role": current_user.role.name
        }), 200

    except Exception:
        current_app.logger.exception(
            "Error obteniendo estudiantes elegibles para todos los programas"
        )
        return jsonify({
            "ok": False,
            "error": _SERVER_ERROR_MESSAGE
        }), 500


@api_interviews.route('/mark-profile-complete/<uuid:user_uuid>', methods=['POST'])
@login_required
@permission_required('interviews.api.manage')
def mark_profile_complete(user_uuid):
    """
    Marca el perfil de un usuario como completo (uso administrativo).

    `profile_completed` is criterion #1 of interview eligibility, so this is a
    program-scoped WRITE: the target applicant must belong to one of the
    caller's programs. `allow_self=False` because no holder of
    `interviews.api.manage` is their own applicant. The guard is imperative
    now that the URL carries a UUID; `guard_user_scope(None)` fails closed, so
    unknown and out-of-scope answer identically.
    """
    target = User.by_uuid(user_uuid)
    denied = guard_user_scope(resolved_id(target), allow_self=False)
    if denied:
        return denied
    user_id = target.id

    try:
        result = InterviewEligibilityService.mark_profile_complete(
            user_id=user_id,
            admin_id=current_user.id,
        )
    except Exception:
        current_app.logger.exception(
            "Error marcando perfil como completo (user_id=%s)", user_id
        )
        return jsonify({
            "ok": False,
            "error": _SERVER_ERROR_MESSAGE
        }), 500

    status = result["status"]
    if status in ('ok', 'already_complete'):
        return jsonify({
            "ok": True,
            "status": status,
            "message": result["message"]
        }), 200

    http_status = 404 if status == 'not_found' else 400
    return jsonify({
        "ok": False,
        "status": status,
        "error": result["message"]
    }), http_status
