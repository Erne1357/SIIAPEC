# app/routes/api/api_archives.py
from __future__ import annotations
import os
from datetime import datetime, timezone
from typing import Set

from flask import Blueprint, request, jsonify, current_app, send_file
from flask_login import login_required, current_user
from sqlalchemy import select, func, join

from werkzeug.utils import secure_filename

from app import db
from app.utils.permissions import permission_required
from app.models.archive import Archive
from app.models.step import Step
from app.models.phase import Phase
from app.models.program import Program
from app.models.program_step import ProgramStep
from app.models.submission import Submission
from app.services import program_scope_service as scope_service
from app.services.user_history_service import UserHistoryService

import shutil
api_archives = Blueprint("api_archives", __name__, url_prefix="/api/v1/archives")

# =========================
# Helpers
# =========================
def _instance_path() -> str:
    return current_app.instance_path

def _templates_dir_for(archive_id: int) -> str:
    return os.path.join(_instance_path(), "uploads", "templates", "archives", str(archive_id))

def _allowed_template_ext() -> Set[str]:
    """Extensiones aceptadas para una plantilla (mismas que los documentos)."""
    return set(current_app.config.get('ALLOWED_DOC_EXT', {'pdf', 'doc', 'docx'}))


def _store_template(archive: Archive, file_storage) -> tuple[str, str]:
    """Guarda un archivo de plantilla en el disco."""
    filename = file_storage.filename or ''
    if '.' not in filename:
        raise ValueError("El archivo no tiene extensión.")
    ext = filename.rsplit('.', 1)[1].lower()

    # Lista blanca obligatoria: sin esto se podía escribir cualquier extensión
    # dentro de instance/uploads/templates.
    allowed = _allowed_template_ext()
    if ext not in allowed:
        raise ValueError(
            "Extensión no permitida. Solo se aceptan: "
            + ", ".join(sorted(allowed))
            + "."
        )

    templates_dir = _templates_dir_for(archive.id)
    os.makedirs(templates_dir, exist_ok=True)
    base_name = secure_filename(archive.name)
    final_filename = f"{base_name}.{ext}"
    abs_path = os.path.join(templates_dir, final_filename)
    file_storage.save(abs_path)
    rel_path = os.path.relpath(abs_path, _instance_path())
    return rel_path, final_filename

def _abs_path_from_archive(a: Archive) -> tuple[str|None, str|None]:
    if not a.file_path:
        return None, None
    fpath = a.file_path
    abs_path = os.path.join(_instance_path(), fpath) if not os.path.isabs(fpath) else fpath
    return abs_path, os.path.basename(abs_path)

def _permitted_step_ids_for_user() -> Set[int]:
    """Step_ids que el usuario actual puede VER en el catálogo.

    - Acceso global (jefe de posgrado): todos los steps.
    - Scoped (program_admin o delegado): steps que tocan alguno de sus
      programas accesibles.

    OJO — esto NO es un permiso de escritura. Un Step puede estar enganchado a
    varios programas a la vez, así que "toca uno de mis programas" no reparte
    el catálogo entre coordinadores. Para escribir usa
    `_exclusive_step_ids_for_user` / `_archive_write_denied`.
    """
    accessible_pids = current_user.get_accessible_program_ids()
    if accessible_pids is None:
        ids = db.session.execute(select(Step.id)).scalars().all()
        return set(ids)
    if not accessible_pids:
        return set()
    step_ids = db.session.execute(
        select(ProgramStep.step_id).where(ProgramStep.program_id.in_(accessible_pids))
    ).scalars().all()
    return set(step_ids)


