from pathlib import Path
import shutil
import uuid
from werkzeug.utils import secure_filename, safe_join
from flask import current_app, abort
from typing import Literal

def _uuid_name(ext: str) -> str:
    return f"{uuid.uuid4().hex}.{ext.lower()}"

# ---------- rutas absolutas -------------------------------------------------
def avatar_dir(user_id: int) -> Path:
    if current_app.config['AVATAR_FOLDER'] / str(user_id) is None:
       #Si no existe la carpeta, la creamos
       current_app.config['AVATAR_FOLDER'] / str(user_id).mkdir(parents=True, exist_ok=True)
    return current_app.config['AVATAR_FOLDER'] / str(user_id)

def docs_dir(user_id: int, phase: Literal['admission', 'permanence', 'conclusion']) -> Path:
    return (current_app.config['USER_DOCS_FOLDER']
            / str(user_id) / phase)

# ---------- operaciones comunes --------------------------------------------
def save_avatar(file_storage, user_id: int) -> str:
    ext = _validate_ext(file_storage.filename, current_app.config['ALLOWED_IMAGE_EXT'])
    folder = avatar_dir(user_id)
    folder.mkdir(parents=True, exist_ok=True)

    filename = _uuid_name(ext)
    file_storage.save(folder / filename)
    return f"{user_id}/{filename}"          

def save_user_doc(file_storage, user_id: int, phase: str, name: str) -> str:
    ext = _validate_ext(file_storage.filename, current_app.config['ALLOWED_DOC_EXT'])
    folder = docs_dir(user_id, phase)
    folder.mkdir(parents=True, exist_ok=True)

    safe = secure_filename(name.rsplit('.', 1)[0])
    filename = f"{safe}.{ext}"
    file_storage.save(folder / filename)
    return f"{user_id}/{phase}/{filename}"

def save_system_template(file_storage, name: str) -> str:
    ext = _validate_ext(file_storage.filename, current_app.config['ALLOWED_DOC_EXT'])
    folder = current_app.config['TEMPLATE_STORE']
    folder.mkdir(parents=True, exist_ok=True)

    safe = secure_filename(name.rsplit('.', 1)[0])
    filename = f"{safe}.{ext}"
    file_storage.save(folder / filename)
    return filename

def _validate_ext(name: str, allowed: set[str]) -> str:
    if '.' not in name:
        abort(400, "Archivo sin extensión")
    ext = name.rsplit('.', 1)[1].lower()
    if ext not in allowed:
        abort(400, "Extensión no permitida")
    return ext

def abs_path_from_db(relative_path: str, base_folder: Path):
    """
    Convierte '42/avatar.webp' ➜ '…/uploads/avatars/42/avatar.webp'.

    DEVUELVE None si la ruta se sale de `base_folder`. `werkzeug.safe_join` no
    lanza en ese caso: retorna None. Todo llamador tiene que comprobarlo —
    `Path(None)` es un TypeError, es decir un 500 donde correspondía un 404.
    El único llamador (`files_api._send_safe`) lo comprueba explícitamente.

    Las rutas que entran aquí vienen SIEMPRE de la base de datos (columna
    `file_path` y compañía), nunca del URL: desde el corte a identificadores
    opacos el cliente no aporta ningún segmento de ruta. Esto es defensa en
    profundidad sobre un valor almacenado, no validación de entrada.
    """
    return safe_join(base_folder, relative_path)  # evita ../ traversal


# ---------- eventos ---------------------------------------------------------
EventImageKind = Literal['cover', 'gallery', 'host']


def events_dir(event_id: int, kind: EventImageKind | None = None) -> Path:
    """Directorio para imágenes de un evento. Si `kind`, retorna subcarpeta."""
    base = current_app.config['EVENTS_FOLDER'] / str(event_id)
    if kind == 'gallery':
        return base / 'gallery'
    if kind == 'host':
        return base / 'hosts'
    return base  # cover vive directamente en el base


def save_event_image(file_storage, event_id: int, kind: EventImageKind) -> str:
    """
    Guarda una imagen de evento y retorna la ruta relativa a EVENTS_FOLDER.
    - cover: `<event_id>/cover.<ext>` (sobrescribe el previo si existe)
    - gallery: `<event_id>/gallery/<uuid>.<ext>`
    - host: `<event_id>/hosts/<uuid>.<ext>`
    """
    if kind not in ('cover', 'gallery', 'host'):
        abort(400, "kind inválido para imagen de evento")

    # Validar tamaño explícito (además de MAX_CONTENT_LENGTH global)
    max_bytes = current_app.config.get('MAX_EVENT_IMAGE_BYTES', 5 * 1024 * 1024)
    file_storage.stream.seek(0, 2)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > max_bytes:
        abort(400, f"Imagen excede el tamaño máximo de {max_bytes // (1024 * 1024)} MB")

    ext = _validate_ext(file_storage.filename, current_app.config['ALLOWED_IMAGE_EXT'])
    folder = events_dir(event_id, kind if kind != 'cover' else None)
    folder.mkdir(parents=True, exist_ok=True)

    if kind == 'cover':
        # Borrar cover previo con cualquier extensión permitida
        for existing_ext in current_app.config['ALLOWED_IMAGE_EXT']:
            previous = folder / f"cover.{existing_ext}"
            if previous.exists():
                previous.unlink()
        filename = f"cover.{ext}"
        relative = f"{event_id}/{filename}"
    else:
        filename = _uuid_name(ext)
        subfolder = 'gallery' if kind == 'gallery' else 'hosts'
        relative = f"{event_id}/{subfolder}/{filename}"

    file_storage.save(folder / filename)
    return relative


def delete_event_image_file(relative_path: str) -> bool:
    """Borra archivo físico de imagen de evento. Retorna True si existía y se eliminó."""
    if not relative_path:
        return False
    base = current_app.config['EVENTS_FOLDER']
    try:
        abs_path = Path(safe_join(str(base), relative_path))
    except Exception:
        return False
    if abs_path.exists() and abs_path.is_file():
        abs_path.unlink()
        return True
    return False


def delete_all_event_files(event_id: int) -> int:
    """
    Borra todas las imágenes de un evento (carpeta completa).
    Se llama al concluir/archivar evento o al cambiar de periodo.
    Retorna conteo de archivos eliminados. Idempotente.
    """
    folder = events_dir(event_id)
    if not folder.exists():
        return 0
    count = sum(1 for _ in folder.rglob('*') if _.is_file())
    shutil.rmtree(folder, ignore_errors=True)
    return count