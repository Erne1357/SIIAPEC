# app/routes/api/admin/history_api.py
"""
API del historial institucional (`/api/v1/admin/history`).

Permiso ≠ alcance. Este blueprint tiene tres clases de endpoint y cada una
necesita una guarda distinta:

  1. CATÁLOGOS ESTÁTICOS — `/actions/critical`, `/retention-policies`.
     Devuelven constantes del servicio (nombres de acción, etiquetas, años de
     retención). No contienen datos de ningún usuario ni de ningún programa,
     así que basta con el permiso.

  2. LECTURA DEL LOG — `/recent`.
     Cada fila serializa su `details` JSON en crudo: número de control,
     nombre del estudiante, nombre del documento, nombre del programa. El
     historial y el número de control están en la lista de campos PROHIBIDOS
     entre programas, de modo que la consulta se filtra por el alcance del
     llamador (`current_accessible_program_ids()`), nunca por su permiso.

  3. OPERACIONES SOBRE EL LOG COMPLETO — `/statistics`, `/cleanup/preview`,
     `/cleanup/execute`.
     `HistoryRetentionService` agrega y borra sobre el historial de TODA la
     institución; no existe una proyección por programa de "la entrada más
     antigua del sistema" ni una limpieza que respete el alcance. Por eso
     exigen alcance GLOBAL (jefatura de posgrado) además del permiso: un
     coordinador con `admin_history.api.statistics` por rol, o una delegación
     de `admin_history.api.cleanup_execute` acotada a un programa, no pueden
     medir ni borrar el log de los demás.
"""

from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user

from app.utils.permissions import (
    permission_required,
    current_accessible_program_ids,
)
from app.services import program_scope_service as scope_service
from app.services.user_history_service import UserHistoryService
from app.services.history_retention_service import HistoryRetentionService
from app.utils.history_formatter import HistoryFormatter

api_admin_history = Blueprint('api_admin_history', __name__, url_prefix='/api/v1/admin/history')


# ─── Respuestas estándar ─────────────────────────────────────────────────────

def _ok(data, meta=None):
    return jsonify({"data": data, "error": None, "meta": meta or {}}), 200


def _business_error(message, code='BUSINESS_ERROR', status=400):
    return jsonify({
        "data": None,
        "flash": [{"level": "warning", "message": message}],
        "error": {"code": code, "message": message},
        "meta": {}
    }), status


def _forbidden(message):
    return jsonify({
        "data": None,
        "flash": [{"level": "danger", "message": message}],
        "error": {"code": "FORBIDDEN", "message": message},
        "meta": {}
    }), 403


def _server_error(exc, context):
    """Registra la excepción y devuelve un mensaje genérico.

    El texto de la excepción puede contener SQL, rutas de archivo o datos de
    otros usuarios; nunca viaja al cliente.
    """
    current_app.logger.exception('history_api: fallo en %s: %s', context, exc)
    message = 'Ocurrió un error al procesar la solicitud del historial.'
    return jsonify({
        "data": None,
        "error": {"code": "SERVER_ERROR", "message": message},
        "meta": {}
    }), 500


# ─── Guardas ─────────────────────────────────────────────────────────────────

_GLOBAL_ONLY_MESSAGE = (
    'El historial completo de la institución sólo está disponible para la '
    'jefatura de posgrado.'
)


def _require_global_scope():
    """
    Exige alcance GLOBAL (`get_accessible_program_ids()` == None).

    Se usa en los endpoints que agregan o borran sobre el log entero. No es
    equivalente a un permiso: `admin_history.api.statistics` lo tiene
    `program_admin` por rol, y `admin_history.api.cleanup_execute` es
    delegable acotado a un programa.

    Returns:
        None si el llamador tiene alcance global; la respuesta 403 si no.
    """
    if scope_service.is_global_scope(current_user):
        return None
    return _forbidden(_GLOBAL_ONLY_MESSAGE)


# ─── Validación de la configuración de retención ─────────────────────────────

_RETENTION_CONFIG_KEYS = frozenset(HistoryRetentionService.DEFAULT_RETENTION.keys())


def _resolve_retention_config(raw):
    """
    Valida el `retention_config` recibido del cliente y lo fusiona sobre los
    valores por defecto.

    Sin esto, el cuerpo de la petición controla directamente qué se borra:
    `{"active_users": 0}` elimina todo el historial no crítico, y una clave
    parcial (sin `active_users`) hace explotar `cleanup_old_history` con un
    KeyError porque el servicio usa esa clave como respaldo.

    Returns:
        (config, error_message). `config` es siempre un dict completo cuando
        no hay error.
    """
    defaults = dict(HistoryRetentionService.DEFAULT_RETENTION)
    if raw is None:
        return defaults, None
    if not isinstance(raw, dict):
        return None, 'La configuración de retención debe ser un objeto.'

    for key, value in raw.items():
        if key not in _RETENTION_CONFIG_KEYS:
            return None, f"Clave de retención no reconocida: '{key}'."
        # bool es subclase de int en Python: se rechaza explícitamente.
        if isinstance(value, bool) or not isinstance(value, int):
            return None, f"El valor de '{key}' debe ser un número entero de años."
        if value != -1 and value < 1:
            return None, (
                f"El valor de '{key}' debe ser -1 (permanente) o de al menos 1 año."
            )
        defaults[key] = value

    return defaults, None


# ─── Endpoints ───────────────────────────────────────────────────────────────