def _exclusive_step_ids_for_user() -> Set[int] | None:
    """Step_ids cuyo CATÁLOGO de documentos puede modificar el usuario actual.

    Returns:
        None si el usuario tiene alcance global (puede modificar cualquiera);
        si no, el conjunto de steps que le pertenecen en exclusiva.

    Un Step es COMPARTIDO por diseño: `program_step` engancha el mismo
    `step_id` a varios programas (en el catálogo de fábrica, 'Documentos
    Generales' y toda la fase de permanencia cuelgan de los cuatro posgrados).
    Los Archive cuelgan del Step, no del Program, así que filtrar por "el step
    de este archivo toca uno de mis programas" NO es una partición: el
    coordinador de MII pasa ese filtro sobre el step 1 y, al renombrar un
    archivo o sustituir su plantilla, reescribe el trámite de MANI, MIA y DCI.

    Modificar un archivo de un step compartido es, por tanto, un acto
    institucional, y sólo el alcance global lo autoriza. Un step queda dentro
    del alcance de escritura de un coordinador únicamente cuando TODOS sus
    `ProgramStep` apuntan a programas suyos (p. ej. 'Documentos Específicos
    DCI', que sólo usa el doctorado). Un step sin ningún ProgramStep no es de
    nadie: falla cerrado y queda para la Jefatura de Posgrado.
    """
    accessible_pids = current_user.get_accessible_program_ids()
    if accessible_pids is None:
        return None
    accessible_pids = set(accessible_pids)
    if not accessible_pids:
        return set()

    rows = db.session.execute(
        select(ProgramStep.step_id, ProgramStep.program_id)
    ).all()

    programs_by_step: dict[int, Set[int]] = {}
    for step_id, program_id in rows:
        programs_by_step.setdefault(step_id, set()).add(program_id)

    return {
        step_id for step_id, pids in programs_by_step.items()
        if pids and pids <= accessible_pids
    }

def _visible_step_ids_for_user() -> Set[int] | None:
    """Steps que el usuario puede CONSULTAR (descargar plantillas).

    Más amplio que `_permitted_step_ids_for_user`, que es para administrar:
    aquí entran también los steps de los programas en los que el usuario está
    inscrito, porque un aspirante debe poder bajar las plantillas de su propio
    proceso.

    Returns:
        None si el usuario tiene acceso global; si no, el conjunto de step_ids.
    """
    if current_user.get_accessible_program_ids() is None:
        return None

    pids = set(current_user.get_accessible_program_ids() or set())
    pids |= scope_service.program_ids_of_user(current_user.id)
    if not pids:
        return set()

    step_ids = db.session.execute(
        select(ProgramStep.step_id).where(ProgramStep.program_id.in_(pids))
    ).scalars().all()
    return set(step_ids)


def _deny(code: str, message: str, status: int):
    """Denegación con el envelope del proyecto y mensaje en español."""
    return jsonify({
        "ok": False,
        "data": None,
        "flash": [{"level": "danger", "message": message}],
        "error": {"code": code, "message": message},
        "meta": {}
    }), status


def _archive_write_denied(step_id):
    """
    Guarda de ESCRITURA sobre el catálogo: alta, edición, borrado y plantilla.

    Exige que el step sea exclusivo del alcance del usuario
    (`_exclusive_step_ids_for_user`). Devuelve la respuesta 403 o None.
    """
    exclusive = _exclusive_step_ids_for_user()
    if exclusive is None:          # alcance global
        return None
    try:
        step_id = int(step_id)
    except (TypeError, ValueError):
        step_id = None
    if step_id is not None and step_id in exclusive:
        return None
    return _deny(
        "FORBIDDEN",
        "Esta etapa la comparten varios programas o pertenece a otro, así que "
        "su catálogo de documentos sólo lo puede modificar la Jefatura de "
        "Posgrado.",
        403,
    )


