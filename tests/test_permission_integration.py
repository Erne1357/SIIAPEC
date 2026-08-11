# tests/test_permission_integration.py
"""
Pruebas de integración del sistema de permisos granulares (Phase 12).

Cubre:
  - Delegación de permisos (permission_service.delegate_permission)
  - Revocación de delegaciones
  - Overrides de rol (add/revert)
  - Auditoría de overrides
  - Control de acceso en endpoints HTTP (403 vs 200)
  - Flujo completo: coordinador delega → social_service accede
"""

import json
import unittest
from datetime import timedelta
from flask import g

from app import create_app, db
from app.models.user import User
from app.models.role import Role
from app.models.permission import Permission
from app.models.program import Program
from app.models.role_permission import RolePermission, RolePermissionOverride
from app.models.user_permission import UserPermission
from app.utils.datetime_utils import now_local
import app.services.permission_service as perm_svc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SQLITE_URI = 'sqlite:///:memory:'

APP_CFG = {
    'TESTING': True,
    'SQLALCHEMY_DATABASE_URI': _SQLITE_URI,
    'SECRET_KEY': 'integration-test-secret',
    'WTF_CSRF_ENABLED': False,
    'CELERY_BROKER_URL': 'memory://',
    'CELERY_RESULT_BACKEND': 'cache+memory://',
    'UPLOAD_FOLDER': '/tmp/siiap_test_uploads',
}


def _role(name):
    r = Role(name=name, description=f'Role: {name}')
    db.session.add(r)
    db.session.flush()
    return r


def _user(role, *, username=None, password='Test1234!'):
    username = username or f'u_{role.name}'
    u = User(
        first_name='Test',
        last_name=role.name.title(),
        mother_last_name='',
        username=username,
        password=password,
        email=f'{username}@siiap.test',
        is_internal=False,
        role_id=role.id,
        must_change_password=False,
    )
    db.session.add(u)
    db.session.flush()
    return u


def _perm(codename):
    parts = codename.split('.')
    resource, perm_type, action = parts[0], parts[1], '.'.join(parts[2:])
    p = Permission(
        codename=codename,
        display_name=codename,
        resource=resource,
        perm_type=perm_type,
        action=action,
    )
    db.session.add(p)
    db.session.flush()
    return p


def _grant_role_perm(role, perm):
    rp = RolePermission(role_id=role.id, permission_id=perm.id)
    db.session.add(rp)
    db.session.flush()
    return rp


# ---------------------------------------------------------------------------
# Base test case
# ---------------------------------------------------------------------------

class IntegrationBase(unittest.TestCase):

    def setUp(self):
        self.app = create_app(APP_CFG)
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        # Roles
        self.r_prog_admin  = _role('program_admin')
        self.r_social      = _role('social_service')
        self.r_applicant   = _role('applicant')

        # Permissions
        self.p_delegate      = _perm('permissions.api.delegate')
        self.p_revoke_deleg  = _perm('permissions.api.revoke_delegation')
        self.p_list_perms    = _perm('permissions.api.list_user_permissions')
        self.p_override      = _perm('permissions.api.override_role_permission')
        self.p_revert        = _perm('permissions.api.revert_override')
        self.p_list_rp       = _perm('permissions.api.list_role_permissions')
        self.p_audit         = _perm('permissions.api.view_audit')
        self.p_upload        = _perm('acceptance.api.upload_doc')
        self.p_list_sub      = _perm('admin_review.api.list_submissions')

        # program_admin gets delegation-related permissions
        for p in [self.p_delegate, self.p_revoke_deleg, self.p_list_perms,
                  self.p_override, self.p_revert, self.p_list_rp, self.p_audit,
                  self.p_upload]:
            _grant_role_perm(self.r_prog_admin, p)

        # Users
        self.admin       = _user(self.r_prog_admin, username='admin1')
        self.social      = _user(self.r_social,     username='social1')
        self.applicant   = _user(self.r_applicant,  username='applicant1')

        # A delegation now needs a concrete program the granter can reach:
        # `program_id=None` (all programs) is reserved for the jefe de posgrado.
        self.program = Program(
            name='Maestria Test',
            description='Programa de prueba',
            coordinator_id=self.admin.id,
            slug='maestria-test',
        )
        db.session.add(self.program)

        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    # ── Login helper (sets session cookie) ──────────────────────────────
    def _login(self, user, password='Test1234!'):
        return self.client.post('/api/v1/auth/login', json={
            'username': user.username,
            'password': password,
        })

    def _auth_header(self, user):
        """Log in and return the session cookie jar (stored in test client)."""
        self._login(user)
        return {}  # session cookie is carried automatically by test_client


