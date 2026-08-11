# app/routes/api/users_api.py
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from app import db
from datetime import datetime

from app.models.user_history import UserHistory
from app.services.user_history_service import UserHistoryService
from app.utils.history_formatter import HistoryFormatter
from app.utils.permissions import permission_required, program_scope_required
from app.utils.validators import (
    InputValidationError,
    validate_person_name,
    validate_short_text,
)

api_users = Blueprint("api_users", __name__, url_prefix="/api/v1/users")

def _sanitize(s: str | None) -> str | None:
    if s is None:
        return None
    s = s.strip()
    return s or None

def _has_text(value) -> bool:
    """True when the payload carries a non-blank string for this field."""
    return isinstance(value, str) and bool(value.strip())

@api_users.get("/me")
@login_required
def me():
    u = current_user
    data = {
        "id": u.id,
        "first_name": u.first_name,
        "last_name": u.last_name,
        "mother_last_name": u.mother_last_name,
        "username": u.username,
        "email": u.email,
        "is_internal": u.is_internal,
        "scolarship_type": u.scolarship_type,
        "role": u.role.name if u.role else None,
        "avatar_url": u.avatar_url,
        "last_login": u.last_login.isoformat() if u.last_login else None,
        "registration_date": u.registration_date.isoformat() if u.registration_date else None,
        "profile_completed": u.profile_completed,
    }
    return jsonify({"data": {"user": data}, "error": None, "meta": {}}), 200

@api_users.patch("/me/complete-profile")
@login_required
def complete_profile():
    """
    Actualiza la información completa del perfil del usuario.
    Todos los campos adicionales que determinan si el perfil está "completo".
    """
    payload = request.get_json(silent=True) or {}
    
    # Campos de información personal
    current_user.phone = _sanitize(payload.get("phone"))
    current_user.mobile_phone = _sanitize(payload.get("mobile_phone"))
    current_user.address = _sanitize(payload.get("address"))
    current_user.curp = _sanitize(payload.get("curp"))
    current_user.rfc = _sanitize(payload.get("rfc"))
    current_user.birth_place = _sanitize(payload.get("birth_place"))
    current_user.cedula_profesional = _sanitize(payload.get("cedula_profesional"))
    current_user.nss = _sanitize(payload.get("nss"))
    
    # Fecha de nacimiento (requiere manejo especial)
    birth_date_str = payload.get("birth_date")
    if birth_date_str:
        try:
            current_user.birth_date = datetime.strptime(birth_date_str, "%Y-%m-%d").date()
        except ValueError:
            return jsonify({
                "data": None,
                "flash": [{"level": "danger", "message": "Formato de fecha de nacimiento inválido."}],
                "error": {"code": "VALIDATION", "message": "Fecha inválida"},
                "meta": {}
            }), 400
    
    # Contacto de emergencia
    current_user.emergency_contact_name = _sanitize(payload.get("emergency_contact_name"))
    current_user.emergency_contact_phone = _sanitize(payload.get("emergency_contact_phone"))
    current_user.emergency_contact_relationship = _sanitize(payload.get("emergency_contact_relationship"))
    
    # Actualizar automáticamente el estado de perfil completo
    was_complete_before = current_user.profile_completed
    is_complete_now = current_user.update_profile_completion_status()
    
    db.session.commit()
    
    # Registrar en el historial si completó el perfil por primera vez
    if not was_complete_before and is_complete_now:
        try:
            UserHistoryService.log_profile_completion(user_id=current_user.id)
            db.session.commit()
        except Exception as e:
            from flask import current_app
            current_app.logger.error(f"Error al registrar completado de perfil en historial: {e}")
    
    # Mensaje diferente si acaba de completar el perfil
    if not was_complete_before and is_complete_now:
        flash_message = "¡Perfil completado exitosamente! Ahora eres elegible para entrevistas."
        flash_level = "success"
    elif is_complete_now:
        flash_message = "Información del perfil actualizada exitosamente."
        flash_level = "success"
    else:
        flash_message = "Información guardada. Completa todos los campos requeridos para finalizar tu perfil."
        flash_level = "warning"
    
    return jsonify({
        "data": {
            "profile_completed": is_complete_now,
            "was_completed_before": was_complete_before,
            "newly_completed": not was_complete_before and is_complete_now
        },
        "flash": [{"level": flash_level, "message": flash_message}],
        "error": None,
        "meta": {"completion_status_changed": was_complete_before != is_complete_now}
    }), 200