def _delete_archive_files(archive_id: int):
    """Borra el directorio de plantillas y los archivos de entrega (submissions) de un archivo."""
    # 1. Borrar directorio de plantillas
    templates_dir = _templates_dir_for(archive_id)
    if os.path.isdir(templates_dir):
        try:
            # shutil.rmtree borra un directorio y todo su contenido
            shutil.rmtree(templates_dir) 
        except OSError as e:
            current_app.logger.error(f"Error borrando el directorio de plantillas {templates_dir}: {e}")

    # 2. Borrar archivos de entrega (submissions) asociados
    # (Asumiendo que tienes una función para obtener la ruta de los archivos de submission)
    submissions = db.session.execute(select(Submission).where(Submission.archive_id == archive_id)).scalars().all()
    for sub in submissions:
        if sub.file_path:
            # Reemplaza 'get_submission_path' con tu lógica real para obtener la ruta absoluta
            abs_submission_path = os.path.join(_instance_path(), sub.file_path)
            if os.path.isfile(abs_submission_path):
                try:
                    os.remove(abs_submission_path)
                except OSError as e:
                    current_app.logger.error(f"Error borrando el archivo de submission {abs_submission_path}: {e}")


# =========================
# Listado principal
# =========================
@api_archives.route("", methods=["GET"])
@login_required
def list_archives():
    """
    ?include=step → agrega nombre del step
    Estructura:
      id, name, description, is_uploadable, is_downloadable,
      allow_coordinator_upload, allow_extension_request,
      step_id, step_name, template_url, template_name, can_manage
    Filtrado por alcance de coordinador (solo archivos en steps permitidos).
    `can_manage` distingue lo que además puede EDITAR: los steps compartidos
    entre programas se listan pero sólo los modifica la Jefatura de Posgrado.
    """
    include_step = request.args.get("include") == "step"
    is_scoped = current_user.get_accessible_program_ids() is not None
    exclusive = _exclusive_step_ids_for_user()

    if include_step:
        j = join(Archive, Step, Archive.step_id == Step.id)
        sel = select(
            Archive.id, Archive.name, Archive.description,
            Archive.is_uploadable, Archive.is_downloadable,
            Archive.step_id, Step.name.label("step_name"),
            Archive.file_path,
            getattr(Archive, "allow_coordinator_upload"),
            getattr(Archive, "allow_extension_request", None),
        ).select_from(j)
        # alcance: usuarios scoped (no globales) sólo ven steps de sus programas accesibles
        if is_scoped:
            permitted = _permitted_step_ids_for_user()
            if not permitted:
                return jsonify({"ok": True, "items": []}), 200
            sel = sel.where(Archive.step_id.in_(permitted))
        rows = db.session.execute(sel).all()
        items = []
        for r in rows:
            (aid, name, desc, up, down, step_id, step_name, fpath, allow_coord, allow_ext) = r
            items.append({
                "id": aid,
                "name": name,
                "description": desc,
                "is_uploadable": bool(up),
                "is_downloadable": bool(down),
                "allow_coordinator_upload": bool(allow_coord),
                "allow_extension_request": bool(allow_ext) if allow_ext is not None else False,
                "step_id": step_id,
                "step_name": step_name,
                "can_manage": exclusive is None or step_id in exclusive,
                "template_url": f"/api/v1/archives/{aid}/template" if fpath else None,
                "template_name": os.path.basename(fpath) if fpath else None
            })
        return jsonify({"ok": True, "items": items}), 200

    # sin join
    sel = select(Archive)
    if is_scoped:
        permitted = _permitted_step_ids_for_user()
        if not permitted:
            return jsonify({"ok": True, "items": []}), 200
        sel = sel.where(Archive.step_id.in_(permitted))
    archives = db.session.execute(sel).scalars().all()
    items = []
    for a in archives:
        allow_ext = getattr(a, "allow_extension_request", False)
        items.append({
            "id": a.id,
            "name": a.name,
            "description": a.description,
            "is_uploadable": a.is_uploadable,
            "is_downloadable": a.is_downloadable,
            "allow_coordinator_upload": a.allow_coordinator_upload,
            "allow_extension_request": bool(allow_ext),
            "step_id": a.step_id,
            "can_manage": exclusive is None or a.step_id in exclusive,
            "template_url": f"/api/v1/archives/{a.id}/template" if a.file_path else None,
            "template_name": os.path.basename(a.file_path) if a.file_path else None
        })
    return jsonify({"ok": True, "items": items}), 200

