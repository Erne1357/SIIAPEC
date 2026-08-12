# app/routes/api/permanence_api.py
"""
API para gestionar la permanencia semestral de estudiantes (Fase 6).
"""

from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from app import db
from app.utils.permissions import (
    permission_required,
    program_scope_required,
    guard_program_scope,
)
from app.models.archive import Archive
from app.models.submission import Submission
from app.models.user_program import UserProgram
from app.services import permanence_service as svc
from app.services import program_scope_service as scope_service
import app.services.semester_transition_service as tsvc

api_permanence = Blueprint(
    'api_permanence',
    __name__,
    url_prefix='/api/v1/permanence'
)


@api_permanence.get('/program/<int:program_id>/students')
@login_required
@permission_required('permanence.api.list_students')
@program_scope_required(program_id_kwarg='program_id')
def api_get_enrolled_students(program_id):
    """Lista estudiantes inscritos con su estado de permanencia."""
    try:
        data = svc.get_enrolled_students(program_id)
        return jsonify({
            "data": data,
            "error": None,
            "meta": {"count": len(data)}
        }), 200
    except Exception as e:
        return jsonify({
            "data": None,
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500


@api_permanence.get('/program/<int:program_id>/stats')
@login_required
@permission_required('permanence.api.list_students')
@program_scope_required(program_id_kwarg='program_id')
def api_get_permanence_stats(program_id):
    """Estadisticas de permanencia para un programa."""
    try:
        stats = svc.get_permanence_stats(program_id)
        return jsonify({
            "data": stats,
            "error": None,
            "meta": {}
        }), 200
    except Exception as e:
        return jsonify({
            "data": None,
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500


def _extract_payment_proof(user_id: int):
    """
    Si la petición es multipart con un archivo 'payment_proof', lo guarda
    en uploads/<user_id>/permanence/ y devuelve el path relativo. Acepta
    sólo PDF. Devuelve None si no se envió archivo.
    """
    file = request.files.get('payment_proof') if request.files else None
    if not file or not file.filename:
        return None
    if not file.filename.lower().endswith('.pdf'):
        raise ValueError("El comprobante de pago debe ser PDF")
    from app.utils.files import save_user_doc
    return save_user_doc(file, user_id, 'permanence', 'payment_proof')


def _extract_schedule(user_id: int):
    """Si viene multipart con archivo 'schedule', guarda PDF y devuelve path."""
    file = request.files.get('schedule') if request.files else None
    if not file or not file.filename:
        return None
    if not file.filename.lower().endswith('.pdf'):
        raise ValueError("El horario debe ser PDF")
    from app.utils.files import save_user_doc
    return save_user_doc(file, user_id, 'permanence', 'horario_semestre')


def _extract_form_or_json(field, default=None):
    """Lee de form-data o JSON indistintamente."""
    if request.content_type and request.content_type.startswith('application/json'):
        body = request.get_json(silent=True) or {}
        return body.get(field, default)
    return request.form.get(field, default)


# ── Alcance de objeto (permiso ≠ alcance) ─────────────────────────────────────
#
# `permission_required` responde QUÉ puede hacer el llamador, nunca SOBRE QUÉ
# objeto. Toda inscripción semestral, ventana de entrega, submission o
# UserProgram pertenece a un programa: `permanence_service.get_*_scope` resuelve
# ese programa y `program_scope_service.program_in_scope` decide.
#
# La negación es 404 y NO 403 a propósito: un coordinador ajeno no debe poder
# distinguir "no existe" de "existe pero es de otro programa" recorriendo el
# espacio de ids. Para objetos públicos (un programa) sí usamos 403, porque su
# existencia no es secreta.

def _object_not_found(message, flash=True):
    """404 con el envelope estándar. `flash` sólo en endpoints de escritura."""
    payload = {"data": None}
    if flash:
        payload["flash"] = [{"level": "danger", "message": message}]
    payload["error"] = {"code": "NOT_FOUND", "message": message}
    payload["meta"] = {}
    return jsonify(payload), 404


def _guard_object_scope(scope, message, flash=True):
    """
    None si el objeto existe y su programa está dentro del alcance del usuario
    actual; si no, la respuesta 404 (idéntica en ambos casos).

    Args:
        scope: dict devuelto por los resolvers de `permanence_service`, o None.
        message: mensaje en español para el 404.
    """
    if scope and scope_service.program_in_scope(current_user, scope.get('program_id')):
        return None
    return _object_not_found(message, flash=flash)


def _guard_user_program_read(scope, codename='permanence.api.list_students'):
    """
    Lectura del expediente de permanencia de un UserProgram: lo ve el propio
    estudiante, o alguien con el permiso indicado DENTRO del programa dueño del
    registro. Cualquier otro caso → 404, no revelamos que el registro existe.
    """
    if not scope:
        return _object_not_found('UserProgram no encontrado', flash=False)

    if current_user.id == scope.get('user_id'):
        return None

    program_id = scope.get('program_id')
    if (current_user.has_permission(codename)
            and scope_service.program_in_scope(current_user, program_id)):
        return None

    return _object_not_found('UserProgram no encontrado', flash=False)


@api_permanence.post('/user-program/<int:user_program_id>/confirm')
@login_required
@permission_required('permanence.api.confirm_enrollment')
def api_confirm_semester_enrollment(user_program_id):
    """
    El coordinador confirma la inscripción semestral de un estudiante.
    Acepta JSON o multipart/form-data (con archivo opcional 'payment_proof').
    """
    academic_period_id = _extract_form_or_json('academic_period_id')
    notes = (_extract_form_or_json('notes') or '').strip() or None
    force_raw = _extract_form_or_json('force', False)
    force = str(force_raw).lower() in ('true', '1', 'yes', 'on') if not isinstance(force_raw, bool) else force_raw

    if not academic_period_id:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Falta el periodo académico"}],
            "error": {"code": "MISSING_FIELD", "message": "academic_period_id es requerido"},
            "meta": {}
        }), 400

    # Alcance: el UserProgram debe pertenecer a un programa del coordinador.
    up_scope = svc.get_user_program_scope(user_program_id)
    denied = _guard_object_scope(up_scope, "UserProgram no encontrado")
    if denied:
        return denied

    try:
        payment_proof_path = _extract_payment_proof(up_scope['user_id'])
        schedule_path = _extract_schedule(up_scope['user_id'])

        se = svc.confirm_semester_enrollment(
            user_program_id=user_program_id,
            academic_period_id=int(academic_period_id),
            coordinator_id=current_user.id,
            notes=notes,
            payment_proof_path=payment_proof_path,
            schedule_path=schedule_path,
            force=force,
        )
        return jsonify({
            "data": se.to_dict(),
            "flash": [{"level": "success", "message": f"Inscripción del semestre {se.semester_number} confirmada exitosamente"}],
            "error": None,
            "meta": {}
        }), 200

    except ValueError as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "warning", "message": str(e)}],
            "error": {"code": "VALIDATION", "message": str(e)},
            "meta": {}
        }), 400

    except svc.StudentNotFound as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": str(e)}],
            "error": {"code": "NOT_FOUND", "message": str(e)},
            "meta": {}
        }), 404

    except svc.InvalidStateTransition as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "warning", "message": str(e)}],
            "error": {"code": "INVALID_STATE", "message": str(e)},
            "meta": {}
        }), 400

    except Exception as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Error al confirmar inscripción"}],
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500


