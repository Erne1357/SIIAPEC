# app/routes/api/extensions_api.py - Actualizado
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from app.utils.permissions import permission_required
from app.services.extensions_service import ExtensionsService
from app.services import extensions_scope_service as escope
from app.services.user_history_service import UserHistoryService
from app import db
from app.models.archive import Archive
from app.models.user import User
from app.utils.datetime_utils import now_local, to_local_timezone
from datetime import datetime

api_extensions = Blueprint('api_extensions', __name__, url_prefix='/api/v1/extensions')


#: Id imposible con el que se filtra cuando el UUID recibido no resuelve: la
#: consulta devuelve vacío en lugar de devolverlo TODO sin filtrar.
_NO_MATCH_ID = -1


def _filter_id(model, raw):
    """UUID público de un filtro de query string → id interno."""
    if not raw:
        return None
    row = model.by_uuid(raw)
    return row.id if row is not None else _NO_MATCH_ID


def _serialize_request(er, include_contact: bool = False) -> dict:
    """
    Serializa una ExtensionRequest para los listados.

    include_contact añade el correo del solicitante (sólo para la vista de
    revisión administrativa, ya acotada a los programas del revisor).
    """
    user = er.user
    item = {
        # `id` sigue siendo entero: ExtensionRequest no tiene identificador
        # público. `user_id` y `archive_id` sí nombran filas que lo tienen.
        "id": er.id,
        "user_id": str(user.uuid) if user and user.uuid else None,
        "user_name": f"{user.first_name} {user.last_name}" if user else None,
        "archive_id": str(er.archive.uuid) if er.archive and er.archive.uuid else None,
        "archive_name": er.archive.name if er.archive else None,
        "status": er.status,
        "reason": er.reason,
        "requested_until": er.requested_until.isoformat() if er.requested_until else None,
        "granted_until": er.granted_until.isoformat() if er.granted_until else None,
        "condition_text": er.condition_text,
        "created_at": er.created_at.isoformat() if er.created_at else None,
        "decided_at": er.decided_at.isoformat() if er.decided_at else None,
    }
    if include_contact:
        item["user_email"] = user.email if user else None
    return item


@api_extensions.route('/requests', methods=['POST'])
@login_required
def create_extension_request():
    """
    Crea una solicitud de prórroga para un archivo específico.
    Ya no requiere que exista una submission previa.

    El objetivo es siempre el propio solicitante (user_id=current_user.id),
    así que no hay alcance de programa que verificar.

    JSON body:
    - archive_id (uuid str): identificador público del archivo
    - requested_until (str): Fecha hasta cuándo se necesita (ISO format)
    - reason (str): Motivo de la solicitud
    """
    data = request.get_json() or {}
    # `archive_id` viaja en el cuerpo como UUID público; el servicio sigue
    # recibiendo el entero.
    archive = Archive.by_uuid(data.get('archive_id'))
    archive_id = archive.id if archive else None
    requested_until = data.get('requested_until')
    reason = (data.get('reason') or '').strip()

    if not archive_id or not requested_until or not reason:
        return jsonify({
            "ok": False,
            "error": "archive_id, requested_until y reason son requeridos"
        }), 400

    # Validar formato de fecha
    try:
        requested_until_dt = datetime.fromisoformat(requested_until.replace('Z', '+00:00'))
    except ValueError:
        return jsonify({
            "ok": False,
            "error": "Formato de fecha inválido. Use ISO format (YYYY-MM-DD)"
        }), 400

    # Validar que la fecha sea futura. to_local_timezone normaliza fechas sin
    # zona horaria; comparar contra datetime.now() reventaba con un ISO en UTC.
    if to_local_timezone(requested_until_dt) <= now_local():
        return jsonify({
            "ok": False,
            "error": "La fecha solicitada debe ser futura"
        }), 400

    role = 'coordinator' if current_user.has_permission('extensions.api.list_for_review') else 'student'

    try:
        er = ExtensionsService.create_request(
            user_id=current_user.id,
            archive_id=archive_id,
            requested_by=current_user.id,
            reason=reason,
            requested_until=requested_until_dt,
            role=role
        )

        # Registrar en el historial
        try:
            archive_name = er.archive.name if er.archive else f"ID {archive_id}"

            UserHistoryService.log_extension_request(
                user_id=current_user.id,
                archive_name=archive_name,
                requested_until=requested_until,
                reason=reason,
                requested_by_admin=(role == 'coordinator')
            )
            db.session.commit()
        except Exception as e:
            from flask import current_app
            current_app.logger.error(f"Error al registrar solicitud de prórroga en historial: {e}")

        return jsonify({
            "ok": True,
            "id": er.id,
            "message": "Solicitud de prórroga enviada exitosamente"
        }), 201

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 400