# =========================
# Steps disponibles (para selects)
# =========================
@api_archives.route("/steps", methods=["GET"])
@login_required
def list_steps():
    """
    Lista de steps. Por defecto devuelve SOLO los permitidos al usuario (scope=permitted).
    Admins pueden pedir scope=all.
    Devuelve: id, name, phase_id, phase_name, can_manage

    `can_manage=False` marca los steps compartidos por varios programas: se
    listan (el coordinador ve el trámite) pero su catálogo no se puede editar
    desde ahí. El front debe deshabilitarlos en los selects de alta/edición.
    """
    scope = request.args.get("scope", "permitted")
    is_scoped = current_user.get_accessible_program_ids() is not None
    exclusive = _exclusive_step_ids_for_user()

    j = join(Step, Phase, Step.phase_id == Phase.id)
    sel = select(
        Step.id, Step.name, Step.phase_id, Phase.name.label("phase_name")
    ).select_from(j)

    # Usuarios scoped siempre quedan filtrados; los globales sólo si scope=permitted
    if scope != "all" or is_scoped:
        permitted = _permitted_step_ids_for_user()
        if not permitted:
            return jsonify({"ok": True, "items": []}), 200
        sel = sel.where(Step.id.in_(permitted))

    rows = db.session.execute(sel.order_by(Phase.id, Step.id)).all()
    items = [{
        "id": i,
        "name": n,
        "phase_id": pid,
        "phase_name": pn,
        "can_manage": exclusive is None or i in exclusive,
    } for (i, n, pid, pn) in rows]
    return jsonify({"ok": True, "items": items}), 200

# =========================
# Crear archivo
# =========================
@api_archives.route("", methods=["POST"])
@login_required
@permission_required('archives.api.create')
def create_archive():
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    step_id = data.get("step_id")
    if not name or not step_id:
        return jsonify({"ok": False, "error": "name y step_id son requeridos"}), 400

    try:
        step_id = int(step_id)
    except (TypeError, ValueError):
        return _deny("VALIDATION_ERROR", "step_id inválido.", 400)

    # Alcance de escritura: sólo steps exclusivos de sus programas. Un step
    # compartido pertenece a toda la institución (ver `_archive_write_denied`).
    denied = _archive_write_denied(step_id)
    if denied:
        return denied

    a = Archive(
        name=name,
        description=data.get("description"),
        file_path=None,
        step_id=step_id,
        is_downloadable=bool(data.get("is_downloadable", False)),
        is_uploadable=bool(data.get("is_uploadable", False)),
    )
    if hasattr(Archive, "allow_coordinator_upload"):
        a.allow_coordinator_upload = bool(data.get("allow_coordinator_upload", False))
    if hasattr(Archive, "allow_extension_request"):
        setattr(a, "allow_extension_request", bool(data.get("allow_extension_request", False)))

    db.session.add(a)
    db.session.commit()
    
    # Registrar en el historial
    try:
        step = db.session.get(Step, step_id)
        step_name = step.name if step else f"Step ID {step_id}"
        
        UserHistoryService.log_archive_created(
            current_user.id,
            name,
            step_name
        )
        db.session.commit()
    except Exception as e:
        current_app.logger.error(f"Error al registrar creación de archivo en historial: {e}")
    
    return jsonify({"ok": True, "id": a.id}), 201

