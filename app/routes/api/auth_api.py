# app/routes/api/auth_api.py
from flask import Blueprint, request, jsonify, session, redirect, url_for, current_app
from flask_login import login_user, logout_user, login_required, current_user
from datetime import datetime, timezone
from werkzeug.security import check_password_hash, generate_password_hash
from app.models.user import User
from app.services.auth_audit_service import AuthAuditService
from app.services.user_history_service import UserHistoryService
from app.utils.csrf import generate_csrf_token
from app.utils.datetime_utils import now_local
from app.utils.rate_limit import (
    clear_login_failures,
    client_ip,
    register_login_attempt,
)
from app import db
import re

api_auth_bp = Blueprint("api_auth", __name__, url_prefix="/api/v1/auth")

# Contraseña por defecto del sistema
DEFAULT_PASSWORD = "tecno#2K"

def validate_password_strength(password):
    """
    Valida la fortaleza de la contraseña.
    Requisitos:
    - Mínimo 8 caracteres
    - Al menos una mayúscula
    - Al menos una minúscula
    - Al menos un número
    - Al menos un caracter especial
    """
    if len(password) < 8:
        return False, "La contraseña debe tener al menos 8 caracteres"
    
    if not re.search(r'[A-Z]', password):
        return False, "La contraseña debe contener al menos una letra mayúscula"
    
    if not re.search(r'[a-z]', password):
        return False, "La contraseña debe contener al menos una letra minúscula"
    
    if not re.search(r'\d', password):
        return False, "La contraseña debe contener al menos un número"
    
    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        return False, "La contraseña debe contener al menos un caracter especial (!@#$%^&*...)"
    
    return True, "Contraseña válida"


# ---------------------------------------------------------------------------
# Login helpers
# ---------------------------------------------------------------------------

# Timing-equalizer hash. When the username does not exist we still have to
# spend one password-hash verification, otherwise the response time alone tells
# an attacker which usernames are real (a scrypt check costs orders of
# magnitude more than a failed SELECT). Built lazily and reused: it must be
# produced by generate_password_hash so it uses the exact same algorithm and
# work factor as the stored hashes.
_TIMING_EQUALIZER_HASH = None


def _burn_password_hash(password: str) -> None:
    """Spend the same CPU a real verification would, and discard the result."""
    global _TIMING_EQUALIZER_HASH
    if _TIMING_EQUALIZER_HASH is None:
        _TIMING_EQUALIZER_HASH = generate_password_hash("siiap-timing-equalizer")
    check_password_hash(_TIMING_EQUALIZER_HASH, password or "")


def _invalid_credentials_response():
    """
    The single generic failure. Wrong password, unknown username and disabled
    account must all return this exact body so none of them is an oracle.
    """
    return jsonify({
        "data": None,
        "error": {
            "code": "INVALID_CREDENTIALS",
            "message": "Usuario o contraseña inválidos"
        },
        "meta": {}
    }), 401


def _log_login_event(action: str, username: str, ip: str, user_agent: str,
                     user=None, reason: str = None) -> None:
    """
    Record an authentication event.

    The single seam between this route and the audit trail. It exists so that
    both failure branches — unknown username and known username — call the
    *same* function with the same arguments except ``user``, which the audit
    never branches on. See app/services/auth_audit_service.py for why these
    events go to the ``log`` table instead of ``user_history``, what is
    truncated, why only the network prefix of the client address is persisted,
    and how repeated failures are capped. Never raises.
    """
    user_id = user.id if user is not None else None
    if action == 'login_success':
        AuthAuditService.record_login_success(
            user_id=user_id, username=username, ip=ip, user_agent=user_agent
        )
    else:
        AuthAuditService.record_login_failure(
            username=username, ip=ip, user_agent=user_agent,
            reason=reason, user_id=user_id
        )