@api_permanence.get('/program/<int:program_id>/enrollment-overview')
@login_required
@permission_required('permanence.api.list_students')
@program_scope_required(program_id_kwarg='program_id')
def api_get_enrollment_overview(program_id):
    """Vista consolidada de inscripción para la pestaña 'Inscripción'."""
    try:
        data = svc.get_enrollment_overview(program_id)
        return jsonify({"data": data, "error": None, "meta": {}}), 200
    except Exception as e:
        return jsonify({
            "data": None,
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500


@api_permanence.post('/user-program/<int:user_program_id>/reinstate')
@login_required
@permission_required('permanence.api.confirm_enrollment')
def api_reinstate_from_leave(user_program_id):
    """Reincorpora a un estudiante en baja temporal creando un nuevo SE activo."""
    academic_period_id = _extract_form_or_json('academic_period_id')
    notes = (_extract_form_or_json('notes') or '').strip() or None

    if not academic_period_id:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Falta el periodo académico"}],
            "error": {"code": "MISSING_FIELD", "message": "academic_period_id es requerido"},
            "meta": {}
        }), 400

    # Alcance: el UserProgram debe pertenecer a un programa del coordinador.
    up_scope = svc.get_user_program_scope(user_program_id)
    denied = _guard_object_scope(up_scope, "UserProgram no encontrado")
    if denied:
        return denied

    try:
        payment_proof_path = _extract_payment_proof(up_scope['user_id'])

        se = svc.reinstate_from_leave(
            user_program_id=user_program_id,
            academic_period_id=int(academic_period_id),
            coordinator_id=current_user.id,
            notes=notes,
            payment_proof_path=payment_proof_path,
        )
        return jsonify({
            "data": se.to_dict(),
            "flash": [{"level": "success", "message": f"Estudiante reincorporado al semestre {se.semester_number}"}],
            "error": None,
            "meta": {}
        }), 200

    except ValueError as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "warning", "message": str(e)}],
            "error": {"code": "VALIDATION", "message": str(e)},
            "meta": {}
        }), 400

    except svc.StudentNotFound as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": str(e)}],
            "error": {"code": "NOT_FOUND", "message": str(e)},
            "meta": {}
        }), 404

    except svc.InvalidStateTransition as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "warning", "message": str(e)}],
            "error": {"code": "INVALID_STATE", "message": str(e)},
            "meta": {}
        }), 400

    except Exception as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Error al reincorporar"}],
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500


@api_permanence.patch('/semester-enrollment/<int:semester_enrollment_id>/status')
@login_required
@permission_required('permanence.api.confirm_enrollment')
def api_update_enrollment_status(semester_enrollment_id):
    """Actualiza el estado de una inscripcion semestral."""
    data = request.get_json() or {}
    new_status = (data.get('status') or '').strip()
    notes = (data.get('notes') or '').strip() or None

    if not new_status:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Falta el nuevo estado"}],
            "error": {"code": "MISSING_FIELD", "message": "status es requerido"},
            "meta": {}
        }), 400

    # Alcance: la inscripción semestral debe pertenecer a un programa del
    # coordinador. Sin esto, cualquiera con el permiso podía marcar 'dropped'
    # (baja definitiva, con notificación y correo) a toda la institución
    # recorriendo el espacio de ids.
    denied = _guard_object_scope(
        svc.get_semester_enrollment_scope(semester_enrollment_id),
        "Inscripción semestral no encontrada",
    )
    if denied:
        return denied

    try:
        se = svc.update_enrollment_status(
            semester_enrollment_id=semester_enrollment_id,
            new_status=new_status,
            coordinator_id=current_user.id,
            notes=notes
        )
        STATUS_LABELS = {
            'active': 'Activo',
            'completed': 'Completado',
            'on_leave': 'Baja temporal',
            'dropped': 'Baja definitiva',
        }
        label = STATUS_LABELS.get(new_status, new_status)
        return jsonify({
            "data": se.to_dict(),
            "flash": [{"level": "success", "message": f"Estado actualizado a: {label}"}],
            "error": None,
            "meta": {}
        }), 200

    except svc.StudentNotFound as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": str(e)}],
            "error": {"code": "NOT_FOUND", "message": str(e)},
            "meta": {}
        }), 404

    except ValueError as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "warning", "message": str(e)}],
            "error": {"code": "INVALID_DATA", "message": str(e)},
            "meta": {}
        }), 400

    except Exception as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Error al actualizar estado"}],
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500


