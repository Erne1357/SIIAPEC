# app/routes/api/acceptance_api.py
"""
API para gestionar los documentos de aceptacion e inscripcion (Fase 4)
y diferimientos de inscripcion (Fase 7).
"""

from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from app.utils.permissions import (
    permission_required,
    any_permission_required,
    program_scope_required,
    guard_program_scope,
)
from app.services import acceptance_service as svc
from app.services import deferral_service as dsvc
from app.services import acceptance_scope_service as ascope
from app.models.acceptance_document import AcceptanceDocument
from app.models.user import User

api_acceptance = Blueprint(
    'api_acceptance',
    __name__,
    url_prefix='/api/v1/acceptance'
)


def _not_found(message: str):
    """
    404 estándar del blueprint.

    Se usa también cuando el objeto existe pero pertenece a otro programa: un
    403 sólo en ese caso confirmaría su existencia. Ver
    `app/services/acceptance_scope_service.py`.
    """
    return jsonify({
        "data": None,
        "flash": [{"level": "danger", "message": message}],
        "error": {"code": "NOT_FOUND", "message": message},
        "meta": {}
    }), 404


@api_acceptance.get('/program/<int:program_id>/applicants')
@login_required
@permission_required('acceptance.api.list_applicants')
@program_scope_required(program_id_kwarg='program_id')
def api_get_accepted_applicants(program_id):
    """Obtiene aspirantes aceptados con estado de sus documentos."""
    try:
        data = svc.get_accepted_applicants(program_id)
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


@api_acceptance.get('/program/<int:program_id>/stats')
@login_required
@permission_required('acceptance.api.list_applicants')
@program_scope_required(program_id_kwarg='program_id')
def api_get_acceptance_stats(program_id):
    """Obtiene estadisticas de documentos de aceptacion para un programa."""
    try:
        stats = svc.get_acceptance_stats(program_id)
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


@api_acceptance.get('/user/<uuid:user_uuid>/program/<int:program_id>/status')
@login_required
def api_get_acceptance_status(user_uuid, program_id):
    """Obtiene el estado de los documentos de aceptacion de un aspirante."""
    # El aspirante puede ver los suyos; el personal sólo los de SUS programas.
    # La respuesta incluye las rutas de los documentos personales, así que el
    # nivel resumido entre programas (nombre/correo/progreso) no aplica aquí.
    #
    # El UUID se resuelve antes de comprobar "¿soy yo?", pero la comprobación de
    # alcance sigue delante del 404: un UUID desconocido responde exactamente lo
    # mismo que un aspirante de otro programa.
    target = User.by_uuid(user_uuid)
    user_id = target.id if target else None

    if current_user.id != user_id:
        if not current_user.has_permission('acceptance.api.list_applicants'):
            return jsonify({
                "data": None,
                "error": {"code": "FORBIDDEN", "message": "No tienes permiso"},
                "meta": {}
            }), 403

        denied = guard_program_scope(program_id)
        if denied:
            return denied

    try:
        from app.models import UserProgram
        up = (UserProgram.query.filter_by(user_id=user_id, program_id=program_id).first()
              if user_id is not None else None)
        if not up:
            return jsonify({
                "data": None,
                "error": {"code": "NOT_FOUND", "message": svc.USER_PROGRAM_NOT_FOUND_MESSAGE},
                "meta": {}
            }), 404

        data = svc.get_acceptance_status(up.id)
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