@api_auth_bp.post("/login")
def api_login():
    """
    Login de usuario.
    Verifica si el usuario debe cambiar su contraseña.
    """
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    ip = client_ip()
    # First cut only, so nothing downstream carries an oversized header around.
    # The audit truncates again to the length it actually stores.
    user_agent = (request.headers.get("User-Agent") or "")[:255]

    # A credential-less request never reaches the throttle. Every empty
    # username hashes to the same digest, so they would all share one counter
    # and one penalty key — the only bucket in the system not tied to a real
    # account — and the caller would get "demasiados intentos" for what is just
    # a malformed request. It would have failed with 401 anyway.
    if not username or not password:
        return jsonify({
            "data": None,
            "error": {
                "code": "INVALID_CREDENTIALS",
                "message": "Usuario o contraseña inválidos"
            },
            "meta": {}
        }), 401

    # Throttle: this must run before the database lookup and before any
    # password hashing, so a flood costs nothing beyond a Redis round trip.
    # It COUNTS as well as decides — the count has to happen before the yield
    # points below (Redis, User.query, scrypt), otherwise concurrent attempts
    # for the same username all read the same pre-increment value and pass
    # together. A successful login undoes it via clear_login_failures().
    verdict = register_login_attempt(username, ip)
    if verdict is not None:
        # No audit row here on purpose: the failures that produced the delay
        # are already recorded in the `log` table, and writing one row per
        # blocked attempt would turn a login flood into a Postgres write flood.
        current_app.logger.warning(
            f"[auth] Intento de login bloqueado por límite de intentos "
            f"(scope={verdict.scope}, ip={ip}, username={username!r})"
        )
        response = jsonify({
            "data": None,
            "error": {
                "code": "RATE_LIMITED",
                "message": "Demasiados intentos de inicio de sesión. "
                           "Espera unos minutos antes de volver a intentarlo."
            },
            "meta": {}
        })
        response.headers["Retry-After"] = str(verdict.retry_after)
        return response, 429

    user = User.query.filter_by(username=username).first()

    if user is None:
        # Unknown username: pay the hash anyway so the timing matches the
        # wrong-password path, then audit through the SAME call and fail
        # identically. The audit must not be cheaper here than on the branch
        # below, or the equalizer hash above buys nothing — a Postgres round
        # trip is far louder than the scrypt delta it is hiding.
        _burn_password_hash(password)
        _log_login_event('login_failed', username, ip, user_agent, reason='unknown_user')
        return _invalid_credentials_response()

    if not check_password_hash(user.password, password):
        _log_login_event('login_failed', username, ip, user_agent, user=user, reason='bad_password')
        return _invalid_credentials_response()

    # Flask-Login refuses to log in a user whose is_active is False and returns
    # False. Ignoring that return value used to report success to a disabled
    # account (and leaked which accounts are disabled), so branch on it and
    # answer with the same generic failure as a wrong password.
    if not login_user(user):
        _log_login_event('login_failed', username, ip, user_agent, user=user, reason='inactive_account')
        return _invalid_credentials_response()

    # Undo the attempt counted at the gate and release any pending delay: the
    # policy is a delay, never a lockout, so one correct password clears it.
    clear_login_failures(username)
    session['last_activity'] = now_local().timestamp()

    # Generar token CSRF para la nueva sesión
    new_csrf_token = generate_csrf_token(force_new=True)

    user.last_login = now_local()
    db.session.commit()

    _log_login_event('login_success', username, ip, user_agent, user=user)

    # NUEVO: Verificar si debe cambiar contraseña
    response_data = {
        "id": user.id, 
        "username": user.username, 
        "role": getattr(getattr(user, "role", None), "name", None),
        "must_change_password": user.must_change_password,  # Flag importante
        "csrf_token": new_csrf_token  # Token para actualizar en cliente
    }

    return jsonify({
        "data": response_data, 
        "error": None, 
        "meta": {}
    }), 200


