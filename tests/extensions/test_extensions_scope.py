# tests/extensions/test_extensions_scope.py
"""
HTTP-level program-scope tests for /api/v1/extensions.

Two defects are pinned here:

  * `GET /requests` and `GET /requests/for-review` used to return EVERY
    extension request in the institution — with the requester's name and
    e-mail — to any account holding `extensions.api.list_for_review`.
  * `PUT /requests/<id>/decision` accepted any request id from any program and
    accepted a `granted_until` in the PAST, which manufactures a document debt
    that blocks the victim's control-number assignment
    (`acceptance_service._check_document_debt`) leaving only `decided_by` as a
    trace.
"""

import unittest
from datetime import timedelta

from app import create_app, db
from app.models.archive import Archive
from app.models.extension_request import ExtensionRequest
from app.models.permission import Permission
from app.models.phase import Phase
from app.models.program_step import ProgramStep
from app.models.role_permission import RolePermission
from app.models.step import Step
from app.utils.datetime_utils import now_local

from tests.program_scope.conftest import (
    make_test_config, make_role, make_user, make_program, make_period,
    make_user_program,
)


def _perm(codename: str) -> Permission:
    parts = codename.split('.')
    p = Permission(
        codename=codename,
        display_name=codename,
        resource=parts[0],
        perm_type=parts[1],
        action='.'.join(parts[2:]),
    )
    db.session.add(p)
    db.session.flush()
    return p


def _grant(role, perm) -> None:
    db.session.add(RolePermission(role_id=role.id, permission_id=perm.id))
    db.session.flush()


class ExtensionsScopeTest(unittest.TestCase):

    def setUp(self):
        self.app = create_app(make_test_config())
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.role_student = make_role('student')
        self.role_coord = make_role('program_admin')
        self.role_boss = make_role('postgraduate_admin')

        for codename in ('extensions.api.list_for_review',
                         'extensions.api.decide'):
            perm = _perm(codename)
            _grant(self.role_coord, perm)
            _grant(self.role_boss, perm)
        _grant(self.role_boss, _perm('academic_periods.api.create'))

        self.coord_a = make_user(self.role_coord, suffix='_a')
        self.coord_b = make_user(self.role_coord, suffix='_b')
        self.boss = make_user(self.role_boss, suffix='_boss')
        self.student = make_user(self.role_student, suffix='_s')

        self.period = make_period()
        self.program_a = make_program(self.coord_a, slug='prog-a')
        self.program_b = make_program(self.coord_b, slug='prog-b')
        make_user_program(self.student, self.program_a, self.period)

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
        self.program_step = ProgramStep(
            sequence=1, program_id=self.program_a.id, step_id=step.id,
        )
        db.session.add(self.program_step)
        db.session.flush()

        self.request = ExtensionRequest(
            user_id=self.student.id,
            archive_id=self.archive.id,
            program_step_id=self.program_step.id,
            requested_by=self.student.id,
            reason='Motivo',
            requested_until=now_local() + timedelta(days=10),
        )
        db.session.add(self.request)
        db.session.commit()
        self.request_id = self.request.id

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self, user):
        res = self.client.post('/api/v1/auth/login', json={
            'username': user.username,
            'password': 'Test1234!',
        })
        with self.client.session_transaction() as sess:
            sess['_csrf_token'] = 'test-csrf-token'
        return res

    @property
    def _csrf(self):
        return {'X-CSRFToken': 'test-csrf-token'}

    def _ids(self, path):
        res = self.client.get(path)
        self.assertEqual(res.status_code, 200)
        return [item['id'] for item in res.get_json()['items']]

    # ── Listing ─────────────────────────────────────────────────────────────

    def test_other_program_requests_are_not_listed(self):
        self._login(self.coord_b)
        self.assertNotIn(self.request_id, self._ids('/api/v1/extensions/requests'))
        self.assertNotIn(
            self.request_id, self._ids('/api/v1/extensions/requests/for-review')
        )

    def test_own_program_requests_are_listed(self):
        self._login(self.coord_a)
        self.assertIn(self.request_id, self._ids('/api/v1/extensions/requests'))
        self.assertIn(
            self.request_id, self._ids('/api/v1/extensions/requests/for-review')
        )

    def test_global_admin_lists_every_program(self):
        self._login(self.boss)
        self.assertIn(self.request_id, self._ids('/api/v1/extensions/requests'))

    def test_student_still_lists_own_requests(self):
        self._login(self.student)
        self.assertIn(self.request_id, self._ids('/api/v1/extensions/requests'))

    # ── Decision ────────────────────────────────────────────────────────────

    def _decide(self, payload):
        return self.client.put(
            f'/api/v1/extensions/requests/{self.request_id}/decision',
            json=payload, headers=self._csrf,
        )

    def test_other_program_decision_is_404(self):
        self._login(self.coord_b)
        res = self._decide({
            'status': 'granted',
            'granted_until': (now_local() + timedelta(days=20)).isoformat(),
        })
        self.assertEqual(res.status_code, 404)
        self.assertEqual(
            db.session.get(ExtensionRequest, self.request_id).status, 'pending'
        )

    def test_granted_until_in_the_past_is_rejected(self):
        self._login(self.coord_a)
        res = self._decide({
            'status': 'granted',
            'granted_until': (now_local() - timedelta(days=1)).isoformat(),
        })
        self.assertEqual(res.status_code, 400)
        er = db.session.get(ExtensionRequest, self.request_id)
        self.assertEqual(er.status, 'pending')
        self.assertIsNone(er.granted_until)

    def test_own_program_decision_still_works(self):
        self._login(self.coord_a)
        res = self._decide({
            'status': 'granted',
            'granted_until': (now_local() + timedelta(days=20)).isoformat(),
        })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(
            db.session.get(ExtensionRequest, self.request_id).status, 'granted'
        )


if __name__ == '__main__':
    unittest.main()