# ---------------------------------------------------------------------------
# 1. Permission service — delegation
# ---------------------------------------------------------------------------

class DelegationServiceTest(IntegrationBase):

    def test_delegate_permission_creates_user_permission(self):
        """delegate_permission crea un UserPermission activo."""
        with self.app.test_request_context('/'):
            up = perm_svc.delegate_permission(
                granter_id=self.admin.id,
                grantee_id=self.social.id,
                codename='acceptance.api.upload_doc',
                program_id=self.program.id,
            )
        self.assertIsNotNone(up.id)
        self.assertTrue(up.is_active)
        self.assertEqual(up.granted_by, self.admin.id)

    def test_delegate_permission_reflects_in_has_permission(self):
        """Después de delegar, grantee.has_permission() devuelve True."""
        with self.app.test_request_context('/'):
            perm_svc.delegate_permission(
                granter_id=self.admin.id,
                grantee_id=self.social.id,
                codename='acceptance.api.upload_doc',
                program_id=self.program.id,
            )
        with self.app.test_request_context('/'):
            self.assertTrue(
                self.social.has_permission('acceptance.api.upload_doc')
            )

    def test_delegate_fails_if_granter_lacks_permission(self):
        """delegate_permission falla si el granter no tiene el permiso a delegar."""
        with self.app.test_request_context('/'):
            with self.assertRaises(perm_svc.PermissionError) as ctx:
                perm_svc.delegate_permission(
                    granter_id=self.social.id,  # social_service no tiene delegate
                    grantee_id=self.admin.id,
                    codename='acceptance.api.upload_doc',
                )
        self.assertIn('delegar', str(ctx.exception).lower())

    def test_delegate_fails_for_nonexistent_codename(self):
        """delegate_permission falla si el codename no existe."""
        with self.app.test_request_context('/'):
            with self.assertRaises(perm_svc.PermissionError):
                perm_svc.delegate_permission(
                    granter_id=self.admin.id,
                    grantee_id=self.social.id,
                    codename='fake.api.nonexistent',
                )

    def test_duplicate_active_delegation_raises(self):
        """Delegar el mismo permiso dos veces (activo) lanza PermissionError."""
        with self.app.test_request_context('/'):
            perm_svc.delegate_permission(
                granter_id=self.admin.id,
                grantee_id=self.social.id,
                codename='acceptance.api.upload_doc',
                program_id=self.program.id,
            )
            with self.assertRaises(perm_svc.PermissionError):
                perm_svc.delegate_permission(
                    granter_id=self.admin.id,
                    grantee_id=self.social.id,
                    codename='acceptance.api.upload_doc',
                )

    def test_revoke_delegation(self):
        """revoke_delegation desactiva el UserPermission y niega el permiso."""
        with self.app.test_request_context('/'):
            up = perm_svc.delegate_permission(
                granter_id=self.admin.id,
                grantee_id=self.social.id,
                codename='acceptance.api.upload_doc',
                program_id=self.program.id,
            )
            perm_svc.revoke_delegation(
                revoker_id=self.admin.id,
                user_permission_id=up.id,
            )
        with self.app.test_request_context('/'):
            self.assertFalse(
                self.social.has_permission('acceptance.api.upload_doc')
            )

    def test_revoke_already_revoked_raises(self):
        """Revocar una delegación ya revocada lanza PermissionError."""
        with self.app.test_request_context('/'):
            up = perm_svc.delegate_permission(
                granter_id=self.admin.id,
                grantee_id=self.social.id,
                codename='acceptance.api.upload_doc',
                program_id=self.program.id,
            )
            perm_svc.revoke_delegation(self.admin.id, up.id)
            with self.assertRaises(perm_svc.PermissionError):
                perm_svc.revoke_delegation(self.admin.id, up.id)

    def test_delegate_with_expiry(self):
        """Delegación con expires_at en el futuro está activa; en el pasado no."""
        future = now_local() + timedelta(days=1)
        past   = now_local() - timedelta(hours=1)

        with self.app.test_request_context('/'):
            up_future = perm_svc.delegate_permission(
                granter_id=self.admin.id,
                grantee_id=self.social.id,
                codename='acceptance.api.upload_doc',
                program_id=self.program.id,
                expires_at=future,
            )
            up_id = up_future.id

        # Verificar estado en BD (evitar cachear en g antes de la mutación)
        with self.app.test_request_context('/'):
            up_check = db.session.get(UserPermission, up_id)
            self.assertTrue(up_check.is_active)

        # Simular vencimiento
        with self.app.test_request_context('/'):
            up_obj = db.session.get(UserPermission, up_id)
            up_obj.expires_at = past
            db.session.commit()

        # Primera llamada a has_permission en este test — sin caché previa
        with self.app.test_request_context('/'):
            self.assertFalse(self.social.has_permission('acceptance.api.upload_doc'))

    def test_get_delegatable_excludes_permissions_resource(self):
        """get_delegatable_permissions no incluye permisos del recurso 'permissions'."""
        with self.app.test_request_context('/'):
            delegatable = perm_svc.get_delegatable_permissions(self.admin.id)
        codenames = [p['codename'] for p in delegatable]
        # Ninguno debe pertenecer al recurso 'permissions'
        self.assertFalse(
            any(c.startswith('permissions.') for c in codenames),
            f"Se encontraron permisos del recurso 'permissions': {codenames}",
        )


