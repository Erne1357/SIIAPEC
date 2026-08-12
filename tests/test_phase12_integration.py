"""
Pruebas de integración end-to-end del sistema de permisos granulares (Fase 12).

Cubre los escenarios definidos en PLAN_PERMISOS_GRANULARES.md:

  - applicant no puede acceder a endpoints de coordinator (HTTP 403)
  - program_admin solo ve sus programas (scope de programa)
  - coordinador delega a social_service → social_service accede
  - coordinador intenta delegar permiso que no tiene → falla con error
  - jefe de posgrado agrega override a rol → se refleja en evaluación
  - RolePermissionAudit registra cada override
  - permiso con expires_at vencido → usuario pierde acceso
  - endpoint con program_id scope → global pasa, scope ajeno falla

Adicionalmente valida los componentes introducidos en Fase 11:

  - User.get_accessible_program_ids() — fuentes coordinator + delegation + global
  - DashboardService.build_dashboard_context() — dispatch por role.name
  - files.api.view_doc_others — control de acceso por permiso (no role hardcoded)
"""

import unittest
from datetime import timedelta

from app import create_app, db
from app.models.user import User, GLOBAL_SCOPE_PERMISSION
from app.models.role import Role
from app.models.permission import Permission
from app.models.program import Program
from app.models.role_permission import RolePermission, RolePermissionOverride
from app.models.role_permission_audit import RolePermissionAudit
from app.models.user_permission import UserPermission
from app.services.dashboard_service import DashboardService
from app.utils.datetime_utils import now_local
import app.services.permission_service as perm_svc