@api_permanence.get('/user-program/<int:user_program_id>/status')
@login_required
def api_get_student_permanence(user_program_id):
    """
    Obtiene el estado de permanencia de un estudiante.
    El propio estudiante puede ver el suyo; el personal, sólo el de los
    estudiantes de sus programas.
    """
    denied = _guard_user_program_read(svc.get_user_program_scope(user_program_id))
    if denied:
        return denied

    try:
        data = svc.get_student_permanence(user_program_id)
        return jsonify({
            "data": data,
            "error": None,
            "meta": {}
        }), 200
    except Exception as e:
        return jsonify({
            "data": None,
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500


# ── Ventanas de entrega (Coordinador) ─────────────────────────────────────────

@api_permanence.get('/program/<int:program_id>/deadlines')
@login_required
@permission_required('permanence.api.manage_deadlines')
@program_scope_required(program_id_kwarg='program_id')
def api_get_deadlines(program_id):
    """Lista ventanas de entrega del periodo activo para un programa.
    Acepta `?include_archived=true` para mostrar también las archivadas."""
    period_id = request.args.get('period_id', type=int)
    include_archived = request.args.get('include_archived', 'false', type=str).lower() == 'true'
    try:
        data = svc.get_deadlines_for_program(
            program_id,
            academic_period_id=period_id,
            include_archived=include_archived,
        )
        return jsonify({"data": data, "error": None, "meta": {"count": len(data)}}), 200
    except Exception as e:
        return jsonify({"data": None, "error": {"code": "SERVER_ERROR", "message": str(e)}, "meta": {}}), 500


@api_permanence.post('/program/<int:program_id>/deadlines')
@login_required
@permission_required('permanence.api.manage_deadlines')
@program_scope_required(program_id_kwarg='program_id')
def api_create_deadline(program_id):
    """Crea una ventana de entrega. Body: {archive_id, label, sequence, academic_period_id, opens_at?, closes_at?}"""
    from datetime import datetime
    data = request.get_json() or {}
    # `archive_id` viaja en el cuerpo como UUID público; el servicio sigue
    # recibiendo el entero.
    archive = Archive.by_uuid(data.get('archive_id'))
    archive_id = archive.id if archive else None
    label = (data.get('label') or '').strip()
    sequence = data.get('sequence', 1)
    academic_period_id = data.get('academic_period_id')
    opens_at_raw = data.get('opens_at')
    closes_at_raw = data.get('closes_at')

    if not archive_id or not label:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "archive_id y label son requeridos"}],
            "error": {"code": "MISSING_FIELD", "message": "archive_id y label son requeridos"},
            "meta": {}
        }), 400

    if not academic_period_id:
        from app.models.academic_period import AcademicPeriod
        period = AcademicPeriod.get_active_period()
        if not period:
            return jsonify({
                "data": None,
                "flash": [{"level": "danger", "message": "No hay periodo académico activo"}],
                "error": {"code": "NO_ACTIVE_PERIOD", "message": "No hay periodo académico activo"},
                "meta": {}
            }), 400
        academic_period_id = period.id

    opens_at = datetime.fromisoformat(opens_at_raw) if opens_at_raw else None
    closes_at = datetime.fromisoformat(closes_at_raw) if closes_at_raw else None

    try:
        dl = svc.create_document_deadline(
            program_id=program_id,
            archive_id=archive_id,
            academic_period_id=academic_period_id,
            label=label,
            sequence=int(sequence),
            opens_at=opens_at,
            closes_at=closes_at,
            coordinator_id=current_user.id,
        )
        return jsonify({
            "data": dl.to_dict(),
            "flash": [{"level": "success", "message": f'Ventana "{dl.label}" creada exitosamente'}],
            "error": None,
            "meta": {}
        }), 201
    except ValueError as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": str(e)}],
            "error": {"code": "INVALID_DATA", "message": str(e)},
            "meta": {}
        }), 400
    except Exception as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Error al crear ventana"}],
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500


@api_permanence.patch('/deadlines/<int:deadline_id>/toggle')
@login_required
@permission_required('permanence.api.manage_deadlines')
def api_toggle_deadline(deadline_id):
    """Abre/cierra manualmente una ventana. Body: {is_open: bool}"""
    data = request.get_json() or {}
    if 'is_open' not in data:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Campo is_open requerido"}],
            "error": {"code": "MISSING_FIELD", "message": "is_open es requerido"},
            "meta": {}
        }), 400

    # Alcance: la ventana debe pertenecer a un programa del coordinador.
    denied = _guard_object_scope(
        svc.get_deadline_scope(deadline_id), "Ventana no encontrada"
    )
    if denied:
        return denied

    try:
        dl = svc.toggle_document_deadline(
            deadline_id=deadline_id,
            is_open=bool(data['is_open']),
            coordinator_id=current_user.id,
        )
        state = 'abierta' if dl.is_open else 'cerrada'
        return jsonify({
            "data": dl.to_dict(),
            "flash": [{"level": "success", "message": f'Ventana "{dl.label}" {state}'}],
            "error": None,
            "meta": {}
        }), 200
    except svc.StudentNotFound as e:
        return jsonify({"data": None, "flash": [{"level": "danger", "message": str(e)}],
                        "error": {"code": "NOT_FOUND", "message": str(e)}, "meta": {}}), 404
    except Exception as e:
        return jsonify({"data": None, "flash": [{"level": "danger", "message": "Error al actualizar ventana"}],
                        "error": {"code": "SERVER_ERROR", "message": str(e)}, "meta": {}}), 500


@api_permanence.patch('/deadlines/<int:deadline_id>')
@login_required
@permission_required('permanence.api.manage_deadlines')
def api_update_deadline(deadline_id):
    """Edita una ventana: label / opens_at / closes_at / is_open."""
    from datetime import datetime

    # Alcance: la ventana debe pertenecer a un programa del coordinador.
    denied = _guard_object_scope(
        svc.get_deadline_scope(deadline_id), "Ventana no encontrada"
    )
    if denied:
        return denied

    data = request.get_json() or {}

    def _parse_dt(value):
        if value in (None, ''):
            return None
        try:
            # Acepta 'YYYY-MM-DD' (date input) o ISO string
            if 'T' in value:
                return datetime.fromisoformat(value.replace('Z', '+00:00'))
            return datetime.strptime(value, '%Y-%m-%d')
        except (TypeError, ValueError):
            return None

    label = data.get('label')
    opens_at = _parse_dt(data.get('opens_at')) if 'opens_at' in data else None
    closes_at = _parse_dt(data.get('closes_at')) if 'closes_at' in data else None
    is_open = data.get('is_open') if 'is_open' in data else None

    try:
        dl = svc.update_document_deadline(
            deadline_id=deadline_id,
            coordinator_id=current_user.id,
            label=label,
            opens_at=opens_at,
            closes_at=closes_at,
            is_open=is_open,
        )
        return jsonify({
            "data": dl.to_dict(),
            "flash": [{"level": "success", "message": "Ventana actualizada"}],
            "error": None,
            "meta": {}
        }), 200
    except svc.StudentNotFound as e:
        return jsonify({"data": None, "flash": [{"level": "danger", "message": str(e)}],
                        "error": {"code": "NOT_FOUND", "message": str(e)}, "meta": {}}), 404
    except Exception as e:
        return jsonify({"data": None, "flash": [{"level": "danger", "message": "Error al actualizar ventana"}],
                        "error": {"code": "SERVER_ERROR", "message": str(e)}, "meta": {}}), 500


