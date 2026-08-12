# app/services/permission_service.py
"""
Servicio centralizado para la gestión de permisos granulares.

Cubre:
  - Delegación de permisos de coordinador → servicio social (Phase 7)
  - Overrides de permisos de rol por el jefe de posgrado (Phase 8)
"""

from app import db
from app.models.permission import Permission
from app.models.role_permission import RolePermission, RolePermissionOverride
from app.models.role_permission_audit import RolePermissionAudit
from app.models.user_permission import UserPermission
from app.models.user import User, GLOBAL_SCOPE_PERMISSION, SELF_SERVICE_PERMISSIONS
from app.models.role import Role
from app.utils.validators import (
    EMAIL_MAX_LENGTH,
    validate_person_name,
    validate_short_text,
)


class PermissionError(Exception):
    """Error de negocio en operaciones de permisos."""


#: Recursos que nadie puede delegar: darlos sería regalar el control del propio
#: sistema de permisos (delegar la delegación, revocar delegaciones ajenas…).
NON_DELEGATABLE_RESOURCES = {'permissions'}


def _normalize_program_id(program_id):
    """`program_id` a int, o None para 'todos los programas'. Basura → error."""
    if program_id is None or program_id == '':
        return None
    try:
        return int(program_id)
    except (TypeError, ValueError):
        raise PermissionError("program_id inválido.")


def _require_delegation_scope(granter, program_id):
    """
    El otorgante sólo reparte lo que ya alcanza.

    - `program_id=None` significa "todos los programas": sólo puede crearlo
      quien ya tiene alcance global (jefe de posgrado).
    - Cualquier otro programa debe estar dentro de
      `granter.get_accessible_program_ids()`.
    """
    from app.services import program_scope_service as scope_service

    if program_id is None:
        if not scope_service.is_global_scope(granter):
            raise PermissionError(
                "Debes indicar el programa de la delegación: sólo el jefe de "
                "posgrado puede delegar sobre todos los programas."
            )
        return

    if not scope_service.program_in_scope(granter, program_id):
        raise PermissionError(
            "No puedes delegar sobre un programa que no está a tu alcance."
        )


# ===========================================================================
# Consultas de permisos efectivos
# ===========================================================================

def get_user_effective_permissions(user_id, program_id=None):
    """
    Retorna la lista completa de permisos efectivos de un usuario.
    No usa caché de g (es para admin/vista, no para evaluación en-request).

    "Efectivo" tiene que significar lo mismo aquí que en `User.has_permission`:
    esta lista alimenta `/api/v1/permissions/me` —con la que el front decide qué
    botones dibuja— y `get_delegatable_permissions`, cuyo contrato es que lo
    ofrecido sea exactamente lo que el servicio acepta. Por eso las tres fuentes
    filtran `Permission.is_active`: un permiso retirado del catálogo ya no lo
    concede ni el rol ni una delegación, así que anunciarlo aquí sólo produciría
    una interfaz que ofrece acciones que el backend rechaza y un selector de
    delegación con opciones que `delegate_permission` niega al enviarlas.
    """
    from app.utils.datetime_utils import now_local

    user = User.query.get_or_404(user_id)
    result = {}

    # 1. Permisos base del rol
    if user.role_id:
        base = (
            db.session.query(RolePermission)
            .join(RolePermission.permission)
            .filter(
                RolePermission.role_id == user.role_id,
                Permission.is_active == True
            )
            .all()
        )
        for rp in base:
            p = rp.permission
            result[p.codename] = {
                'codename': p.codename,
                'display_name': p.display_name,
                'source': 'role',
                'permission_id': p.id,
            }

    # 2. Overrides del rol
    if user.role_id:
        overrides = (
            db.session.query(RolePermissionOverride)
            .join(RolePermissionOverride.permission)
            .filter(
                RolePermissionOverride.role_id == user.role_id,
                RolePermissionOverride.is_active == True,
                Permission.is_active == True
            )
            .all()
        )
        for ov in overrides:
            p = ov.permission
            if p.codename not in result:
                result[p.codename] = {
                    'codename': p.codename,
                    'display_name': p.display_name,
                    'source': 'override',
                    'permission_id': p.id,
                }

    # 3. UserPermissions directos/delegados (activos y no vencidos)
    q = (
        UserPermission.query
        .join(UserPermission.permission)
        .filter(
            UserPermission.user_id == user_id,
            UserPermission.is_active == True,
            Permission.is_active == True,
        )
    )
    if program_id is not None:
        q = q.filter(
            (UserPermission.program_id == program_id) |
            (UserPermission.program_id == None)
        )
    for up in q.all():
        if up.is_expired:
            continue
        p = up.permission
        if p.codename not in result:
            result[p.codename] = {
                'codename': p.codename,
                'display_name': p.display_name,
                'source': 'delegation',
                'permission_id': p.id,
                'user_permission_id': up.id,
                'granted_by': up.granted_by,
                'expires_at': up.expires_at.isoformat() if up.expires_at else None,
            }

    return list(result.values())


