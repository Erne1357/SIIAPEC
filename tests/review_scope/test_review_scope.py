# tests/review_scope/test_review_scope.py
"""
HTTP-level tests for the document-review blueprints (R11).

The defect: `admin_review.*` is granted to social_service ROLE-WIDE, so an
account with ZERO delegations used to list every pending submission in the
institution, open the detail page (which publishes the file URL) and approve
or reject another program's documents.

Every handler must now answer with the target object's program, not with the
caller's codename. Out-of-scope submissions answer **404**, never 403: the
reviewer must not be able to tell "does not exist" from "not yours".

World:
    program_a — coordinated by coord_a, has submission_a (pending)
    program_b — coordinated by coord_b, has submission_b (pending)
    pg_admin  — global scope (academic_periods.api.create)
    social_none — social_service, no delegation at all
    social_a    — social_service, delegated `admin_review.api.decide` on A
"""

import unittest

from app import create_app, db
from app.models.archive import Archive
from app.models.phase import Phase
from app.models.program_step import ProgramStep
from app.models.step import Step
from app.models.submission import Submission
from app.utils.csrf import CSRF_HEADER, CSRF_SESSION_KEY

from tests.program_scope.conftest import (
    make_test_config, make_role, make_user, make_program, make_period,
    grant_permission, delegate_permission, make_user_program,
)

REVIEW_PERMS = (
    'admin_review.page.view',
    'admin_review.api.list_submissions',
    'admin_review.api.detail_submission',
    'admin_review.api.decide',
)