# =========================
# Actualizar (toggles + meta + mover de step)
# =========================
@api_archives.route("/<int:archive_id>", methods=["PUT", "PATCH"])
@login_required
@permission_required('archives.api.update')
def update_archive(archive_id: int):
    data = request.get_json() or {}
    a = db.session.get(Archive, archive_id)
    if not a:
        return jsonify({"ok": False, "error": "Archivo no encontrado"}), 404

    # Alcance de escritura sobre el step actual Y sobre el destino si cambia.
    denied = _archive_write_denied(a.step_id)
    if denied:
        return denied
    if "step_id" in data and data["step_id"]:
        try:
            target_step_id = int(data["step_id"])
        except (TypeError, ValueError):
            return _deny("VALIDATION_ERROR", "step_id inválido.", 400)
        denied = _archive_write_denied(target_step_id)
        if denied:
            return denied

    try:
        # Capturar cambios para el historial
        changes = {}
        original_name = a.name
        
        # meta
        if "name" in data and data["name"].strip():
            new_name = data["name"].strip()
            if new_name != a.name:
                changes['name'] = {'old': a.name, 'new': new_name}
            a.name = new_name
        if "description" in data:
            if data["description"] != a.description:
                changes['description'] = {'old': a.description, 'new': data["description"]}
            a.description = data["description"]
        if "step_id" in data and data["step_id"]:
            new_step_id = int(data["step_id"])
            if new_step_id != a.step_id:
                old_step = db.session.get(Step, a.step_id)
                new_step = db.session.get(Step, new_step_id)
                changes['step'] = {
                    'old': old_step.name if old_step else f"Step {a.step_id}",
                    'new': new_step.name if new_step else f"Step {new_step_id}"
                }
            a.step_id = new_step_id

        # toggles
        if "is_uploadable" in data:
            new_value = bool(data["is_uploadable"])
            if new_value != a.is_uploadable:
                changes['is_uploadable'] = {'old': a.is_uploadable, 'new': new_value}
            a.is_uploadable = new_value
        if "is_downloadable" in data:
            new_value = bool(data["is_downloadable"])
            if new_value != a.is_downloadable:
                changes['is_downloadable'] = {'old': a.is_downloadable, 'new': new_value}
            a.is_downloadable = new_value
        if "allow_coordinator_upload" in data and hasattr(Archive, "allow_coordinator_upload"):
            new_value = bool(data["allow_coordinator_upload"])
            old_value = getattr(a, 'allow_coordinator_upload', False)
            if new_value != old_value:
                changes['allow_coordinator_upload'] = {'old': old_value, 'new': new_value}
            a.allow_coordinator_upload = new_value
        if "allow_extension_request" in data and hasattr(Archive, "allow_extension_request"):
            new_value = bool(data["allow_extension_request"])
            old_value = getattr(a, 'allow_extension_request', False)
            if new_value != old_value:
                changes['allow_extension_request'] = {'old': old_value, 'new': new_value}
            setattr(a, "allow_extension_request", new_value)

        db.session.commit()
        
        # Registrar en el historial solo si hubo cambios
        if changes:
            try:
                step = db.session.get(Step, a.step_id)
                step_name = step.name if step else f"Step ID {a.step_id}"
                
                UserHistoryService.log_archive_updated(
                    current_user.id,
                    a.name,
                    step_name,
                    changes
                )
                db.session.commit()
            except Exception as e:
                current_app.logger.error(f"Error al registrar actualización de archivo en historial: {e}")
        
        return jsonify({"ok": True, "id": a.id}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 400

# =========================
# Borrar archivo (con seguridad)
# =========================
@api_archives.route("/<int:archive_id>", methods=["DELETE"])
@login_required
@permission_required('archives.api.delete')
def delete_archive(archive_id: int):
    force = request.args.get("force") in ("1", "true", "True", "yes")
    a = db.session.get(Archive, archive_id)
    if not a:
        return jsonify({"ok": False, "error": "Archivo no encontrado"}), 404

    # Alcance de escritura por step. El borrado arrastra las entregas de todos
    # los programas que usan esa etapa, así que exige step exclusivo.
    denied = _archive_write_denied(a.step_id)
    if denied:
        return denied

    cnt = db.session.execute(
        select(func.count(Submission.id)).where(Submission.archive_id == archive_id)
    ).scalar_one()

    if cnt and not force:
        return jsonify({"ok": False, "requires_force": True, "message": f"Hay {cnt} submissions relacionados. Usa ?force=true para eliminar."}), 409

    try:
        # Guardar información para el historial antes del borrado
        archive_name = a.name
        archive_description = a.description
        
        # --- INICIO DE LA LÓGICA DE BORRADO DE ARCHIVOS ---
        
        # Llama a la función de borrado ANTES de hacer commit a la DB.
        # Si esto falla, la transacción se revierte y no se pierde el registro en la DB.
        _delete_archive_files(archive_id)
        
        # --- FIN DE LA LÓGICA DE BORRADO DE ARCHIVOS ---
        
        # El borrado en cascada de la DB se encargará de los registros de submission
        db.session.delete(a)
        db.session.commit()
        
        # Registrar en el historial después del commit exitoso
        UserHistoryService.log_archive_deleted(
            current_user.id,
            archive_name,
            archive_description,
            force
        )
        
        return jsonify({"ok": True, "deleted": archive_id}), 200
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error en delete_archive para el id {archive_id}: {e}")
        return jsonify({"ok": False, "error": str(e)}), 400

# =========================
# Plantillas
# =========================
@api_archives.route("/<int:archive_id>/template", methods=["POST"])
@login_required
@permission_required('archives.api.manage_template')
def upload_template(archive_id: int):
    a = db.session.get(Archive, archive_id)
    if not a:
        return jsonify({"ok": False, "error": "Archivo no encontrado"}), 404

    # Alcance de escritura por step: sin esto un coordinador sobrescribía la
    # plantilla oficial que descargan los aspirantes de otro programa. Como el
    # step es compartido, "toca mi programa" no basta: tiene que ser mío entero.
    denied = _archive_write_denied(a.step_id)
    if denied:
        return denied

    if "file" not in request.files:
        return jsonify({"ok": False, "error": "Archivo no provisto"}), 400
    fs = request.files["file"]
    if not fs or not fs.filename:
        return jsonify({"ok": False, "error": "Nombre de archivo inválido"}), 400
    try:
        # Verificar si tenía plantilla previa
        had_previous_template = bool(a.file_path)
        
        rel_path, fname = _store_template(a, fs)
        a.file_path = rel_path
        db.session.commit()
        
        # Registrar en el historial después del commit exitoso
        UserHistoryService.log_template_uploaded(
            current_user.id,
            a.name,
            fname,
            had_previous_template
        )
        
        return jsonify({"ok": True, "id": a.id, "template_url": f"/api/v1/archives/{a.id}/template", "template_name": fname}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 400

@api_archives.route("/<int:archive_id>/template", methods=["GET"])
@login_required
def download_template(archive_id: int):
    a = db.session.get(Archive, archive_id)
    if not a or not a.file_path:
        return jsonify({"ok": False, "error": "Plantilla no disponible"}), 404

    # `is_downloadable` marca las plantillas publicadas: son formatos en
    # blanco que la página del programa ofrece a cualquier interesado, así que
    # siguen abiertas a cualquier usuario autenticado.
    #
    # Las que NO son descargables son material interno: sólo las ve quien
    # administra ese step o quien cursa un programa que lo incluye.
    # 404 deliberado: quien no ve el step tampoco debe confirmar que existe.
    if not a.is_downloadable:
        visible = _visible_step_ids_for_user()
        if visible is not None and a.step_id not in visible:
            return jsonify({"ok": False, "error": "Plantilla no disponible"}), 404

    abs_path, fname = _abs_path_from_archive(a)
    if not abs_path or not os.path.exists(abs_path):
        return jsonify({"ok": False, "error": "Archivo no encontrado en el servidor"}), 404
    return send_file(abs_path, as_attachment=True, download_name=fname or f"plantilla_{archive_id}")
