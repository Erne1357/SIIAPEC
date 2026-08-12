# app/routes/api/invitations_api.py
from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user
from app.utils.permissions import permission_required
from app.services import program_scope_service as scope_service
from app.services.events_service import EventsService
from app.models.event import Event, EventInvitation
from app.models.program import Program
from app import db

api_invitations = Blueprint('api_invitations', __name__, url_prefix='/api/v1/invitations')

#: Tope duro de destinatarios por petición. Sin él, `allow_external=True` con
#: una lista larga convertía este endpoint en correo masivo institucional
#: enviado de forma síncrona (≈6 consultas por usuario) desde el buzón oficial.
DEFAULT_MAX_INVITE_BATCH = 100


def _deny(code: str, message: str, status: int):
    """Denegación con el envelope del proyecto y mensaje en español."""
    return jsonify({
        "ok": False,
        "data": None,
        "flash": [{"level": "danger", "message": message}],
        "error": {"code": code, "message": message},
        "meta": {}
    }), status


def _load_managed_event(event_id: int):
    """
    Carga el evento y exige autoridad de GESTIÓN sobre él.

    La regla es una sola y vive en `EventsService.user_may_manage_event`:
    evento CON programa → alcance sobre ese programa; evento SIN programa
    (institucional) → sólo alcance global.

    La copia local que estaba aquí devolvía True para todo evento sin
    programa. Con ella, el coordinador de cualquier programa reescribía las
    fechas del evento institucional, invitaba a aspirantes de otros posgrados
    (con correo real de por medio) y leía la lista completa de invitados
    —nombre, correo, notas del organizador— de toda la institución.

    Returns:
        (event, None) si procede; (None, respuesta_error) si no.
    """
    event = db.session.get(Event, event_id)
    if not event:
        return None, _deny("NOT_FOUND", "Evento no encontrado.", 404)
    if not EventsService.user_may_manage_event(current_user, event):
        return None, _deny("FORBIDDEN", "No tienes acceso a este evento.", 403)
    return event, None


@api_invitations.route('/event/<int:event_id>/invite', methods=['POST'])
@login_required
@permission_required('invitations.api.send')
def invite_students(event_id: int):
    """Invitar estudiantes a un evento"""
    event, err = _load_managed_event(event_id)
    if err:
        return err

    data = request.get_json() or {}
    user_ids = data.get('user_ids', [])
    notes = data.get('notes')
    allow_external = bool(data.get('allow_external', False))

    if not user_ids or not isinstance(user_ids, list):
        return _deny("VALIDATION_ERROR", "user_ids debe ser una lista.", 400)

    # `user_ids` es una lista de UUID públicos. Se rechaza la peticion entera
    # si alguno falla, en vez de invitar a medias.
    #
    # Un UUID que no resuelve sale por el MISMO 403 que uno fuera de alcance,
    # no por un 400 propio: con dos respuestas distintas, quien tuviera un UUID
    # ajeno averiguaria si señala a alguien real sin llegar nunca a invitarlo.
    # Se consigue mapeando lo irresoluble a un id imposible, que jamas puede
    # estar en el alcance de nadie y por tanto arrastra la peticion al 403 de
    # abajo.
    from app.models.user import User
    resolved = [User.by_uuid(raw) for raw in user_ids]
    target_ids = {row.id if row is not None else -1 for row in resolved}

    max_batch = current_app.config.get('MAX_INVITE_BATCH', DEFAULT_MAX_INVITE_BATCH)
    if len(target_ids) > max_batch:
        return _deny(
            "VALIDATION_ERROR",
            f"No puedes invitar a más de {max_batch} personas por envío. "
            f"Divide la lista en varios envíos.",
            400,
        )

    # `allow_external=True` salta la validación de programa del servicio: es
    # correo a personas ajenas al programa desde el buzón oficial. Reservado
    # al jefe de posgrado (alcance global).
    #
    # En un evento SIN programa la bandera no concede nada (el servicio sólo
    # la consulta cuando el evento tiene programa), así que se normaliza a
    # False en vez de rechazar la petición: el front la envía siempre que el
    # selector de alcance no es 'event_program'. A esa rama sólo llega ya el
    # alcance global, único que gestiona eventos institucionales.
    if event.program_id is None:
        allow_external = False
    elif allow_external and not scope_service.is_global_scope(current_user):
        return _deny(
            "FORBIDDEN",
            "Solo el jefe de posgrado puede invitar a personas ajenas al programa.",
            403,
        )

    # Defensa en profundidad: cada destinatario debe estar dentro del alcance
    # de quien invita. (El servicio ya descarta a los ajenos al programa del
    # evento como 'wrong_program'; aquí la petición se rechaza entera en vez
    # de invitar a medias.)
    #
    # La condición NO se ancla a que el evento tenga programa: esa forma
    # —`if event.program_id and not is_global_scope(...)`— era el mismo hueco
    # institucional en pequeño, porque en un evento sin programa se saltaba la
    # comprobación entera y el destinatario podía ser de cualquier posgrado.
    if not scope_service.is_global_scope(current_user):
        in_scope = scope_service.users_in_scope(current_user, target_ids, allow_self=False)
        if in_scope != target_ids:
            return _deny(
                "FORBIDDEN",
                "Solo puedes invitar a estudiantes de tus programas.",
                403,
            )

    try:
        results = EventsService.invite_students(
            event_id=event_id,
            user_ids=sorted(target_ids),
            invited_by=current_user.id,
            notes=notes,
            allow_external=allow_external
        )

        # Invitar a un evento en borrador está permitido —preparar la lista
        # antes de publicar es el flujo normal—, pero el invitado no verá la
        # invitación hasta la publicación, así que se le dice al organizador.
        flash = []
        if results.get('pending_publication'):
            flash.append({
                "level": "warning",
                "message": (
                    "Las invitaciones quedaron registradas, pero el evento aún no "
                    "está publicado: los invitados podrán confirmarlas cuando lo "
                    "publiques."
                ),
            })

        return jsonify({
            "ok": True,
            "invited": len(results['invited']),
            "already_invited": len(results['already_invited']),
            "already_registered": len(results['already_registered']),
            "pending_publication": bool(results.get('pending_publication')),
            "flash": flash,
            "details": results
        }), 201

    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500