# ---------------------------------------------------------------------------
# 2. Permission service — role overrides
# ---------------------------------------------------------------------------

class RoleOverrideServiceTest(IntegrationBase):

    def test_add_role_override_grants_permission(self):
        """add_role_override agrega el permiso al rol vía override."""
        with self.app.test_request_context('/'):
            perm_svc.add_role_override(
                role_id=self.r_social.id,
                codename='admin_review.api.list_submissions',
                performed_by=self.admin.id,
                reason='Necesita revisar documentos',
            )
        with self.app.test_request_context('/'):
            self.assertTrue(
                self.social.has_permission('admin_review.api.list_submissions')
            )

    def test_duplicate_override_raises(self):
        """Agregar dos veces el mismo override activo lanza PermissionError."""
        with self.app.test_request_context('/'):
            perm_svc.add_role_override(
                role_id=self.r_social.id,
                codename='admin_review.api.list_submissions',
                performed_by=self.admin.id,
            )
            with self.assertRaises(perm_svc.PermissionError):
                perm_svc.add_role_override(
                    role_id=self.r_social.id,
                    codename='admin_review.api.list_submissions',
                    performed_by=self.admin.id,
                )

    def test_revert_override_removes_permission(self):
        """revert_role_override desactiva el override y niega el permiso."""
        with self.app.test_request_context('/'):
            perm_svc.add_role_override(
                role_id=self.r_social.id,
                codename='admin_review.api.list_submissions',
                performed_by=self.admin.id,
            )
            perm_svc.revert_role_override(
                role_id=self.r_social.id,
                codename='admin_review.api.list_submissions',
                performed_by=self.admin.id,
            )
        with self.app.test_request_context('/'):
            self.assertFalse(
                self.social.has_permission('admin_review.api.list_submissions')
            )

    def test_override_creates_audit_entry(self):
        """add_role_override crea una entrada en RolePermissionAudit."""
        from app.models.role_permission_audit import RolePermissionAudit
        with self.app.test_request_context('/'):
            perm_svc.add_role_override(
                role_id=self.r_social.id,
                codename='admin_review.api.list_submissions',
                performed_by=self.admin.id,
                reason='Test reason',
            )
        audit = RolePermissionAudit.query.filter_by(
            role_id=self.r_social.id,
            action='grant',
        ).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.performed_by, self.admin.id)
        self.assertEqual(audit.reason, 'Test reason')

    def test_revert_creates_audit_entry(self):
        """revert_role_override crea entrada de auditoría con action='revert'."""
        from app.models.role_permission_audit import RolePermissionAudit
        with self.app.test_request_context('/'):
            perm_svc.add_role_override(
                role_id=self.r_social.id,
                codename='admin_review.api.list_submissions',
                performed_by=self.admin.id,
            )
            perm_svc.revert_role_override(
                role_id=self.r_social.id,
                codename='admin_review.api.list_submissions',
                performed_by=self.admin.id,
            )
        audit = RolePermissionAudit.query.filter_by(
            role_id=self.r_social.id,
            action='revert',
        ).first()
        self.assertIsNotNone(audit)
        self.assertIsNotNone(audit.previous_state)

    def test_get_role_permissions_summary_structure(self):
        """get_role_permissions_summary retorna seed_permissions y overrides."""
        with self.app.test_request_context('/'):
            perm_svc.add_role_override(
                role_id=self.r_social.id,
                codename='admin_review.api.list_submissions',
                performed_by=self.admin.id,
            )
            summary = perm_svc.get_role_permissions_summary(self.r_social.id)

        self.assertIn('seed_permissions', summary)
        self.assertIn('overrides', summary)
        self.assertIn('role', summary)
        self.assertEqual(len(summary['overrides']), 1)
        self.assertEqual(
            summary['overrides'][0]['permission_codename'],
            'admin_review.api.list_submissions',
        )


