"""
Decoradores y utilidades para el sistema de permisos granulares de SIIAP.

Uso en rutas API:

    @permission_required('acceptance.api.list_applicants')
    def list_applicants(program_id):
        ...

    # Si basta con tener al menos uno de varios permisos:
    @any_permission_required('acceptance.api.list_applicants', 'acceptance.api.view_stats')
    def vista():
        ...

IMPORTANTE — permiso ≠ alcance:
    `permission_required` responde "¿QUÉ puede hacer este usuario?".
    NO responde "¿SOBRE QUIÉN?". Toda ruta que toque un estudiante, programa,
    documento, foto o decisión concreta debe añadir además:

    @program_scope_required(user_id_kwarg='student_id')     # objetivo = usuario
    @program_scope_required(program_id_kwarg='program_id')  # objetivo = programa

    La lógica vive en `app/services/program_scope_service.py`; aquí sólo está
    la capa Flask (current_user + respuesta 403).

    `permission_required` ya NO acepta `program_id_kwarg`. Aceptaba ese
    argumento y no hacía nada útil con él: los permisos de rol (de donde
    program_admin saca TODOS los suyos) no llevan programa, así que el permiso
    se concedía para cualquier program_id del URL. Parecía una garantía de
    alcance y no lo era. Pasarlo hoy es un TypeError en tiempo de import — a
    propósito: la ruta debe declarar `@program_scope_required(...)`.
"""

from functools import wraps
from flask import abort, request, jsonify, g
from flask_login import current_user

from app.services import program_scope_service as scope_service


def _abort_403():
    """
    Devuelve 403 en JSON para rutas API o AJAX,
    y deja que el error handler global maneje el resto (redirect + flash).
    """
    if (request.path.startswith('/api/') or
            request.is_json or
            request.headers.get('X-Requested-With') == 'XMLHttpRequest'):
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": "No tienes permiso para realizar esta acción."}],
            "error": {"code": "FORBIDDEN", "message": "No tienes permiso para realizar esta acción."},
            "meta": {}
        }), 403
    abort(403)


def permission_required(codename):
    """
    Decorador que protege una vista exigiendo un permiso específico.

    Responde SOLO "¿qué puede hacer este usuario?". Si la vista toca un
    programa, estudiante, documento o decisión concretos hay que añadir
    `@program_scope_required(...)` debajo; este decorador no da ninguna
    garantía de alcance y no acepta `program_id_kwarg`.

    Args:
        codename (str): Codename del permiso requerido. Ej: 'acceptance.api.upload_doc'

    Comportamiento:
        - 401 si el usuario no está autenticado.
        - 403 si no tiene el permiso (JSON para /api/, redirect para páginas).
    """
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)

            if not current_user.has_permission(codename):
                return _abort_403()

            return view(*args, **kwargs)
        return wrapped
    return decorator


def any_permission_required(*codenames):
    """
    Decorador que permite el acceso si el usuario tiene AL MENOS UNO
    de los permisos indicados.

    Args:
        *codenames: Uno o más codenames de permiso.

    Ejemplo:
        @any_permission_required('admin_review.api.decide', 'admin_review.api.list_submissions')
        def vista():
            ...
    """
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)

            if not any(current_user.has_permission(c) for c in codenames):
                return _abort_403()

            return view(*args, **kwargs)
        return wrapped
    return decorator


# ─── Alcance de programa (program scope) ─────────────────────────────────────
#
# Un permiso dice QUÉ puede hacer el usuario; el alcance dice SOBRE QUIÉN.
# Estas utilidades son la capa Flask de `app/services/program_scope_service.py`:
# leen `current_user` y devuelven el 403 estándar. La regla vive en el servicio.

_SCOPE_403_MESSAGE = 'No tienes acceso a la información de este programa.'


def _abort_scope_403():
    """403 de alcance: mismo formato que `_abort_403`, mensaje distinto."""
    if (request.path.startswith('/api/') or
            request.is_json or
            request.headers.get('X-Requested-With') == 'XMLHttpRequest'):
        return jsonify({
            "data": None,
            "flash": [{"level": "danger", "message": _SCOPE_403_MESSAGE}],
            "error": {"code": "FORBIDDEN", "message": _SCOPE_403_MESSAGE},
            "meta": {}
        }), 403
    abort(403)