@api_extensions.route('/requests', methods=['GET'])
@login_required
def list_extension_requests():
    """
    Lista solicitudes de prórroga.

    El estudiante sólo ve las suyas. El personal ve únicamente las de los
    programas a su alcance: sin ese recorte, cualquier cuenta con
    'extensions.api.list_for_review' listaba toda la institución con el nombre
    del solicitante.

    Query params:
    - user_id (uuid str): Filtrar por usuario (solo revisores)
    - archive_id (uuid str): Filtrar por archivo
    - status (str): Filtrar por estado
    - program_id (int): Filtrar por programa (solo revisores)
    """
    # Los dos filtros llegan por query string como UUID público. `type=int`
    # los habría leído como None en silencio y el listado habría salido SIN
    # filtrar; un UUID desconocido filtra por un id imposible → lista vacía.
    user_id = _filter_id(User, request.args.get('user_id'))
    archive_id = _filter_id(Archive, request.args.get('archive_id'))
    status = request.args.get('status')
    program_id = request.args.get('program_id', type=int)

    try:
        if current_user.has_permission('extensions.api.list_for_review'):
            requests = escope.list_requests_in_scope(
                current_user,
                user_id=user_id,
                archive_id=archive_id,
                status=status,
                program_id=program_id,
            )
        else:
            # Los estudiantes solo ven las suyas.
            requests = ExtensionsService.list_requests(
                user_id=current_user.id,
                archive_id=archive_id,
                status=status,
                program_id=program_id
            )

        items = [_serialize_request(er) for er in requests]

        return jsonify({"ok": True, "items": items}), 200

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@api_extensions.route('/requests/<int:req_id>/decision', methods=['PUT'])
@login_required
@permission_required('extensions.api.decide')
def decide_extension_request(req_id: int):
    """
    Decide sobre una solicitud de prórroga.

    JSON body:
    - status (str): 'granted', 'rejected', o 'cancelled'
    - granted_until (str, opcional): Fecha hasta cuándo se concede (requerido si status='granted')
    - condition_text (str, opcional): Condiciones específicas
    """
    # El permiso no dice de qué programa es la solicitud. Solicitud inexistente
    # y solicitud de otro programa responden igual (404), para no confirmar la
    # existencia de prórrogas ajenas.
    if not escope.request_in_scope(current_user, req_id):
        return jsonify({
            "ok": False,
            "error": "Solicitud de prórroga no encontrada"
        }), 404

    data = request.get_json() or {}
    status = data.get('status')
    granted_until = data.get('granted_until')
    condition_text = data.get('condition_text')

    if status not in ('granted', 'rejected', 'cancelled'):
        return jsonify({
            "ok": False,
            "error": "status debe ser 'granted', 'rejected' o 'cancelled'"
        }), 400

    granted_until_dt = None
    if status == 'granted':
        if not granted_until:
            return jsonify({
                "ok": False,
                "error": "granted_until es requerido cuando status='granted'"
            }), 400

        try:
            granted_until_dt = datetime.fromisoformat(granted_until.replace('Z', '+00:00'))
        except ValueError:
            return jsonify({
                "ok": False,
                "error": "Formato de granted_until inválido"
            }), 400

        # Una prórroga que vence en el pasado nace vencida: fabrica una deuda
        # documental que bloquea la asignación del número de control
        # (acceptance_service._check_document_debt) sin dejar más rastro que
        # decided_by. No se acepta.
        if to_local_timezone(granted_until_dt) <= now_local():
            return jsonify({
                "ok": False,
                "error": "La fecha de la prórroga debe ser futura"
            }), 400

    try:
        er = ExtensionsService.decide_request(
            request_id=req_id,
            status=status,
            decided_by=current_user.id,
            granted_until=granted_until_dt,
            condition_text=condition_text
        )

        # Primero hacer commit de la decisión de prórroga
        db.session.commit()

        # Luego registrar en el historial (con sus notificaciones)
        try:
            archive_name = er.archive.name if er.archive else f"ID {er.archive_id}"

            # Formatear la fecha para mostrarla al usuario de manera legible
            formatted_date = None
            if status == 'granted' and granted_until_dt:
                formatted_date = granted_until_dt.strftime('%d/%m/%Y')

            UserHistoryService.log_extension_decision(
                user_id=er.user_id,
                archive_name=archive_name,
                decision=status,
                granted_until=formatted_date,
                condition_text=condition_text,
                admin_id=current_user.id
            )
            # Commit del historial y notificaciones
            db.session.commit()
        except Exception as e:
            from flask import current_app
            current_app.logger.error(f"Error al registrar decisión de prórroga en historial: {e}")
            # No fallar la operación por un error de log
            pass

        return jsonify({
            "ok": True,
            "id": er.id,
            "status": er.status,
            "message": f"Solicitud {status} exitosamente"
        }), 200

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

