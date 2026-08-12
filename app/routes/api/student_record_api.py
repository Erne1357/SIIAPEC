# app/routes/api/student_record_api.py
"""
REST endpoints for the Student Record (Expediente Completo).

Denial policy — read this before changing a status code here: "no such user"
and "that user is not yours" answer EXACTLY the same, 404 with
`svc.RECORD_NOT_FOUND_MESSAGE`. Splitting them into 404/403 published which
user ids exist: walking /api/v1/students/1..N and reading the status code
enumerated the whole account table for any holder of `students.api.view_record`
(every program_admin and every social_service account, by role). The operator
still gets the distinction — in the log, via `_log_denial`.
"""
from flask import Blueprint, current_app, jsonify, request, send_file
from flask_login import login_required, current_user
from io import BytesIO

from app import db
from app.services import student_record_service as svc
from app.utils.permissions import permission_required


api_student_record = Blueprint(
    'api_student_record',
    __name__,
    url_prefix='/api/v1/students',
)


def _log_denial(error: svc.StudentRecordError, user_id: int, action: str) -> None:
    """Record WHICH of the two denials happened. The response never says."""
    reason = 'unknown_user' if isinstance(error, svc.StudentNotFound) else 'out_of_scope'
    current_app.logger.warning(
        "[student_record] %s denied (%s): requester=%s target=%s — %s",
        action, reason, getattr(current_user, 'id', None), user_id, error,
    )


def _record_not_found(with_flash: bool = False):
    """The single denial response. Same body for both causes."""
    payload = {
        "data": None,
        "error": {"code": "NOT_FOUND", "message": svc.RECORD_NOT_FOUND_MESSAGE},
        "meta": {},
    }
    if with_flash:
        payload["flash"] = [{"level": "danger", "message": svc.RECORD_NOT_FOUND_MESSAGE}]
    return jsonify(payload), 404


@api_student_record.get('/<int:user_id>/record')
@login_required
@permission_required('students.api.view_record')
def get_record(user_id):
    try:
        data = svc.get_full_record(user_id, requester=current_user)
        return jsonify({"data": data, "error": None, "meta": {}}), 200
    except svc.StudentRecordError as e:
        _log_denial(e, user_id, 'get_record')
        return _record_not_found()
    except Exception as e:
        return jsonify({
            "data": None,
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500


@api_student_record.patch('/<int:user_id>/personal-info')
@login_required
@permission_required('students.api.edit_personal_info')
def patch_personal_info(user_id):
    payload = request.get_json(silent=True) or {}
    try:
        user = svc.update_personal_info(user_id, current_user.id, payload)
        return jsonify({
            "data": svc._user_dict(user),
            "flash": [{"level": "success", "message": "Información actualizada"}],
            "error": None,
            "meta": {}
        }), 200
    except svc.StudentRecordError as e:
        _log_denial(e, user_id, 'patch_personal_info')
        return _record_not_found(with_flash=True)
    except Exception as e:
        db.session.rollback()
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Error al actualizar"}],
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500


@api_student_record.get('/<int:user_id>/record/pdf')
@login_required
@permission_required('students.api.export_record_pdf')
def export_record_pdf(user_id):
    """Generates a PDF of the student record using WeasyPrint."""
    try:
        data = svc.get_full_record(user_id, requester=current_user)
    except svc.StudentRecordError as e:
        _log_denial(e, user_id, 'export_record_pdf')
        return _record_not_found()

    try:
        from flask import render_template
        from weasyprint import HTML
        html = render_template('coordinator/student_record/_pdf.html', record=data)
        pdf_bytes = HTML(string=html).write_pdf()
        full_name = (
            f"{data['user']['first_name']}_{data['user']['last_name']}"
        ).replace(' ', '_')
        filename = f"expediente_{full_name}_{user_id}.pdf"
        return send_file(
            BytesIO(pdf_bytes),
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename,
        )
    except Exception as e:
        return jsonify({
            "data": None,
            "error": {"code": "SERVER_ERROR", "message": f"Error generando PDF: {e}"},
            "meta": {}
        }), 500
