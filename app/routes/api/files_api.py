# app/routes/api/files_api.py
"""
Servidor de bytes.

EL URL YA NO ES LA RUTA EN DISCO. `/files/doc/<uuid>` y `/files/avatar/<uuid>`
nombran una FILA; el servidor la busca, lee su `file_path` y sirve el archivo
desde donde realmente está. Los archivos en disco NO se movieron ni se
renombraron —decisión del dueño— para que quien inspeccione el servidor, o
abra un ZIP de retención, siga leyendo `Titulo.pdf`. Lo que dejó de existir es
el URL que publicaba `<user_id>/<fase>/<nombre>`: ese formato entregaba el id
entero del alumno, el nombre exacto del documento y una superficie
enumerable, todo antes de cualquier comprobación.

`Content-Disposition` devuelve el nombre humano original, así que la descarga
se sigue llamando igual. Werkzeug emite la forma RFC 5987
(`filename*=UTF-8''…`) en cuanto el nombre deja de ser ASCII puro, que es lo
que mantiene intacto un 'Título.pdf'.

NADA DEL CONTROL DE ACCESO CAMBIÓ. `file_access_service.user_doc_owner_id`
sigue resolviendo al dueño desde la fila de BD que referencia la ruta —nunca
desde el URL— y `may_view_user_doc` / `may_view_avatar` son exactamente las
mismas funciones. Eso era precisamente el punto de haberlas escrito así.
"""
from pathlib import Path
from flask import Blueprint, current_app, send_file, abort
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from app.models.user import User
from app.services import file_access_service
from app.services import template_access_service
from app.utils.files import abs_path_from_db
from app.utils.permissions import permission_required

api_files = Blueprint('api_files', __name__, url_prefix='/files')

INLINE_EXT = {'pdf'}  # muestra inline; el resto descarga

def _send_safe(base: Path, rel_path: str, inline: bool, download_name: str = None):
    """
    base: carpeta base permitida (Path)
    rel_path: ruta relativa guardada en BD (p.ej. '42/admission/mi_archivo.pdf')
    inline: True -> inline, False -> attachment
    download_name: nombre humano con el que debe guardarse la descarga. Si es
        None, `send_file` usa el basename del archivo en disco, que es el mismo
        valor; se pasa explícito para que el contrato quede a la vista.

    `abs_path_from_db` envuelve `werkzeug.safe_join`, que NO lanza: devuelve
    None cuando la ruta se sale del directorio base. Antes eso caía en
    `Path(None)` → TypeError → 500, o sea que un intento de traversal
    contestaba distinto que un archivo inexistente. Ahora ambos son 404.
    """
    try:
        abs_path = abs_path_from_db(rel_path, base)  # safe_join
    except Exception:
        abort(404)

    if abs_path is None:
        # Traversal: la ruta almacenada intenta salir de `base`.
        abort(404)

    p = Path(abs_path)
    if not p.is_file():
        # La fila existe pero los bytes no están en disco → 404, nunca 500.
        abort(404)

    # send_file con ETag/If-Modified-Since. `download_name` es lo que produce
    # el `Content-Disposition`; werkzeug añade `filename*=UTF-8''…` solo, sin
    # que haya que construir la cabecera a mano.
    return send_file(
        str(p),
        as_attachment=not inline,
        download_name=download_name or p.name,
        conditional=True
    )

@api_files.route('/avatar/<uuid:user_uuid>', methods=['GET'])
@login_required
def avatar(user_uuid):
    """
    GET /files/avatar/<uuid del usuario>

    Antes: `/files/avatar/<int:user_id>/<filename>`. El nombre del archivo no
    aportaba nada —cada usuario tiene una sola foto y la fila ya dice cuál—
    mientras que el id entero convertía el URL en un barrido de cuentas.

    ACL sin cambios: `file_access_service.may_view_avatar`.
    """
    # La foto de perfil es una fotografía del rostro: NO forma parte del nivel
    # reducido entre programas (nombre / correo / progreso). Sólo la ve el
    # propio usuario, quien comparte programa con él (jefe de posgrado = todos)
    # o quien lo ve como ponente de un evento visible.
    #
    # 404 —no 403— a propósito, y por eso "no existe la cuenta", "no puedes
    # verla" y "no tiene foto" contestan las tres lo mismo: cualquier
    # diferencia volvería a confirmar qué cuentas existen.
    user = User.by_uuid(user_uuid)
    if user is None:
        abort(404)

    if not file_access_service.may_view_avatar(current_user, user.id):
        abort(404)

    # 'default.jpg' es un centinela: significa "sin foto" y vive en /static,
    # no en AVATAR_FOLDER.
    stored = user.avatar
    if not stored or stored == 'default.jpg':
        abort(404)

    filename = secure_filename(stored)
    if not filename:
        abort(404)

    rel = f"{user.id}/{filename}"
    base: Path = current_app.config['AVATAR_FOLDER']
    # imágenes -> inline
    return _send_safe(base, rel, inline=True, download_name=filename)