@api_auth_bp.post("/change-password")
@login_required
def change_password():
    """
    Cambia la contraseña del usuario actual.
    
    JSON esperado:
        - current_password: str (contraseña actual)
        - new_password: str (nueva contraseña)
        - confirm_password: str (confirmación de nueva contraseña)
    
    Validaciones:
        - Contraseña actual correcta
        - Nueva contraseña cumple requisitos de seguridad
        - Nueva contraseña != contraseña por defecto
        - Nueva y confirmación coinciden
    """
    data = request.get_json(silent=True) or {}
    
    current_password = data.get("current_password", "").strip()
    new_password = data.get("new_password", "").strip()
    confirm_password = data.get("confirm_password", "").strip()
    
    # Validación 1: Todos los campos requeridos
    if not all([current_password, new_password, confirm_password]):
        return jsonify({
            "data": None,
            "flash": [{
                "level": "danger",
                "message": "Todos los campos son obligatorios"
            }],
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Campos requeridos faltantes"
            },
            "meta": {}
        }), 400
    
    # Validación 2: Contraseña actual correcta
    if not check_password_hash(current_user.password, current_password):
        return jsonify({
            "data": None,
            "flash": [{
                "level": "danger",
                "message": "La contraseña actual es incorrecta"
            }],
            "error": {
                "code": "INVALID_PASSWORD",
                "message": "Contraseña actual incorrecta"
            },
            "meta": {}
        }), 401
    
    # Validación 3: Nueva contraseña no puede ser la contraseña por defecto
    if new_password == DEFAULT_PASSWORD:
        return jsonify({
            "data": None,
            "flash": [{
                "level": "danger",
                "message": "No puedes usar la contraseña por defecto del sistema"
            }],
            "error": {
                "code": "INVALID_PASSWORD",
                "message": "Contraseña no permitida"
            },
            "meta": {}
        }), 400
    
    # Validación 4: Nueva contraseña y confirmación coinciden
    if new_password != confirm_password:
        return jsonify({
            "data": None,
            "flash": [{
                "level": "danger",
                "message": "Las contraseñas no coinciden"
            }],
            "error": {
                "code": "PASSWORD_MISMATCH",
                "message": "Las contraseñas no coinciden"
            },
            "meta": {}
        }), 400
    
    # Validación 5: Fortaleza de la contraseña
    is_valid, message = validate_password_strength(new_password)
    if not is_valid:
        return jsonify({
            "data": None,
            "flash": [{
                "level": "danger",
                "message": message
            }],
            "error": {
                "code": "WEAK_PASSWORD",
                "message": message
            },
            "meta": {}
        }), 400
    
    # Validación 6: Nueva contraseña diferente a la actual
    if check_password_hash(current_user.password, new_password):
        return jsonify({
            "data": None,
            "flash": [{
                "level": "warning",
                "message": "La nueva contraseña debe ser diferente a la actual"
            }],
            "error": {
                "code": "SAME_PASSWORD",
                "message": "Contraseña idéntica a la actual"
            },
            "meta": {}
        }), 400
    
    # Todo OK, cambiar contraseña
    current_user.password = generate_password_hash(new_password)
    current_user.must_change_password = False  # Ya cambió su contraseña
    db.session.commit()
    
    # Registrar en el historial
    try:
        UserHistoryService.log_password_change(
            user_id=current_user.id,
            changed_by_user=True
        )
        db.session.commit()
    except Exception as e:
        from flask import current_app
        current_app.logger.error(f"Error al registrar cambio de contraseña en historial: {e}")
    
    return jsonify({
        "data": {
            "user_id": current_user.id,
            "username": current_user.username,
            "password_changed": True
        },
        "flash": [{
            "level": "success",
            "message": "¡Contraseña cambiada exitosamente!"
        }],
        "error": None,
        "meta": {
            "must_change_password": False
        }
    }), 200


@api_auth_bp.route('/logout', methods=['GET', 'POST'])
@login_required
def api_logout():
    """
    - GET: permite usar <a href="..."> para cerrar sesión y redirigir a /login
    - POST: usado por JS (session timeout) => responde JSON
    """
    from app.utils.csrf import CSRF_SESSION_KEY
    
    session.pop('_flashes', None)
    logout_user()
    session.pop('last_activity', None)
    
    # IMPORTANTE: Eliminar el token CSRF viejo para forzar regeneración
    # Cuando se recargue /login, se generará un token fresco
    session.pop(CSRF_SESSION_KEY, None)

    if request.method == 'GET':
        return redirect(url_for('pages_auth.login_page'))

    return jsonify({
        "data": None,
        "flash": [{"level": "info", "message": "Has cerrado sesión."}],
        "error": None, 
        "meta": {}
    }), 200


@api_auth_bp.get("/me")
@login_required
def api_me():
    """
    Obtiene información del usuario actual.
    Incluye el flag must_change_password.
    """
    u = current_user
    # Obtener el token CSRF actual de la sesión
    from flask import session
    csrf_token = session.get("_csrf_token", "")
    
    return jsonify({
        "data": {
            "id": u.id, 
            "username": u.username, 
            "email": u.email,
            "first_name": u.first_name, 
            "last_name": u.last_name,
            "role": getattr(getattr(u, "role", None), "name", None),
            "must_change_password": u.must_change_password,
            "csrf_token": csrf_token  # Para que el cliente pueda actualizar si es necesario
        }, 
        "error": None, 
        "meta": {}
    }), 200