def get_delegatable_permissions(user_id, program_id=None):
    """
    Retorna los permisos que user puede delegar (los que él mismo tiene).

    Sólo permisos de tipo 'api' (no pages). Se excluyen:
      - los del propio sistema de permisos (escalación de privilegios),
      - los de cuenta propia, que no confieren alcance sobre ningún programa y
        que `delegate_permission` rechaza.
    Esta lista alimenta el selector de la UI: lo que aparece aquí tiene que ser
    exactamente lo que el servicio acepta.
    """
    perms = get_user_effective_permissions(user_id, program_id)
    return [
        p for p in perms
        if p['codename'].split('.')[0] not in NON_DELEGATABLE_RESOURCES
        and p['codename'].split('.')[1] == 'api'
        and p['codename'] not in SELF_SERVICE_PERMISSIONS
    ]


# ===========================================================================
# Delegación (Phase 7)
# ===========================================================================

def delegate_permission(granter_id, grantee_id, codename, program_id=None, note=None, expires_at=None):
    """
    Delega un permiso de granter → grantee.

    Una delegación otorga capacidad Y alcance: el programa de la delegación
    entra en `grantee.get_accessible_program_ids()`. Por eso las validaciones
    de abajo no son formalidades — cada una cierra una vía de escalación:

      - No se puede delegar a uno mismo. Antes, un coordinador del programa A
        se autodelegaba cualquier codename sobre el programa B y a partir de la
        siguiente petición su alcance era {A, B}: expediente completo, PII,
        documentos, fotos y escrituras de B.
      - Sólo se delega DENTRO del alcance propio: nadie reparte acceso a un
        programa que no controla. `program_id=None` (todos los programas) queda
        reservado a quien ya tiene alcance global.
      - No se delegan permisos del propio sistema de permisos: delegar
        'permissions.api.delegate' sería regalar la llave de la puerta.
      - No se delegan permisos de cuenta propia (`SELF_SERVICE_PERMISSIONS`).
        Todo el mundo los tiene ya sobre sí mismo, así que delegarlos no aporta
        capacidad; lo único que podían aportar era alcance sobre el programa
        indicado, que es justo la escalada que se quiere cerrar.
        `get_accessible_program_ids()` los ignora, y aquí se rechazan en voz
        alta en lugar de crear una fila que no hace nada.
    """
    granter = User.query.get(granter_id)
    if not granter:
        raise PermissionError("Usuario otorgante no encontrado.")

    grantee = User.query.get(grantee_id)
    if not grantee:
        raise PermissionError("Usuario destinatario no encontrado.")

    if grantee.id == granter.id:
        raise PermissionError(
            "No puedes delegarte permisos a ti mismo. Una delegación amplía el "
            "alcance del destinatario; autodelegarse sería ampliarse el propio."
        )

    if not granter.has_permission('permissions.api.delegate'):
        raise PermissionError("No tienes permiso para delegar.")

    if not granter.has_permission(codename):
        raise PermissionError(f"No puedes delegar '{codename}' porque no lo tienes.")

    perm = Permission.query.filter_by(codename=codename, is_active=True).first()
    if not perm:
        raise PermissionError(f"Permiso '{codename}' no existe o está inactivo.")

    if perm.resource in NON_DELEGATABLE_RESOURCES:
        raise PermissionError(
            f"No se puede delegar permisos del sistema de permisos: '{codename}'."
        )

    if codename in SELF_SERVICE_PERMISSIONS:
        raise PermissionError(
            f"'{codename}' es un permiso de cuenta propia: cada usuario ya lo "
            "ejerce sobre sí mismo y delegarlo no da acceso a ningún programa."
        )

    program_id = _normalize_program_id(program_id)
    _require_delegation_scope(granter, program_id)

    # Check duplicado activo (usando la constraint uq_user_permission_active)
    existing = UserPermission.query.filter_by(
        user_id=grantee.id,
        permission_id=perm.id,
        program_id=program_id,
        is_active=True
    ).first()
    if existing and not existing.is_expired:
        raise PermissionError(
            f"El usuario ya tiene el permiso '{codename}' delegado (activo)."
        )

    up = UserPermission(
        user_id=grantee.id,
        permission_id=perm.id,
        granted_by=granter.id,
        program_id=program_id,
        expires_at=expires_at,
        note=note,
    )
    db.session.add(up)
    db.session.commit()
    return up


