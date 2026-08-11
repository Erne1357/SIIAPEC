# app/routes/api/files_api.py
from pathlib import Path
from flask import Blueprint, current_app, send_file, abort
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from app.services import file_access_service
from app.services import program_scope_service as scope_service
from app.utils.files import abs_path_from_db

api_files = Blueprint('api_files', __name__, url_prefix='/files')

INLINE_EXT = {'pdf'}  # muestra inline; el resto descarga

def _send_safe(base: Path, rel_path: str, inline: bool):
    """
    base: carpeta base permitida (Path)
    rel_path: ruta relativa guardada en BD/URL (p.ej. '42/admission/mi_archivo.pdf')
    inline: True -> inline, False -> attachment
    """
    try:
        abs_path = abs_path_from_db(rel_path, base)  # safe_join
    except Exception:
        abort(400)  # traversal o ruta inválida

    p = Path(abs_path)
    if not p.is_file():
        abort(404)

    # send_file con ETag/If-Modified-Since
    return send_file(
        str(p),
        as_attachment=not inline,
        conditional=True
    )

@api_files.route('/avatar/<int:user_id>/<path:filename>', methods=['GET'])
@login_required
def avatar(user_id: int, filename: str):
    # La foto de perfil es una fotografía del rostro: NO forma parte del nivel
    # reducido entre programas (nombre / correo / progreso). Sólo la ve el
    # propio usuario, quien comparte programa con él (jefe de posgrado = todos)
    # o quien lo ve como ponente de un evento visible.
    #
    # 404 —no 403— a propósito: el nombre del archivo es constante, así que un
    # 403 confirmaría "este usuario sí tiene foto" y permitiría enumerar
    # cuentas recorriendo /files/avatar/1..N.
    if not file_access_service.may_view_avatar(current_user, user_id):
        abort(404)

    filename = secure_filename(filename)
    rel = f"{user_id}/{filename}"
    base: Path = current_app.config['AVATAR_FOLDER']
    # imágenes -> inline
    return _send_safe(base, rel, inline=True)

@api_files.route('/doc/<int:user_id>/<phase>/<path:filename>', methods=['GET'])
@login_required
def user_doc(user_id: int, phase: str, filename: str):
    # 1) Puerta gruesa por permiso, ANTES de tocar la BD: quien ni siquiera
    #    puede ver documentos ajenos recibe 403 sin que la respuesta revele si
    #    el archivo existe.
    if user_id != current_user.id and not current_user.has_permission(
            file_access_service.VIEW_DOC_OTHERS_PERMISSION):
        abort(403)

    # Fases válidas
    valid_phases = {'admission', 'permanence', 'conclusion', 'acceptance'}
    if phase not in valid_phases:
        abort(400)

    filename = secure_filename(filename)
    rel = f"{user_id}/{phase}/{filename}"

    # 2) Autorización real: el DUEÑO se resuelve desde la fila de BD que
    #    referencia esta ruta (Submission / AcceptanceDocument /
    #    SemesterEnrollment), no desde los segmentos del URL. Así el día que
    #    estas rutas sean UUID opacos no hay que reescribir el control de
    #    acceso. Sin fila que la referencie, el archivo no existe para la
    #    aplicación (bytes huérfanos de una re-subida) → 404.
    owner_id = file_access_service.user_doc_owner_id(rel)
    if owner_id is None:
        abort(404)

    # 404 —no 403— cuando el dueño queda fuera del alcance: un documento
    # personal está prohibido entre programas y un 403 delataría qué
    # documentos subió un estudiante de otro programa.
    if not file_access_service.may_view_user_doc(current_user, owner_id):
        abort(404)

    base: Path = current_app.config['USER_DOCS_FOLDER']

    # inline sólo para ciertas extensiones (PDF por ahora)
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    return _send_safe(base, rel, inline=(ext in INLINE_EXT))

@api_files.route('/template/<path:filename>', methods=['GET'])
@login_required
def template(filename: str):
    filename = secure_filename(filename)
    # Aquí tus plantillas viven "flat" (sin user_id/phase)
    base: Path = current_app.config['TEMPLATE_STORE']
    return _send_safe(base, filename, inline=False)  # siempre como descarga


@api_files.route('/event/<int:event_id>/<kind>/<path:filename>', methods=['GET'])
@login_required
def event_image(event_id: int, kind: str, filename: str):
    """
    Sirve imágenes de eventos. ACL: mismas reglas que la visibilidad del evento público
    o admin del programa.
    kind: 'cover' (archivo directo en <event_id>/) | 'gallery' | 'hosts'
    Para cover, filename ej: cover.webp → rel = <event_id>/cover.webp
    Para gallery/hosts, rel = <event_id>/<kind>/<filename>
    """
    from app.models.event import Event
    from app import db as _db

    if kind not in ('cover', 'gallery', 'hosts'):
        abort(400)

    event = _db.session.get(Event, event_id)
    if not event:
        abort(404)

    # ACL — el alcance se pregunta al predicado compartido, nunca se
    # reimplementa la intersección a mano.
    is_admin = (
        scope_service.is_global_scope(current_user)
        or scope_service.program_in_scope(current_user, event.program_id)
    )
    is_public_accessible = (
        event.visible_to_students
        and event.status == 'published'
        and event.capacity_type != 'single'
    )
    if not (is_admin or is_public_accessible):
        abort(403)

    filename = secure_filename(filename)
    if kind == 'cover':
        rel = f"{event_id}/{filename}"
    else:
        rel = f"{event_id}/{kind}/{filename}"

    base: Path = current_app.config['EVENTS_FOLDER']
    return _send_safe(base, rel, inline=True)
