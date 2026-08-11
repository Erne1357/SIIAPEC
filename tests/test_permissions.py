# tests/test_permissions.py
"""
Tests unitarios para el sistema de permisos granulares.

Cubre:
  - Permisos heredados del rol (RolePermission / seed)
  - Permisos por override de rol (RolePermissionOverride)
  - Permisos directos de usuario (UserPermission / delegación)
  - Scope de programa en UserPermission
  - Permisos vencidos (expires_at)
  - Permisos revocados (is_active=False)
  - Caché por request (flask.g)
"""

import unittest
from datetime import timedelta
from app import create_app, db
from app.models.user import User
from app.models.role import Role
from app.models.permission import Permission
from app.models.role_permission import RolePermission, RolePermissionOverride
from app.models.user_permission import UserPermission
from app.utils.datetime_utils import now_local


# ---------------------------------------------------------------------------
# Helpers de setup
# ---------------------------------------------------------------------------

def _make_role(name):
    role = Role(name=name, description=f'Rol de prueba: {name}')
    db.session.add(role)
    db.session.flush()
    return role


def _make_user(role):
    user = User(
        first_name='Test',
        last_name='User',
        mother_last_name='',
        username=f'test_{role.name}',
        password='Test1234!',
        email=f'test_{role.name}@siiap.test',
        is_internal=False,
        role_id=role.id,
        must_change_password=False,
    )
    db.session.add(user)
    db.session.flush()
    return user


def _make_permission(codename):
    resource, perm_type, action = codename.split('.')
    perm = Permission(
        codename=codename,
        display_name=codename,
        resource=resource,
        perm_type=perm_type,
        action=action,
    )
    db.session.add(perm)
    db.session.flush()
    return perm


# ---------------------------------------------------------------------------
# Test case
# ---------------------------------------------------------------------------

