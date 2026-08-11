# tests/permanence/test_permanence_scope_api.py
"""
HTTP-level tests for the permanence blueprint (R14 + R15).

R14 — the seed mistake: `permanence.api.confirm_enrollment` was mapped to the
STUDENT role, so any student could PATCH
`/api/v1/permanence/semester-enrollment/<id>/status {"status": "dropped"}` and
walk the id space marking the whole institution as baja definitiva. The grant is
gone from `02_role_permissions.sql` (+ an explicit DELETE, the seed is
INSERT-only), and the handler now checks the OBJECT too — the tests below pin
that second line of defence by re-granting the permission.

R15 — holding a codename never meant the object was yours: a program_admin of A
reviewed B's documents, archived B's deadline windows and flipped B's CONACyT
flags. Objects that belong to another program answer **404**, never 403: a
coordinator must not be able to tell "does not exist" from "not yours".
Programs themselves answer 403 — their existence is not a secret.

World:
    program_a — coordinated by coord_a, student_a enrolled, deadline_a, se_a
    program_b — coordinated by coord_b, student_b enrolled, deadline_b, se_b
    pg_admin  — global scope (academic_periods.api.create)
"""

import unittest
from datetime import date, timedelta

from app import create_app, db
from app.models.archive import Archive
from app.models.document_deadline import DocumentDeadline
from app.models.phase import Phase
from app.models.program_step import ProgramStep
from app.models.semester_enrollment import SemesterEnrollment
from app.models.step import Step
from app.models.user_program import UserProgram

from tests.program_scope.conftest import (
    make_test_config, make_role, make_user, make_program, make_period,
    grant_permission, make_user_program,
)

PERMANENCE_PERMS = (
    'permanence.api.list_students',
    'permanence.api.confirm_enrollment',
    'permanence.api.manage_deadlines',
    'permanence.api.review_doc',
    'permanence.api.manage_students',
    'permanence.api.advance_bulk',
)


