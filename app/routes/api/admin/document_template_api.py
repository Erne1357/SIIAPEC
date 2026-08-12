# app/routes/api/admin/document_template_api.py
"""
API para gestionar plantillas de documentos y generar documentos rellenos.

Endpoints:
  GET    /api/admin/document-templates            — listar plantillas
  POST   /api/admin/document-templates            — subir plantilla (multipart)
  GET    /api/admin/document-templates/<id>       — detalle
  PATCH  /api/admin/document-templates/<id>       — activar/desactivar / cambiar nombre
  DELETE /api/admin/document-templates/<id>       — eliminar
  POST   /api/admin/document-templates/generate   — generar documento para un estudiante
  GET    /api/admin/document-templates/variables  — lista de variables disponibles
"""
import os

from flask import Blueprint, jsonify, request, send_file, current_app
from flask_login import login_required, current_user
from app.utils.permissions import permission_required
from app.services import program_scope_service as scope_service
from app.services import template_access_service
from app import db
from app.models.document_template import (
    DocumentTemplate, DOCUMENT_TYPES, TEMPLATE_FILE_TYPES
)

api_document_templates = Blueprint(
    'api_document_templates',
    __name__,
    url_prefix='/api/admin/document-templates',
)

ALLOWED_EXTENSIONS = {'html', 'docx'}


def _ok(data=None, **kwargs):
    payload = {'ok': True}
    if data is not None:
        payload['data'] = data
    payload.update(kwargs)
    return jsonify(payload)


def _err(msg, code=400):
    return jsonify({'ok': False, 'error': msg}), code


def _allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _load_template_in_scope(template_id):
    """
    Carga una plantilla que esté dentro del ALCANCE del llamador.

    La capacidad (`admin_templates.api.list` / `.manage` / `.delete`) la exige
    el decorador; aquí sólo se responde "¿es tuya?" con la misma regla que
    guarda los bytes (`template_access_service.document_template_in_scope`).

    404 y no 403, igual que en el resto de la auditoría: una plantilla que no
    aparece en tu catálogo tampoco debe poder confirmarse recorriendo ids.

    Returns:
        (template, None) si procede; (None, respuesta_error) si no.
    """
    t = DocumentTemplate.query.get(template_id)
    if t is None or not template_access_service.document_template_in_scope(
            current_user, t.program_id):
        return None, _err("Plantilla no encontrada.", 404)
    return t, None


def _templates_sys_dir():
    return current_app.config.get(
        'TEMPLATES_SYS_FOLDER',
        os.path.join(current_app.instance_path, 'templates_sys'),
    )


# ─────────────────────────────────────────────────────────────────────────────
# LISTAR
# ─────────────────────────────────────────────────────────────────────────────

@api_document_templates.get('')
@login_required
@permission_required('admin_templates.api.list')
def list_templates():
    """
    Lista las plantillas que el llamador puede consultar.

    Filtros opcionales: document_type, program_id, active_only.

    ALCANCE: el permiso `admin_templates.api.list` lo tiene todo coordinador, y
    esta ruta devolvía el catálogo COMPLETO de la institución —nombre,
    descripción, programa y `file_path`— mientras `/files/template/<archivo>`
    ya negaba los bytes de esas mismas filas. Un índice que nombra archivos que
    no puedes abrir es la misma fuga a un salto de distancia, así que el
    filtrado lo hace `template_access_service.visible_document_templates`, que
    es literalmente el predicado que guarda los bytes: lo que se ve listado es
    exactamente lo que se puede descargar.
    """
    q = DocumentTemplate.query

    doc_type = request.args.get('document_type')
    if doc_type:
        q = q.filter_by(document_type=doc_type)

    program_id = request.args.get('program_id', type=int)
    if program_id is not None:
        q = q.filter_by(program_id=program_id)

    active_only = request.args.get('active_only', 'false').lower() == 'true'
    if active_only:
        q = q.filter_by(is_active=True)

    templates = template_access_service.visible_document_templates(
        current_user,
        q.order_by(DocumentTemplate.document_type, DocumentTemplate.name).all(),
    )
    return _ok([t.to_dict() for t in templates], total=len(templates))


# ─────────────────────────────────────────────────────────────────────────────
# SUBIR PLANTILLA
# ─────────────────────────────────────────────────────────────────────────────