@api_admin_history.route('/statistics', methods=['GET'])
@login_required
@permission_required('admin_history.api.statistics')
def get_history_statistics():
    """Obtiene estadísticas del historial de usuarios (log completo, sólo jefatura)"""
    denied = _require_global_scope()
    if denied:
        return denied

    try:
        stats = HistoryRetentionService.get_retention_statistics()
        return _ok(stats)
    except Exception as e:
        return _server_error(e, 'get_history_statistics')


@api_admin_history.route('/cleanup/preview', methods=['POST'])
@login_required
@permission_required('admin_history.api.cleanup_preview')
def preview_cleanup():
    """Previsualiza qué registros se eliminarían en una limpieza"""
    denied = _require_global_scope()
    if denied:
        return denied

    data = request.get_json(silent=True) or {}
    retention_config, config_error = _resolve_retention_config(data.get('retention_config'))
    if config_error:
        return _business_error(config_error, code='VALIDATION_ERROR')

    try:
        preview_stats = HistoryRetentionService.cleanup_old_history(
            dry_run=True,
            retention_config=retention_config
        )
        return _ok(
            preview_stats,
            meta={
                "dry_run": True,
                "retention_config": retention_config,
                "message": "Previsualización completada - no se eliminó ningún registro",
            },
        )
    except Exception as e:
        return _server_error(e, 'preview_cleanup')


@api_admin_history.route('/cleanup/execute', methods=['POST'])
@login_required
@permission_required('admin_history.api.cleanup_execute')
def execute_cleanup():
    """Ejecuta la limpieza del historial (OPERACIÓN DESTRUCTIVA)"""
    denied = _require_global_scope()
    if denied:
        return denied

    data = request.get_json(silent=True) or {}
    confirm = data.get('confirm', False)
    if confirm is not True:
        return _business_error("Debe confirmar la operación con 'confirm': true")

    retention_config, config_error = _resolve_retention_config(data.get('retention_config'))
    if config_error:
        return _business_error(config_error, code='VALIDATION_ERROR')

    try:
        cleanup_stats = HistoryRetentionService.cleanup_old_history(
            dry_run=False,
            retention_config=retention_config
        )
        return _ok(
            cleanup_stats,
            meta={
                "dry_run": False,
                "retention_config": retention_config,
                "message": (
                    f"Limpieza completada - {cleanup_stats['entries_deleted']} "
                    "registros eliminados"
                ),
            },
        )
    except Exception as e:
        from app import db
        db.session.rollback()
        return _server_error(e, 'execute_cleanup')


@api_admin_history.route('/actions/critical', methods=['GET'])
@login_required
@permission_required('admin_history.api.critical_actions')
def get_critical_actions():
    """Lista todas las acciones críticas definidas (catálogo estático)"""
    # Catálogo de constantes: no toca la tabla `user_history` ni ningún dato
    # de usuario, por eso no lleva guarda de alcance.
    return _ok({
        "critical_actions": sorted(UserHistoryService.CRITICAL_ACTIONS),
        "high_importance_actions": sorted(UserHistoryService.HIGH_IMPORTANCE_ACTIONS),
        "medium_importance_actions": sorted(UserHistoryService.MEDIUM_IMPORTANCE_ACTIONS),
        "action_descriptions": UserHistoryService.ACTIONS,
    })


@api_admin_history.route('/recent', methods=['GET'])
@login_required
@permission_required('admin_history.api.view')
def get_recent_history():
    """
    Obtiene el historial reciente de los usuarios DENTRO del alcance del
    llamador, con formato legible.

    El alcance viene de `current_accessible_program_ids()`: None sólo para la
    jefatura de posgrado (todos los programas); para cualquier otro llamador,
    el conjunto de sus programas — y un conjunto vacío significa "ninguno",
    nunca "todos". El límite se recorta en el servicio
    (`UserHistoryService.MAX_ACTIVITY_LIMIT`).
    """
    limit = UserHistoryService.clamp_activity_limit(
        request.args.get('limit', UserHistoryService.DEFAULT_ACTIVITY_LIMIT, type=int)
    )
    format_type = request.args.get('format', 'formatted')  # 'formatted' o 'raw'

    program_ids = current_accessible_program_ids()

    try:
        recent_entries = UserHistoryService.get_recent_activity(
            limit=limit,
            program_ids=program_ids,
            viewer_id=current_user.id,
        )

        formatted_entries = []
        for entry in recent_entries:
            entry_dict = entry.to_dict()
            if format_type == 'formatted':
                entry_dict['formatted_description'] = HistoryFormatter.format_details(
                    entry.action, entry.details
                )
            formatted_entries.append(entry_dict)

        return _ok(
            formatted_entries,
            meta={
                "count": len(formatted_entries),
                "limit": limit,
                "max_limit": UserHistoryService.MAX_ACTIVITY_LIMIT,
                "format_type": format_type,
                "scoped": program_ids is not None,
            },
        )
    except Exception as e:
        return _server_error(e, 'get_recent_history')


@api_admin_history.route('/retention-policies', methods=['GET'])
@login_required
@permission_required('admin_history.api.view')
def get_retention_policies():
    """Obtiene las políticas de retención para cada tipo de acción (catálogo estático)"""
    # Se deriva del catálogo de acciones y de las constantes de retención: no
    # lee la tabla `user_history`, por eso no lleva guarda de alcance.
    try:
        policies = {
            action: UserHistoryService.get_retention_policy_for_action(action)
            for action in UserHistoryService.ACTIONS.keys()
        }
        return _ok({
            "policies": policies,
            "default_config": HistoryRetentionService.DEFAULT_RETENTION,
        })
    except Exception as e:
        return _server_error(e, 'get_retention_policies')