@api_users.get("/me/profile-completion")
@login_required
def profile_completion_status():
    """
    Obtiene el estado detallado de completitud del perfil.
    """
    required_fields = {
        "phone_or_mobile": bool(current_user.phone or current_user.mobile_phone),
        "address": bool(current_user.address and current_user.address.strip()),
        "curp": bool(current_user.curp and current_user.curp.strip()),
        "birth_date": bool(current_user.birth_date),
        "emergency_contact_name": bool(current_user.emergency_contact_name and current_user.emergency_contact_name.strip()),
        "emergency_contact_phone": bool(current_user.emergency_contact_phone and current_user.emergency_contact_phone.strip()),
        "emergency_contact_relationship": bool(current_user.emergency_contact_relationship and current_user.emergency_contact_relationship.strip())
    }
    
    completed_count = sum(required_fields.values())
    total_count = len(required_fields)
    completion_percentage = (completed_count / total_count) * 100
    
    return jsonify({
        "data": {
            "profile_completed": current_user.profile_completed,
            "completion_percentage": completion_percentage,
            "required_fields_status": required_fields,
            "completed_count": completed_count,
            "total_count": total_count,
            "missing_fields": [field for field, completed in required_fields.items() if not completed]
        },
        "error": None,
        "meta": {}
    }), 200

# Actualizar el método update_me existente para que también verifique el perfil:
@api_users.patch("/me")
@login_required  
def update_me():
    """
    JSON:
      - first_name (str, req)
      - last_name (str, req)
      - mother_last_name (str, opt)
      - scolarship_type (str, opt)
    """
    payload = request.get_json(silent=True) or {}

    first_raw = payload.get("first_name")
    last_raw = payload.get("last_name")
    if not _has_text(first_raw) or not _has_text(last_raw):
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Nombre y Apellido Paterno son obligatorios."}],
            "error": {"code": "VALIDATION", "message": "Campos requeridos faltantes"},
            "meta": {}
        }), 400

    # Same rules as self-registration (app/utils/validators.py): these values
    # are rendered by the staff consoles, so storage must stay clean.
    try:
        first = validate_person_name(first_raw, label="Nombre")
        last = validate_person_name(last_raw, label="Apellido paterno")
        mother = validate_person_name(
            payload.get("mother_last_name"), label="Apellido materno", required=False
        )
        scol = validate_short_text(
            payload.get("scolarship_type"), label="Tipo de beca"
        )
    except InputValidationError as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": e.message}],
            "error": {"code": "VALIDATION", "message": e.message},
            "meta": {}
        }), 400

    current_user.first_name = first
    current_user.last_name = last
    current_user.mother_last_name = mother
    if scol is not None:
        current_user.scolarship_type = scol

    # Actualizar estado de perfil completo
    current_user.update_profile_completion_status()

    db.session.commit()

    return jsonify({
        "data": {"user": {
            "first_name": current_user.first_name,
            "last_name": current_user.last_name,
            "mother_last_name": current_user.mother_last_name,
            "scolarship_type": current_user.scolarship_type,
            "profile_completed": current_user.profile_completed
        }},
        "flash": [{"level": "success", "message": "Perfil actualizado exitosamente."}],
        "error": None, "meta": {}
    }), 200

@api_users.get("/me/history")
@login_required
def user_history():
    """
    Obtiene el historial de cambios del usuario con formato legible.
    Parámetros de query:
    - format: 'formatted' para descripción legible o 'raw' para datos originales (default: formatted)
    - limit: número de registros a retornar (default: 50, max: 100)
    """
    format_type = request.args.get('format', 'formatted')
    limit = min(int(request.args.get('limit', 50)), 100)
    
    # Obtener el historial del usuario
    history_entries = UserHistoryService.get_user_history(
        user_id=current_user.id,
        limit=limit,
        order_by='desc'
    )
    
    # Formatear las entradas si se solicita
    formatted_history = []
    formatter = HistoryFormatter()
    
    for entry in history_entries:
        entry_dict = entry.to_dict()
        
        # Agregar descripción formateada si se solicita
        if format_type == 'formatted':
            entry_dict['formatted_description'] = formatter.format_history_entry(entry)
        
        formatted_history.append(entry_dict)
    
    return jsonify({
        "data": {
            "history": formatted_history,
            "total_count": len(history_entries),
            "format_type": format_type
        },
        "error": None,
        "meta": {
            "user_id": current_user.id,
            "ordered_by": "timestamp_desc",
            "limit_applied": limit
        }
    }), 200