@api_invitations.route('/event/<int:event_id>/list', methods=['GET'])
@login_required
@permission_required('invitations.api.list')
def list_event_invitations(event_id: int):
    """Listar invitaciones de un evento"""
    event, err = _load_managed_event(event_id)
    if err:
        return err

    try:
        invitations = EventsService.get_event_invitations(event_id)

        return jsonify({
            "ok": True,
            "invitations": invitations,
            "total": len(invitations)
        }), 200

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@api_invitations.route('/<int:invitation_id>/respond', methods=['POST'])
@login_required
def respond_to_invitation(invitation_id: int):
    """Responder a una invitación (estudiante)"""
    data = request.get_json() or {}
    accept = data.get('accept', False)

    try:
        invitation = EventsService.respond_to_invitation(
            invitation_id=invitation_id,
            user_id=current_user.id,
            accept=accept
        )

        return jsonify({
            "ok": True,
            "status": invitation.status,
            "message": "Invitación aceptada" if accept else "Invitación rechazada"
        }), 200

    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500


@api_invitations.route('/my-invitations', methods=['GET'])
@login_required
def my_invitations():
    """Obtener mis invitaciones pendientes"""
    try:
        invitations = EventsService.get_my_invitations(current_user.id)

        return jsonify({
            "ok": True,
            "invitations": invitations,
            "total": len(invitations)
        }), 200

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@api_invitations.route('/<int:invitation_id>', methods=['DELETE'])
@login_required
@permission_required('invitations.api.manage')
def cancel_invitation(invitation_id: int):
    """Cancelar una invitación"""
    # Alcance: la invitación pertenece a un evento, y el evento a un programa.
    # Sin esta comprobación cualquier portador del permiso —incluido un
    # coordinador con alcance vacío— cancelaba invitaciones de otros programas.
    invitation = db.session.get(EventInvitation, invitation_id)
    if not invitation:
        return _deny("NOT_FOUND", "Invitación no encontrada.", 404)

    event = db.session.get(Event, invitation.event_id)
    if not EventsService.user_may_manage_event(current_user, event):
        # 404 deliberado: quien no gestiona el evento no debe poder confirmar
        # qué ids de invitación existen.
        return _deny("NOT_FOUND", "Invitación no encontrada.", 404)

    try:
        EventsService.cancel_invitation(invitation_id)

        return jsonify({
            "ok": True,
            "message": "Invitación cancelada"
        }), 200

    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500


@api_invitations.route('/event/<int:event_id>/dates', methods=['PUT'])
@login_required
@permission_required('invitations.api.manage')
def update_event_dates(event_id: int):
    """Actualizar fechas del evento"""
    from datetime import datetime

    event, err = _load_managed_event(event_id)
    if err:
        return err

    data = request.get_json() or {}

    event_date = None
    event_end_date = None

    if data.get('event_date'):
        event_date = datetime.fromisoformat(data['event_date'].replace('Z', '+00:00'))

    if data.get('event_end_date'):
        event_end_date = datetime.fromisoformat(data['event_end_date'].replace('Z', '+00:00'))

    try:
        updated_event = EventsService.update_event_dates(
            event_id=event_id,
            event_date=event_date,
            event_end_date=event_end_date
        )

        return jsonify({
            "ok": True,
            "event_date": updated_event.event_date.isoformat() if updated_event.event_date else None,
            "event_end_date": updated_event.event_end_date.isoformat() if updated_event.event_end_date else None
        }), 200

    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