@api_permanence.delete('/deadlines/<int:deadline_id>')
@login_required
@permission_required('permanence.api.manage_deadlines')
def api_delete_deadline(deadline_id):
    """
    Archiva una ventana (soft-delete). Mantiene FK con submissions y permite
    restaurar después. El verbo HTTP DELETE se conserva para compatibilidad.
    """
    # Alcance: la ventana debe pertenecer a un programa del coordinador.
    denied = _guard_object_scope(
        svc.get_deadline_scope(deadline_id), "Ventana no encontrada"
    )
    if denied:
        return denied

    try:
        svc.archive_document_deadline(deadline_id=deadline_id, coordinator_id=current_user.id)
        return jsonify({
            "data": None,
            "flash": [{"level": "success", "message": "Ventana archivada"}],
            "error": None,
            "meta": {}
        }), 200
    except svc.StudentNotFound as e:
        return jsonify({"data": None, "flash": [{"level": "danger", "message": str(e)}],
                        "error": {"code": "NOT_FOUND", "message": str(e)}, "meta": {}}), 404
    except svc.InvalidStateTransition as e:
        return jsonify({"data": None, "flash": [{"level": "warning", "message": str(e)}],
                        "error": {"code": "INVALID_STATE", "message": str(e)}, "meta": {}}), 400
    except Exception as e:
        return jsonify({"data": None, "flash": [{"level": "danger", "message": "Error al archivar ventana"}],
                        "error": {"code": "SERVER_ERROR", "message": str(e)}, "meta": {}}), 500


@api_permanence.post('/deadlines/<int:deadline_id>/restore')
@login_required
@permission_required('permanence.api.manage_deadlines')
def api_restore_deadline(deadline_id):
    """Restaura una ventana archivada (la deja cerrada por seguridad)."""
    # Alcance: la ventana debe pertenecer a un programa del coordinador.
    denied = _guard_object_scope(
        svc.get_deadline_scope(deadline_id), "Ventana no encontrada"
    )
    if denied:
        return denied

    try:
        svc.restore_document_deadline(deadline_id=deadline_id, coordinator_id=current_user.id)
        return jsonify({
            "data": None,
            "flash": [{"level": "success", "message": "Ventana restaurada (queda cerrada)"}],
            "error": None,
            "meta": {}
        }), 200
    except svc.StudentNotFound as e:
        return jsonify({"data": None, "flash": [{"level": "danger", "message": str(e)}],
                        "error": {"code": "NOT_FOUND", "message": str(e)}, "meta": {}}), 404
    except svc.InvalidStateTransition as e:
        return jsonify({"data": None, "flash": [{"level": "warning", "message": str(e)}],
                        "error": {"code": "INVALID_STATE", "message": str(e)}, "meta": {}}), 400
    except Exception as e:
        return jsonify({"data": None, "flash": [{"level": "danger", "message": "Error al restaurar ventana"}],
                        "error": {"code": "SERVER_ERROR", "message": str(e)}, "meta": {}}), 500


# ── Documentos del estudiante ──────────────────────────────────────────────────

@api_permanence.get('/user-program/<int:user_program_id>/documents')
@login_required
def api_get_student_documents(user_program_id):
    """Lista ventanas y estado de submission del estudiante en el periodo activo."""
    denied = _guard_user_program_read(svc.get_user_program_scope(user_program_id))
    if denied:
        return denied

    try:
        data = svc.get_student_documents_for_period(user_program_id)
        return jsonify({"data": data, "error": None, "meta": {"count": len(data)}}), 200
    except svc.StudentNotFound as e:
        return jsonify({"data": None, "error": {"code": "NOT_FOUND", "message": str(e)}, "meta": {}}), 404
    except Exception as e:
        return jsonify({"data": None, "error": {"code": "SERVER_ERROR", "message": str(e)}, "meta": {}}), 500


@api_permanence.post('/user-program/<int:user_program_id>/documents/<int:deadline_id>')
@login_required
def api_submit_permanence_document(user_program_id, deadline_id):
    """El estudiante sube un documento para una ventana de entrega. Multipart con field 'file'."""
    from flask import request as req
    file = req.files.get('file')
    if not file or not file.filename:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "No se recibió ningún archivo"}],
            "error": {"code": "MISSING_FILE", "message": "Campo 'file' requerido"},
            "meta": {}
        }), 400

    try:
        sub = svc.submit_permanence_document(
            user_program_id=user_program_id,
            document_deadline_id=deadline_id,
            file_storage=file,
            student_id=current_user.id,
        )
        return jsonify({
            "data": sub,
            "flash": [{"level": "success", "message": "Documento enviado exitosamente. Espera la revisión del coordinador."}],
            "error": None,
            "meta": {}
        }), 201
    except svc.StudentNotFound as e:
        return jsonify({"data": None, "flash": [{"level": "danger", "message": str(e)}],
                        "error": {"code": "NOT_FOUND", "message": str(e)}, "meta": {}}), 404
    except svc.InvalidStateTransition as e:
        return jsonify({"data": None, "flash": [{"level": "warning", "message": str(e)}],
                        "error": {"code": "INVALID_STATE", "message": str(e)}, "meta": {}}), 400
    except Exception as e:
        return jsonify({"data": None, "flash": [{"level": "danger", "message": "Error al subir documento"}],
                        "error": {"code": "SERVER_ERROR", "message": str(e)}, "meta": {}}), 500


# ── Revisión de documentos (Coordinador) ──────────────────────────────────────

@api_permanence.get('/program/<int:program_id>/pending-documents')
@login_required
@permission_required('permanence.api.review_doc')
@program_scope_required(program_id_kwarg='program_id')
def api_get_pending_documents(program_id):
    """Lista submissions de permanencia en estado 'review' para el programa."""
    try:
        data = svc.get_pending_documents(program_id)
        return jsonify({"data": data, "error": None, "meta": {"count": len(data)}}), 200
    except Exception as e:
        return jsonify({"data": None, "error": {"code": "SERVER_ERROR", "message": str(e)}, "meta": {}}), 500