APP_CFG = {
    'TESTING': True,
    'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
    'SECRET_KEY': 'phase12-test-secret',
    'WTF_CSRF_ENABLED': False,
    'CELERY_BROKER_URL': 'memory://',
    'CELERY_RESULT_BACKEND': 'cache+memory://',
    'UPLOAD_FOLDER': '/tmp/siiap_test_uploads',
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _role(name):
    r = Role(name=name, description=f'Role: {name}')
    db.session.add(r)
    db.session.flush()
    return r


def _user(role, *, username=None, email=None):
    username = username or f'u_{role.name}'
    u = User(
        first_name='Test',
        last_name=role.name.title(),
        mother_last_name='',
        username=username,
        password='Test1234!',
        email=email or f'{username}@siiap.test',
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


def _program(name, coordinator, slug=None):
    p = Program(
        name=name,
        description=f'{name} description',
        coordinator_id=coordinator.id,
        slug=slug or name.lower().replace(' ', '-'),
    )
    db.session.add(p)
    db.session.flush()
    return p


# ---------------------------------------------------------------------------
# Base con seed mínimo de permisos granulares relevantes
# ---------------------------------------------------------------------------

class Phase12Base(unittest.TestCase):
    """
    Provee un entorno con los roles reales del sistema y los permisos clave
    usados en Fase 11/12. Cada test case puede construir sus propios usuarios.
    """

    def setUp(self):
        self.app = create_app(APP_CFG)
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        # Roles oficiales del sistema
        self.r_applicant   = _role('applicant')
        self.r_student     = _role('student')
        self.r_program     = _role('program_admin')
        self.r_postgrad    = _role('postgraduate_admin')
        self.r_social      = _role('social_service')

        # Permisos relevantes
        self.p_delegate      = _perm('permissions.api.delegate')
        self.p_revoke_deleg  = _perm('permissions.api.revoke_delegation')
        self.p_list_students = _perm('coordinator.api.list_students')
        self.p_list_appl     = _perm('acceptance.api.list_applicants')
        self.p_upload_doc    = _perm('acceptance.api.upload_doc')
        self.p_review_sub    = _perm('admin_review.api.list_submissions')
        self.p_ap_create     = _perm('academic_periods.api.create')
        self.p_doc_others    = _perm('files.api.view_doc_others')
        self.p_list_user_p   = _perm('permissions.api.list_user_permissions')
        # Permiso corriente que program_admin NO tiene por seed: sirve de
        # cobaya para los tests de override sin arrastrar el marcador de alcance
        # global, que `add_role_override` rechaza a propósito.
        self.p_ap_update     = _perm('academic_periods.api.update')

        # Mapeos base por rol (mínimo reflejando el seed real del sistema)
        for p in [self.p_delegate, self.p_revoke_deleg, self.p_list_students,
                  self.p_list_appl, self.p_upload_doc, self.p_review_sub,
                  self.p_doc_others, self.p_list_user_p]:
            _grant_role_perm(self.r_program, p)

        for p in [self.p_delegate, self.p_revoke_deleg, self.p_list_students,
                  self.p_list_appl, self.p_upload_doc, self.p_review_sub,
                  self.p_doc_others, self.p_ap_create, self.p_ap_update,
                  self.p_list_user_p]:
            _grant_role_perm(self.r_postgrad, p)

        # Usuarios comunes (cada test puede crear más)
        self.postgrad = _user(self.r_postgrad, username='jefe_posgrado')
        self.coord    = _user(self.r_program,  username='coord_alice')
        self.coord2   = _user(self.r_program,  username='coord_bob')
        self.social   = _user(self.r_social,   username='ss_carol')
        self.applicant = _user(self.r_applicant, username='aspirante_dan')
        self.student  = _user(self.r_student,  username='estudiante_eve')

        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    # Helper: simula sesión autenticada para el test_client
    def _login_as(self, user):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(user.id)
            sess['_fresh'] = True


# ---------------------------------------------------------------------------
# 1. HTTP endpoint authorization — applicant / admin / social_service
# ---------------------------------------------------------------------------

class EndpointAuthorizationTest(Phase12Base):
    """Decorador @permission_required aplicado a endpoints reales (HTTP 403/2xx)."""

    COORDINATOR_PROGRAMS_URL = '/api/v1/coordinator/programs'

    def test_applicant_forbidden_from_coordinator_endpoint(self):
        """applicant no tiene coordinator.api.list_students → 403."""
        self._login_as(self.applicant)
        resp = self.client.get(self.COORDINATOR_PROGRAMS_URL)
        self.assertEqual(resp.status_code, 403)
        payload = resp.get_json()
        self.assertEqual(payload['error']['code'], 'FORBIDDEN')

    def test_program_admin_allowed_on_coordinator_endpoint(self):
        """program_admin tiene coordinator.api.list_students → pasa el decorador."""
        self._login_as(self.coord)
        resp = self.client.get(self.COORDINATOR_PROGRAMS_URL)
        # El decorador pasa; la vista puede retornar 200 o 500 según estado de BD
        self.assertNotEqual(resp.status_code, 403)

    def test_social_service_forbidden_without_delegation(self):
        """social_service no tiene el permiso por rol base → 403."""
        self._login_as(self.social)
        resp = self.client.get(self.COORDINATOR_PROGRAMS_URL)
        self.assertEqual(resp.status_code, 403)

    def test_social_service_allowed_after_delegation(self):
        """Tras delegar coordinator.api.list_students a social → pasa el decorador."""
        with self.app.test_request_context('/'):
            perm_svc.delegate_permission(
                granter_id=self.postgrad.id,
                grantee_id=self.social.id,
                codename='coordinator.api.list_students',
            )

        self._login_as(self.social)
        resp = self.client.get(self.COORDINATOR_PROGRAMS_URL)
        self.assertNotEqual(resp.status_code, 403)

    def test_unauthenticated_receives_401(self):
        """Sin sesión, el decorador devuelve 401 (flask_login)."""
        resp = self.client.get(self.COORDINATOR_PROGRAMS_URL)
        # flask-login redirige; en /api/ se configura para 401 — aceptar ambos
        self.assertIn(resp.status_code, (401, 302))


# ---------------------------------------------------------------------------
# 2. Program-scope enforcement (@program_scope_required)
# ---------------------------------------------------------------------------

class ScopedEndpointTest(Phase12Base):
    """
    Endpoint con @permission_required(codename) + @program_scope_required(...).

    Usamos /api/v1/acceptance/program/<program_id>/stats:
      - el permiso de rol es institucional: NO abre programas ajenos, el
        alcance lo decide `get_accessible_program_ids()`
      - social_service con delegación scoped pasa solo para su program_id
      - social_service sin permiso es rechazado con 403
    """

    def _stats_url(self, program_id):
        return f'/api/v1/acceptance/program/{program_id}/stats'

    def setUp(self):
        super().setUp()
        # Crear dos programas: A coordinado por self.coord, B por self.coord2
        self.prog_a = _program('Maestria A', self.coord, slug='maestria-a')
        self.prog_b = _program('Maestria B', self.coord2, slug='maestria-b')
        db.session.commit()

    def test_role_permission_does_not_open_other_programs(self):
        """
        El permiso de rol da capacidad, no alcance: el coordinador de A entra a
        A y recibe 403 en B, que coordina otro.
        """
        self._login_as(self.coord)

        resp_a = self.client.get(self._stats_url(self.prog_a.id))
        self.assertNotEqual(resp_a.status_code, 403,
                            msg='El coordinador no pudo entrar a su propio programa')

        resp_b = self.client.get(self._stats_url(self.prog_b.id))
        self.assertEqual(resp_b.status_code, 403,
                         msg='El permiso de rol abrió un programa ajeno')

    def test_postgraduate_admin_reaches_every_program(self):
        """El jefe de posgrado conserva acceso a todos los programas."""
        self._login_as(self.postgrad)

        for pid in (self.prog_a.id, self.prog_b.id):
            resp = self.client.get(self._stats_url(pid))
            self.assertNotEqual(resp.status_code, 403,
                                msg=f'403 inesperado para program_id={pid}')

    def test_applicant_denied_on_scoped_endpoint(self):
        """applicant nunca tiene el permiso → 403 con y sin scope."""
        self._login_as(self.applicant)
        resp = self.client.get(self._stats_url(self.prog_a.id))
        self.assertEqual(resp.status_code, 403)

    def test_social_service_with_scoped_delegation(self):
        """
        Delegación scoped a program_a: pasa para program_a, rechaza para program_b.
        """
        with self.app.test_request_context('/'):
            perm_svc.delegate_permission(
                granter_id=self.postgrad.id,
                grantee_id=self.social.id,
                codename='acceptance.api.list_applicants',
                program_id=self.prog_a.id,
            )

        self._login_as(self.social)

        resp_a = self.client.get(self._stats_url(self.prog_a.id))
        self.assertNotEqual(resp_a.status_code, 403,
                            msg='Delegación scoped a prog_a no funcionó')

        resp_b = self.client.get(self._stats_url(self.prog_b.id))
        self.assertEqual(resp_b.status_code, 403,
                         msg='Delegación para prog_a concedió acceso a prog_b')

    def test_global_delegation_grants_no_program_scope(self):
        """
        Delegación sin program_id: da capacidad, no alcance. `NULL` no se lee
        como "todos los programas" — fail-closed —, así que la cuenta sigue sin
        poder abrir ningún programa concreto.
        """
        with self.app.test_request_context('/'):
            perm_svc.delegate_permission(
                granter_id=self.postgrad.id,
                grantee_id=self.social.id,
                codename='acceptance.api.list_applicants',
                # program_id=None → sólo el jefe de posgrado puede crearla
            )

        self._login_as(self.social)
        for pid in (self.prog_a.id, self.prog_b.id):
            resp = self.client.get(self._stats_url(pid))
            self.assertEqual(resp.status_code, 403,
                             msg=f'Delegación NULL dio alcance sobre pid={pid}')


# ---------------------------------------------------------------------------
# 3. Role override propagation + audit trail
# ---------------------------------------------------------------------------

class RoleOverridePropagationTest(Phase12Base):
    """Cobertura específica del plan: override de rol y auditoría."""

    def test_override_on_program_admin_reflects_in_evaluation(self):
        """
        Jefe de posgrado agrega override a rol 'program_admin' → todos los
        usuarios con ese rol reciben el permiso.

        Nota: evitamos una precondición con has_permission() porque la caché
        de permisos vive en flask.g (app-context) y persiste entre bloques
        test_request_context nested en el mismo app_context de setUp.
        """
        with self.app.test_request_context('/'):
            perm_svc.add_role_override(
                role_id=self.r_program.id,
                codename='academic_periods.api.update',
                performed_by=self.postgrad.id,
                reason='Permitir a coordinadores editar periodos',
            )

        with self.app.test_request_context('/'):
            self.assertTrue(self.coord.has_permission('academic_periods.api.update'))
            self.assertTrue(self.coord2.has_permission('academic_periods.api.update'))

    def test_revert_override_removes_permission_for_role(self):
        """Al revertir el override, el rol vuelve a no tenerlo."""
        with self.app.test_request_context('/'):
            perm_svc.add_role_override(
                role_id=self.r_program.id,
                codename='academic_periods.api.update',
                performed_by=self.postgrad.id,
            )
            perm_svc.revert_role_override(
                role_id=self.r_program.id,
                codename='academic_periods.api.update',
                performed_by=self.postgrad.id,
            )

        with self.app.test_request_context('/'):
            self.assertFalse(self.coord.has_permission('academic_periods.api.update'))

    def test_audit_entries_for_grant_and_revert(self):
        """Cada cambio de override deja una entrada en RolePermissionAudit."""
        with self.app.test_request_context('/'):
            perm_svc.add_role_override(
                role_id=self.r_program.id,
                codename='academic_periods.api.update',
                performed_by=self.postgrad.id,
                reason='grant test',
            )
            perm_svc.revert_role_override(
                role_id=self.r_program.id,
                codename='academic_periods.api.update',
                performed_by=self.postgrad.id,
            )

        actions = [a.action for a in RolePermissionAudit.query
                   .filter_by(role_id=self.r_program.id)
                   .order_by(RolePermissionAudit.id)
                   .all()]
        self.assertEqual(actions, ['grant', 'revert'])

    def test_global_scope_permission_cannot_be_granted_as_override(self):
        """
        El marcador de alcance global no se reparte por override de rol.

        Un override ES un grant de rol, y `has_global_program_scope()` lee el
        rol: concederlo a 'program_admin' convertiría a TODOS los coordinadores
        en administradores globales sin que aparezca ningún cambio de rol. La
        vía legítima es asignar el rol de jefe de posgrado.
        """
        with self.app.test_request_context('/'):
            with self.assertRaises(perm_svc.PermissionError):
                perm_svc.add_role_override(
                    role_id=self.r_program.id,
                    codename=GLOBAL_SCOPE_PERMISSION,
                    performed_by=self.postgrad.id,
                )

        # Nada se escribió: ni override ni entrada de auditoría.
        self.assertIsNone(
            RolePermissionOverride.query.filter_by(
                role_id=self.r_program.id,
                permission_id=self.p_ap_create.id,
            ).first()
        )
        self.assertEqual(
            RolePermissionAudit.query.filter_by(role_id=self.r_program.id).count(), 0
        )

        with self.app.test_request_context('/'):
            self.assertFalse(self.coord.has_global_program_scope())
            self.assertIsNotNone(self.coord.get_accessible_program_ids())


# ---------------------------------------------------------------------------
# 4. Expiry semantics (UserPermission.expires_at)
# ---------------------------------------------------------------------------

class PermissionExpiryTest(Phase12Base):
    """Permisos delegados con expires_at vencido no otorgan acceso."""

    def test_expired_delegation_denies_access(self):
        """
        Delegamos upload_doc con expires_at en el pasado; has_permission()
        debe retornar False.
        """
        past = now_local() - timedelta(hours=1)

        with self.app.test_request_context('/'):
            perm_svc.delegate_permission(
                granter_id=self.postgrad.id,
                grantee_id=self.social.id,
                codename='acceptance.api.upload_doc',
                expires_at=past,
            )

        with self.app.test_request_context('/'):
            self.assertFalse(
                self.social.has_permission('acceptance.api.upload_doc')
            )

    def test_future_expiry_still_grants_access(self):
        """expires_at en el futuro → permiso activo."""
        future = now_local() + timedelta(days=10)

        with self.app.test_request_context('/'):
            perm_svc.delegate_permission(
                granter_id=self.postgrad.id,
                grantee_id=self.social.id,
                codename='acceptance.api.upload_doc',
                expires_at=future,
            )

        with self.app.test_request_context('/'):
            self.assertTrue(
                self.social.has_permission('acceptance.api.upload_doc')
            )


# ---------------------------------------------------------------------------
# 5. get_accessible_program_ids() — helper de Fase 11
# ---------------------------------------------------------------------------

class AccessibleProgramIdsTest(Phase12Base):
    """
    Fuentes de programas accesibles:
      - coordinator_id en Program (coordinated_programs)
      - UserPermission con program_id (delegación scoped)
      - academic_periods.api.create (acceso global → None)
    """

    def setUp(self):
        super().setUp()
        self.prog_a = _program('Prog A', self.coord, slug='prog-a')
        self.prog_b = _program('Prog B', self.coord2, slug='prog-b')
        self.prog_c = _program('Prog C', self.coord, slug='prog-c')
        db.session.commit()

    def test_global_access_returns_none(self):
        """postgraduate_admin (con academic_periods.api.create) → None."""
        with self.app.test_request_context('/'):
            self.assertIsNone(self.postgrad.get_accessible_program_ids())

    def test_coordinator_returns_own_programs(self):
        """coord coordina A y C → {A.id, C.id}."""
        with self.app.test_request_context('/'):
            pids = self.coord.get_accessible_program_ids()
        self.assertEqual(pids, {self.prog_a.id, self.prog_c.id})

    def test_no_access_returns_empty_set(self):
        """social sin delegación y sin coordinación → set() vacío."""
        with self.app.test_request_context('/'):
            pids = self.social.get_accessible_program_ids()
        self.assertEqual(pids, set())

    def test_delegation_adds_program_to_accessible(self):
        """Delegar con program_id=X agrega X al conjunto."""
        with self.app.test_request_context('/'):
            perm_svc.delegate_permission(
                granter_id=self.postgrad.id,
                grantee_id=self.social.id,
                codename='coordinator.api.list_students',
                program_id=self.prog_b.id,
            )

        with self.app.test_request_context('/'):
            pids = self.social.get_accessible_program_ids()
        self.assertEqual(pids, {self.prog_b.id})

    def test_coordinator_plus_delegation_is_union(self):
        """
        coord coordina A y C; le delegamos acceso a B vía UserPermission.
        Resultado: {A, B, C}.
        """
        with self.app.test_request_context('/'):
            perm_svc.delegate_permission(
                granter_id=self.postgrad.id,
                grantee_id=self.coord.id,
                codename='coordinator.api.list_students',
                program_id=self.prog_b.id,
            )

        with self.app.test_request_context('/'):
            pids = self.coord.get_accessible_program_ids()
        self.assertEqual(pids, {self.prog_a.id, self.prog_b.id, self.prog_c.id})

    def test_global_permission_short_circuits_delegation_scope(self):
        """
        Si el ROL otorga academic_periods.api.create, get_accessible_program_ids()
        retorna None aunque el usuario tenga delegaciones scoped.

        El alcance global se concede por asignación de rol —aquí, un
        RolePermission de seed—, nunca por override: `add_role_override` rechaza
        ese codename (ver RoleOverridePropagationTest).
        """
        with self.app.test_request_context('/'):
            # Delegación scoped
            perm_svc.delegate_permission(
                granter_id=self.postgrad.id,
                grantee_id=self.coord.id,
                codename='coordinator.api.list_students',
                program_id=self.prog_b.id,
            )

        # El rol del usuario pasa a conceder el marcador de alcance global.
        _grant_role_perm(self.r_program, self.p_ap_create)
        db.session.commit()

        with self.app.test_request_context('/'):
            self.assertIsNone(self.coord.get_accessible_program_ids())


# ---------------------------------------------------------------------------
# 6. DashboardService.build_dashboard_context — extracción Fase 11
# ---------------------------------------------------------------------------

class DashboardDispatchTest(Phase12Base):
    """
    Verifica que build_dashboard_context retorne la estructura correcta
    según el role.name del usuario. El contexto completo es responsabilidad
    del template; aquí validamos discriminación + claves clave.
    """

    def test_applicant_variant_has_admission_keys(self):
        with self.app.test_request_context('/'):
            ctx = DashboardService.build_dashboard_context(self.applicant)
        # Sin user_program: retorna defaults
        for key in ('program', 'progress_segments', 'status_count',
                    'admission_status', 'user_program_id'):
            self.assertIn(key, ctx)
        self.assertIsNone(ctx['admission_status'])
        self.assertIsNone(ctx['user_program_id'])

    def test_student_variant_has_permanence_keys(self):
        with self.app.test_request_context('/'):
            ctx = DashboardService.build_dashboard_context(self.student)
        for key in ('program', 'up', 'permanence_data',
                    'pending_admission_docs', 'pending_permanence_docs'):
            self.assertIn(key, ctx)
        self.assertIsNone(ctx['program'])
        self.assertEqual(ctx['pending_admission_docs'], [])

    def test_program_admin_without_programs_returns_empty_shape(self):
        """program_admin sin programas → estructura con listas vacías."""
        with self.app.test_request_context('/'):
            ctx = DashboardService.build_dashboard_context(self.coord)
        self.assertEqual(ctx['coordinated_programs'], [])
        self.assertFalse(ctx['show_program_selector'])
        self.assertFalse(ctx['show_all_programs'])
        self.assertIsNone(ctx['metrics'])

    def test_program_admin_with_programs_picks_first_by_default(self):
        prog_a = _program('Prog A', self.coord, slug='pa')
        prog_b = _program('Prog B', self.coord, slug='pb')
        db.session.commit()

        with self.app.test_request_context('/'):
            ctx = DashboardService.build_dashboard_context(self.coord)
        self.assertEqual(ctx['selected_program_id'], prog_a.id)
        self.assertTrue(ctx['show_program_selector'])
        self.assertFalse(ctx['show_all_programs'])
        self.assertIsNotNone(ctx['metrics'])

    def test_program_admin_show_all_mode(self):
        _program('Prog A', self.coord, slug='pa2')
        _program('Prog B', self.coord, slug='pb2')
        db.session.commit()

        with self.app.test_request_context('/'):
            ctx = DashboardService.build_dashboard_context(
                self.coord, program_id_param='all'
            )
        self.assertEqual(ctx['selected_program_id'], 'all')
        self.assertTrue(ctx['show_all_programs'])
        self.assertIsNone(ctx['program'])

    def test_program_admin_specific_program_param(self):
        prog_a = _program('Prog A', self.coord, slug='pa3')
        prog_b = _program('Prog B', self.coord, slug='pb3')
        db.session.commit()

        with self.app.test_request_context('/'):
            ctx = DashboardService.build_dashboard_context(
                self.coord, program_id_param=str(prog_b.id)
            )
        self.assertEqual(ctx['selected_program_id'], prog_b.id)
        self.assertEqual(ctx['program'].id, prog_b.id)

    def test_postgraduate_admin_returns_metrics_only(self):
        with self.app.test_request_context('/'):
            ctx = DashboardService.build_dashboard_context(self.postgrad)
        self.assertIn('metrics', ctx)
        # No debe incluir keys de otras variantes
        self.assertNotIn('coordinated_programs', ctx)
        self.assertNotIn('admission_status', ctx)

    def test_unknown_role_returns_empty_context(self):
        """Rol desconocido (no en el switch) → dict vacío (no explota)."""
        other_role = _role('other_role_for_dispatch_test')
        other_user = _user(other_role, username='other_user_xyz')
        db.session.commit()

        with self.app.test_request_context('/'):
            ctx = DashboardService.build_dashboard_context(other_user)
        self.assertEqual(ctx, {})


# ---------------------------------------------------------------------------
# 7. files.api.view_doc_others — control de acceso por permiso (Fase 11)
# ---------------------------------------------------------------------------

class FileAccessPermissionTest(Phase12Base):
    """
    /files/doc/<user_id>/<phase>/<filename> (blueprint prefix /files, no /api):
      - user_id == self → pasa el check (la vista rompe al leer config;
        para aislar el check, usamos rutas que no hacen I/O real: fs missing)
      - user_id != self y sin permiso → 403 (vía AJAX) ó 302 (redirect page)
      - user_id != self y con permiso → pasa el check

    Como el blueprint no está bajo /api/, el 403 se maneja por el errorhandler
    de página (flash+redirect 302). Enviamos header X-Requested-With para
    forzar la rama JSON y recibir 403 real.
    """

    URL_TMPL = '/files/doc/{uid}/admission/test.pdf'
    AJAX = {'X-Requested-With': 'XMLHttpRequest'}

    def setUp(self):
        super().setUp()
        # Stubear config para evitar KeyError cuando el check pasa y la vista
        # intenta resolver la ruta del archivo (no nos interesa servir aquí).
        self.app.config['USER_DOCS_FOLDER'] = '/tmp/siiap_test_user_docs'

    def _grant_doc_others(self, user):
        up = UserPermission(
            user_id=user.id,
            permission_id=self.p_doc_others.id,
            granted_by=self.postgrad.id,
        )
        db.session.add(up)
        db.session.commit()

    def test_owner_passes_permission_check(self):
        """user_id == current_user.id → check pasa; archivo no existe → 404."""
        self._login_as(self.applicant)
        resp = self.client.get(self.URL_TMPL.format(uid=self.applicant.id),
                               headers=self.AJAX)
        self.assertNotEqual(resp.status_code, 403)
        self.assertEqual(resp.status_code, 404)

    def test_non_owner_without_permission_receives_403(self):
        """applicant intenta ver doc de otro usuario → 403."""
        self._login_as(self.applicant)
        resp = self.client.get(self.URL_TMPL.format(uid=self.social.id),
                               headers=self.AJAX)
        self.assertEqual(resp.status_code, 403)

    def test_non_owner_with_role_permission_passes(self):
        """program_admin (rol trae files.api.view_doc_others) pasa el check."""
        self._login_as(self.coord)
        resp = self.client.get(self.URL_TMPL.format(uid=self.applicant.id),
                               headers=self.AJAX)
        self.assertNotEqual(resp.status_code, 403)
        self.assertEqual(resp.status_code, 404)

    def test_social_service_without_delegation_denied(self):
        """social_service sin UserPermission ni rol que lo grant → 403."""
        self._login_as(self.social)
        resp = self.client.get(self.URL_TMPL.format(uid=self.applicant.id),
                               headers=self.AJAX)
        self.assertEqual(resp.status_code, 403)

    def test_social_service_with_delegation_passes(self):
        """
        social_service con UserPermission delegado de files.api.view_doc_others
        pasa el check (archivo no existe → 404).

        Nota: hacemos la delegación ANTES del primer request para evitar que
        la caché de permisos en flask.g (app-context de setUp) almacene False
        durante una llamada previa con estado sin delegar.
        """
        self._grant_doc_others(self.social)

        self._login_as(self.social)
        resp = self.client.get(self.URL_TMPL.format(uid=self.applicant.id),
                               headers=self.AJAX)
        self.assertNotEqual(resp.status_code, 403)
        self.assertEqual(resp.status_code, 404)


if __name__ == '__main__':
    unittest.main()