class ReviewScopeTestCase(unittest.TestCase):

    def setUp(self):
        self.app = create_app(make_test_config())
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.role_applicant = make_role('applicant')
        self.role_coord = make_role('program_admin')
        self.role_social = make_role('social_service')
        self.role_pg = make_role('postgraduate_admin')

        for codename in REVIEW_PERMS:
            grant_permission(self.role_coord, codename)
            grant_permission(self.role_social, codename)
            grant_permission(self.role_pg, codename)
        grant_permission(self.role_pg, 'academic_periods.api.create')

        self.coord_a = make_user(self.role_coord, '_a')
        self.coord_b = make_user(self.role_coord, '_b')
        self.pg_admin = make_user(self.role_pg)
        self.social_none = make_user(self.role_social, '_none')
        self.social_a = make_user(self.role_social, '_a')

        self.program_a = make_program(self.coord_a, 'prog-a')
        self.program_b = make_program(self.coord_b, 'prog-b')
        self.period = make_period()

        delegate_permission(
            self.social_a, 'admin_review.api.decide',
            self.program_a, self.coord_a,
        )

        phase = Phase(name='admission', description='Admisión')
        db.session.add(phase)
        db.session.flush()
        step = Step(name='Documentos', description='', phase_id=phase.id)
        db.session.add(step)
        db.session.flush()
        self.archive = Archive(
            name='Acta de nacimiento', description='', file_path=None,
            step_id=step.id,
        )
        db.session.add(self.archive)
        db.session.flush()

        self.ps_a = ProgramStep(sequence=1, program_id=self.program_a.id, step_id=step.id)
        self.ps_b = ProgramStep(sequence=1, program_id=self.program_b.id, step_id=step.id)
        db.session.add_all([self.ps_a, self.ps_b])
        db.session.flush()

        self.applicant_a = make_user(self.role_applicant, '_a')
        self.applicant_b = make_user(self.role_applicant, '_b')
        make_user_program(self.applicant_a, self.program_a, self.period, 'in_progress')
        make_user_program(self.applicant_b, self.program_b, self.period, 'in_progress')

        self.sub_a = Submission(
            file_path=f'{self.applicant_a.id}/admission/acta.pdf', status='pending',
            user_id=self.applicant_a.id, archive_id=self.archive.id,
            program_step_id=self.ps_a.id, semester=None,
        )
        self.sub_b = Submission(
            file_path=f'{self.applicant_b.id}/admission/acta.pdf', status='pending',
            user_id=self.applicant_b.id, archive_id=self.archive.id,
            program_step_id=self.ps_b.id, semester=None,
        )
        db.session.add_all([self.sub_a, self.sub_b])
        db.session.commit()

        self.sub_a_id = self.sub_a.id
        self.sub_b_id = self.sub_b.id
        # Las URLs y los payloads hablan UUID público; las comprobaciones
        # contra la BD siguen usando el id interno, que no cambió.
        self.sub_a_uuid = str(self.sub_a.uuid)
        self.sub_b_uuid = str(self.sub_b.uuid)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self, user):
        return self.client.post('/api/v1/auth/login', json={
            'username': user.username, 'password': 'Test1234!',
        })

    def _csrf_headers(self):
        """El guard CSRF de la app compara el header con el token de sesión."""
        with self.client.session_transaction() as sess:
            token = sess.get(CSRF_SESSION_KEY)
            if not token:
                token = 'test-csrf-token'
                sess[CSRF_SESSION_KEY] = token
        return {CSRF_HEADER: token}

    def _decide(self, sub_uuid, action='approve', comment=''):
        return self.client.post(
            f'/api/v1/admin/review/submissions/{sub_uuid}/decision',
            json={'action': action, 'comment': comment},
            headers=self._csrf_headers(),
        )

    # ── API: listado ────────────────────────────────────────────────────
    def test_list_is_scoped_to_the_callers_programs(self):
        self._login(self.coord_a)
        resp = self.client.get('/api/v1/admin/review/submissions')
        self.assertEqual(resp.status_code, 200)
        ids = {s['id'] for s in resp.get_json()['data']['submissions']}
        self.assertEqual(ids, {self.sub_a_uuid})

    def test_undelegated_social_service_lists_nothing(self):
        """R11: antes veía TODAS las entregas de la institución."""
        self._login(self.social_none)
        resp = self.client.get('/api/v1/admin/review/submissions')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()['data']['submissions'], [])

    def test_global_admin_still_sees_everything(self):
        self._login(self.pg_admin)
        resp = self.client.get('/api/v1/admin/review/submissions')
        ids = {s['id'] for s in resp.get_json()['data']['submissions']}
        self.assertEqual(ids, {self.sub_a_uuid, self.sub_b_uuid})

    def test_program_id_filter_cannot_widen_the_scope(self):
        self._login(self.coord_a)
        resp = self.client.get(
            f'/api/v1/admin/review/submissions?program_id={self.program_b.id}'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()['data']['submissions'], [])

    # ── API: detalle ────────────────────────────────────────────────────
    def test_detail_of_another_program_is_404_not_403(self):
        self._login(self.coord_a)
        resp = self.client.get(f'/api/v1/admin/review/submissions/{self.sub_b_uuid}')
        self.assertEqual(resp.status_code, 404)
        body = resp.get_json()
        self.assertIsNone(body['data'])
        self.assertEqual(body['error']['code'], 'NOT_FOUND')

    def test_detail_of_own_program_still_works(self):
        self._login(self.coord_a)
        resp = self.client.get(f'/api/v1/admin/review/submissions/{self.sub_a_uuid}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()['data']['submission']['id'], self.sub_a_uuid)

    # ── API: decisión ───────────────────────────────────────────────────
    def test_decide_on_another_program_is_denied_and_writes_nothing(self):
        self._login(self.coord_a)
        resp = self._decide(self.sub_b_uuid)
        self.assertEqual(resp.status_code, 404)
        db.session.expire_all()
        self.assertEqual(db.session.get(Submission, self.sub_b_id).status, 'pending')

    def test_undelegated_social_service_cannot_decide(self):
        self._login(self.social_none)
        resp = self._decide(self.sub_a_uuid)
        self.assertEqual(resp.status_code, 404)
        db.session.expire_all()
        self.assertEqual(db.session.get(Submission, self.sub_a_id).status, 'pending')

    def test_delegated_social_service_decides_inside_its_delegation(self):
        self._login(self.social_a)
        resp = self._decide(self.sub_a_uuid, comment='ok')
        self.assertEqual(resp.status_code, 200)
        db.session.expire_all()
        self.assertEqual(db.session.get(Submission, self.sub_a_id).status, 'approved')

    def test_delegated_social_service_cannot_decide_outside_its_delegation(self):
        self._login(self.social_a)
        resp = self._decide(self.sub_b_uuid, comment='ok')
        self.assertEqual(resp.status_code, 404)
        db.session.expire_all()
        self.assertEqual(db.session.get(Submission, self.sub_b_id).status, 'pending')

    # ── Página: listado ─────────────────────────────────────────────────
    def test_submissions_page_ignores_show_all(self):
        """El interruptor `show_all` ya no amplía el listado a otros programas."""
        self._login(self.coord_a)
        resp = self.client.get('/admin/review/submissions?show_all=true&phase=admission')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn(f'/admin/review/submission/{self.sub_a_uuid}"', html)
        self.assertNotIn(f'/admin/review/submission/{self.sub_b_uuid}"', html)

    def test_submissions_page_is_empty_for_undelegated_social_service(self):
        self._login(self.social_none)
        resp = self.client.get('/admin/review/submissions?show_all=true&phase=admission')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertNotIn(f'/admin/review/submission/{self.sub_a_uuid}"', html)
        self.assertNotIn(f'/admin/review/submission/{self.sub_b_uuid}"', html)

    def test_program_id_filter_outside_scope_is_rejected(self):
        self._login(self.coord_a)
        resp = self.client.get(
            f'/admin/review/submissions?program_id={self.program_b.id}',
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)

    # ── Página: el detalle publica la URL del archivo ────────────────────
    def test_detail_page_of_another_program_is_404(self):
        self._login(self.coord_a)
        resp = self.client.get(f'/admin/review/submission/{self.sub_b_uuid}')
        self.assertEqual(resp.status_code, 404)

    def test_detail_page_of_undelegated_social_service_is_404(self):
        self._login(self.social_none)
        resp = self.client.get(f'/admin/review/submission/{self.sub_a_uuid}')
        self.assertEqual(resp.status_code, 404)


if __name__ == '__main__':
    unittest.main()