@api_permanence.post('/submissions/<uuid:submission_uuid>/review')
@login_required
@permission_required('permanence.api.review_doc')
def api_review_permanence_document(submission_uuid):
    """Aprueba o rechaza un documento. Body: {status: 'approved'|'rejected', notes?: str}"""
    data = request.get_json() or {}
    status = (data.get('status') or '').strip()
    notes = (data.get('notes') or '').strip() or None

    if status not in ('approved', 'rejected'):
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "status debe ser 'approved' o 'rejected'"}],
            "error": {"code": "INVALID_DATA", "message": "status inválido"},
            "meta": {}
        }), 400

    # Alcance: la submission debe pertenecer a un programa del coordinador.
    # Un UUID desconocido llega aquí como None y `get_submission_scope(None)`
    # devuelve None, que la guarda traduce al MISMO 404 que una entrega ajena.
    row = Submission.by_uuid(submission_uuid)
    submission_id = row.id if row else None
    denied = _guard_object_scope(
        svc.get_submission_scope(submission_id), "Documento no encontrado"
    )
    if denied:
        return denied

    try:
        sub = svc.review_permanence_document(
            submission_id=submission_id,
            coordinator_id=current_user.id,
            status=status,
            notes=notes,
        )
        action = 'aprobado' if status == 'approved' else 'rechazado'
        return jsonify({
            "data": sub,
            "flash": [{"level": "success", "message": f"Documento {action} exitosamente"}],
            "error": None,
            "meta": {}
        }), 200
    except svc.StudentNotFound as e:
        return jsonify({"data": None, "flash": [{"level": "danger", "message": str(e)}],
                        "error": {"code": "NOT_FOUND", "message": str(e)}, "meta": {}}), 404
    except (svc.InvalidStateTransition, ValueError) as e:
        return jsonify({"data": None, "flash": [{"level": "warning", "message": str(e)}],
                        "error": {"code": "INVALID_STATE", "message": str(e)}, "meta": {}}), 400
    except Exception as e:
        return jsonify({"data": None, "flash": [{"level": "danger", "message": "Error al revisar documento"}],
                        "error": {"code": "SERVER_ERROR", "message": str(e)}, "meta": {}}), 500


# ── Referencia Bancaria ────────────────────────────────────────────────────────

@api_permanence.get('/user-program/<int:user_program_id>/payment-reference')
@login_required
def api_get_payment_reference(user_program_id):
    """
    Retorna la referencia bancaria del estudiante para el periodo activo.
    Si no está configurada, retorna configured=False (no es error HTTP).
    """
    from app.services.payment_reference_service import get_payment_reference_for_student

    denied = _guard_user_program_read(svc.get_user_program_scope(user_program_id))
    if denied:
        return denied

    try:
        data = get_payment_reference_for_student(user_program_id)
        return jsonify({"data": data, "error": None, "meta": {}}), 200
    except Exception as e:
        return jsonify({
            "data": None,
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500


# ── Solicitudes de Baja Temporal ──────────────────────────────────────────────

@api_permanence.get('/program/<int:program_id>/leave-requests')
@login_required
@permission_required('permanence.api.review_doc')
@program_scope_required(program_id_kwarg='program_id')
def api_get_pending_leave_requests(program_id):
    """Lista solicitudes de baja temporal en estado 'review' para el programa."""
    try:
        data = svc.get_pending_leave_requests(program_id)
        return jsonify({"data": data, "error": None, "meta": {"count": len(data)}}), 200
    except Exception as e:
        return jsonify({"data": None, "error": {"code": "SERVER_ERROR", "message": str(e)}, "meta": {}}), 500


@api_permanence.get('/user-program/<int:user_program_id>/leave-request')
@login_required
def api_get_student_leave_request(user_program_id):
    """Retorna el estado de la solicitud de baja temporal del estudiante."""
    denied = _guard_user_program_read(svc.get_user_program_scope(user_program_id))
    if denied:
        return denied

    try:
        data = svc.get_student_leave_request(user_program_id)
        return jsonify({"data": data, "error": None, "meta": {}}), 200
    except svc.StudentNotFound as e:
        return jsonify({"data": None, "error": {"code": "NOT_FOUND", "message": str(e)}, "meta": {}}), 404
    except Exception as e:
        return jsonify({"data": None, "error": {"code": "SERVER_ERROR", "message": str(e)}, "meta": {}}), 500


@api_permanence.post('/user-program/<int:user_program_id>/leave-request')
@login_required
def api_submit_leave_request(user_program_id):
    """El estudiante sube la Solicitud de Baja Temporal (multipart, field 'file')."""
    from flask import request as req
    file = req.files.get('file')
    if not file or not file.filename:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "No se recibió ningún archivo"}],
            "error": {"code": "MISSING_FILE", "message": "Campo 'file' requerido"},
            "meta": {}
        }), 400

    try:
        sub = svc.submit_leave_request(
            user_program_id=user_program_id,
            file_storage=file,
            student_id=current_user.id,
        )
        return jsonify({
            "data": sub,
            "flash": [{"level": "success", "message": "Solicitud enviada. El coordinador la revisará a la brevedad."}],
            "error": None,
            "meta": {}
        }), 201
    except svc.StudentNotFound as e:
        return jsonify({"data": None, "flash": [{"level": "danger", "message": str(e)}],
                        "error": {"code": "NOT_FOUND", "message": str(e)}, "meta": {}}), 404
    except svc.InvalidStateTransition as e:
        return jsonify({"data": None, "flash": [{"level": "warning", "message": str(e)}],
                        "error": {"code": "INVALID_STATE", "message": str(e)}, "meta": {}}), 400
    except Exception as e:
        return jsonify({"data": None, "flash": [{"level": "danger", "message": "Error al subir la solicitud"}],
                        "error": {"code": "SERVER_ERROR", "message": str(e)}, "meta": {}}), 500


@api_permanence.post('/submissions/<uuid:submission_uuid>/leave-request')
@login_required
@permission_required('permanence.api.review_doc')
def api_process_leave_request(submission_uuid):
    """Aprueba o rechaza una solicitud de baja temporal. Body: {approve: bool, notes?: str}"""
    data = request.get_json() or {}
    approve = data.get('approve')
    notes = (data.get('notes') or '').strip() or None

    if approve is None:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "El campo 'approve' es requerido"}],
            "error": {"code": "MISSING_FIELD", "message": "approve es requerido"},
            "meta": {}
        }), 400

    # Alcance: la solicitud debe pertenecer a un programa del coordinador.
    # Un UUID desconocido llega aquí como None y `get_submission_scope(None)`
    # devuelve None, que la guarda traduce al MISMO 404 que una entrega ajena.
    row = Submission.by_uuid(submission_uuid)
    submission_id = row.id if row else None
    denied = _guard_object_scope(
        svc.get_submission_scope(submission_id), "Solicitud no encontrada"
    )
    if denied:
        return denied

    try:
        result = svc.process_leave_request(
            submission_id=submission_id,
            coordinator_id=current_user.id,
            approve=bool(approve),
            notes=notes,
        )
        action = 'aprobada' if approve else 'rechazada'
        return jsonify({
            "data": result,
            "flash": [{"level": "success", "message": f"Solicitud de baja temporal {action}"}],
            "error": None,
            "meta": {}
        }), 200
    except svc.StudentNotFound as e:
        return jsonify({"data": None, "flash": [{"level": "danger", "message": str(e)}],
                        "error": {"code": "NOT_FOUND", "message": str(e)}, "meta": {}}), 404
    except svc.InvalidStateTransition as e:
        return jsonify({"data": None, "flash": [{"level": "warning", "message": str(e)}],
                        "error": {"code": "INVALID_STATE", "message": str(e)}, "meta": {}}), 400
    except Exception as e:
        return jsonify({"data": None, "flash": [{"level": "danger", "message": "Error al procesar la solicitud"}],
                        "error": {"code": "SERVER_ERROR", "message": str(e)}, "meta": {}}), 500


