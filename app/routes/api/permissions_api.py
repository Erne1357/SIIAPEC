# app/routes/api/permissions_api.py
"""
API de permisos granulares:
  - Phase 7: delegación coordinador → servicio social
  - Phase 8: overrides de rol (jefe de posgrado)
"""

from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from app.utils.permissions import permission_required
from app.models.role import Role
from app.models.user import User
from app.models.permission import Permission
import app.services.permission_service as svc

api_permissions = Blueprint('api_permissions', __name__, url_prefix='/api/v1/permissions')


# ===========================================================================
# Permisos propios del usuario
# ===========================================================================

@api_permissions.get('/me')
@login_required
def my_permissions():
    """Retorna los permisos efectivos del usuario autenticado."""
    program_id = request.args.get('program_id', type=int)
    perms = svc.get_user_effective_permissions(current_user.id, program_id)
    return jsonify({'data': perms})


@api_permissions.get('/delegatable')
@login_required
@permission_required('permissions.api.delegate')
def delegatable_permissions():
    """Permisos que el usuario actual puede delegar."""
    program_id = request.args.get('program_id', type=int)
    perms = svc.get_delegatable_permissions(current_user.id, program_id)
    return jsonify({'data': perms})


# ===========================================================================
# Permisos de un usuario específico (vista admin)
# ===========================================================================

@api_permissions.get('/user/<uuid:user_uuid>')
@login_required
@permission_required('permissions.api.list_user_permissions')
def user_permissions(user_uuid):
    """
    Delegaciones (activas e inactivas) de un usuario, recortadas al alcance
    del solicitante.

    Sin recorte, este endpoint dice de CUALQUIER cuenta qué permisos tiene y
    sobre qué programas: es el paso previo a elegir una delegación que revocar
    o a diseñar una escalada. El jefe de posgrado ve todo; un coordinador ve
    sólo lo que otorgó él y lo que cae en sus programas.

    Un UUID desconocido devuelve una lista VACÍA, exactamente igual que una
    cuenta sin delegaciones visibles para el llamador: el recorte ya hacía
    indistinguibles ambos casos y el cambio de identificador no lo rompe.
    """
    target = User.by_uuid(user_uuid)
    delegations = (
        svc.get_user_delegations_for_viewer(current_user, target.id)
        if target is not None else []
    )
    return jsonify({
        'data': [d.to_dict() for d in delegations],
        'error': None,
        'meta': {'count': len(delegations)},
    })


# ===========================================================================
# Delegación (Phase 7)
# ===========================================================================

@api_permissions.post('/delegate')
@login_required
@permission_required('permissions.api.delegate')
def delegate():
    """
    Delega un permiso a otro usuario.

    Body JSON:
      grantee_id   : str       — UUID público del usuario destino
      codename     : str       — permiso a delegar
      program_id   : int|null  — scope de programa (opcional)
      note         : str|null  — razón de la delegación
      expires_at   : str|null  — ISO 8601 timestamp de vencimiento
    """
    data = request.get_json(force=True) or {}
    # `grantee_id` llega como UUID público; el servicio sigue recibiendo el id
    # interno, como todo lo que se persiste.
    grantee = User.by_uuid(data.get('grantee_id'))
    grantee_id = grantee.id if grantee else None
    codename   = data.get('codename')

    if not grantee_id or not codename:
        return jsonify({'error': 'grantee_id y codename son obligatorios.'}), 400

    expires_at = None
    if data.get('expires_at'):
        from datetime import datetime
        try:
            expires_at = datetime.fromisoformat(data['expires_at'])
        except ValueError:
            return jsonify({'error': 'expires_at debe ser ISO 8601.'}), 400

    try:
        up = svc.delegate_permission(
            granter_id=current_user.id,
            grantee_id=grantee_id,
            codename=codename,
            program_id=data.get('program_id'),
            note=data.get('note'),
            expires_at=expires_at,
        )
        return jsonify({
            'data': up.to_dict(),
            'flash': [['Permiso delegado exitosamente.', 'success']],
        }), 201
    except svc.PermissionError as e:
        return jsonify({'error': str(e)}), 400