class PermanenceScopeTestCase(unittest.TestCase):

    def setUp(self):
        self.app = create_app(make_test_config())
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.role_student = make_role('student')
        self.role_coord = make_role('program_admin')
        self.role_pg = make_role('postgraduate_admin')

        for codename in PERMANENCE_PERMS:
            grant_permission(self.role_coord, codename)
            grant_permission(self.role_pg, codename)
        grant_permission(self.role_pg, 'academic_periods.api.create')
        # R14: the legacy (wrong) seed row, kept here on purpose so the test
        # proves the HANDLER also refuses, not just the missing grant.
        grant_permission(self.role_student, 'permanence.api.confirm_enrollment')

        self.coord_a = make_user(self.role_coord, '_a')
        self.coord_b = make_user(self.role_coord, '_b')
        self.pg_admin = make_user(self.role_pg)
        self.student_a = make_user(self.role_student, '_a')
        self.student_b = make_user(self.role_student, '_b')

        self.program_a = make_program(self.coord_a, 'prog-a')
        self.program_b = make_program(self.coord_b, 'prog-b')
        self.period = make_period()

        self.up_a = make_user_program(self.student_a, self.program_a, self.period)
        self.up_b = make_user_program(self.student_b, self.program_b, self.period)

        phase = Phase(name='permanence', description='Permanencia')
        db.session.add(phase)
        db.session.flush()
        step = Step(name='Documentos', description='', phase_id=phase.id)
        db.session.add(step)
        db.session.flush()
        self.archive = Archive(
            name='Constancia', description='', file_path=None, step_id=step.id,
        )
        db.session.add(self.archive)
        db.session.flush()
        db.session.add_all([
            ProgramStep(sequence=1, program_id=self.program_a.id, step_id=step.id),
            ProgramStep(sequence=1, program_id=self.program_b.id, step_id=step.id),
        ])

        self.se_a = SemesterEnrollment(
            user_program_id=self.up_a.id, academic_period_id=self.period.id,
            semester_number=1, status='active', enrollment_confirmed=True,
        )
        self.se_b = SemesterEnrollment(
            user_program_id=self.up_b.id, academic_period_id=self.period.id,
            semester_number=1, status='active', enrollment_confirmed=True,
        )
        db.session.add_all([self.se_a, self.se_b])

        today = date.today()
        self.dl_a = DocumentDeadline(
            archive_id=self.archive.id, program_id=self.program_a.id,
            academic_period_id=self.period.id, sequence=1, label='Ventana A',
            closes_at=today + timedelta(days=10), is_open=True,
        )
        self.dl_b = DocumentDeadline(
            archive_id=self.archive.id, program_id=self.program_b.id,
            academic_period_id=self.period.id, sequence=1, label='Ventana B',
            closes_at=today + timedelta(days=10), is_open=True,
        )
        db.session.add_all([self.dl_a, self.dl_b])
        db.session.commit()

        self.se_a_id, self.se_b_id = self.se_a.id, self.se_b.id
        self.dl_a_id, self.dl_b_id = self.dl_a.id, self.dl_b.id
        self.up_a_id, self.up_b_id = self.up_a.id, self.up_b.id

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self, user):
        return self.client.post('/api/v1/auth/login', json={
            'username': user.username, 'password': 'Test1234!',
        })

    def _logout(self):
        self.client.get('/api/v1/auth/logout')

    def _csrf(self):
        """Inyecta un token CSRF conocido y devuelve la cabecera."""
        token = 'test-csrf-token'
        with self.client.session_transaction() as sess:
            sess['_csrf_token'] = token
        return {'X-CSRFToken': token}

    def _status(self, se_id):
        db.session.expire_all()
        return db.session.get(SemesterEnrollment, se_id).status

    # ── R14: baja definitiva ────────────────────────────────────────────
    def test_student_cannot_drop_anyone(self):
        """Incluso con el permiso mal sembrado, el handler responde 404."""
        self._login(self.student_a)
        resp = self.client.patch(
            f'/api/v1/permanence/semester-enrollment/{self.se_b_id}/status',
            json={'status': 'dropped'}, headers=self._csrf(),
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(self._status(self.se_b_id), 'active')

    def test_student_cannot_drop_even_their_own_enrollment(self):
        self._login(self.student_a)
        resp = self.client.patch(
            f'/api/v1/permanence/semester-enrollment/{self.se_a_id}/status',
            json={'status': 'dropped'}, headers=self._csrf(),
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(self._status(self.se_a_id), 'active')

    def test_coordinator_cannot_touch_another_programs_enrollment(self):
        self._login(self.coord_a)
        resp = self.client.patch(
            f'/api/v1/permanence/semester-enrollment/{self.se_b_id}/status',
            json={'status': 'dropped'}, headers=self._csrf(),
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.get_json()['error']['code'], 'NOT_FOUND')
        self.assertEqual(self._status(self.se_b_id), 'active')

    def test_coordinator_still_manages_their_own_program(self):
        self._login(self.coord_a)
        resp = self.client.patch(
            f'/api/v1/permanence/semester-enrollment/{self.se_a_id}/status',
            json={'status': 'completed'}, headers=self._csrf(),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._status(self.se_a_id), 'completed')

    def test_global_admin_reaches_every_program(self):
        self._login(self.pg_admin)
        resp = self.client.patch(
            f'/api/v1/permanence/semester-enrollment/{self.se_b_id}/status',
            json={'status': 'completed'}, headers=self._csrf(),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._status(self.se_b_id), 'completed')

    # ── R15: ventanas de entrega ────────────────────────────────────────
    def test_cannot_archive_another_programs_deadline(self):
        self._login(self.coord_a)
        resp = self.client.delete(
            f'/api/v1/permanence/deadlines/{self.dl_b_id}', headers=self._csrf(),
        )
        self.assertEqual(resp.status_code, 404)
        db.session.expire_all()
        self.assertFalse(db.session.get(DocumentDeadline, self.dl_b_id).is_archived)

    def test_cannot_toggle_another_programs_deadline(self):
        self._login(self.coord_a)
        resp = self.client.patch(
            f'/api/v1/permanence/deadlines/{self.dl_b_id}/toggle',
            json={'is_open': False}, headers=self._csrf(),
        )
        self.assertEqual(resp.status_code, 404)
        db.session.expire_all()
        self.assertTrue(db.session.get(DocumentDeadline, self.dl_b_id).is_open)

    def test_can_archive_own_deadline(self):
        self._login(self.coord_a)
        resp = self.client.delete(
            f'/api/v1/permanence/deadlines/{self.dl_a_id}', headers=self._csrf(),
        )
        self.assertEqual(resp.status_code, 200)
        db.session.expire_all()
        self.assertTrue(db.session.get(DocumentDeadline, self.dl_a_id).is_archived)

    # ── R15: listados por programa ──────────────────────────────────────
    def test_listing_another_program_is_403(self):
        """El programa existe públicamente: 403, no 404."""
        self._login(self.coord_a)
        resp = self.client.get(
            f'/api/v1/permanence/program/{self.program_b.id}/students'
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.get_json()['error']['code'], 'FORBIDDEN')

    def test_listing_own_program_still_works(self):
        self._login(self.coord_a)
        resp = self.client.get(
            f'/api/v1/permanence/program/{self.program_a.id}/students'
        )
        self.assertEqual(resp.status_code, 200)

    # ── R15: beca CONACyT ───────────────────────────────────────────────
    def test_cannot_flip_another_programs_conacyt_flag(self):
        self._login(self.coord_a)
        resp = self.client.patch(
            f'/api/v1/permanence/user-program/{self.up_b_id}/conacyt-scholarship',
            json={'value': True}, headers=self._csrf(),
        )
        self.assertEqual(resp.status_code, 404)
        db.session.expire_all()
        self.assertFalse(db.session.get(UserProgram, self.up_b_id).has_conacyt_scholarship)

    def test_can_flip_own_programs_conacyt_flag(self):
        self._login(self.coord_a)
        resp = self.client.patch(
            f'/api/v1/permanence/user-program/{self.up_a_id}/conacyt-scholarship',
            json={'value': True}, headers=self._csrf(),
        )
        self.assertEqual(resp.status_code, 200)
        db.session.expire_all()
        self.assertTrue(db.session.get(UserProgram, self.up_a_id).has_conacyt_scholarship)

    # ── R15: expediente de permanencia de otro estudiante ───────────────
    def test_cannot_read_another_programs_student_status(self):
        self._login(self.coord_a)
        resp = self.client.get(
            f'/api/v1/permanence/user-program/{self.up_b_id}/status'
        )
        self.assertEqual(resp.status_code, 404)

    def test_student_reads_their_own_status(self):
        self._login(self.student_a)
        resp = self.client.get(
            f'/api/v1/permanence/user-program/{self.up_a_id}/status'
        )
        self.assertEqual(resp.status_code, 200)

    def test_student_cannot_read_another_students_status(self):
        self._login(self.student_a)
        resp = self.client.get(
            f'/api/v1/permanence/user-program/{self.up_b_id}/status'
        )
        self.assertEqual(resp.status_code, 404)

    # ── R15: transición semestral ───────────────────────────────────────
    def test_global_transition_requires_global_scope(self):
        self._login(self.coord_a)
        resp = self.client.post(
            '/api/v1/permanence/transition/execute',
            json={
                'source_period_id': self.period.id,
                'target_period_id': self.period.id,
            },
            headers=self._csrf(),
        )
        self.assertEqual(resp.status_code, 403)

    def test_transition_preview_of_another_program_is_403(self):
        self._login(self.coord_a)
        resp = self.client.get(
            '/api/v1/permanence/transition/preview'
            f'?program_id={self.program_b.id}'
            f'&source_period_id={self.period.id}&target_period_id={self.period.id}'
        )
        self.assertEqual(resp.status_code, 403)


if __name__ == '__main__':
    unittest.main()