@api_files.route('/doc/<uuid:doc_uuid>', methods=['GET'])
@api_files.route('/doc/<uuid:doc_uuid>/<slot>', methods=['GET'])
@login_required
def user_doc(doc_uuid, slot: str = None):
    """
    GET /files/doc/<uuid de la fila>[/<slot>]

    Antes: `/files/doc/<int:user_id>/<phase>/<path:filename>`.

    `<slot>` sólo aplica a `SemesterEnrollment`, la única fila con DOS archivos
    (`payment-proof` | `schedule`). `Submission` y `AcceptanceDocument` tienen
    uno solo y no lo llevan. La lista blanca de fases desapareció con el URL:
    la ruta ya no viene del cliente, sale de la columna.
    """
    # 1) Qué bytes. La fila se busca por su identificador opaco; de ahí sale la
    #    ruta almacenada, en su forma canónica (los dos formatos históricos
    #    —'documents/x/y' y 'x/y'— los sigue normalizando
    #    `file_access_service`, y en disco no se tocó nada).
    #
    #    Fila inexistente, slot desconocido y columna NULL contestan lo mismo.
    rel = file_access_service.user_doc_path(doc_uuid, slot)
    if not rel:
        abort(404)

    # 2) De quién son. EL DUEÑO SE RESUELVE DESDE LA FILA DE BD, no desde el
    #    URL — esta llamada es idéntica a la de antes del cambio de URL, que
    #    era justamente la promesa de haberla escrito contra la ruta guardada.
    #    Sin fila que la referencie, el archivo no existe para la aplicación
    #    (bytes huérfanos de una re-subida) → 404.
    owner_id = file_access_service.user_doc_owner_id(rel)
    if owner_id is None:
        abort(404)

    # 3) Puerta gruesa por permiso: quien ni siquiera puede ver documentos
    #    ajenos recibe 403. Ya no puede ir antes de tocar la BD porque el dueño
    #    sólo se conoce después de resolver la fila; no abre nada, porque para
    #    llegar aquí hay que traer un UUID válido, que no se adivina.
    if owner_id != current_user.id and not current_user.has_permission(
            file_access_service.VIEW_DOC_OTHERS_PERMISSION):
        abort(403)

    # 404 —no 403— cuando el dueño queda fuera del alcance: un documento
    # personal está prohibido entre programas y un 403 delataría qué
    # documentos subió un estudiante de otro programa.
    if not file_access_service.may_view_user_doc(current_user, owner_id):
        abort(404)

    base: Path = current_app.config['USER_DOCS_FOLDER']

    # 4) Los bytes, desde su ruta real en disco, con el nombre humano de vuelta
    #    en Content-Disposition.
    download_name = file_access_service.document_download_name(rel)
    ext = download_name.rsplit('.', 1)[-1].lower() if '.' in (download_name or '') else ''
    return _send_safe(base, rel, inline=(ext in INLINE_EXT),
                      download_name=download_name)