def revoke_delegation(revoker_id, user_permission_id):
    """
    Revoca una delegación activa.

    Puede revocar:
      - quien la otorgó (`granted_by`),
      - quien tiene alcance global (jefe de posgrado),
      - quien tiene 'permissions.api.revoke_delegation' Y el programa de la
        delegación está dentro de su alcance.

    Antes bastaba con tener 'permissions.api.delegate' —que todo program_admin
    tiene por rol— y el id es enumerable: cualquier coordinador podía revocar
    CUALQUIER delegación del sistema, incluidas las del jefe de posgrado.
    """
    from app.services import program_scope_service as scope_service

    up = UserPermission.query.get(user_permission_id)
    if not up:
        raise PermissionError("Delegación no encontrada.")
    if not up.is_active:
        raise PermissionError("Esta delegación ya fue revocada.")

    revoker = User.query.get(revoker_id)
    if not revoker:
        raise PermissionError("Usuario no encontrado.")

    can_revoke = (
        up.granted_by == revoker_id
        or scope_service.is_global_scope(revoker)
        or (
            revoker.has_permission('permissions.api.revoke_delegation')
            and up.program_id is not None
            and scope_service.program_in_scope(revoker, up.program_id)
        )
    )
    if not can_revoke:
        raise PermissionError("No tienes permiso para revocar esta delegación.")

    up.revoke()
    db.session.commit()
    return up


def get_user_delegations(user_id):
    """
    Permisos directamente delegados a un usuario (no los de rol), SIN filtrar.

    Uso interno / alcance global. Una ruta expuesta debe usar
    `get_user_delegations_for_viewer`: esta lista revela qué permisos y sobre
    qué programas tiene una cuenta, que es el paso de reconocimiento previo a
    una escalada por delegación.
    """
    return (
        UserPermission.query
        .filter_by(user_id=user_id)
        .order_by(UserPermission.granted_at.desc())
        .all()
    )


def get_user_delegations_for_viewer(viewer, user_id):
    """
    Delegaciones de `user_id` que `viewer` tiene derecho a ver.

    Reglas:
      - alcance global (jefe de posgrado) o consultarse a sí mismo → todo;
      - en otro caso, sólo las delegaciones que el propio viewer otorgó y las
        que caen dentro de su alcance de programas.

    Nunca lanza por "no autorizado": devuelve la lista recortada (vacía si no
    hay nada visible), de modo que el endpoint no sirva como oráculo de
    existencia de cuentas ni de permisos ajenos.

    Args:
        viewer (User): el usuario que consulta (objeto, no id — este servicio
            no toca `current_user`).
        user_id (int): cuenta consultada.
    """
    from app.services import program_scope_service as scope_service

    rows = get_user_delegations(user_id)

    viewer_id = getattr(viewer, 'id', None)
    if viewer_id is None:
        return []
    if viewer_id == user_id or scope_service.is_global_scope(viewer):
        return rows

    # Mismo cuidado que en `create_social_service_user`: None significa TODOS
    # los programas, nunca "ninguno". Escrito como `or set()` esta línea
    # convertía el alcance global en un filtro vacío, es decir en "no ve nada",
    # justo al revés de lo que None quiere decir. Hoy la rama es inalcanzable
    # —`is_global_scope()` se evalúa arriba y está definido como
    # `accessible_program_ids(...) is None`—, pero se escribe explícita para que
    # el archivo que define la regla no contenga el error que la contradice.
    scope = scope_service.accessible_program_ids(viewer)
    if scope is None:
        return rows

    return [
        up for up in rows
        if up.granted_by == viewer_id
        or (up.program_id is not None and up.program_id in scope)
    ]