@api_acceptance.post('/user/<uuid:user_uuid>/program/<int:program_id>/upload-doc')
@login_required
@permission_required('acceptance.api.upload_doc')
@program_scope_required(program_id_kwarg='program_id')
def api_upload_coordinator_doc(user_uuid, program_id):
    """El coordinador sube carta de aceptacion o tira de materias."""
    # UUID desconocido y aspirante que no pertenece a este programa responden
    # exactamente igual: mismo 404 y mismo texto (APPLICANT_NOT_FOUND_MESSAGE).
    target = User.by_uuid(user_uuid)
    if target is None:
        return _not_found(svc.APPLICANT_NOT_FOUND_MESSAGE)
    user_id = target.id

    document_type = request.form.get('document_type')
    file = request.files.get('file')

    if not document_type:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Falta el tipo de documento"}],
            "error": {"code": "MISSING_FIELD", "message": "document_type es requerido"},
            "meta": {}
        }), 400

    if not file or file.filename == '':
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "No se envio ningun archivo"}],
            "error": {"code": "MISSING_FILE", "message": "Archivo requerido"},
            "meta": {}
        }), 400

    try:
        doc = svc.upload_coordinator_doc(
            user_id=user_id,
            program_id=program_id,
            document_type=document_type,
            file_storage=file,
            coordinator_id=current_user.id
        )
        label = svc.DOC_TYPE_LABELS.get(document_type, document_type)
        return jsonify({
            "data": doc.to_dict(),
            "flash": [{"level": "success", "message": f"{label} subida exitosamente"}],
            "error": None,
            "meta": {}
        }), 200

    except svc.ApplicantNotFound as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": str(e)}],
            "error": {"code": "NOT_FOUND", "message": str(e)},
            "meta": {}
        }), 404

    except svc.InvalidDocumentType as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": str(e)}],
            "error": {"code": "INVALID_DOC_TYPE", "message": str(e)},
            "meta": {}
        }), 400

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
            "flash": [{"level": "danger", "message": "Error al subir el documento"}],
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500


@api_acceptance.post('/user/<uuid:user_uuid>/program/<int:program_id>/submit-receipt')
@login_required
def api_submit_enrollment_receipt(user_uuid, program_id):
    """El aspirante sube su boleta de servicios escolares."""
    # Solo el propio aspirante puede subir su boleta. Un UUID desconocido cae en
    # la misma rama que el UUID de otra persona: mismo 403 y mismo texto.
    target = User.by_uuid(user_uuid)
    user_id = target.id if target else None

    if current_user.id != user_id:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Solo puedes subir tu propia boleta"}],
            "error": {"code": "FORBIDDEN", "message": "No autorizado"},
            "meta": {}
        }), 403

    file = request.files.get('file')
    if not file or file.filename == '':
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "No se envio ningun archivo"}],
            "error": {"code": "MISSING_FILE", "message": "Archivo requerido"},
            "meta": {}
        }), 400

    try:
        doc = svc.submit_enrollment_receipt(
            user_id=user_id,
            program_id=program_id,
            file_storage=file,
            aspirant_id=current_user.id
        )
        return jsonify({
            "data": doc.to_dict(),
            "flash": [{"level": "success", "message": "Boleta enviada exitosamente. Espera la revision del coordinador."}],
            "error": None,
            "meta": {}
        }), 200

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
            "flash": [{"level": "danger", "message": "Error al enviar la boleta"}],
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500


@api_acceptance.post('/document/<uuid:doc_uuid>/review')
@login_required
@permission_required('acceptance.api.review_doc')
def api_review_enrollment_receipt(doc_uuid):
    """El coordinador aprueba o rechaza la boleta del aspirante."""
    # El permiso no dice de qué programa es el documento: resolverlo antes de
    # tocarlo. Documento inexistente, UUID malformado y documento ajeno
    # responden igual (404, mismo texto).
    doc = AcceptanceDocument.by_uuid(doc_uuid)
    if doc is None or not ascope.document_in_scope(current_user, doc.id):
        return _not_found(svc.DOCUMENT_NOT_FOUND_MESSAGE)
    doc_id = doc.id

    data = request.get_json() or {}
    status = data.get('status')
    notes = data.get('notes')

    if not status:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Falta el estado de revision"}],
            "error": {"code": "MISSING_FIELD", "message": "status es requerido"},
            "meta": {}
        }), 400

    if status == 'rejected' and not notes:
        return jsonify({
            "data": None,
            "flash": [{"level": "warning", "message": "Debes indicar el motivo del rechazo"}],
            "error": {"code": "MISSING_NOTES", "message": "Las notas son requeridas al rechazar"},
            "meta": {}
        }), 400

    try:
        doc = svc.review_enrollment_receipt(
            doc_id=doc_id,
            coordinator_id=current_user.id,
            status=status,
            notes=notes
        )
        msg = "Boleta aprobada exitosamente" if status == 'approved' else "Boleta rechazada"
        return jsonify({
            "data": doc.to_dict(),
            "flash": [{"level": "success" if status == 'approved' else "warning", "message": msg}],
            "error": None,
            "meta": {}
        }), 200

    except svc.ApplicantNotFound as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": str(e)}],
            "error": {"code": "NOT_FOUND", "message": str(e)},
            "meta": {}
        }), 404

    except (svc.InvalidDocumentType, svc.InvalidStateTransition) as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "warning", "message": str(e)}],
            "error": {"code": "INVALID_STATE", "message": str(e)},
            "meta": {}
        }), 400

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
            "flash": [{"level": "danger", "message": "Error al revisar la boleta"}],
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500