@api_document_templates.post('')
@login_required
@permission_required('admin_templates.api.create')
def upload_template():
    """
    Sube un archivo de plantilla (HTML o DOCX) y registra la plantilla en BD.

    Form fields:
      file          (required) — archivo .html o .docx
      name          (required) — nombre descriptivo
      document_type (required) — acceptance_letter | enrollment_confirmation | course_schedule
      program_id    (optional) — si omitido, la plantilla es global
      description   (optional)

    ALCANCE: `program_id` lo elige quien sube el archivo, así que sin cerco se
    crea una plantilla para cualquier programa —o, omitiéndolo, la GLOBAL, que
    `DocumentTemplate.get_for_program` sirve como respaldo a todos—. Se exige
    el mismo alcance que para leerla: el programa dentro del alcance, y alcance
    global para una plantilla sin programa. Los roles semilla dan `create` sólo
    a la Jefatura, pero estos permisos se delegan (`permissions.api.delegate`),
    y un permiso delegado no amplía el alcance de quien lo recibe.
    """
    if 'file' not in request.files:
        return _err("Se requiere un archivo en el campo 'file'.")

    file = request.files['file']
    if not file.filename:
        return _err("El archivo no tiene nombre.")

    if not _allowed_file(file.filename):
        return _err(f"Tipo de archivo no permitido. Use: {', '.join(ALLOWED_EXTENSIONS)}")

    name = (request.form.get('name') or '').strip()
    doc_type = (request.form.get('document_type') or '').strip()
    program_id = request.form.get('program_id', type=int)
    description = (request.form.get('description') or '').strip() or None

    if not name:
        return _err("El campo 'name' es requerido.")
    if doc_type not in DOCUMENT_TYPES:
        return _err(f"document_type inválido. Opciones: {list(DOCUMENT_TYPES.keys())}")

    if not template_access_service.document_template_in_scope(current_user, program_id):
        return _err(
            "No tienes acceso a este programa."
            if program_id is not None
            else "Sólo la Jefatura de Posgrado puede crear plantillas globales.",
            403,
        )

    ext = file.filename.rsplit('.', 1)[1].lower()

    # Guardar en instance/templates_sys/<document_type>/
    base_dir = _templates_sys_dir()
    subdir = os.path.join(base_dir, doc_type)
    os.makedirs(subdir, exist_ok=True)

    from werkzeug.utils import secure_filename
    from app.utils.datetime_utils import now_local
    timestamp = now_local().strftime('%Y%m%d_%H%M%S')
    safe_name = secure_filename(f"{timestamp}_{file.filename}")
    abs_path = os.path.join(subdir, safe_name)
    file.save(abs_path)

    # Ruta relativa guardada en BD
    rel_path = os.path.join(doc_type, safe_name)

    template = DocumentTemplate(
        program_id=program_id,
        document_type=doc_type,
        name=name,
        file_path=rel_path,
        file_type=ext,
        description=description,
        is_active=True,
        created_by=current_user.id,
    )
    db.session.add(template)
    db.session.commit()

    return _ok(template.to_dict()), 201


# ─────────────────────────────────────────────────────────────────────────────
# DETALLE
# ─────────────────────────────────────────────────────────────────────────────

@api_document_templates.get('/<int:template_id>')
@login_required
@permission_required('admin_templates.api.list')
def get_template(template_id):
    """
    Detalle de una plantilla.

    Mismo alcance que el listado —y que la descarga— vía
    `may_download_document_template`: si no sale en tu catálogo, tampoco se
    lee por id.
    """
    t = DocumentTemplate.query.get(template_id)
    if t is None or not template_access_service.may_download_document_template(
            current_user, t):
        return _err("Plantilla no encontrada.", 404)
    return _ok(t.to_dict())


# ─────────────────────────────────────────────────────────────────────────────
# ACTUALIZAR (nombre, descripción, activar/desactivar)
# ─────────────────────────────────────────────────────────────────────────────

@api_document_templates.patch('/<int:template_id>')
@login_required
@permission_required('admin_templates.api.manage')
def update_template(template_id):
    """
    Renombra, redescribe o (des)activa una plantilla dentro del alcance.

    `program_id` no es editable a propósito: mover una plantilla de programa
    sería una escritura fuera del alcance disfrazada de edición.
    """
    t, err = _load_template_in_scope(template_id)
    if err:
        return err

    body = request.get_json(silent=True) or {}

    if 'name' in body:
        t.name = body['name'].strip()
    if 'description' in body:
        t.description = body['description'] or None
    if 'is_active' in body:
        t.is_active = bool(body['is_active'])

    db.session.commit()
    return _ok(t.to_dict())


# ─────────────────────────────────────────────────────────────────────────────
# ELIMINAR
# ─────────────────────────────────────────────────────────────────────────────