class PermissionTestCase(unittest.TestCase):

    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            'SECRET_KEY': 'test-secret-key',
            'WTF_CSRF_ENABLED': False,
            # Celery no se usa en tests pero create_app lo inicializa
            'CELERY_BROKER_URL': 'memory://',
            'CELERY_RESULT_BACKEND': 'cache+memory://',
        })
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        # Roles base
        self.role_admin = _make_role('program_admin')
        self.role_social = _make_role('social_service')

        # Usuarios
        self.admin_user = _make_user(self.role_admin)
        self.social_user = _make_user(self.role_social)

        # Permisos de prueba
        self.perm_list = _make_permission('acceptance.api.list_applicants')
        self.perm_upload = _make_permission('acceptance.api.upload_doc')
        self.perm_decide = _make_permission('admin_review.api.decide')

        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    # ------------------------------------------------------------------
    # 1. Permiso vía rol (RolePermission seed)
    # ------------------------------------------------------------------

    def test_permission_from_role(self):
        """El usuario tiene el permiso si su rol lo incluye."""
        rp = RolePermission(role_id=self.role_admin.id, permission_id=self.perm_list.id)
        db.session.add(rp)
        db.session.commit()

        with self.app.test_request_context('/'):
            self.assertTrue(self.admin_user.has_permission('acceptance.api.list_applicants'))

    def test_no_permission_without_role_mapping(self):
        """El usuario NO tiene el permiso si no hay mapeo de rol."""
        with self.app.test_request_context('/'):
            self.assertFalse(self.admin_user.has_permission('acceptance.api.list_applicants'))

    def test_role_without_permission_is_denied(self):
        """social_service no tiene perm_decide porque no está en su rol."""
        with self.app.test_request_context('/'):
            self.assertFalse(self.social_user.has_permission('admin_review.api.decide'))

    # ------------------------------------------------------------------
    # 2. Permiso vía override de rol (RolePermissionOverride)
    # ------------------------------------------------------------------

    def test_permission_from_role_override(self):
        """Override activo agrega el permiso al rol."""
        override = RolePermissionOverride(
            role_id=self.role_social.id,
            permission_id=self.perm_decide.id,
        )
        db.session.add(override)
        db.session.commit()

        with self.app.test_request_context('/'):
            self.assertTrue(self.social_user.has_permission('admin_review.api.decide'))

    def test_revoked_override_denies_permission(self):
        """Override revocado (is_active=False) no otorga el permiso."""
        override = RolePermissionOverride(
            role_id=self.role_social.id,
            permission_id=self.perm_decide.id,
        )
        db.session.add(override)
        db.session.commit()
        override.revoke()
        db.session.commit()

        with self.app.test_request_context('/'):
            self.assertFalse(self.social_user.has_permission('admin_review.api.decide'))

    # ------------------------------------------------------------------
    # 3. Permiso directo (UserPermission / delegación)
    # ------------------------------------------------------------------

    def test_direct_permission_granted(self):
        """UserPermission activo otorga el permiso sin importar el rol."""
        up = UserPermission(
            user_id=self.social_user.id,
            permission_id=self.perm_upload.id,
            granted_by=self.admin_user.id,
        )
        db.session.add(up)
        db.session.commit()

        with self.app.test_request_context('/'):
            self.assertTrue(self.social_user.has_permission('acceptance.api.upload_doc'))

    def test_revoked_direct_permission_denied(self):
        """UserPermission revocado (is_active=False) NO otorga el permiso."""
        up = UserPermission(
            user_id=self.social_user.id,
            permission_id=self.perm_upload.id,
            granted_by=self.admin_user.id,
        )
        db.session.add(up)
        db.session.commit()
        up.revoke()
        db.session.commit()

        with self.app.test_request_context('/'):
            self.assertFalse(self.social_user.has_permission('acceptance.api.upload_doc'))

    def test_expired_direct_permission_denied(self):
        """UserPermission con expires_at en el pasado NO otorga el permiso."""
        past = now_local() - timedelta(hours=1)
        up = UserPermission(
            user_id=self.social_user.id,
            permission_id=self.perm_upload.id,
            granted_by=self.admin_user.id,
            expires_at=past,
        )
        db.session.add(up)
        db.session.commit()

        with self.app.test_request_context('/'):
            self.assertFalse(self.social_user.has_permission('acceptance.api.upload_doc'))

    def test_future_expiry_still_grants(self):
        """UserPermission con expires_at en el futuro SÍ otorga el permiso."""
        future = now_local() + timedelta(days=30)
        up = UserPermission(
            user_id=self.social_user.id,
            permission_id=self.perm_upload.id,
            granted_by=self.admin_user.id,
            expires_at=future,
        )
        db.session.add(up)
        db.session.commit()

        with self.app.test_request_context('/'):
            self.assertTrue(self.social_user.has_permission('acceptance.api.upload_doc'))

    # ------------------------------------------------------------------
    # 4. Scope de programa: NO lo responde has_permission()
    # ------------------------------------------------------------------
    #
    # has_permission() ya no acepta program_id: un permiso de rol es
    # institucional, así que filtrarlo por programa devolvía una respuesta
    # falsa. El alcance lo da get_accessible_program_ids() y las guardas de
    # program_scope_service.

    def test_has_permission_rejects_program_id_argument(self):
        """Pasar program_id es un error de programación, no un filtro."""
        with self.app.test_request_context('/'):
            with self.assertRaises(TypeError):
                self.social_user.has_permission(
                    'acceptance.api.upload_doc', program_id=99
                )

    def test_global_delegation_grants_capability_but_no_scope(self):
        """UserPermission con program_id NULL da capacidad, nunca alcance."""
        up = UserPermission(
            user_id=self.social_user.id,
            permission_id=self.perm_upload.id,
            granted_by=self.admin_user.id,
            program_id=None,
        )
        db.session.add(up)
        db.session.commit()

        with self.app.test_request_context('/'):
            self.assertTrue(self.social_user.has_permission('acceptance.api.upload_doc'))
            # NULL no significa "todos los programas" para el alcance.
            self.assertEqual(self.social_user.get_accessible_program_ids(), set())

    def test_scoped_delegation_defines_the_scope(self):
        """UserPermission con program_id=5 mete el 5 —y sólo el 5— en el alcance."""
        up = UserPermission(
            user_id=self.social_user.id,
            permission_id=self.perm_upload.id,
            granted_by=self.admin_user.id,
            program_id=5,
        )
        db.session.add(up)
        db.session.commit()

        with self.app.test_request_context('/'):
            self.assertTrue(self.social_user.has_permission('acceptance.api.upload_doc'))
            self.assertEqual(self.social_user.get_accessible_program_ids(), {5})

    # ------------------------------------------------------------------
    # 5. Caché por request (flask.g)
    # ------------------------------------------------------------------

    def test_cache_is_used_within_same_request(self):
        """has_permission devuelve el mismo resultado cacheado en la misma request."""
        rp = RolePermission(role_id=self.role_admin.id, permission_id=self.perm_list.id)
        db.session.add(rp)
        db.session.commit()

        with self.app.test_request_context('/'):
            result1 = self.admin_user.has_permission('acceptance.api.list_applicants')
            result2 = self.admin_user.has_permission('acceptance.api.list_applicants')
            self.assertEqual(result1, result2)
            self.assertTrue(result1)

    def test_cache_resets_between_requests(self):
        """El caché de flask.g no persiste entre requests distintas."""
        rp = RolePermission(role_id=self.role_admin.id, permission_id=self.perm_list.id)
        db.session.add(rp)
        db.session.commit()

        with self.app.test_request_context('/'):
            result_req1 = self.admin_user.has_permission('acceptance.api.list_applicants')

        with self.app.test_request_context('/'):
            result_req2 = self.admin_user.has_permission('acceptance.api.list_applicants')

        self.assertTrue(result_req1)
        self.assertTrue(result_req2)

    # ------------------------------------------------------------------
    # 6. Permiso inexistente
    # ------------------------------------------------------------------

    def test_nonexistent_codename_returns_false(self):
        """Un codename que no existe en BD devuelve False, no lanza excepción."""
        with self.app.test_request_context('/'):
            self.assertFalse(self.admin_user.has_permission('recurso.api.no_existe'))


if __name__ == '__main__':
    unittest.main()