@api_permanence.post('/program/<int:program_id>/deadlines/conacyt-monthly')
@login_required
@permission_required('permanence.api.manage_deadlines')
@program_scope_required(program_id_kwarg='program_id')
def api_create_conacyt_monthly_deadlines(program_id):
    """
    Crea las ventanas mensuales CONACyT del periodo activo (idempotente).
    Body opcional: {academic_period_id: int}
    """
    from app.models.academic_period import AcademicPeriod
    data = request.get_json() or {}
    academic_period_id = data.get('academic_period_id')

    if not academic_period_id:
        period = AcademicPeriod.get_active_period()
        if not period:
            return jsonify({
                "data": None,
                "flash": [{"level": "danger", "message": "No hay periodo académico activo"}],
                "error": {"code": "NO_ACTIVE_PERIOD", "message": "No hay periodo académico activo"},
                "meta": {}
            }), 400
        academic_period_id = period.id

    try:
        result = svc.create_monthly_conacyt_deadlines(
            program_id=program_id,
            academic_period_id=academic_period_id,
            coordinator_id=current_user.id,
        )
        created = result['created']
        skipped = result['skipped']
        if created == 0:
            msg = f"Todas las ventanas CONACyT ya existen para este periodo ({skipped} omitidas)"
            level = "info"
        elif skipped > 0:
            msg = f"{created} ventana(s) creadas, {skipped} ya existían"
            level = "success"
        else:
            msg = f"{created} ventana(s) mensuales CONACyT creadas exitosamente"
            level = "success"

        return jsonify({
            "data": result,
            "flash": [{"level": level, "message": msg}],
            "error": None,
            "meta": {}
        }), 201 if created > 0 else 200

    except ValueError as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": str(e)}],
            "error": {"code": "INVALID_DATA", "message": str(e)},
            "meta": {}
        }), 400
    except svc.StudentNotFound as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": str(e)}],
            "error": {"code": "NOT_FOUND", "message": str(e)},
            "meta": {}
        }), 404
    except Exception as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Error al crear ventanas CONACyT"}],
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500


@api_permanence.patch('/user-program/<int:user_program_id>/conacyt-scholarship')
@login_required
@permission_required('permanence.api.manage_students')
def api_toggle_conacyt_scholarship(user_program_id):
    """Activa o desactiva la beca CONACyT de un estudiante."""
    # Alcance: el estudiante debe pertenecer a un programa del coordinador.
    up_scope = svc.get_user_program_scope(user_program_id)
    denied = _guard_object_scope(up_scope, "Estudiante no encontrado")
    if denied:
        return denied

    data = request.get_json() or {}
    # Acepta valor explícito o simplemente alterna el valor actual
    value = bool(data['value']) if 'value' in data else None

    try:
        result = svc.set_conacyt_scholarship(
            user_program_id=user_program_id,
            coordinator_id=current_user.id,
            value=value,
        )
    except svc.StudentNotFound as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Estudiante no encontrado"}],
            "error": {"code": "NOT_FOUND", "message": str(e)},
            "meta": {}
        }), 404
    except Exception:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Error al actualizar beca CONACyT"}],
            "error": {"code": "SERVER_ERROR", "message": "Error interno"},
            "meta": {}
        }), 500

    label = 'activada' if result['has_conacyt_scholarship'] else 'desactivada'
    return jsonify({
        "data": {"has_conacyt_scholarship": result['has_conacyt_scholarship']},
        "flash": [{"level": "success", "message": f"Beca CONACyT {label} exitosamente"}],
        "error": None,
        "meta": {}
    }), 200


# ── Transición semestral (Pasar Semestre) ─────────────────────────────────────

#: Capacidad DECLARADA para correr la transición sobre TODOS los programas.
#:
#: `permanence.api.advance_bulk` dice que el llamador puede correr una
#: transición; este codename dice que puede correrla sobre el instituto entero.
#: Antes esa segunda condición se leía con `is_global_scope()`, que en realidad
#: pregunta por `academic_periods.api.create`: un permiso de otro recurso, que
#: no aparece por ningún lado en el catálogo junto a la transición y que dejaba
#: fuera del cambio de semestre a cualquier despliegue donde `advance_bulk`
#: estuviera concedido por separado. El requisito ahora se llama por su nombre.
#:
#: Se evalúa a nivel de ROL (`has_role_permission`) y no con `has_permission`:
#: una delegación siempre lleva `program_id`, y "puedo trabajar en el programa
#: A" jamás debe convertirse en "puedo cerrar el periodo de los 20 programas".
#: Quien lo tenga delegado corre su transición indicando su `program_id`.
GLOBAL_TRANSITION_PERMISSION = 'permanence.api.transition_all'


def _may_run_global_transition(user) -> bool:
    """True si el rol del usuario declara la transición de todos los programas."""
    return bool(user) and user.has_role_permission(GLOBAL_TRANSITION_PERMISSION)


def _deny_global_transition():
    """
    403 en español para la transición GLOBAL, nombrando el permiso que falta
    para que la denegación sea diagnosticable desde el catálogo de permisos.
    """
    msg = ('La transición de todos los programas requiere el permiso '
           '«permanence.api.transition_all» (jefatura de posgrado). '
           'Indica un programa específico para correr sólo el tuyo.')
    return jsonify({
        "data": None,
        "flash": [{"level": "danger", "message": msg}],
        "error": {"code": "FORBIDDEN", "message": msg},
        "meta": {}
    }), 403