@api_acceptance.post('/user/<uuid:user_uuid>/program/<int:program_id>/assign-control-number')
@login_required
@permission_required('acceptance.api.assign_control_number')
@program_scope_required(program_id_kwarg='program_id')
def api_assign_control_number(user_uuid, program_id):
    """El coordinador asigna el número de control al aspirante aceptado."""
    target = User.by_uuid(user_uuid)
    if target is None:
        return _not_found(svc.APPLICANT_NOT_FOUND_MESSAGE)
    user_id = target.id

    data = request.get_json() or {}
    control_number = (data.get('control_number') or '').strip()

    if not control_number:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "El número de control es requerido"}],
            "error": {"code": "MISSING_FIELD", "message": "control_number es requerido"},
            "meta": {}
        }), 400

    try:
        up = svc.assign_control_number(user_id, program_id, control_number, current_user.id)
        return jsonify({
            "data": up.to_dict(include_deliberation=True),
            "flash": [{"level": "success", "message": f"Número de control {control_number} asignado exitosamente"}],
            "error": None,
            "meta": {}
        }), 200

    except svc.ApplicantNotFound as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": str(e)}],
            "error": {"code": "NOT_FOUND", "message": str(e)},
            "meta": {}
        }), 404

    except svc.DocumentDebtError as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "warning", "message": str(e)}],
            "error": {
                "code": "DOCUMENT_DEBT",
                "message": str(e),
                "debts": e.debts,
            },
            "meta": {}
        }), 409

    except svc.InvalidStateTransition as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "warning", "message": str(e)}],
            "error": {"code": "INVALID_STATE", "message": str(e)},
            "meta": {}
        }), 400

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
            "flash": [{"level": "danger", "message": "Error al asignar número de control"}],
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500


@api_acceptance.delete('/document/<uuid:doc_uuid>')
@login_required
@permission_required('acceptance.api.upload_doc')
def api_delete_coordinator_doc(doc_uuid):
    """Elimina un documento de aceptacion subido por el coordinador."""
    # Igual que en la revisión: el id del documento no dice de qué programa es.
    doc = AcceptanceDocument.by_uuid(doc_uuid)
    if doc is None or not ascope.document_in_scope(current_user, doc.id):
        return _not_found(svc.DOCUMENT_NOT_FOUND_MESSAGE)
    doc_id = doc.id

    try:
        svc.delete_coordinator_doc(doc_id=doc_id, coordinator_id=current_user.id)
        return jsonify({
            "data": None,
            "flash": [{"level": "success", "message": "Documento eliminado"}],
            "error": None,
            "meta": {}
        }), 200

    except svc.ApplicantNotFound as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": str(e)}],
            "error": {"code": "NOT_FOUND", "message": str(e)},
            "meta": {}
        }), 404

    except svc.InvalidDocumentType as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "warning", "message": str(e)}],
            "error": {"code": "INVALID_DOC_TYPE", "message": str(e)},
            "meta": {}
        }), 400

    except Exception as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Error al eliminar el documento"}],
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500


# ─────────────────────────────────────────────────────────────────────────────
# FASE 7: Diferimiento de inscripción
# ─────────────────────────────────────────────────────────────────────────────

