# app/routes/api/admin/review_api.py
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user

from app.utils.permissions import any_permission_required, permission_required
from app.models import Submission
from app.services import review_service
from app.services.review_service import InvalidReviewAction, SubmissionNotFound

api_review = Blueprint("api_review", __name__, url_prefix="/api/v1/admin/review")

_NOT_FOUND_MESSAGE = "La entrega no existe o no pertenece a tus programas."


def _not_found(message: str = _NOT_FOUND_MESSAGE):
    """
    404 (no 403) para entregas de otros programas: el revisor no debe poder
    distinguir "no existe" de "existe pero no es tuya".
    """
    return jsonify({
        "data": None,
        "flash": [{"level": "warning", "message": message}],
        "error": {"code": "NOT_FOUND", "message": message},
        "meta": {}
    }), 404


def _sub_to_dict(sub: Submission) -> dict:
    return {
        "id": sub.id,
        "status": sub.status,
        "upload_date": sub.upload_date.isoformat() if sub.upload_date else None,
        "review_date": sub.review_date.isoformat() if sub.review_date else None,
        "reviewer_comment": sub.reviewer_comment,
        "user": {
            "id": sub.user.id,
            "name": f"{sub.user.first_name} {sub.user.last_name}"
        } if sub.user else None,
        "program": {
            "id": sub.program_step.program.id,
            "name": sub.program_step.program.name
        } if sub.program_step and sub.program_step.program else None,
        "step": {
            "id": sub.program_step.step.id,
            "name": sub.program_step.step.name
        } if sub.program_step and sub.program_step.step else None,
        "archive": {
            "id": sub.archive.id,
            "name": sub.archive.name
        } if sub.archive else None,
    }


@api_review.get("/submissions")
@login_required
@any_permission_required('admin_review.api.list_submissions', 'admin_review.api.decide')
def list_submissions():
    """
    Entregas del alcance de programas del revisor. Los filtros sólo reducen
    ese conjunto: nunca lo amplían a programas ajenos.
    """
    applicant_id = request.args.get('applicant_id', type=int)
    program_id   = request.args.get('program_id',   type=int)
    status       = request.args.get('status',       'pending', type=str)
    sort         = request.args.get('sort',         'desc',    type=str)

    subs = review_service.list_submissions_for_review(
        current_user,
        status=status,
        applicant_id=applicant_id,
        program_id=program_id,
        sort=sort,
    )
    return jsonify({
        "data": {"submissions": [_sub_to_dict(s) for s in subs]},
        "error": None,
        "meta": {}
    }), 200


@api_review.get("/submissions/<int:sub_id>")
@login_required
@any_permission_required('admin_review.api.detail_submission', 'admin_review.api.decide')
def get_submission(sub_id: int):
    try:
        sub = review_service.get_submission_for_review(current_user, sub_id)
    except SubmissionNotFound as e:
        return _not_found(e.message)

    return jsonify({"data": {"submission": _sub_to_dict(sub)}, "error": None, "meta": {}}), 200


@api_review.post("/submissions/<int:sub_id>/decision")
@login_required
@permission_required('admin_review.api.decide')
def decide_submission(sub_id: int):
    """
    JSON:
      - action: 'approve' | 'reject'
      - comment: str (optional)

    El alcance se verifica ANTES de escribir: una entrega de otro programa
    responde 404 y no se modifica.
    """
    payload = request.get_json(silent=True) or {}
    action  = (payload.get("action") or "").strip().lower()
    comment = (payload.get("comment") or "").strip()

    try:
        sub = review_service.decide_submission(current_user, sub_id, action, comment)
    except InvalidReviewAction as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": e.message}],
            "error": {"code": "BAD_ACTION", "message": e.message},
            "meta": {}
        }), 400
    except SubmissionNotFound as e:
        return _not_found(e.message)

    return jsonify({
        "data": {"submission": _sub_to_dict(sub)},
        "flash": [{"level": "success", "message": f"Documento {'aprobado' if action=='approve' else 'rechazado'} con éxito."}],
        "error": None, "meta": {}
    }), 200