@api_permanence.get('/transition/preview')
@login_required
@permission_required('permanence.api.advance_bulk')
def api_transition_preview():
    """
    Vista previa de la transición semestral para un programa o para todos.

    Permisos:
        permanence.api.advance_bulk    — correr una transición (obligatorio)
        permanence.api.transition_all  — además, correrla sobre TODOS los
                                         programas (sólo el modo global)

    Query params:
        program_id  (int|"all") — omitido o "all" → preview global
        source_period_id (int) — periodo origen
        target_period_id (int) — periodo destino
    """
    program_id_raw = request.args.get('program_id')
    source_period_id = request.args.get('source_period_id', type=int)
    target_period_id = request.args.get('target_period_id', type=int)

    if not source_period_id or not target_period_id:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "source_period_id y target_period_id son requeridos"}],
            "error": {"code": "MISSING_FIELD", "message": "source_period_id y target_period_id son requeridos"},
            "meta": {}
        }), 400

    global_mode = (not program_id_raw or str(program_id_raw).strip().lower() == 'all')

    # Capacidad + alcance. El modo global recorre TODOS los programas y exige
    # la capacidad declarada `permanence.api.transition_all`. Con un programa
    # concreto basta `advance_bulk` y que el programa esté en su alcance.
    if global_mode:
        if not _may_run_global_transition(current_user):
            return _deny_global_transition()
    else:
        try:
            program_id = int(program_id_raw)
        except (TypeError, ValueError):
            return jsonify({
                "data": None,
                "error": {"code": "INVALID_PARAM", "message": "program_id debe ser un entero o 'all'"},
                "meta": {}
            }), 400
        denied = guard_program_scope(program_id)
        if denied:
            return denied

    try:
        if global_mode:
            data = tsvc.preview_global(source_period_id, target_period_id)
        else:
            data = tsvc.preview_program(program_id, source_period_id, target_period_id)

        return jsonify({"data": data, "error": None, "meta": {}}), 200

    except tsvc.PeriodNotFound as e:
        return jsonify({
            "data": None,
            "error": {"code": "NOT_FOUND", "message": str(e)},
            "meta": {}
        }), 404
    except tsvc.ProgramNotFound as e:
        return jsonify({
            "data": None,
            "error": {"code": "NOT_FOUND", "message": str(e)},
            "meta": {}
        }), 404
    except tsvc.TransitionError as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "warning", "message": str(e)}],
            "error": {"code": "TRANSITION_ERROR", "message": str(e)},
            "meta": {}
        }), 400
    except Exception as e:
        return jsonify({
            "data": None,
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500


@api_permanence.post('/transition/execute')
@login_required
@permission_required('permanence.api.advance_bulk')
def api_transition_execute():
    """
    Ejecuta la transición semestral (cierre de periodo + avance masivo).

    Permisos:
        permanence.api.advance_bulk    — correr una transición (obligatorio)
        permanence.api.transition_all  — además, correrla sobre TODOS los
                                         programas (cuando se omite program_id)

    Body JSON:
        source_period_id (int) — requerido
        target_period_id (int) — requerido
        program_id       (int, opcional) — omitido → ejecuta en todos los programas
    """
    data = request.get_json() or {}
    source_period_id = data.get('source_period_id')
    target_period_id = data.get('target_period_id')
    program_id = data.get('program_id')

    if not source_period_id or not target_period_id:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "source_period_id y target_period_id son requeridos"}],
            "error": {"code": "MISSING_FIELD", "message": "source_period_id y target_period_id son requeridos"},
            "meta": {}
        }), 400

    try:
        source_period_id = int(source_period_id)
        target_period_id = int(target_period_id)
    except (TypeError, ValueError):
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Los IDs de periodo deben ser enteros"}],
            "error": {"code": "INVALID_PARAM", "message": "source_period_id y target_period_id deben ser enteros"},
            "meta": {}
        }), 400

    # Capacidad + alcance. Sin program_id la transición corre sobre TODOS los
    # programas y exige la capacidad declarada `permanence.api.transition_all`.
    # Con program_id, el programa debe estar dentro del alcance del llamador.
    if program_id:
        try:
            program_id = int(program_id)
        except (TypeError, ValueError):
            return jsonify({
                "data": None,
                "error": {"code": "INVALID_PARAM", "message": "program_id debe ser un entero"},
                "meta": {}
            }), 400
        denied = guard_program_scope(program_id)
        if denied:
            return denied
    elif not _may_run_global_transition(current_user):
        return _deny_global_transition()

    try:
        if program_id:
            result = tsvc.execute_program_transition(
                program_id=program_id,
                source_period_id=source_period_id,
                target_period_id=target_period_id,
                coordinator_id=current_user.id,
            )
        else:
            result = tsvc.execute_global_transition(
                source_period_id=source_period_id,
                target_period_id=target_period_id,
                coordinator_id=current_user.id,
            )

        # ── Respaldo preventivo (snapshot) de aspirantes Δ=2 expirados ──
        archive_block = None
        if program_id:
            expired_ids = result.get('expired_user_program_ids') or []
            stats_for_response = result
        else:
            expired_ids = (result.get('total') or {}).get('expired_user_program_ids') or []
            stats_for_response = result

        if expired_ids:
            try:
                import app.services.applicant_archive_service as archive_svc
                run = archive_svc.create_purge_run(
                    user_program_ids=expired_ids,
                    purge_type='transition_snapshot',
                    initiated_by_id=current_user.id,
                    program_id=program_id if program_id else None,
                    source_period_id=source_period_id,
                    target_period_id=target_period_id,
                    notes=(
                        f'Snapshot preventivo de transición '
                        f'{source_period_id}→{target_period_id}'
                    ),
                )
                archive_block = {
                    'run_id': run.run_id,
                    'archive_url': f'/api/v1/admin/purge/{run.run_id}/archive.zip',
                    'expires_at': run.expires_at.isoformat() if run.expires_at else None,
                    'item_count': len(expired_ids),
                    'size_bytes': run.archive_size_bytes,
                }
            except Exception as e:
                # Snapshot fallido no debe romper la transición ya commiteada.
                archive_block = {'error': f'No se pudo generar snapshot: {e}'}

        return jsonify({
            "data": {
                **(stats_for_response if isinstance(stats_for_response, dict) else {}),
                "archive": archive_block,
            },
            "error": None,
            "meta": {}
        }), 200

    except tsvc.PeriodNotFound as e:
        return jsonify({
            "data": None,
            "error": {"code": "NOT_FOUND", "message": str(e)},
            "meta": {}
        }), 404
    except tsvc.ProgramNotFound as e:
        return jsonify({
            "data": None,
            "error": {"code": "NOT_FOUND", "message": str(e)},
            "meta": {}
        }), 404
    except tsvc.TransitionError as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "warning", "message": str(e)}],
            "error": {"code": "TRANSITION_ERROR", "message": str(e)},
            "meta": {}
        }), 400
    except Exception as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Error al ejecutar la transición semestral"}],
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500