@api_acceptance.get('/program/<int:program_id>/deferred')
@login_required
@permission_required('acceptance.api.list_applicants')
@program_scope_required(program_id_kwarg='program_id')
def api_get_deferred_applicants(program_id):
    """Obtiene todos los aspirantes diferidos de un programa."""
    try:
        data = dsvc.get_deferred_applicants(program_id)
        pending = dsvc.get_pending_deferral_requests(program_id)
        return jsonify({
            "data": {"deferred": data, "pending_requests": pending},
            "error": None,
            "meta": {"deferred_count": len(data), "pending_count": len(pending)}
        }), 200

    except Exception as e:
        return jsonify({
            "data": None,
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500


@api_acceptance.post('/user/<uuid:user_uuid>/program/<int:program_id>/defer')
@login_required
@permission_required('acceptance.api.defer_applicant')
@program_scope_required(program_id_kwarg='program_id')
def api_defer_applicant(user_uuid, program_id):
    """El coordinador difiere directamente la inscripción de un aspirante aceptado."""
    target = User.by_uuid(user_uuid)
    if target is None:
        return _not_found(dsvc.DEFERRAL_NOT_FOUND_MESSAGE)
    user_id = target.id

    data = request.get_json() or {}
    
    # Safely handle reason
    reason_val = data.get('reason')
    reason = str(reason_val).strip() if reason_val else None

    try:
        deferral = dsvc.defer_applicant(
            user_id=user_id,
            program_id=program_id,
            coordinator_id=current_user.id,
            reason=reason,
        )
        next_period = deferral.deferred_to_period_name if hasattr(deferral, 'deferred_to_period_name') else None
        msg = f"Inscripción diferida exitosamente"
        if deferral.deferred_to_period:
            msg += f" al periodo {deferral.deferred_to_period.name}"
        return jsonify({
            "data": deferral.to_dict(),
            "flash": [{"level": "success", "message": msg}],
            "error": None,
            "meta": {}
        }), 200

    except dsvc.DeferralNotFound as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": str(e)}],
            "error": {"code": "NOT_FOUND", "message": str(e)},
            "meta": {}
        }), 404

    except dsvc.DeferralNotAllowed as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "warning", "message": str(e)}],
            "error": {"code": "NOT_ALLOWED", "message": str(e)},
            "meta": {}
        }), 400

    except Exception as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Error al diferir la inscripción"}],
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500


@api_acceptance.post('/program/<int:program_id>/request-deferral')
@login_required
@permission_required('acceptance.api.request_deferral')
def api_request_deferral(program_id):
    """
    El aspirante solicita diferir su inscripción.

    No lleva `program_scope_required`: el objetivo es SIEMPRE el propio
    solicitante (`user_id=current_user.id`) y un aspirante no tiene programas
    "a su alcance", así que el decorador lo bloquearía. El alcance lo garantiza
    `deferral_service._get_user_program`, que exige un UserProgram del propio
    usuario en ese programa y si no existe lanza DeferralNotFound (404).
    """
    data = request.get_json() or {}
    reason = (data.get('reason') or '').strip() or None

    try:
        deferral = dsvc.request_deferral(
            user_id=current_user.id,
            program_id=program_id,
            reason=reason,
        )
        return jsonify({
            "data": deferral.to_dict(),
            "flash": [{"level": "success", "message": "Solicitud de diferimiento enviada. El coordinador la revisará pronto."}],
            "error": None,
            "meta": {}
        }), 200

    except dsvc.DeferralNotFound as e:
        # El aspirante no tiene proceso en ese programa: no existe para él.
        return _not_found(str(e))

    except dsvc.DeferralNotAllowed as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "warning", "message": str(e)}],
            "error": {"code": "NOT_ALLOWED", "message": str(e)},
            "meta": {}
        }), 400

    except Exception as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Error al enviar la solicitud"}],
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500


@api_acceptance.post('/deferral/<int:deferral_id>/approve')
@login_required
@permission_required('acceptance.api.defer_applicant')
def api_approve_deferral(deferral_id):
    """El coordinador aprueba una solicitud de diferimiento del aspirante."""
    # Aprobar borra la tira de materias y la boleta del aspirante: el objeto
    # DEBE ser de un programa propio. Inexistente y ajeno responden igual.
    if not ascope.deferral_in_scope(current_user, deferral_id):
        return _not_found("Diferimiento no encontrado")

    data = request.get_json() or {}
    notes = (data.get('notes') or '').strip() or None

    try:
        deferral = dsvc.approve_deferral(
            deferral_id=deferral_id,
            coordinator_id=current_user.id,
            notes=notes,
        )
        return jsonify({
            "data": deferral.to_dict(),
            "flash": [{"level": "success", "message": "Diferimiento aprobado exitosamente"}],
            "error": None,
            "meta": {}
        }), 200

    except dsvc.DeferralNotFound as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": str(e)}],
            "error": {"code": "NOT_FOUND", "message": str(e)},
            "meta": {}
        }), 404

    except dsvc.DeferralNotAllowed as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "warning", "message": str(e)}],
            "error": {"code": "NOT_ALLOWED", "message": str(e)},
            "meta": {}
        }), 400

    except Exception as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Error al aprobar el diferimiento"}],
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500


