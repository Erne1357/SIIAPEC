# app/routes/api/submissions_api.py
from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user
from app.utils.permissions import permission_required
from app import db
from app.models import Program, Archive, Submission, UserProgram, ProgramStep
from app.utils.files import save_user_doc
from app.services.admission_service import get_admission_state
from app.services.user_history_service import UserHistoryService
import logging

api_submissions = Blueprint("api_submissions", __name__, url_prefix="/api/v1/submissions")

@api_submissions.post("")
@login_required
@permission_required('submissions.api.create')
def upload_submission():
    """
    multipart/form-data:
      - archive_id (uuid)         obligatorio — identificador público
      - file (File)               obligatorio
      - program_id (int)          opcional (uno de program_id o program_slug)
      - program_slug (string)     opcional
    """
    # `archive_id` viaja en el FORM y es el UUID público del Archive.
    # `type=int` lo habría leído como None en silencio.
    archive = Archive.by_uuid(request.form.get("archive_id"))
    program_id   = request.form.get("program_id", type=int)
    program_slug = request.form.get("program_slug", type=str)
    file         = request.files.get("file")

    if not archive or not file or (not program_id and not program_slug):
        return jsonify({
            "data": None,
            "error": {"code": "BAD_REQUEST", "message": "Faltan parámetros (archive_id, file, program_id/slug)."},
            "meta": {}
        }), 400

    # 2) Programa (id o slug)
    program = (Program.query.get(program_id) if program_id
               else Program.query.filter_by(slug=program_slug).first())
    if not program:
        return jsonify({
            "data": None,
            "error": {"code": "PROGRAM_NOT_FOUND", "message": "Programa no encontrado."},
            "meta": {}
        }), 404

    # 3) Verificar que el step del archive pertenezca a ese programa
    #    (hay un ProgramStep por (program_id, step_id))
    ps = ProgramStep.query.filter_by(program_id=program.id, step_id=archive.step_id).first()
    if not ps:
        return jsonify({
            "data": None,
            "error": {"code": "STEP_NOT_IN_PROGRAM", "message": "El documento no pertenece a este programa."},
            "meta": {}
        }), 409

    # 4) Verificar inscripción del usuario en ese programa
    up = UserProgram.query.filter_by(program_id=program.id, user_id=current_user.id).first()
    current_app.logger.info(
        f"[upload_submission] user={current_user.id} program={program.id if program else None} "
        f"archive={archive.id} step={archive.step_id} ps={ps.id if ps else None} up={bool(up)}"
    )
    if not up:
        return jsonify({
            "data": None,
            "error": {"code": "NOT_ENROLLED", "message": "Debes inscribirte antes de subir documentos."},
            "meta": {}
        }), 403

    # 5) Lock check según el estado de admisión del programa elegido
    state = get_admission_state(current_user.id, program.id, up)
    if state["lock_info"].get(archive.step_id or archive.step.id):
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Debes aprobar el paso anterior."}],
            "error": {"code": "STEP_LOCKED", "message": "Paso bloqueado"},
            "meta": {}
        }), 409

    # 6) Guardar archivo
    rel = save_user_doc(file, current_user.id, phase='admission', name=archive.name)

    # 7) Reusar submission (si existía) o crear
    existing = state["subs"].get(archive.id)
    sub = existing or Submission(
        user_id=current_user.id,
        archive_id=archive.id,
        program_step_id=ps.id,
        file_path=rel,
        semester=0,
        uploaded_by=current_user.id,
        uploaded_by_role=current_user.role.name,
        status='pending'
    )

    # Actualizar campos base
    sub.program_step_id = ps.id
    sub.upload_date = db.func.now()
    sub.file_path   = rel
    sub.status      = 'pending'

    db.session.add(sub)
    db.session.commit()

    # Registrar en el historial
    try:
        UserHistoryService.log_document_upload(
            user_id=current_user.id,
            archive_name=archive.name,
            program_name=program.name,
            uploaded_by_admin=False
        )
        db.session.commit()
    except Exception as e:
        current_app.logger.error(f"Error al registrar subida de documento en historial: {e}")

    # Notificar al coordinador del programa
    try:
        from app.services.notification_service import NotificationService
        if program.coordinator_id:
            student_name = f"{current_user.first_name} {current_user.last_name}"
            NotificationService.create_notification(
                user_id=program.coordinator_id,
                notification_type='document_submitted',
                title='Nuevo documento recibido',
                message=f'{student_name} ha subido el documento "{archive.name}" en {program.name}.',
                priority='low',
                action_url='/admin/review',
                data={'student_id': current_user.id, 'program_id': program.id},
            )
            db.session.commit()
    except Exception as e:
        current_app.logger.error(f"Error al notificar subida de documento: {e}")

    # WebSocket: notificar a coordinadores en tiempo real
    try:
        # Sólo a los coordinadores con alcance sobre el programa: el payload ata
        # un user_id al nombre de un documento suyo y eso no cruza de programa.
        from app.sockets.emitters import emit_to_coordinators
        emit_to_coordinators('submission:new', {
            # Identificadores públicos: el navegador los compara con los `id`
            # que publican los payloads REST, que también son UUID.
            'user_id': str(current_user.uuid) if current_user.uuid else None,
            'submission_id': str(sub.uuid) if sub.uuid else None,
            'archive_name': archive.name,
            'program_id': program.id,
        }, program.id)
    except Exception:
        pass

    return jsonify({
        "data": {
            "submission": {
                "id": str(sub.uuid) if sub.uuid else None,
                "archive_id": str(archive.uuid) if archive.uuid else None,
                "status": sub.status,
                "file_path": sub.file_path,
                "program_id": program.id,
                "program_step_id": ps.id
            }
        },
        "flash": [{"level": "success", "message": "Documento enviado exitosamente."}],
        "error": None,
        "meta": {}
    }), 201

#: Denial text for DELETE /submissions/<uuid>. One string for every cause.
_SUBMISSION_NOT_FOUND_MESSAGE = "Submission no encontrada"


@api_submissions.delete("/<uuid:sub_uuid>")
@login_required
@permission_required('submissions.api.delete_own')
def delete_submission(sub_uuid):
    # "No existe" y "no es tuya" responden EXACTAMENTE lo mismo. Antes eran 404
    # y 403: recorriendo ids, el 403 marcaba las entregas reales de otras
    # personas. Con el identificador opaco esa diferencia ya no aporta nada al
    # dueño legítimo y sí al que sondea, así que se colapsa.
    sub = Submission.by_uuid(sub_uuid)
    if not sub or sub.user_id != current_user.id:
        return jsonify({
            "data": None,
            "error": {"code": "NOT_FOUND", "message": _SUBMISSION_NOT_FOUND_MESSAGE},
            "meta": {}
        }), 404

    # Obtener información antes de eliminar
    archive_name = sub.archive.name if sub.archive else "Desconocido"
    program_name = "Desconocido"
    if sub.program_step and sub.program_step.program:
        program_name = sub.program_step.program.name

    db.session.delete(sub)
    db.session.commit()

    # Registrar en el historial
    try:
        UserHistoryService.log_document_deletion(
            user_id=current_user.id,
            archive_name=archive_name,
            program_name=program_name,
            deleted_by_admin=False
        )
        db.session.commit()
    except Exception as e:
        current_app.logger.error(f"Error al registrar eliminación de documento en historial: {e}")

    return jsonify({
        "data": True,
        "flash": [{"level": "success", "message": "Archivo eliminado."}],
        "error": None, "meta": {}
    }), 200