@api_document_templates.delete('/<int:template_id>')
@login_required
@permission_required('admin_templates.api.delete')
def delete_template(template_id):
    """Elimina la fila y su archivo. Mismo alcance que leerla y editarla."""
    t, err = _load_template_in_scope(template_id)
    if err:
        return err

    # Eliminar archivo físico
    base_dir = _templates_sys_dir()
    abs_path = os.path.join(base_dir, t.file_path)
    if os.path.exists(abs_path):
        try:
            os.remove(abs_path)
        except OSError:
            pass  # archivo ya no existe, continuar

    db.session.delete(t)
    db.session.commit()
    return _ok({'deleted_id': template_id})


# ─────────────────────────────────────────────────────────────────────────────
# VARIABLES DISPONIBLES
# ─────────────────────────────────────────────────────────────────────────────

@api_document_templates.get('/variables')
@login_required
@permission_required('admin_templates.api.list')
def list_variables():
    """Retorna la lista de variables disponibles para usar en plantillas."""
    variables = [
        {'key': '{{student_name}}',     'description': 'Nombre completo del estudiante'},
        {'key': '{{student_curp}}',     'description': 'CURP del estudiante'},
        {'key': '{{student_email}}',    'description': 'Correo electrónico del estudiante'},
        {'key': '{{control_number}}',   'description': 'Número de control asignado'},
        {'key': '{{program_name}}',     'description': 'Nombre del programa de posgrado'},
        {'key': '{{program_level}}',    'description': 'Nivel del programa (Maestría, Doctorado…)'},
        {'key': '{{period_code}}',      'description': 'Código del período académico (ej. 20261)'},
        {'key': '{{period_name}}',      'description': 'Nombre del período (ej. Enero-Junio 2026)'},
        {'key': '{{acceptance_date}}',  'description': 'Fecha de aceptación en formato largo'},
        {'key': '{{coordinator_name}}', 'description': 'Nombre del coordinador del programa'},
        {'key': '{{current_date}}',     'description': 'Fecha actual en formato dd/mm/yyyy'},
    ]
    return _ok(variables)


# ─────────────────────────────────────────────────────────────────────────────
# GENERAR DOCUMENTO
# ─────────────────────────────────────────────────────────────────────────────

@api_document_templates.post('/generate')
@login_required
@permission_required('admin_templates.api.generate')
def generate_document():
    """
    Genera un documento relleno para un estudiante y lo retorna como descarga.

    Body JSON:
      user_id       (int, required)
      program_id    (int, required)
      document_type (str, required)
      period_id     (int, optional)

    Alcance: la plantilla puede incrustar `{{student_curp}}` y
    `{{control_number}}`, así que esto NO es una lectura de catálogo. Exige el
    permiso propio `admin_templates.api.generate` y que TANTO el programa como
    el estudiante estén dentro del alcance del llamador. No hay nivel reducido:
    un documento relleno es dato personal completo o no es nada.
    """
    body = request.get_json(silent=True) or {}
    user_id = body.get('user_id')
    program_id = body.get('program_id')
    doc_type = body.get('document_type')
    period_id = body.get('period_id')

    if not user_id or not program_id or not doc_type:
        return _err("Se requieren: user_id, program_id, document_type.")

    if doc_type not in DOCUMENT_TYPES:
        return _err(f"document_type inválido. Opciones: {list(DOCUMENT_TYPES.keys())}")

    if not scope_service.program_in_scope(current_user, program_id):
        return _err("No tienes acceso a este programa.", 403)

    if not scope_service.user_in_scope(current_user, user_id, allow_self=False):
        return _err("No tienes acceso al expediente de este estudiante.", 403)

    try:
        from app.services.document_generation_service import DocumentGenerationService
        result = DocumentGenerationService.generate(
            user_id=user_id,
            program_id=program_id,
            document_type=doc_type,
            period_id=period_id,
        )
    except ValueError as e:
        return _err(str(e), 404)
    except RuntimeError as e:
        return _err(str(e), 500)
    except Exception as e:
        current_app.logger.error(f"[generate_document] Error: {e}", exc_info=True)
        return _err(f"Error interno al generar el documento: {e}", 500)

    mime = 'application/pdf' if result['file_type'] == 'pdf' else \
           'application/vnd.openxmlformats-officedocument.wordprocessingml.document'

    return send_file(
        result['output_path'],
        mimetype=mime,
        as_attachment=True,
        download_name=result['filename'],
    )
