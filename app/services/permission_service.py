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
from app.models.user import User
from app.models.role import Role
from app.utils.validators import (
    EMAIL_MAX_LENGTH,
    validate_person_name,
    validate_short_text,
)


class PermissionError(Exception):
    """Error de negocio en operaciones de permisos."""


# ===========================================================================
# Consultas de permisos efectivos
# ===========================================================================

def get_user_effective_permissions(user_id, program_id=None):
    """
    Retorna la lista completa de permisos efectivos de un usuario.
    No usa caché de g (es para admin/vista, no para evaluación en-request).
    """
    from app.utils.datetime_utils import now_local

    user = User.query.get_or_404(user_id)
    result = {}

    # 1. Permisos base del rol
    if user.role_id:
        base = (
            db.session.query(RolePermission)
            .join(RolePermission.permission)
            .filter(RolePermission.role_id == user.role_id)
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
                RolePermissionOverride.is_active == True
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
    q = UserPermission.query.filter_by(user_id=user_id, is_active=True)
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
    Solo retorna permisos de tipo 'api' (no pages) y excluye permisos de
    permisos-sobre-permisos para evitar escalación de privilegios.
    """
    EXCLUDED_RESOURCES = {'permissions'}  # no se puede delegar control del sistema de permisos

    perms = get_user_effective_permissions(user_id, program_id)
    return [
        p for p in perms
        if p['codename'].split('.')[0] not in EXCLUDED_RESOURCES
        and p['codename'].split('.')[1] == 'api'
    ]


# ===========================================================================
# Delegación (Phase 7)
# ===========================================================================

def delegate_permission(granter_id, grantee_id, codename, program_id=None, note=None, expires_at=None):
    """
    Delega un permiso de granter → grantee.

    Validaciones:
      - granter debe tener permissions.api.delegate
      - granter debe tener el permiso que quiere delegar
      - El par (grantee, codename, program_id) no debe tener ya una delegación activa
    """
    granter = User.query.get(granter_id)
    if not granter:
        raise PermissionError("Usuario otorgante no encontrado.")

    if not granter.has_permission('permissions.api.delegate'):
        raise PermissionError("No tienes permiso para delegar.")

    if not granter.has_permission(codename, program_id=program_id):
        raise PermissionError(f"No puedes delegar '{codename}' porque no lo tienes.")

    perm = Permission.query.filter_by(codename=codename, is_active=True).first()
    if not perm:
        raise PermissionError(f"Permiso '{codename}' no existe o está inactivo.")

    # Check duplicado activo (usando la constraint uq_user_permission_active)
    existing = UserPermission.query.filter_by(
        user_id=grantee_id,
        permission_id=perm.id,
        program_id=program_id,
        is_active=True
    ).first()
    if existing and not existing.is_expired:
        raise PermissionError(
            f"El usuario ya tiene el permiso '{codename}' delegado (activo)."
        )

    up = UserPermission(
        user_id=grantee_id,
        permission_id=perm.id,
        granted_by=granter_id,
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
    Solo puede revocar el granted_by original o alguien con permissions.api.delegate global.
    """
    up = UserPermission.query.get(user_permission_id)
    if not up:
        raise PermissionError("Delegación no encontrada.")
    if not up.is_active:
        raise PermissionError("Esta delegación ya fue revocada.")

    revoker = User.query.get(revoker_id)
    can_revoke = (
        up.granted_by == revoker_id
        or revoker.has_permission('permissions.api.delegate')
    )
    if not can_revoke:
        raise PermissionError("No tienes permiso para revocar esta delegación.")

    up.revoke()
    db.session.commit()
    return up


def get_user_delegations(user_id):
    """Permisos directamente delegados a un usuario (no los de rol)."""
    return (
        UserPermission.query
        .filter_by(user_id=user_id)
        .order_by(UserPermission.granted_at.desc())
        .all()
    )


def create_social_service_user(creator_id, user_data, permissions_to_delegate,
                                program_ids=None, expires_at=None):
    """
    Crea un usuario con rol 'social_service' y delega los permisos especificados.

    Reglas de scope:
      - program_admin (sin academic_periods.api.create): scope se auto-asigna a
        todos los programas que coordina. Se ignora program_ids del payload.
      - postgraduate_admin: respeta program_ids; si vacío/None, scope global (NULL).

    Args:
      creator_id: ID del usuario otorgante.
      user_data: dict con first_name, last_name, mother_last_name, email, is_internal.
      permissions_to_delegate: lista de codenames a delegar.
      program_ids: lista opcional de program_id (solo relevante para postgraduate_admin).
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

    is_postgraduate = creator.has_permission('academic_periods.api.create')
    if is_postgraduate:
        effective_pids = list(program_ids) if program_ids else [None]
    else:
        coord_pids = [p.id for p in creator.coordinated_programs]
        if not coord_pids:
            raise PermissionError("No coordinas programas. No puedes crear servicio social.")
        effective_pids = coord_pids

    perm_objects = {}
    for codename in permissions_to_delegate:
        perm = Permission.query.filter_by(codename=codename, is_active=True).first()
        if not perm:
            raise PermissionError(f"Permiso '{codename}' no existe o está inactivo.")
        if perm.resource == 'permissions':
            raise PermissionError(f"No se puede delegar permisos del sistema de permisos: '{codename}'.")
        perm_objects[codename] = perm
        for pid in effective_pids:
            if not creator.has_permission(codename, program_id=pid):
                scope = f"programa {pid}" if pid else "ámbito global"
                raise PermissionError(f"No puedes delegar '{codename}' en {scope}.")

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

    # Notificar a admins en tiempo real
    try:
        from app.extensions import socketio
        socketio.emit(
            'admin_user:changed',
            {
                'action': 'created',
                'user_id': new_user.id,
                'role': 'social_service',
                'email': new_user.email,
                'full_name': f'{new_user.first_name} {new_user.last_name}',
            },
            room='role:postgraduate_admin',
        )
        socketio.emit(
            'admin_user:changed',
            {
                'action': 'created',
                'user_id': new_user.id,
                'role': 'social_service',
                'email': new_user.email,
                'full_name': f'{new_user.first_name} {new_user.last_name}',
            },
            room='role:coordinator',
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
    """
    role = Role.query.get(role_id)
    if not role:
        raise PermissionError("Rol no encontrado.")

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