@api_permissions.delete('/delegation/<int:up_id>')
@login_required
@permission_required('permissions.api.revoke_delegation')
def revoke_delegation(up_id):
    """Revoca una delegación activa."""
    try:
        up = svc.revoke_delegation(revoker_id=current_user.id, user_permission_id=up_id)
        return jsonify({
            'data': up.to_dict(),
            'flash': [['Delegación revocada.', 'success']],
        })
    except svc.PermissionError as e:
        return jsonify({'error': str(e)}), 400


# ===========================================================================
# Overrides de rol (Phase 8)
# ===========================================================================

@api_permissions.get('/roles')
@login_required
@permission_required('permissions.api.list_role_permissions')
def list_roles():
    """Lista todos los roles del sistema."""
    roles = Role.query.order_by(Role.name).all()
    return jsonify({'data': [{'id': r.id, 'name': r.name} for r in roles]})


@api_permissions.get('/roles/<int:role_id>')
@login_required
@permission_required('permissions.api.list_role_permissions')
def role_permissions(role_id):
    """Retorna los permisos base (seed) y overrides de un rol."""
    summary = svc.get_role_permissions_summary(role_id)
    return jsonify({'data': summary})


@api_permissions.post('/roles/<int:role_id>/override')
@login_required
@permission_required('permissions.api.override_role_permission')
def add_override(role_id):
    """
    Agrega un override de permiso a un rol.

    Body JSON:
      codename : str  — codename del permiso a agregar
      reason   : str  — justificación (opcional)
    """
    data = request.get_json(force=True) or {}
    codename = data.get('codename')
    if not codename:
        return jsonify({'error': 'codename es obligatorio.'}), 400

    try:
        override = svc.add_role_override(
            role_id=role_id,
            codename=codename,
            performed_by=current_user.id,
            reason=data.get('reason'),
        )
        return jsonify({
            'data': override.to_dict(),
            'flash': [['Override agregado exitosamente.', 'success']],
        }), 201
    except svc.PermissionError as e:
        return jsonify({'error': str(e)}), 400


@api_permissions.delete('/roles/<int:role_id>/override/<codename>')
@login_required
@permission_required('permissions.api.revert_override')
def revert_override(role_id, codename):
    """Revierte (desactiva) un override activo de rol."""
    try:
        override = svc.revert_role_override(
            role_id=role_id,
            codename=codename,
            performed_by=current_user.id,
        )
        return jsonify({
            'data': override.to_dict(),
            'flash': [['Override revertido.', 'success']],
        })
    except svc.PermissionError as e:
        return jsonify({'error': str(e)}), 400


@api_permissions.get('/audit')
@login_required
@permission_required('permissions.api.view_audit')
def audit_log():
    """
    Historial de cambios de overrides de rol.

    Query params:
      role_id       : int  — filtrar por rol
      permission_id : int  — filtrar por permiso
      page          : int  — paginación
      per_page      : int  — elementos por página (max 100)
    """
    role_id       = request.args.get('role_id',       type=int)
    permission_id = request.args.get('permission_id', type=int)
    page     = request.args.get('page',     1,  type=int)
    per_page = min(request.args.get('per_page', 50, type=int), 100)
    offset   = (page - 1) * per_page

    result = svc.get_audit_log(
        role_id=role_id,
        permission_id=permission_id,
        limit=per_page,
        offset=offset,
    )
    return jsonify({
        'data':     result['items'],
        'meta':     {'total': result['total'], 'page': page, 'per_page': per_page},
    })


@api_permissions.get('/catalog')
@login_required
@permission_required('permissions.api.list_role_permissions')
def permission_catalog():
    """Lista el catálogo completo de permisos (para el selector de overrides)."""
    resource   = request.args.get('resource')
    perm_type  = request.args.get('type')
    perms = svc.list_all_permissions(resource=resource, perm_type=perm_type)
    return jsonify({'data': [p.to_dict() for p in perms]})