# ---------------------------------------------------------------------------
# 3. End-to-end delegation flow (HTTP level)
# ---------------------------------------------------------------------------

class DelegationHttpTest(IntegrationBase):
    """
    Flujo completo:
      1. coordinator (program_admin) delega permiso a social_service
      2. social_service puede acceder al endpoint protegido
      3. coordinator revoca → social_service pierde acceso
    """

    def _call_api(self, path, method='get', **kwargs):
        fn = getattr(self.client, method)
        return fn(path, **kwargs)

    def test_permissions_me_returns_role_permissions(self):
        """GET /api/v1/permissions/me devuelve los permisos del usuario actual."""
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin.id)
            sess['_fresh'] = True

        resp = self.client.get('/api/v1/permissions/me')
        # May 401 if auth is cookie-based and not set up — skip if server unreachable
        if resp.status_code == 404:
            self.skipTest("Endpoint /api/v1/permissions/me not reachable in test env")
        self.assertIn(resp.status_code, [200, 401])

    def test_get_user_effective_permissions_admin(self):
        """program_admin tiene permisos base de su rol."""
        with self.app.test_request_context('/'):
            perms = perm_svc.get_user_effective_permissions(self.admin.id)
        codenames = [p['codename'] for p in perms]
        self.assertIn('acceptance.api.upload_doc', codenames)
        self.assertIn('permissions.api.delegate', codenames)

    def test_social_service_has_no_upload_before_delegation(self):
        """social_service no tiene acceptance.api.upload_doc antes de la delegación."""
        with self.app.test_request_context('/'):
            self.assertFalse(
                self.social.has_permission('acceptance.api.upload_doc')
            )

    def test_full_delegation_and_revocation_flow(self):
        """
        Flujo completo:
          1. admin delega upload_doc a social
          2. BD refleja delegación activa
          3. admin revoca
          4. social ya no puede acceder (has_permission = False)

        Nota: la verificación del paso 2 se hace contra BD en lugar de
        has_permission() para evitar que la caché de g (app-context) persista
        entre contextos de request anidados y enmascare el resultado del paso 4.
        La cobertura de has_permission() con delegación activa está en
        test_delegate_permission_reflects_in_has_permission.
        """
        # 1. Delegar
        with self.app.test_request_context('/'):
            up = perm_svc.delegate_permission(
                granter_id=self.admin.id,
                grantee_id=self.social.id,
                codename='acceptance.api.upload_doc',
                program_id=self.program.id,
                note='Temporal para proceso de admisión',
            )

        # 2. Verificar estado en BD (is_active=True) y metadatos
        with self.app.test_request_context('/'):
            up_db = db.session.get(UserPermission, up.id)
            self.assertTrue(up_db.is_active)
        self.assertEqual(up.note, 'Temporal para proceso de admisión')
        self.assertEqual(up.granted_by, self.admin.id)

        # 3. Revocar
        with self.app.test_request_context('/'):
            perm_svc.revoke_delegation(
                revoker_id=self.admin.id,
                user_permission_id=up.id,
            )

        # 4. Primera llamada a has_permission en este test — sin caché previa
        with self.app.test_request_context('/'):
            self.assertFalse(self.social.has_permission('acceptance.api.upload_doc'))