# ─────────────────────────────────────────────────────────────────────────────
# Profile Activity Feed + Upcoming Events + Documents grouped by phase
# ─────────────────────────────────────────────────────────────────────────────

@api_users.get('/me/activity')
@login_required
@permission_required('profile.api.view_own_activity')
def my_activity():
    """Unified recent-activity feed (history + notifications + submissions + events)."""
    from app.services import profile_activity_service as profile_svc
    try:
        limit = min(int(request.args.get('limit', 6)), 50)
    except (TypeError, ValueError):
        limit = 6

    items = profile_svc.get_recent_activity(current_user.id, limit=limit)
    return jsonify({
        "data": items,
        "error": None,
        "meta": {"count": len(items), "limit": limit}
    }), 200


@api_users.get('/me/upcoming-events')
@login_required
@permission_required('profile.api.view_own_upcoming_events')
def my_upcoming_events():
    """Events the user is registered for whose date is in the future."""
    from app.services import profile_activity_service as profile_svc
    try:
        limit = min(int(request.args.get('limit', 5)), 20)
    except (TypeError, ValueError):
        limit = 5

    items = profile_svc.get_upcoming_events(current_user.id, limit=limit)
    return jsonify({
        "data": items,
        "error": None,
        "meta": {"count": len(items), "limit": limit}
    }), 200


@api_users.get('/me/documents-history')
@login_required
@permission_required('profile.api.view_own_documents')
def my_documents_history():
    """Submissions grouped by phase (admission/permanence/conclusion). Permanence by semester."""
    from app.services import profile_activity_service as profile_svc
    grouped = profile_svc.get_user_documents_grouped(current_user.id)
    return jsonify({
        "data": grouped,
        "error": None,
        "meta": {}
    }), 200


# ─────────────────────────────────────────────────────────────────────────────
# Profile Photo
# ─────────────────────────────────────────────────────────────────────────────

@api_users.get('/me/photo-status')
@login_required
def my_photo_status():
    """Returns the current photo flags so the UI can decide which button to show."""
    has_photo = bool(current_user.avatar and current_user.avatar != 'default.jpg')
    return jsonify({
        "data": {
            "has_photo": has_photo,
            "avatar_url": current_user.avatar_url,
            "photo_change_allowed": bool(current_user.photo_change_allowed),
            "photo_change_requested_at": (
                current_user.photo_change_requested_at.isoformat()
                if current_user.photo_change_requested_at else None
            ),
            "can_upload": (not has_photo) or bool(current_user.photo_change_allowed),
        },
        "error": None,
        "meta": {}
    }), 200


@api_users.post('/me/photo')
@login_required
@permission_required('profile.api.upload_photo')
def upload_my_photo():
    """Upload a new profile photo. Compressed to 512px JPEG q85."""
    from app.services import profile_photo_service as photo_svc

    file_storage = request.files.get('photo')
    if not file_storage:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "No se recibió ningún archivo."}],
            "error": {"code": "MISSING_FILE", "message": "Falta el archivo 'photo'"},
            "meta": {}
        }), 400

    try:
        photo_svc.upload_photo(
            user_id=current_user.id,
            file_storage=file_storage,
            requester_id=current_user.id,
            is_self=True,
        )
        return jsonify({
            "data": {"avatar_url": current_user.avatar_url},
            "flash": [{"level": "success", "message": "Foto de perfil actualizada"}],
            "error": None,
            "meta": {}
        }), 200
    except photo_svc.PhotoChangeNotAllowed as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "warning", "message": str(e)}],
            "error": {"code": "NOT_ALLOWED", "message": str(e)},
            "meta": {}
        }), 403
    except photo_svc.ProfilePhotoError as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": str(e)}],
            "error": {"code": "PHOTO_ERROR", "message": str(e)},
            "meta": {}
        }), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Error al actualizar foto"}],
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500


