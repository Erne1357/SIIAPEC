"""
API de cambios de programa.

Dos flujos distintos conviven aquí y NO deben confundirse:

  1. Autoservicio del aspirante — `POST /analyze` y `POST /execute`.
     Los llama el propio aspirante desde el panel de admisión; no llevan
     `@permission_required` porque el rol `applicant` no tiene ningún permiso
     de `program_changes.*`. Lo que los protege es la validación de negocio de
     `ProgramTransferService.validate_transfer`: ser dueño del programa de
     origen, estar en un estado transferible y tener la convocatoria abierta.

  2. Decisión del coordinador — `PUT /requests/<id>/decision`.
     Exige `program_changes.api.decide` Y que ambos programas de la solicitud
     estén dentro del alcance del coordinador.

El autoservicio NUNCA aprueba la solicitud ni se firma como decisor: deja la
solicitud en `executed`, que es lo que de verdad ocurrió.
"""

from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user
from app.utils.permissions import permission_required
from app.services.program_changes_service import ProgramChangesService
from app.services.program_transfer_service import (
    ProgramTransferService,
    ProgramTransferError,
    ProgramTransferNotFound,
)
from app.services.user_history_service import UserHistoryService
from app.services import program_scope_service as scope_service
from app import db
from app.utils.datetime_utils import now_local

api_program_changes = Blueprint('api_program_changes', __name__, url_prefix='/api/v1/program-changes')