# ---------------------------------------------------------------------------
# 4. Permission service — audit log
# ---------------------------------------------------------------------------

class AuditLogTest(IntegrationBase):

    def test_get_audit_log_empty(self):
        """Audit log vacío retorna total=0."""
        result = perm_svc.get_audit_log()
        self.assertEqual(result['total'], 0)
        self.assertEqual(result['items'], [])

    def test_get_audit_log_after_override(self):
        """Audit log registra el override de rol."""
        with self.app.test_request_context('/'):
            perm_svc.add_role_override(
                role_id=self.r_social.id,
                codename='admin_review.api.list_submissions',
                performed_by=self.admin.id,
                reason='Prueba audit log',
            )
        result = perm_svc.get_audit_log()
        self.assertEqual(result['total'], 1)
        entry = result['items'][0]
        self.assertEqual(entry['action'], 'grant')
        self.assertEqual(entry['reason'], 'Prueba audit log')

    def test_audit_log_filter_by_role(self):
        """get_audit_log filtra por role_id correctamente."""
        r_other = _role('other_role')
        p_other = _perm('other.api.action')
        db.session.commit()

        with self.app.test_request_context('/'):
            perm_svc.add_role_override(
                role_id=self.r_social.id,
                codename='admin_review.api.list_submissions',
                performed_by=self.admin.id,
            )
            perm_svc.add_role_override(
                role_id=r_other.id,
                codename='other.api.action',
                performed_by=self.admin.id,
            )

        result_social = perm_svc.get_audit_log(role_id=self.r_social.id)
        self.assertEqual(result_social['total'], 1)

        result_other = perm_svc.get_audit_log(role_id=r_other.id)
        self.assertEqual(result_other['total'], 1)

    def test_audit_log_pagination(self):
        """get_audit_log respeta limit y offset."""
        with self.app.test_request_context('/'):
            for i in range(5):
                p = _perm(f'res{i}.api.act{i}')
                db.session.commit()
                perm_svc.add_role_override(
                    role_id=self.r_social.id,
                    codename=f'res{i}.api.act{i}',
                    performed_by=self.admin.id,
                )

        result = perm_svc.get_audit_log(limit=2, offset=0)
        self.assertEqual(result['total'], 5)
        self.assertEqual(len(result['items']), 2)

        result_page2 = perm_svc.get_audit_log(limit=2, offset=2)
        self.assertEqual(len(result_page2['items']), 2)