@api_users.post('/me/photo/request-change')
@login_required
@permission_required('profile.api.request_photo_change')
def request_my_photo_change():
    """Student requests permission from the coordinator to change their photo."""
    from app.services import profile_photo_service as photo_svc

    payload = request.get_json(silent=True) or {}
    reason = (payload.get('reason') or '').strip() or None

    try:
        photo_svc.request_photo_change(user_id=current_user.id, reason=reason)
        return jsonify({
            "data": {"requested": True},
            "flash": [{"level": "success", "message": "Solicitud enviada al coordinador"}],
            "error": None,
            "meta": {}
        }), 200
    except photo_svc.ProfilePhotoError as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "warning", "message": str(e)}],
            "error": {"code": "INVALID_STATE", "message": str(e)},
            "meta": {}
        }), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Error al enviar solicitud"}],
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500


# Coordinator endpoints

@api_users.get('/photo-requests')
@login_required
@permission_required('profile.api.list_photo_requests')
def list_photo_requests():
    """Coordinator: lists pending photo-change requests for their programs."""
    from app.services import profile_photo_service as photo_svc
    items = photo_svc.list_pending_photo_requests(coordinator_id=current_user.id)
    return jsonify({"data": items, "error": None, "meta": {"count": len(items)}}), 200


@api_users.post('/<int:user_id>/photo/enable-change')
@login_required
@permission_required('profile.api.enable_photo_change')
@program_scope_required(user_id_kwarg='user_id')
def enable_photo_change(user_id):
    """
    Coordinator approves or rejects a photo-change request.

    El permiso dice QUÉ; `program_scope_required` dice SOBRE QUIÉN: sólo
    estudiantes de los programas del solicitante (el jefe de posgrado, todos).
    """
    from app.services import profile_photo_service as photo_svc
    from app.services.program_scope_service import ProgramScopeDenied

    payload = request.get_json(silent=True) or {}
    approve = bool(payload.get('approve', True))
    reason = (payload.get('reason') or '').strip() or None

    try:
        photo_svc.enable_photo_change(
            target_user_id=user_id,
            coordinator_id=current_user.id,
            approve=approve,
            reason=reason,
        )
        msg = (
            'Cambio de foto habilitado para el estudiante'
            if approve else 'Solicitud de cambio rechazada'
        )
        return jsonify({
            "data": {"approved": approve},
            "flash": [{"level": "success", "message": msg}],
            "error": None,
            "meta": {}
        }), 200
    except ProgramScopeDenied as e:
        db.session.rollback()
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": e.message}],
            "error": {"code": "FORBIDDEN", "message": e.message},
            "meta": {}
        }), 403
    except photo_svc.ProfilePhotoError as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "warning", "message": str(e)}],
            "error": {"code": "INVALID_STATE", "message": str(e)},
            "meta": {}
        }), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Error al procesar solicitud"}],
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500


@api_users.post('/<int:user_id>/photo')
@login_required
@permission_required('profile.api.upload_photo_for_student')
@program_scope_required(user_id_kwarg='user_id')
def coordinator_upload_photo(user_id):
    """
    Coordinator uploads a photo on behalf of a student.

    Sobrescribir la foto de alguien borra la anterior del disco y deja su
    `photo_change_allowed` en False, así que es una escritura sobre datos
    personales: exige que el estudiante esté dentro del alcance de programas.
    """
    from app.services import profile_photo_service as photo_svc
    from app.services.program_scope_service import ProgramScopeDenied

    file_storage = request.files.get('photo')
    if not file_storage:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "No se recibió ningún archivo."}],
            "error": {"code": "MISSING_FILE", "message": "Falta el archivo 'photo'"},
            "meta": {}
        }), 400

    try:
        photo_svc.upload_photo(
            user_id=user_id,
            file_storage=file_storage,
            requester_id=current_user.id,
            is_self=False,
        )
        return jsonify({
            "data": {"user_id": user_id},
            "flash": [{"level": "success", "message": "Foto del estudiante actualizada"}],
            "error": None,
            "meta": {}
        }), 200
    except ProgramScopeDenied as e:
        db.session.rollback()
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": e.message}],
            "error": {"code": "FORBIDDEN", "message": e.message},
            "meta": {}
        }), 403
    except photo_svc.ProfilePhotoError as e:
        return jsonify({
            "data": None,
            "flash": [{"level": "warning", "message": str(e)}],
            "error": {"code": "PHOTO_ERROR", "message": str(e)},
            "meta": {}
        }), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Error al actualizar foto"}],
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500