@api_files.route('/template/<path:filename>', methods=['GET'])
@login_required
@permission_required('files.api.view_template')
def template(filename: str):
    # Las plantillas viven "flat" en TEMPLATE_STORE (sin user_id/phase), así
    # que el nombre del archivo es todo lo que trae el URL. Antes eso bastaba
    # para servirlo: cualquier cuenta autenticada se llevaba TODO el almacén.
    #
    # Autorización desde la FILA DE BD que referencia el archivo, igual que en
    # `user_doc`: la fila (DocumentTemplate o Archive) dice a qué programa o a
    # qué etapa pertenece la plantilla, y de ahí sale el alcance. La regla es la
    # misma que aplica `archives_api.download_template` —vive una sola vez, en
    # `template_access_service`— para que las dos rutas no diverjan.
    #
    # 404 —no 403— cuando no hay fila o queda fuera del alcance: quien no puede
    # verla tampoco debe poder confirmar qué plantillas existen recorriendo
    # nombres, y un archivo que ninguna fila referencia no existe para la
    # aplicación (bytes huérfanos de una re-subida).
    #
    # POR QUÉ ESTA RUTA NO PASÓ A UUID, a diferencia de /doc y /avatar:
    #   1. No hay dato personal. Una plantilla es un FORMATO EN BLANCO
    #      ('Formato_de_solicitud.docx'): los mismos bytes para todo el que
    #      esté en alcance. El "¿de quién es esto?" que motiva el URL opaco de
    #      un documento personal aquí no existe.
    #   2. El nombre plano ya no es un oráculo. `may_download_flat_template`
    #      contesta 404 tanto para un nombre inexistente como para uno fuera de
    #      alcance, así que barrer nombres no confirma nada.
    #   3. NADA EN LA APLICACIÓN PRODUCE ESTE URL. Ni una plantilla Jinja, ni
    #      un `url_for`, ni un literal en JS. La puerta viva a los bytes de una
    #      plantilla es `GET /api/v1/archives/<uuid:archive_uuid>/template`,
    #      que ya habla UUID. Darle aquí una forma nueva sería código muerto.
    # El final honesto de esta ruta es BORRARLA y repuntar sus pruebas a
    # `archives_api.download_template`, pero eliminar una puerta es otra
    # decisión —del dueño— y no la de este lote.
    filename = secure_filename(filename)
    if not filename:
        abort(404)

    if not template_access_service.may_download_flat_template(current_user, filename):
        abort(404)

    base: Path = current_app.config['TEMPLATE_STORE']
    return _send_safe(base, filename, inline=False)  # siempre como descarga


@api_files.route('/event/<int:event_id>/<kind>/<path:filename>', methods=['GET'])
@login_required
def event_image(event_id: int, kind: str, filename: str):
    """
    Sirve los bytes de las imágenes de un evento (portada, galería y foto de
    ponente externo).

    ACL: la MISMA regla del módulo de eventos — gestión
    (`EventsService.user_may_manage_event`) o participación
    (`EventsService.user_may_participate_in_event`). Esta ruta es el servidor
    de bytes del índice que publica `events_api.list_event_images`, así que las
    dos tienen que contestar lo mismo.

    Antes bastaba con `visible_to_students and published and capacity_type !=
    'single'`: ni `visibility` ni programa, o sea que cualquier cuenta
    autenticada se descargaba la portada y la galería completas de un evento
    PRIVADO de cualquier posgrado sabiendo sólo su id.

    404 —no 403— cuando no procede: el nombre de la portada es predecible
    (`/files/event/<id>/cover/cover.webp`), así que un 403 confirmaría qué
    eventos existen y cuáles son privados recorriendo ids.

    POR QUÉ ESTA RUTA TAMPOCO PASÓ A UUID:
      1. `Event` no tiene identificador público. Las cinco entidades que lo
         tienen las eligió el dueño y `Event` no está entre ellas; inventarle
         uno es una decisión distinta a la que se tomó.
      2. La portada y la galería no son dato personal: son el material de
         difusión del propio evento, publicado a todo el que participa en él.
      3. El id ya lo conoce cualquiera que pueda ver el evento —viene en
         `GET /api/v1/events`— y barrer ids no revela nada, porque "no existe"
         y "no es tuyo" contestan ambos 404 uniforme.
      4. Única excepción real, y aun así no compra nada: la foto de un ponente
         EXTERNO (`EventHost.external_photo_path`) sí es la cara de una
         persona. Pero su nombre de archivo ya es un uuid4 —lo genera
         `save_event_image`— así que la hoja del URL ya es inadivinable, y el
         ACL es la misma regla de participación del módulo de eventos.
     La foto de un ponente INTERNO no pasa por aquí: se sirve por
     `/files/avatar/<uuid>`, que sí es opaca.

    kind: 'cover' (archivo directo en <event_id>/) | 'gallery' | 'hosts'
    Para cover, filename ej: cover.webp → rel = <event_id>/cover.webp
    Para gallery/hosts, rel = <event_id>/<kind>/<filename>
    """
    from app.models.event import Event
    from app.services.events_service import EventsService
    from app import db as _db

    if kind not in ('cover', 'gallery', 'hosts'):
        abort(400)

    event = _db.session.get(Event, event_id)
    if not event:
        abort(404)

    if not (
        EventsService.user_may_manage_event(current_user, event)
        or EventsService.user_may_participate_in_event(current_user, event)
    ):
        abort(404)

    filename = secure_filename(filename)
    if kind == 'cover':
        rel = f"{event_id}/{filename}"
    else:
        rel = f"{event_id}/{kind}/{filename}"

    base: Path = current_app.config['EVENTS_FOLDER']
    return _send_safe(base, rel, inline=True)