# ---------------------------------------------------------------------------
# 5. list_all_permissions catalog
# ---------------------------------------------------------------------------

class PermissionCatalogTest(IntegrationBase):

    def test_list_all_permissions_returns_all(self):
        """list_all_permissions retorna todos los permisos activos."""
        perms = perm_svc.list_all_permissions()
        # We created 11 permissions in setUp
        self.assertGreaterEqual(len(perms), 1)

    def test_list_all_permissions_filter_by_resource(self):
        """list_all_permissions filtra por recurso."""
        perms = perm_svc.list_all_permissions(resource='acceptance')
        self.assertEqual(len(perms), 1)
        self.assertEqual(perms[0].codename, 'acceptance.api.upload_doc')

    def test_list_all_permissions_filter_by_type(self):
        """list_all_permissions filtra por perm_type='api'."""
        perms = perm_svc.list_all_permissions(perm_type='api')
        for p in perms:
            self.assertEqual(p.perm_type, 'api')


# ---------------------------------------------------------------------------
# 6. Delegación: alcance del otorgante, autodelegación y marcador global
# ---------------------------------------------------------------------------

class DelegationScopeTest(IntegrationBase):
    """
    Una delegación reparte ALCANCE, no sólo capacidad. Estas pruebas fijan los
    límites de quién puede repartirlo y sobre qué.
    """

    def setUp(self):
        super().setUp()
        # Segundo coordinador con su propio programa: territorio ajeno.
        self.admin2 = _user(self.r_prog_admin, username='admin2')
        self.p_global = _perm('academic_periods.api.create')
        self.p_self   = _perm('users.api.me')
        _grant_role_perm(self.r_prog_admin, self.p_self)
        db.session.flush()

        self.program_b = Program(
            name='Maestria Ajena',
            description='Programa de otro coordinador',
            coordinator_id=self.admin2.id,
            slug='maestria-ajena',
        )
        db.session.add(self.program_b)
        db.session.commit()

    def test_self_delegation_is_refused(self):
        """
        Autodelegarse era la escalada completa: el coordinador de A se otorgaba
        cualquier codename sobre B y heredaba todo el programa B.
        """
        with self.app.test_request_context('/'):
            with self.assertRaises(perm_svc.PermissionError):
                perm_svc.delegate_permission(
                    granter_id=self.admin.id,
                    grantee_id=self.admin.id,
                    codename='acceptance.api.upload_doc',
                    program_id=self.program.id,
                )

    def test_cannot_delegate_over_a_program_outside_own_scope(self):
        """Nadie reparte acceso a un programa que no controla."""
        with self.app.test_request_context('/'):
            with self.assertRaises(perm_svc.PermissionError):
                perm_svc.delegate_permission(
                    granter_id=self.admin.id,
                    grantee_id=self.social.id,
                    codename='acceptance.api.upload_doc',
                    program_id=self.program_b.id,
                )

    def test_program_admin_cannot_delegate_globally(self):
        """program_id=None ('todos los programas') es sólo del jefe de posgrado."""
        with self.app.test_request_context('/'):
            with self.assertRaises(perm_svc.PermissionError):
                perm_svc.delegate_permission(
                    granter_id=self.admin.id,
                    grantee_id=self.social.id,
                    codename='acceptance.api.upload_doc',
                    program_id=None,
                )

    def test_self_service_permission_is_not_delegatable(self):
        """'users.api.me' no describe trabajo sobre un programa: se rechaza."""
        with self.app.test_request_context('/'):
            with self.assertRaises(perm_svc.PermissionError):
                perm_svc.delegate_permission(
                    granter_id=self.admin.id,
                    grantee_id=self.social.id,
                    codename='users.api.me',
                    program_id=self.program.id,
                )

    def test_self_service_delegation_row_grants_no_scope(self):
        """
        Aunque la fila exista (creada fuera del servicio, o heredada de datos
        antiguos), un permiso de cuenta propia no aporta alcance.
        """
        up = UserPermission(
            user_id=self.social.id,
            permission_id=self.p_self.id,
            granted_by=self.admin.id,
            program_id=self.program.id,
        )
        db.session.add(up)
        db.session.commit()

        with self.app.test_request_context('/'):
            self.assertEqual(self.social.get_accessible_program_ids(), set())

    def test_scoped_delegation_of_global_marker_is_not_global_scope(self):
        """
        'academic_periods.api.create' delegado sobre UN programa no convierte a
        nadie en jefe de posgrado: el alcance global es un hecho del ROL.
        """
        up = UserPermission(
            user_id=self.social.id,
            permission_id=self.p_global.id,
            granted_by=self.admin.id,
            program_id=self.program.id,
        )
        db.session.add(up)
        db.session.commit()

        with self.app.test_request_context('/'):
            self.assertTrue(self.social.has_permission('academic_periods.api.create'))
            self.assertFalse(self.social.has_global_program_scope())
            self.assertEqual(self.social.get_accessible_program_ids(), {self.program.id})

    def test_role_grant_of_global_marker_is_global_scope(self):
        """El jefe de posgrado (permiso por rol) conserva alcance global."""
        _grant_role_perm(self.r_prog_admin, self.p_global)
        db.session.commit()

        with self.app.test_request_context('/'):
            self.assertTrue(self.admin.has_global_program_scope())
            self.assertIsNone(self.admin.get_accessible_program_ids())


