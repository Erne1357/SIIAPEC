# app/routes/api/programs_api.py
from flask import Blueprint, jsonify, request, abort
from flask_login import login_required, current_user
from app.utils.permissions import permission_required, any_permission_required
from app.services import programs_service as svc
from app.services.user_history_service import UserHistoryService

api_programs = Blueprint('api_programs', __name__, url_prefix='/api/v1/programs')

@api_programs.get('')
@login_required
def api_list_programs():
    items = svc.list_programs()
    data = [{"id": p.id, "name": p.name, "slug": p.slug} for p in items]
    return jsonify({"data": data, "error": None, "meta": {"count": len(data)}}), 200

@api_programs.get('/<string:slug>')
@login_required
def api_get_program(slug):
    try:
        p = svc.get_program_by_slug(slug)
    except svc.ProgramNotFound:
        return jsonify({"data": None, "error": {"code":"NOT_FOUND","message":"Programa no encontrado"}, "meta":{}}), 404
    data = {
        "id": p.id, "name": p.name, "slug": p.slug,
        "steps": [
            {
              "id": ps.step.id,
              "name": ps.step.name,
              "phase": getattr(ps.step.phase, "name", None),
              # `id` del Archive es su handle público; `step` y `program`
              # siguen siendo enteros (esos modelos no tienen handle).
              "archives": [
                  {"id": str(a.uuid) if a.uuid else None, "name": a.name}
                  for a in ps.step.archives
              ]
            }
            for ps in p.program_steps
        ]
    }
    return jsonify({"data": data, "error": None, "meta": {}}), 200

@api_programs.post('/<int:program_id>/inscription')
@login_required
@permission_required('programs.api.enroll')
def api_enroll(program_id):
    try:
        program = svc.enroll_user_once(program_id, current_user.id)
        
        # Registrar en el historial
        try:
            UserHistoryService.log_program_enrollment(
                user_id=current_user.id,
                program_name=program.name,
                program_id=program.id
            )
            from app import db
            db.session.commit()
        except Exception as e:
            from flask import current_app
            current_app.logger.error(f"Error al registrar postulación en historial: {e}")
        
        return jsonify({
            "data": {"program": {"id": program.id, "slug": program.slug}},
            "flash": [{"level": "success", "message": "Te has postulado en el programa."}],
            "error": None, "meta": {}
        }), 200
    except svc.AlreadyEnrolledError as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "warning", "message": str(e)}],
            "error": {"code": "ALREADY_ENROLLED", "message": str(e)},
            "meta": {}
        }), 409
    except svc.ProgramNotFound:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Programa no encontrado."}],
            "error": {"code": "NOT_FOUND", "message": "Programa no encontrado"},
            "meta": {}
        }), 404


@api_programs.post('/<int:program_id>/admission-interest')
@login_required
@permission_required('programs.api.register_interest')
def api_register_admission_interest(program_id):
    """
    Registra que el usuario quiere aviso cuando reabra la admisión.

    Es la acción disponible cuando NO hay convocatoria abierta: sustituye a un
    botón que sólo podía decir que no.
    """
    try:
        interest = svc.register_admission_interest(program_id, current_user.id)
    except svc.ProgramNotFound:
        return jsonify({
            "data": None,
            "error": {"code": "NOT_FOUND", "message": "Programa no encontrado"},
            "meta": {}
        }), 404
    except svc.AdmissionAlreadyOpenError:
        msg = "Las inscripciones ya están abiertas: puedes postularte ahora."
        return jsonify({
            "data": None,
            "flash": [{"level": "info", "message": msg}],
            "error": {"code": "BUSINESS_ERROR", "message": msg},
            "meta": {}
        }), 400
    except svc.AlreadyInterestedError:
        msg = "Ya tienes activado el aviso para este programa."
        return jsonify({
            "data": None,
            "flash": [{"level": "info", "message": msg}],
            "error": {"code": "BUSINESS_ERROR", "message": msg},
            "meta": {}
        }), 409
    except Exception:
        # str(e) se pinta tal cual en el diálogo del aspirante: un ValueError
        # interno llegó a mostrarle la lista completa de acciones válidas del
        # historial. El detalle va al log; al usuario, algo que pueda accionar.
        from flask import current_app
        current_app.logger.exception('Error al registrar interés de convocatoria')
        return jsonify({
            "data": None,
            "flash": [{"level": "danger",
                       "message": "No pudimos activar el aviso. Inténtalo de nuevo."}],
            "error": {"code": "SERVER_ERROR",
                      "message": "No pudimos activar el aviso. Inténtalo de nuevo."},
            "meta": {}
        }), 500

    return jsonify({
        "data": interest.to_dict(),
        "flash": [{"level": "success",
                   "message": "Listo. Te avisaremos en cuanto abra la convocatoria."}],
        "error": None,
        "meta": {}
    }), 201


@api_programs.patch('/<string:slug>')
@login_required
@permission_required('programs.api.update')
def api_update_program(slug):
    """
    Actualizar configuración del programa.
    - postgraduate_admin: puede editar todos los programas
    - program_admin: solo puede editar los programas que coordina
    """
    try:
        program = svc.get_program_by_slug(slug)
    except svc.ProgramNotFound:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Programa no encontrado."}],
            "error": {"code": "NOT_FOUND", "message": "Programa no encontrado"},
            "meta": {}
        }), 404

    # Verificar permisos: scoped users solo pueden editar sus programas accesibles
    accessible_pids = current_user.get_accessible_program_ids()
    if accessible_pids is not None and program.id not in accessible_pids:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "No tienes permiso para editar este programa."}],
            "error": {"code": "FORBIDDEN", "message": "No autorizado"},
            "meta": {}
        }), 403

    # Obtener datos del request
    data = request.get_json()
    if not data:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Datos inválidos."}],
            "error": {"code": "INVALID_DATA", "message": "Datos inválidos"},
            "meta": {}
        }), 400

    try:
        # Actualizar programa
        updated_program = svc.update_program_config(program.id, data)

        return jsonify({
            "data": {"program": updated_program.to_dict()},
            "flash": [{"level": "success", "message": "Programa actualizado exitosamente."}],
            "error": None,
            "meta": {}
        }), 200
    except Exception as e:
        from flask import current_app
        current_app.logger.error(f"Error al actualizar programa: {e}")
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Error al actualizar el programa."}],
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500