@api_permanence.get('/my-enrollment')
@login_required
@permission_required('permanence.api.view_my_enrollment')
def api_get_my_enrollment():
    """
    El estudiante consulta su inscripción semestral del periodo activo.

    Returns el SemesterEnrollment activo + datos del programa + referencia de pago (stub).
    """
    from app.models.academic_period import AcademicPeriod
    from app.models.semester_enrollment import SemesterEnrollment
    from app.services.payment_reference_service import PaymentReferenceService

    active_period = AcademicPeriod.get_active_period()
    if not active_period:
        return jsonify({
            "data": None,
            "error": {"code": "NO_ACTIVE_PERIOD", "message": "No hay periodo académico activo"},
            "meta": {}
        }), 404

    # Buscar UserProgram del usuario (puede estar en varios programas — usamos el enrolled)
    up = (
        UserProgram.query
        .filter_by(user_id=current_user.id, admission_status='enrolled')
        .first()
    )
    if not up:
        return jsonify({
            "data": None,
            "error": {"code": "NOT_FOUND", "message": "No tienes una inscripción activa"},
            "meta": {}
        }), 404

    try:
        se = SemesterEnrollment.query.filter_by(
            user_program_id=up.id,
            academic_period_id=active_period.id,
        ).first()

        payment_ref = PaymentReferenceService.generate(up.id, active_period.id)

        return jsonify({
            "data": {
                "user_program": up.to_dict(),
                "program": {
                    "id": up.program.id,
                    "name": up.program.name,
                } if up.program else None,
                "current_period": active_period.to_dict(),
                "enrollment": se.to_dict() if se else None,
                "payment_reference": payment_ref,
            },
            "error": None,
            "meta": {}
        }), 200

    except Exception as e:
        return jsonify({
            "data": None,
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500


@api_permanence.post('/my-enrollment/payment-proof')
@login_required
@permission_required('permanence.api.upload_my_payment')
def api_upload_my_payment_proof():
    """
    El estudiante sube su comprobante de pago semestral (PDF).

    Multipart con campo 'payment_proof'.
    Auto-detecta el SemesterEnrollment del periodo activo.
    NO marca enrollment_confirmed=True (eso lo hace el coordinador).
    Notifica al coordinador del programa.
    """
    from app.models.academic_period import AcademicPeriod
    from app.models.semester_enrollment import SemesterEnrollment
    from app.utils.files import save_user_doc
    from app.utils.datetime_utils import now_local as _now

    file = request.files.get('payment_proof')
    if not file or not file.filename:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "No se recibió ningún archivo"}],
            "error": {"code": "MISSING_FILE", "message": "Campo 'payment_proof' requerido"},
            "meta": {}
        }), 400

    if not file.filename.lower().endswith('.pdf'):
        return jsonify({
            "data": None,
            "flash": [{"level": "warning", "message": "El comprobante debe ser un archivo PDF"}],
            "error": {"code": "INVALID_FILE", "message": "Solo se aceptan archivos PDF"},
            "meta": {}
        }), 400

    active_period = AcademicPeriod.get_active_period()
    if not active_period:
        return jsonify({
            "data": None,
            "error": {"code": "NO_ACTIVE_PERIOD", "message": "No hay periodo académico activo"},
            "meta": {}
        }), 404

    up = (
        UserProgram.query
        .filter_by(user_id=current_user.id, admission_status='enrolled')
        .first()
    )
    if not up:
        return jsonify({
            "data": None,
            "error": {"code": "NOT_FOUND", "message": "No tienes una inscripción activa"},
            "meta": {}
        }), 404

    try:
        se = SemesterEnrollment.query.filter_by(
            user_program_id=up.id,
            academic_period_id=active_period.id,
        ).first()

        if not se:
            return jsonify({
                "data": None,
                "flash": [{"level": "warning", "message": "No tienes inscripción semestral en el periodo activo"}],
                "error": {"code": "NOT_FOUND", "message": "SemesterEnrollment no encontrado para el periodo activo"},
                "meta": {}
            }), 404

        if se.enrollment_confirmed:
            return jsonify({
                "data": None,
                "flash": [{"level": "info", "message": "Tu inscripción ya fue confirmada por el coordinador"}],
                "error": {"code": "ALREADY_CONFIRMED", "message": "La inscripción ya está confirmada"},
                "meta": {}
            }), 400

        # Guardar archivo
        file_path = save_user_doc(file, current_user.id, 'permanence', 'payment_proof')
        se.payment_proof_path = file_path
        se.updated_at = _now()

        # Historial
        from app.services.user_history_service import UserHistoryService
        UserHistoryService.log_action(
            user_id=current_user.id,
            admin_id=current_user.id,
            action='payment_proof_uploaded',
            details=(
                f'Subió comprobante de pago para semestre {se.semester_number} '
                f'({active_period.name}) en {up.program.name if up.program else up.program_id}'
            ),
        )

        # Notificar al coordinador del programa
        try:
            from app.services.notification_service import NotificationService
            if up.program and up.program.coordinator_id:
                student_name = (
                    f"{current_user.first_name} {current_user.last_name}"
                )
                NotificationService.create_notification(
                    user_id=up.program.coordinator_id,
                    notification_type='payment_proof_uploaded',
                    title='Comprobante de pago recibido',
                    message=(
                        f'{student_name} ha subido su comprobante de pago '
                        f'para el semestre {se.semester_number} ({active_period.name}) '
                        f'en {up.program.name}.'
                    ),
                    priority='normal',
                    action_url='/coordinator/permanence',
                    data={
                        'student_id': current_user.id,
                        'program_id': up.program_id,
                        'semester_enrollment_id': se.id,
                    },
                )
        except Exception as notify_err:
            import logging as _log
            _log.error(f"Error notificando coordinador de comprobante: {notify_err}")

        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise

        return jsonify({
            "data": se.to_dict(),
            "flash": [{"level": "success", "message": "Comprobante de pago enviado exitosamente. El coordinador lo revisará pronto."}],
            "error": None,
            "meta": {}
        }), 200

    except Exception as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Error al subir el comprobante de pago"}],
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500