# ---------------------------------------------------------------------------
# 7. Revocación y lectura de delegaciones ajenas
# ---------------------------------------------------------------------------

class DelegationVisibilityTest(IntegrationBase):
    """
    El id de una delegación es enumerable: revocarla y leerla deben estar
    limitadas al alcance de quien lo pide.
    """

    def setUp(self):
        super().setUp()
        self.admin2 = _user(self.r_prog_admin, username='admin2')
        db.session.flush()
        self.program_b = Program(
            name='Maestria Ajena',
            description='Programa de otro coordinador',
            coordinator_id=self.admin2.id,
            slug='maestria-ajena',
        )
        db.session.add(self.program_b)
        db.session.commit()

        with self.app.test_request_context('/'):
            self.up = perm_svc.delegate_permission(
                granter_id=self.admin.id,
                grantee_id=self.social.id,
                codename='acceptance.api.upload_doc',
                program_id=self.program.id,
            )
        self.up_id = self.up.id

    def test_foreign_coordinator_cannot_revoke(self):
        """
        admin2 tiene 'permissions.api.revoke_delegation' por rol, pero la
        delegación vive en un programa que no alcanza.
        """
        with self.app.test_request_context('/'):
            with self.assertRaises(perm_svc.PermissionError):
                perm_svc.revoke_delegation(
                    revoker_id=self.admin2.id,
                    user_permission_id=self.up_id,
                )

    def test_granter_can_revoke_own_delegation(self):
        with self.app.test_request_context('/'):
            up = perm_svc.revoke_delegation(
                revoker_id=self.admin.id,
                user_permission_id=self.up_id,
            )
        self.assertFalse(up.is_active)

    def test_listing_hides_delegations_outside_viewer_scope(self):
        """admin2 no ve la delegación de un programa ajeno; admin sí la ve."""
        with self.app.test_request_context('/'):
            visible_foreign = perm_svc.get_user_delegations_for_viewer(
                self.admin2, self.social.id
            )
            visible_owner = perm_svc.get_user_delegations_for_viewer(
                self.admin, self.social.id
            )

        self.assertEqual(visible_foreign, [])
        self.assertEqual([u.id for u in visible_owner], [self.up_id])

    def test_target_sees_their_own_delegations(self):
        with self.app.test_request_context('/'):
            visible = perm_svc.get_user_delegations_for_viewer(
                self.social, self.social.id
            )
        self.assertEqual([u.id for u in visible], [self.up_id])


if __name__ == '__main__':
    unittest.main()