def create_social_service_user(creator_id, user_data, permissions_to_delegate,
                                program_ids=None, expires_at=None):
    """
    Crea un usuario con rol 'social_service' y delega los permisos especificados.

    Reglas de scope — una cuenta de servicio social SIEMPRE nace atada a
    programas concretos:
      - Creador con alcance global (jefe de posgrado): `program_ids` es
        OBLIGATORIO. Antes, una lista vacía se traducía en `program_id = NULL`
        en cada delegación; como `get_accessible_program_ids()` descarta las
        delegaciones sin programa, la cuenta nacía con alcance `set()`: la
        persona recibía su correo de activación, entraba y encontraba /admin/review
        permanentemente vacío, con cada endpoint respondiendo 403 y sin nada en
        la interfaz que lo explicara. Ahora se rechaza antes de crear nada.
      - Creador sin alcance global (coordinador): se toma su alcance real
        (programas que coordina + delegaciones vigentes) y se ignora
        `program_ids` del payload. Si no alcanza ningún programa, no puede crear
        la cuenta.

    Args:
      creator_id: ID del usuario otorgante.
      user_data: dict con first_name, last_name, mother_last_name, email, is_internal.
      permissions_to_delegate: lista de codenames a delegar.
      program_ids: lista de program_id. Obligatoria para un creador global;
        ignorada para un coordinador (se usa su propio alcance).
      expires_at: datetime opcional de vencimiento común para todas las delegaciones.

    La cuenta NO recibe contraseña compartida: se guarda una aleatoria que nadie
    conoce y el acceso llega por un enlace de un solo uso enviado al correo.

    Returns:
      (User, [UserPermission], bool) — usuario creado, delegaciones creadas y si
      el correo de activación pudo encolarse.
    """
    creator = User.query.get(creator_id)
    if not creator:
        raise PermissionError("Usuario creador no encontrado.")

    if not creator.has_permission('admin_users.api.create_social_service'):
        raise PermissionError("No tienes permiso para crear usuarios de servicio social.")

    if not creator.has_permission('permissions.api.delegate'):
        raise PermissionError("No tienes permiso para delegar.")

    # Same rules as self-registration (app/utils/validators.py). These columns
    # are rendered by the staff consoles, so storage stays clean here too.
    # InputValidationError propagates: the route translates it to the envelope.
    first_name = validate_person_name(user_data.get('first_name'), label="Nombre")
    last_name = validate_person_name(user_data.get('last_name'), label="Apellido paterno")
    mother_last_name = validate_person_name(
        user_data.get('mother_last_name'), label="Apellido materno", required=False
    )
    email = validate_short_text(
        user_data.get('email'),
        label="Correo electrónico",
        required=True,
        max_length=EMAIL_MAX_LENGTH,
    ).lower()

    # The only way into this account is a link mailed to this address, so an
    # address nothing can be delivered to must stop the creation here — before
    # anything is written — instead of leaving an account nobody can enter.
    if '@' not in email:
        raise PermissionError(
            "El correo electrónico no es una dirección válida. La cuenta se "
            "activa mediante un enlace enviado por correo, así que no puede "
            "crearse sin un buzón al que llegar."
        )

    if User.query.filter_by(email=email).first():
        raise PermissionError(f"El email '{email}' ya está registrado.")
    if User.query.filter_by(username=email).first():
        raise PermissionError(f"El usuario '{email}' ya existe.")

    ss_role = Role.query.filter_by(name='social_service').first()
    if not ss_role:
        raise PermissionError("Rol 'social_service' no existe en el sistema.")

    if not permissions_to_delegate:
        raise PermissionError("Debe delegarse al menos un permiso.")

    # Alcance global se decide por ROL, nunca por un permiso delegado: si se
    # leyera con has_permission(), delegar 'academic_periods.api.create' sobre
    # UN programa convertiría al destinatario en administrador global.
    is_postgraduate = creator.has_global_program_scope()
    if is_postgraduate:
        try:
            effective_pids = sorted({int(pid) for pid in (program_ids or [])})
        except (TypeError, ValueError):
            raise PermissionError("La lista de programas contiene un valor inválido.")
    else:
        # Alcance real del creador, no sólo lo que coordina: una cuenta de
        # servicio social nunca puede nacer con más alcance que quien la crea.
        creator_scope = creator.get_accessible_program_ids()
        if creator_scope is None:
            # None significa TODOS los programas, jamás "ninguno". Escrito como
            # `or set()` esta línea convertía "todos" en "ninguno" —la confusión
            # exacta que este módulo existe para evitar— en el archivo que define
            # la regla. Hoy la rama es inalcanzable (sólo el alcance global
            # devuelve None y ya se resolvió arriba con `is_postgraduate`), pero
            # si las dos definiciones llegaran a separarse hay que fallar cerrado:
            # tratar None como "todos" aquí crearía una cuenta de servicio social
            # con alcance sobre el instituto entero a manos de quien no es jefe
            # de posgrado.
            raise PermissionError(
                "Tu cuenta tiene alcance global sin ser jefe de posgrado. No se "
                "creará la cuenta de servicio social: reporta esta inconsistencia "
                "de permisos antes de continuar."
            )
        creator_pids = sorted(creator_scope)
        if not creator_pids:
            raise PermissionError("No coordinas programas. No puedes crear servicio social.")
        effective_pids = creator_pids

    # Sin programas no hay cuenta. Una delegación con program_id NULL no aporta
    # alcance (`User.get_accessible_program_ids` la ignora), así que crear la
    # cuenta aquí sería fabricar un usuario que nunca podrá ver un expediente.
    if not effective_pids:
        raise PermissionError(
            "Selecciona al menos un programa para la cuenta de servicio social. "
            "Una delegación sin programa no otorga acceso a ningún expediente: "
            "la cuenta se crearía sin poder trabajar."
        )

    # Los programas deben existir. Con alcance global `_require_delegation_scope`
    # acepta cualquier id, así que un id inventado crearía delegaciones colgando
    # de un programa inexistente.
    from app.models.program import Program

    found_pids = {
        pid for (pid,) in db.session.query(Program.id)
        .filter(Program.id.in_(effective_pids))
        .all()
    }
    missing_pids = [pid for pid in effective_pids if pid not in found_pids]
    if missing_pids:
        raise PermissionError(
            "Alguno de los programas seleccionados no existe: "
            + ", ".join(str(pid) for pid in missing_pids)
        )

    # Alcance: cada programa destino debe estar dentro del alcance del creador.
    for pid in effective_pids:
        _require_delegation_scope(creator, pid)

    perm_objects = {}
    for codename in permissions_to_delegate:
        perm = Permission.query.filter_by(codename=codename, is_active=True).first()
        if not perm:
            raise PermissionError(f"Permiso '{codename}' no existe o está inactivo.")
        if perm.resource in NON_DELEGATABLE_RESOURCES:
            raise PermissionError(f"No se puede delegar permisos del sistema de permisos: '{codename}'.")
        if codename in SELF_SERVICE_PERMISSIONS:
            raise PermissionError(
                f"'{codename}' es un permiso de cuenta propia y no otorga "
                "acceso a ningún programa; quítalo de la selección."
            )
        perm_objects[codename] = perm
        # Capacidad: el creador debe tener el permiso.
        if not creator.has_permission(codename):
            raise PermissionError(f"No puedes delegar '{codename}' porque no lo tienes.")

    # The account is born with a password nobody knows — not the creator, not
    # the holder. Access arrives exclusively through the single-use link mailed
    # below, and `must_change_password` stays True so the holder still sets
    # their own secret. There is no shared default credential in this system.
    from app.services import password_reset_service as prs

    new_user = User(
        first_name=first_name,
        last_name=last_name,
        mother_last_name=mother_last_name,
        username=email,
        password=prs.random_password(),
        email=email,
        is_internal=bool(user_data.get('is_internal', True)),
        role_id=ss_role.id,
        must_change_password=True,
    )
    db.session.add(new_user)
    db.session.flush()

    created_delegations = []
    for codename, perm in perm_objects.items():
        for pid in effective_pids:
            up = UserPermission(
                user_id=new_user.id,
                permission_id=perm.id,
                granted_by=creator_id,
                program_id=pid,
                expires_at=expires_at,
                note="Delegación inicial al crear usuario de servicio social.",
            )
            db.session.add(up)
            created_delegations.append(up)

    # Single-use activation link (45 min — an admin created this account
    # interactively and can tell the holder to check their inbox now). Mirrors
    # student_bulk_service: generate the token, queue exactly one e-mail, and
    # tolerate a mail failure — the token row is already persisted, so the
    # administrator can still have it re-sent instead of losing the account.
    prt = prs.generate_token(
        user_id=new_user.id,
        purpose='set_password',
        ttl_minutes=prs.INTERACTIVE_TTL_MINUTES,
        created_by_id=creator_id,
    )
    token_link = prs.build_reset_password_url(prt.token)

    email_sent = False
    try:
        from app.services.email_templates import EmailTemplates
        from app.services.email_service import EmailService

        subject, html = EmailTemplates.staff_account_set_password(
            user_name=f'{new_user.first_name} {new_user.last_name}'.strip(),
            username=new_user.username,
            token_link=token_link,
            expires_at=prt.expires_at,
        )
        EmailService.queue_email(
            user_id=new_user.id,
            subject=subject,
            html_content=html,
        )
        email_sent = True
    except Exception as email_exc:
        import logging
        logging.getLogger(__name__).warning(
            "[permission_service] No se pudo encolar el correo de activación "
            "para el usuario %s: %s", new_user.id, email_exc
        )

    db.session.commit()

    # Notificar a admins en tiempo real.
    #
    # Este aviso lleva el nombre y el correo de una cuenta de PERSONAL. La
    # segunda emisión iba a `role:coordinator`, que reúne a todo titular de
    # `coordinator.page.view` — o sea a cada program_admin de la institución—,
    # y eso contradice la regla del propio módulo de alcance: una cuenta de
    # personal no es alumno de nadie, así que su identidad no sale del alcance
    # global. `emit_admin_user_change(payload, None)` es exactamente ese
    # reparto, y es el mismo emisor que usan las otras dos rutas que publican
    # `admin_user:changed` (`admin/users_api.update_user` y `delete_user`).
    # El try/except se conserva porque este bloque corre DESPUÉS del commit:
    # la cuenta ya existe y un fallo del transporte no debe convertir un alta
    # correcta en un 500. (`emit_admin_user_change` ya traga sus propios
    # errores; esto sólo cubre el import.)
    try:
        from app.sockets.emitters import emit_admin_user_change

        emit_admin_user_change(
            {
                'action': 'created',
                'user_id': new_user.id,
                'role': 'social_service',
                'email': new_user.email,
                'full_name': f'{new_user.first_name} {new_user.last_name}',
            },
            None,   # cuenta de personal: sin audiencia program-scoped
        )
    except Exception:
        pass

    return new_user, created_delegations, email_sent