@api_extensions.route('/archive/<uuid:archive_uuid>/status', methods=['GET'])
@login_required
def get_archive_extension_status(archive_uuid):
    """
    Obtiene el estado de prórroga para un archivo específico del usuario actual.

    Siempre consulta sobre current_user, así que no hay alcance que verificar.

    Retorna:
    - has_pending: bool - Si tiene solicitud pendiente
    - has_active: bool - Si tiene prórroga activa
    - effective_deadline: str - Fecha límite efectiva (si hay prórroga)
    - pending_request: object - Detalles de solicitud pendiente (si existe)
    """
    archive = Archive.by_uuid(archive_uuid)
    if archive is None:
        return jsonify({"ok": False, "error": "Archivo no encontrado"}), 404
    archive_id = archive.id

    try:
        has_pending = ExtensionsService.has_pending_request(current_user.id, archive_id)
        active_extension = ExtensionsService.get_active_extension(current_user.id, archive_id)
        effective_deadline = ExtensionsService.get_effective_deadline(current_user.id, archive_id)

        pending_request = None
        if has_pending:
            pending_requests = ExtensionsService.list_requests(
                user_id=current_user.id,
                archive_id=archive_id,
                status='pending'
            )
            if pending_requests:
                pr = pending_requests[0]
                pending_request = {
                    "id": pr.id,
                    "reason": pr.reason,
                    "requested_until": pr.requested_until.isoformat(),
                    "created_at": pr.created_at.isoformat()
                }

        return jsonify({
            "ok": True,
            "has_pending": has_pending,
            "has_active": bool(active_extension),
            "effective_deadline": effective_deadline.isoformat() if effective_deadline else None,
            "pending_request": pending_request
        }), 200

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@api_extensions.route('/requests/for-review', methods=['GET'])
@login_required
@permission_required('extensions.api.list_for_review')
def list_extension_requests_for_review():
    """
    Lista solicitudes con información adicional del usuario para revisión
    administrativa, acotada a los programas del revisor.

    Query params: user_id (uuid), status, program_id
    """
    user_id = _filter_id(User, request.args.get('user_id'))
    status = request.args.get('status')
    program_id = request.args.get('program_id', type=int)

    requests = escope.list_requests_in_scope(
        current_user,
        user_id=user_id,
        status=status,
        program_id=program_id,
    )

    items = [_serialize_request(er, include_contact=True) for er in requests]

    return jsonify({"ok": True, "items": items}), 200