@api_auth_bp.get("/keepalive")
@login_required
def api_keepalive():
    """Mantiene la sesión activa"""
    session['last_activity'] = now_local().timestamp()
    return jsonify({
        "data": "OK",
        "error": None,
        "meta": {}
    }), 200


# ---------------------------------------------------------------------------
# Password reset / set via token (no auth required — token IS the auth)
# ---------------------------------------------------------------------------

@api_auth_bp.get("/reset-password/<token>/info")
def api_reset_password_info(token):
    """
    Verifica que el token sea válido y devuelve datos mínimos del usuario
    para mostrar en la página de configuración de contraseña.
    """
    from app.services import password_reset_service as prs

    try:
        prt = prs.get_token(token)
    except prs.TokenNotFound as e:
        return jsonify({
            "data": None,
            "error": {"code": "TOKEN_NOT_FOUND", "message": str(e)},
            "meta": {}
        }), 404
    except prs.TokenExpired as e:
        return jsonify({
            "data": None,
            "error": {"code": "TOKEN_EXPIRED", "message": str(e)},
            "meta": {}
        }), 410
    except prs.TokenAlreadyUsed as e:
        return jsonify({
            "data": None,
            "error": {"code": "TOKEN_USED", "message": str(e)},
            "meta": {}
        }), 410

    user = prt.user
    return jsonify({
        "data": {
            "username": user.username,
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "purpose": prt.purpose,
            "expires_at": prt.expires_at.isoformat() if prt.expires_at else None,
        },
        "error": None,
        "meta": {}
    }), 200


@api_auth_bp.post("/reset-password/<token>")
def api_reset_password(token):
    """
    Consume el token y establece una nueva contraseña.
    JSON: {new_password, confirm_password}
    """
    from app.services import password_reset_service as prs

    data = request.get_json(silent=True) or {}
    new_password = (data.get("new_password") or "").strip()
    confirm_password = (data.get("confirm_password") or "").strip()

    if not new_password or not confirm_password:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Todos los campos son obligatorios."}],
            "error": {"code": "VALIDATION_ERROR", "message": "Campos requeridos faltantes"},
            "meta": {}
        }), 400

    if new_password != confirm_password:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "Las contraseñas no coinciden."}],
            "error": {"code": "PASSWORD_MISMATCH", "message": "Las contraseñas no coinciden"},
            "meta": {}
        }), 400

    if new_password == DEFAULT_PASSWORD:
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "No puedes usar la contraseña por defecto del sistema."}],
            "error": {"code": "INVALID_PASSWORD", "message": "Contraseña no permitida"},
            "meta": {}
        }), 400

    try:
        user = prs.consume_token(token, new_password)
        db.session.commit()
    except prs.TokenNotFound as e:
        db.session.rollback()
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": str(e)}],
            "error": {"code": "TOKEN_NOT_FOUND", "message": str(e)},
            "meta": {}
        }), 404
    except prs.TokenExpired as e:
        db.session.rollback()
        return jsonify({
            "data": None,
            "flash": [{"level": "warning", "message": str(e)}],
            "error": {"code": "TOKEN_EXPIRED", "message": str(e)},
            "meta": {}
        }), 410
    except prs.TokenAlreadyUsed as e:
        db.session.rollback()
        return jsonify({
            "data": None,
            "flash": [{"level": "warning", "message": str(e)}],
            "error": {"code": "TOKEN_USED", "message": str(e)},
            "meta": {}
        }), 410
    except prs.WeakPassword as e:
        db.session.rollback()
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": str(e)}],
            "error": {"code": "WEAK_PASSWORD", "message": str(e)},
            "meta": {}
        }), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({
            "data": None,
            "error": {"code": "SERVER_ERROR", "message": str(e)},
            "meta": {}
        }), 500

    return jsonify({
        "data": {"username": user.username},
        "flash": [{"level": "success", "message": "Contraseña configurada. Ya puedes iniciar sesión."}],
        "error": None,
        "meta": {}
    }), 200