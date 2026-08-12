# app/routes/api/admission_api.py
from flask import Blueprint, jsonify
from flask_login import login_required, current_user
from app.models import Program, UserProgram
from app.services.admission_service import get_admission_state

api_admission = Blueprint("api_admission", __name__, url_prefix="/api/v1/admission")

@api_admission.get("/<string:slug>/state")
@login_required
def admission_state(slug: str):
    program = Program.query.filter_by(slug=slug).first()
    if not program:
        return jsonify({"data": None, "error": {"code": "NOT_FOUND", "message": "Programa no encontrado"}, "meta": {}}), 404

    up = UserProgram.query.filter_by(program_id=program.id, user_id=current_user.id).first()
    if not up:
        return jsonify({"data": None, "error": {"code": "NOT_ENROLLED", "message": "Debes inscribirte antes de subir documentos."}, "meta": {}}), 403

    state = get_admission_state(current_user.id, program.id, up)
    # mapear a JSON “limpio”
    def sub_info(aid):
        # `aid` sigue siendo el id INTERNO del Archive: es la clave con la que
        # `get_admission_state` indexó `subs`. Lo que sale al JSON es el UUID.
        s = state["subs"].get(aid)
        return None if not s else {
            "id": str(s.uuid) if s.uuid else None,
            "status": s.status,
            "file_path": s.file_path,
            "upload_date": s.upload_date.isoformat() if s.upload_date else None,
            "reviewer_comment": getattr(s, "reviewer_comment", None)
        }

    steps_json = []
    for step in state["steps"]:
        seq = step.program_steps[0].sequence if step.program_steps else None
        steps_json.append({
            "id": step.id,
            "name": step.name,
            "sequence": seq,
            "phase": getattr(step.phase, "name", None),
            "locked": bool(state["lock_info"].get(step.id)),
            "state": state["step_states"].get(step.id),
            # `id` del Archive es su handle público: el formulario de subida lo
            # devuelve tal cual como `archive_id`, y esa ruta ya resuelve el
            # UUID. `step.id` y `program.id` siguen siendo enteros (Step y
            # Program no tienen handle público).
            "archives": [
                {
                    "id": str(a.uuid) if a.uuid else None,
                    "name": a.name,
                    "submission": sub_info(a.id),
                }
                for a in step.archives
            ],
        })

    data = {
        "program": {"id": program.id, "slug": program.slug, "name": program.name},
        "steps": steps_json,
        "progress": {
            "segments": state["progress_segments"],
            "status_count": state["status_count"],
            "progress_pct": state["progress_pct"],
        },
        "pending_items": state["pending_items"],
        "timeline": state["timeline"],
    }
    return jsonify({"data": data, "error": None, "meta": {}}), 200