def _int_or_none(value):
    """Convierte a int o devuelve None (falla cerrado, sin excepciones)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _transfer_denied(exc: ProgramTransferError):
    """
    Traduce una denegación del servicio a la respuesta HTTP.

    404 cuando el objeto no existe PARA ESTE USUARIO (su inscripción de origen,
    el programa destino): un 403 confirmaría que el objeto existe.
    409 cuando el objeto existe pero su estado no admite la operación.

    Se conserva el envoltorio {"ok": false, "error": "<texto>"} de este
    blueprint porque `program_transfer.js` lee `json.error` como cadena.
    """
    status = 404 if isinstance(exc, ProgramTransferNotFound) else 409
    return jsonify({"ok": False, "error": exc.message}), status


@api_program_changes.route('', methods=['POST'])
@login_required
def create_request():
    """
    Registra una solicitud de cambio de programa del propio usuario.

    El solicitante siempre es `current_user`: no se acepta un applicant_id del
    cuerpo. Además debe ser dueño de una inscripción en el programa de origen.
    """
    data = request.get_json() or {}
    from_program_id = _int_or_none(data.get('from_program_id'))
    to_program_id = _int_or_none(data.get('to_program_id'))

    if from_program_id is None or to_program_id is None:
        return jsonify({
            "ok": False,
            "error": "Se requieren from_program_id y to_program_id"
        }), 400

    try:
        ProgramTransferService.validate_change_request(
            current_user.id, from_program_id, to_program_id
        )
    except ProgramTransferError as e:
        return _transfer_denied(e)

    req = ProgramChangesService.create_request(
        applicant_id=current_user.id,
        from_program_id=from_program_id,
        to_program_id=to_program_id,
        reason=data.get('reason')
    )

    # Registrar en el historial
    try:
        from_program = ProgramTransferService.get_program(from_program_id)
        to_program = ProgramTransferService.get_program(to_program_id)

        UserHistoryService.log_program_transfer_request(
            user_id=current_user.id,
            from_program=from_program.name if from_program else f"ID {from_program_id}",
            to_program=to_program.name if to_program else f"ID {to_program_id}",
            reason=data.get('reason', '')
        )
        db.session.commit()
    except Exception as e:
        current_app.logger.error(f"Error al registrar solicitud de cambio en historial: {e}")

    return jsonify({"ok": True, "id": req.id}), 201


@api_program_changes.route('/requests', methods=['GET'])
@login_required
def list_requests():
    """
    Solicitudes de cambio.

    Un decisor ve las de SUS programas (origen o destino dentro de su alcance);
    el jefe de posgrado, todas; cualquier otro usuario, sólo las propias.
    """
    if current_user.has_permission('program_changes.api.decide'):
        # None significa "todos los programas" (jefe de posgrado); un conjunto
        # vacío significa "ninguno" y devuelve lista vacía. Nunca lo trates
        # como "todos".
        program_ids = scope_service.accessible_program_ids(current_user)
        items = ProgramTransferService.list_change_requests(program_ids=program_ids)
    else:
        items = ProgramTransferService.list_change_requests(applicant_id=current_user.id)

    payload = [{"id": r.id, "from_program_id": r.from_program_id, "to_program_id": r.to_program_id, "status": r.status} for r in items]
    return jsonify({"ok": True, "items": payload}), 200


@api_program_changes.route('/requests/<int:req_id>/decision', methods=['PUT'])
@login_required
@permission_required('program_changes.api.decide')
def decide(req_id: int):
    """
    Decide una solicitud de cambio.

    Tener `program_changes.api.decide` dice QUÉ puede hacer el coordinador, no
    SOBRE QUIÉN: la decisión mueve documentos entre dos programas, así que
    ambos deben estar dentro de su alcance. Una solicitud fuera de alcance
    responde 404, igual que una inexistente — un 403 delataría que existe.
    """
    data = request.get_json() or {}

    try:
        req = ProgramTransferService.get_change_request(req_id)
    except ProgramTransferNotFound as e:
        return jsonify({"ok": False, "error": e.message}), 404

    in_scope = scope_service.programs_in_scope(
        current_user, [req.from_program_id, req.to_program_id]
    )
    if {req.from_program_id, req.to_program_id} - in_scope:
        return jsonify({"ok": False, "error": "La solicitud de cambio no existe."}), 404

    if req.status != 'pending':
        return jsonify({
            "ok": False,
            "error": "Esta solicitud ya fue resuelta y no puede volver a decidirse."
        }), 409

    try:
        r = ProgramChangesService.decide_request(
            request_id=req_id,
            status=data.get('status'),
            decided_by=current_user.id
        )
        return jsonify({"ok": True, "id": r.id, "status": r.status}), 200
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        current_app.logger.error(f"Error al decidir solicitud de cambio {req_id}: {e}")
        return jsonify({"ok": False, "error": "No se pudo procesar la decisión."}), 500


@api_program_changes.route('/analyze', methods=['POST'])
@login_required
def analyze_transfer():
    """
    Analiza viabilidad de cambio de programa sin ejecutarlo.
    Body: {from_program_id, to_program_id}

    Analiza SIEMPRE el proceso del propio usuario y sólo si el cambio sería
    ejecutable: así el aspirante ve la razón antes de llegar al botón de
    confirmar, en vez de descubrirla al final.
    """
    data = request.get_json() or {}
    from_id = _int_or_none(data.get('from_program_id'))
    to_id = _int_or_none(data.get('to_program_id'))

    if from_id is None or to_id is None:
        return jsonify({
            "ok": False,
            "error": "Se requieren from_program_id y to_program_id"
        }), 400

    try:
        ProgramTransferService.validate_transfer(current_user.id, from_id, to_id)
    except ProgramTransferError as e:
        return _transfer_denied(e)

    try:
        analysis = ProgramTransferService.analyze_transfer(
            current_user.id,
            from_id,
            to_id
        )
        return jsonify({"ok": True, "analysis": analysis}), 200
    except Exception as e:
        current_app.logger.error(f"Error al analizar cambio de programa: {e}")
        return jsonify({"ok": False, "error": "No se pudo analizar el cambio de programa."}), 500


@api_program_changes.route('/execute', methods=['POST'])
@login_required
def execute_transfer():
    """
    Ejecuta el cambio de programa del propio usuario.
    Body: {from_program_id, to_program_id, reason}
    """
    data = request.get_json() or {}
    from_id = _int_or_none(data.get('from_program_id'))
    to_id = _int_or_none(data.get('to_program_id'))
    reason = data.get('reason', '')

    if from_id is None or to_id is None:
        return jsonify({
            "ok": False,
            "error": "Se requieren from_program_id y to_program_id"
        }), 400

    # Validar ANTES de crear la solicitud: un cambio denegado no debe dejar
    # basura en program_change_request.
    try:
        ProgramTransferService.validate_transfer(current_user.id, from_id, to_id)
    except ProgramTransferError as e:
        return _transfer_denied(e)

    # Crear el request (para auditoría). Queda 'pending' hasta que se ejecuta.
    change_request = ProgramChangesService.create_request(
        applicant_id=current_user.id,
        from_program_id=from_id,
        to_program_id=to_id,
        reason=reason
    )

    # Ejecutar transferencia. `validate_transfer` corre de nuevo dentro del
    # servicio (defensa en profundidad) y puede volver a denegar si algo cambió
    # entre ambas llamadas.
    try:
        result = ProgramTransferService.execute_transfer(
            current_user.id,
            from_id,
            to_id,
            change_request_id=change_request.id
        )
    except ProgramTransferError as e:
        change_request.status = 'cancelled'
        db.session.commit()
        return _transfer_denied(e)

    if result['success']:
        # El cambio lo ejecutó el propio aspirante bajo las reglas del
        # autoservicio: NADIE lo aprobó. Marcarlo 'approved' con
        # decided_by = el propio solicitante era una firma falsa.
        change_request.status = 'executed'
        change_request.decided_at = now_local()
        db.session.commit()

        # Registrar transferencia ejecutada en el historial
        try:
            from_program = ProgramTransferService.get_program(from_id)
            to_program = ProgramTransferService.get_program(to_id)

            UserHistoryService.log_program_transfer_execution(
                user_id=current_user.id,
                from_program=from_program.name if from_program else f"ID {from_id}",
                to_program=to_program.name if to_program else f"ID {to_id}",
                documents_moved=result.get('updated_documents', 0),
                documents_lost=result.get('deleted_documents', 0)
            )
            db.session.commit()
        except Exception as e:
            current_app.logger.error(f"Error al registrar transferencia ejecutada en historial: {e}")

        return jsonify({
            "ok": True,
            "message": "Cambio de programa completado exitosamente",
            "result": result
        }), 200
    else:
        current_app.logger.error(
            f"Fallo al ejecutar cambio de programa {from_id}->{to_id} "
            f"del usuario {current_user.id}: {result.get('error')}"
        )
        change_request.status = 'cancelled'
        db.session.commit()
        return jsonify({
            "ok": False,
            "error": "No se pudo completar el cambio de programa."
        }), 500