def current_accessible_program_ids():
    """
    Programas sobre los que `current_user` puede operar, cacheado por request.

    Returns:
        set[int] | None — None significa acceso global (jefe de posgrado).
        set() vacío significa SIN alcance (p. ej. servicio social sin
        delegación); nunca lo trates como "todos".
    """
    if not getattr(current_user, 'is_authenticated', False):
        return set()

    cache_key = f'_scope_pids_{current_user.id}'
    if not hasattr(g, cache_key):
        setattr(g, cache_key, scope_service.accessible_program_ids(current_user))
    return getattr(g, cache_key)


def _extract_id(name, kwargs):
    """
    Busca un id en, por orden: kwargs de la vista, view_args, query string,
    form y cuerpo JSON. Si no aparece o no es entero devuelve None
    (el llamador falla cerrado).
    """
    sources = [kwargs, request.view_args or {}, request.args, request.form]
    payload = request.get_json(silent=True)
    if isinstance(payload, dict):
        sources.append(payload)

    for source in sources:
        if not source:
            continue
        raw = source.get(name)
        if raw is None or raw == '':
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None
    return None


def guard_program_scope(program_id):
    """
    Guarda imperativa para rutas cuyo programa objetivo no está en el URL.

        denied = guard_program_scope(program_id)
        if denied:
            return denied

    Returns:
        None si el programa está dentro del alcance; la respuesta 403 si no.
    """
    if not getattr(current_user, 'is_authenticated', False):
        abort(401)
    if not scope_service.program_in_scope(current_user, program_id):
        return _abort_scope_403()
    return None


def guard_user_scope(user_id, allow_self=True):
    """
    Guarda imperativa para rutas cuyo usuario objetivo no está en el URL
    (viene en el body, en la query o se resuelve a mitad del handler).

        denied = guard_user_scope(student_id)
        if denied:
            return denied

    Pasar esta guarda autoriza el expediente COMPLETO y la escritura. Para el
    nivel reducido entre programas (nombre / correo / progreso) usa
    `program_scope_service.may_view_cross_program_summary` +
    `to_cross_program_summary`.

    Returns:
        None si el usuario objetivo está dentro del alcance; 403 si no.
    """
    if not getattr(current_user, 'is_authenticated', False):
        abort(401)
    if not scope_service.user_in_scope(current_user, user_id, allow_self=allow_self):
        return _abort_scope_403()
    return None


def program_scope_required(program_id_kwarg=None, user_id_kwarg=None, allow_self=True):
    """
    Decorador que exige que el OBJETO destino esté dentro del alcance de
    programas de `current_user`. Se coloca DESPUÉS de `permission_required`:

        @api_x.route('/students/<int:student_id>/record')
        @login_required
        @permission_required('students.api.view_record')
        @program_scope_required(user_id_kwarg='student_id')
        def student_record(student_id):
            ...

        @api_x.route('/programs/<int:program_id>/applicants')
        @login_required
        @permission_required('acceptance.api.list_applicants')
        @program_scope_required(program_id_kwarg='program_id')
        def applicants(program_id):
            ...

    Args:
        program_id_kwarg (str | None): nombre del argumento que contiene el
            program_id destino.
        user_id_kwarg (str | None): nombre del argumento que contiene el
            user_id destino. Se resuelven sus UserProgram y se intersectan con
            el alcance del llamador.
        allow_self (bool): si True (por defecto) el usuario siempre pasa
            cuando el destino es él mismo.

    Si se indican ambos, deben cumplirse los dos.

    Comportamiento:
        - 401 si no está autenticado.
        - 403 (JSON en /api/, redirect en páginas) si el destino queda fuera
          del alcance, incluido el caso "no se pudo resolver el id"
          (falla cerrado).
        - El jefe de posgrado (alcance global) siempre pasa.
    """
    if not program_id_kwarg and not user_id_kwarg:
        raise ValueError(
            'program_scope_required necesita program_id_kwarg o user_id_kwarg'
        )

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)

            if program_id_kwarg:
                program_id = _extract_id(program_id_kwarg, kwargs)
                if program_id is None:
                    return _abort_scope_403()
                if not scope_service.program_in_scope(current_user, program_id):
                    return _abort_scope_403()

            if user_id_kwarg:
                target_id = _extract_id(user_id_kwarg, kwargs)
                if target_id is None:
                    return _abort_scope_403()
                if not scope_service.user_in_scope(
                    current_user, target_id, allow_self=allow_self
                ):
                    return _abort_scope_403()

            return view(*args, **kwargs)
        return wrapped
    return decorator