# ===========================================================================
# Overrides de rol (Phase 8)
# ===========================================================================

def get_role_permissions_summary(role_id):
    """
    Retorna los permisos de un rol en dos grupos:
      - 'seed': permisos base del seed (no editables desde UI)
      - 'overrides': overrides activos e inactivos del jefe de posgrado
    """
    role = Role.query.get_or_404(role_id)

    seed = (
        db.session.query(RolePermission)
        .join(RolePermission.permission)
        .filter(RolePermission.role_id == role_id)
        .order_by(Permission.resource, Permission.action)
        .all()
    )

    overrides = (
        db.session.query(RolePermissionOverride)
        .join(RolePermissionOverride.permission)
        .filter(RolePermissionOverride.role_id == role_id)
        .order_by(RolePermissionOverride.created_at.desc())
        .all()
    )

    # Codenames base para saber si un override duplica un permiso de seed
    seed_codenames = {rp.permission.codename for rp in seed}

    return {
        'role': {'id': role.id, 'name': role.name},
        'seed_permissions': [rp.permission.to_dict() for rp in seed],
        'overrides': [
            {
                **ov.to_dict(),
                'is_seed_duplicate': ov.permission.codename in seed_codenames,
            }
            for ov in overrides
        ],
    }


def add_role_override(role_id, codename, performed_by, reason=None):
    """
    Agrega un override de permiso a un rol.
    Solo el jefe de posgrado puede hacer esto.

    - Si el rol ya tiene el permiso vía seed, se permite (el override queda inactivo
      pero documentado).
    - Si ya hay un override activo para ese par, lanza error.
    - `GLOBAL_SCOPE_PERMISSION` no puede otorgarse por esta vía (ver abajo).
    """
    role = Role.query.get(role_id)
    if not role:
        raise PermissionError("Rol no encontrado.")

    # El alcance global no se reparte: se es jefe de posgrado o no se es.
    #
    # `has_global_program_scope()` lee el ROL, y un override de rol ES un grant
    # de rol (`User._role_permission_codenames` lo incluye), así que este
    # endpoint es la única puerta por la que el marcador de alcance global puede
    # colarse en un rol que no debería tenerlo. Aplicarlo a 'program_admin'
    # convertiría de golpe a TODOS los coordinadores —presentes y futuros— en
    # administradores globales: expedientes, PII y escrituras de cualquier
    # programa, sin que aparezca ni un cambio de rol en la consola de usuarios.
    #
    # Se rechaza en seco en vez de pedir una bandera de confirmación: la
    # confirmación sólo protege del clic equivocado, y quien manda el JSON a
    # mano la incluye. La vía legítima para dar alcance global existe, es
    # explícita y es visible: asignar el rol 'postgraduate_admin' a la cuenta.
    if codename == GLOBAL_SCOPE_PERMISSION:
        raise PermissionError(
            f"'{codename}' define el alcance global del sistema y no puede "
            "otorgarse como override de rol: daría acceso a todos los programas "
            "a cada cuenta con ese rol. Si alguien debe tener alcance global, "
            "asígnale el rol de jefe de posgrado."
        )

    perm = Permission.query.filter_by(codename=codename, is_active=True).first()
    if not perm:
        raise PermissionError(f"Permiso '{codename}' no existe o está inactivo.")

    # Verificar override activo existente
    existing = RolePermissionOverride.query.filter_by(
        role_id=role_id,
        permission_id=perm.id,
        is_active=True
    ).first()
    if existing:
        raise PermissionError(
            f"El rol '{role.name}' ya tiene un override activo para '{codename}'."
        )

    override = RolePermissionOverride(role_id=role_id, permission_id=perm.id)
    db.session.add(override)

    audit = RolePermissionAudit(
        role_id=role_id,
        permission_id=perm.id,
        action='grant',
        performed_by=performed_by,
        reason=reason,
        previous_state=None,
    )
    db.session.add(audit)
    db.session.commit()

    # Notificar a admins en tiempo real
    try:
        from app.extensions import socketio
        socketio.emit(
            'role_permission:changed',
            {
                'action': 'grant',
                'role_id': role_id,
                'role_name': role.name,
                'codename': codename,
            },
            room='role:postgraduate_admin',
        )
    except Exception:
        pass

    return override