@api_acceptance.post('/deferral/<int:deferral_id>/reject')
@login_required
@permission_required('acceptance.api.defer_applicant')
def api_reject_deferral(deferral_id):
    """El coordinador rechaza una solicitud de diferimiento del aspirante."""
    if not ascope.deferral_in_scope(current_user, deferral_id):
        return _not_found("Diferimiento no encontrado")

    data = request.get_json() or {}
    notes = (data.get('notes') or '').strip() or None

    if not notes:
        return jsonify({
            "data": None,
            "flash": [{"level": "warning", "message": "Debes indicar el motivo del rechazo"}],
            "error": {"code": "MISSING_NOTES", "message": "Las notas son requeridas al rechazar"},
            "meta": {}
        }), 400

    try:
        deferral = dsvc.reject_deferral(
            deferral_id=deferral_id,
            coordinator_id=current_user.id,
            notes=notes,
        )
        return jsonify({
            "data": deferral.to_dict(),
            "flash": [{"level": "warning", "message": "Solicitud de diferimiento rechazada"}],
            "error": None,
            "meta": {}
        }), 200

    except dsvc.DeferralNotFound as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": str(e)}],
            "error": {"code": "NOT_FOUND", "message": str(e)},
            "meta": {}
        }), 404

    except dsvc.DeferralNotAllowed as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "warning", "message": str(e)}],
            "error": {"code": "NOT_ALLOWED", "message": str(e)},
            "meta": {}
        }), 400

    except Exception as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Error al rechazar el diferimiento"}],
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500


@api_acceptance.post('/user/<uuid:user_uuid>/program/<int:program_id>/reactivate')
@login_required
@permission_required('acceptance.api.defer_applicant')
@program_scope_required(program_id_kwarg='program_id')
def api_reactivate_deferred(user_uuid, program_id):
    """
    El coordinador reactiva a un aspirante diferido en el nuevo periodo.
    Vuelve a 'accepted' y actualiza el periodo de admisión.
    """
    target = User.by_uuid(user_uuid)
    if target is None:
        return _not_found(dsvc.DEFERRAL_NOT_FOUND_MESSAGE)
    user_id = target.id

    try:
        up = dsvc.reactivate_deferred(
            user_id=user_id,
            program_id=program_id,
            coordinator_id=current_user.id,
        )
        return jsonify({
            "data": up.to_dict(include_deliberation=True),
            "flash": [{"level": "success", "message": "Aspirante reactivado exitosamente en el nuevo periodo"}],
            "error": None,
            "meta": {}
        }), 200

    except dsvc.DeferralNotFound as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": str(e)}],
            "error": {"code": "NOT_FOUND", "message": str(e)},
            "meta": {}
        }), 404

    except dsvc.DeferralNotAllowed as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "warning", "message": str(e)}],
            "error": {"code": "NOT_ALLOWED", "message": str(e)},
            "meta": {}
        }), 400

    except Exception as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Error al reactivar al aspirante"}],
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500


@api_acceptance.get('/user/<uuid:user_uuid>/program/<int:program_id>/deferral-status')
@login_required
def api_get_deferral_status(user_uuid, program_id):
    """Obtiene el estado de diferimiento de un aspirante."""
    # El aspirante ve el suyo; el personal sólo el de SUS programas.
    target = User.by_uuid(user_uuid)
    user_id = target.id if target else None

    if current_user.id != user_id:
        if not current_user.has_permission('acceptance.api.list_deferred'):
            return jsonify({
                "data": None,
                "error": {"code": "FORBIDDEN", "message": "No tienes permiso"},
                "meta": {}
            }), 403

        denied = guard_program_scope(program_id)
        if denied:
            return denied

    try:
        from app.models import UserProgram
        up = (UserProgram.query.filter_by(user_id=user_id, program_id=program_id).first()
              if user_id is not None else None)
        if not up:
            return jsonify({
                "data": None,
                "error": {"code": "NOT_FOUND", "message": svc.USER_PROGRAM_NOT_FOUND_MESSAGE},
                "meta": {}
            }), 404

        data = dsvc.get_deferral_status(up.id)
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