def revert_role_override(role_id, codename, performed_by):
    """
    Revierte (desactiva) un override activo de rol.
    Registra la acción en el audit log.
    """
    perm = Permission.query.filter_by(codename=codename).first()
    if not perm:
        raise PermissionError(f"Permiso '{codename}' no encontrado.")

    override = RolePermissionOverride.query.filter_by(
        role_id=role_id,
        permission_id=perm.id,
        is_active=True
    ).first()
    if not override:
        raise PermissionError(
            f"No hay override activo de '{codename}' para este rol."
        )

    previous = override.to_dict()
    override.revoke()

    audit = RolePermissionAudit(
        role_id=role_id,
        permission_id=perm.id,
        action='revert',
        performed_by=performed_by,
        previous_state=previous,
    )
    db.session.add(audit)
    db.session.commit()

    # Notificar a admins en tiempo real
    try:
        from app.extensions import socketio
        role = Role.query.get(role_id)
        socketio.emit(
            'role_permission:changed',
            {
                'action': 'revert',
                'role_id': role_id,
                'role_name': role.name if role else None,
                'codename': codename,
            },
            room='role:postgraduate_admin',
        )
    except Exception:
        pass

    return override


def get_audit_log(role_id=None, permission_id=None, limit=100, offset=0):
    """Retorna el historial de cambios de overrides de rol."""
    q = RolePermissionAudit.query.order_by(RolePermissionAudit.performed_at.desc())
    if role_id:
        q = q.filter_by(role_id=role_id)
    if permission_id:
        q = q.filter_by(permission_id=permission_id)
    total = q.count()
    items = q.offset(offset).limit(limit).all()
    return {'total': total, 'items': [e.to_dict() for e in items]}


def list_all_permissions(resource=None, perm_type=None):
    """Lista todos los permisos del catálogo (para el selector de override)."""
    q = Permission.query.filter_by(is_active=True)
    if resource:
        q = q.filter_by(resource=resource)
    if perm_type:
        q = q.filter_by(perm_type=perm_type)
    return q.order_by(Permission.resource, Permission.action).all()
