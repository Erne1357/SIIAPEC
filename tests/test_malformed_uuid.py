"""
The new front door must not answer "does this identifier name something real?".

Three inputs have to be indistinguishable to a caller who is authenticated but
not entitled:

    malformed        'not-a-uuid'
    well-formed, unknown          a valid v7 that names no row
    well-formed, exists, not mine a real student outside the caller's scope

The first two are separated only by the URL converter, which the attacker can
evaluate locally without asking us, so that split is free. The third is the one
that matters: if it differs from the second, a stolen UUID can be checked for
validity without ever being usable.

An earlier version of this file issued the requests ANONYMOUSLY and only
printed the responses. That tested nothing: the well-formed request stopped at
@login_required with a 401 and never reached the view, so the property named in
the test could not have been observed either way.
"""

import unittest

from app import db
from app import create_app
from tests.student_record.conftest import (
    make_test_config, make_role, make_user, grant_permission,
)


PASSWORD = 'Test1234!'


class MalformedUUIDEnvelopeTest(unittest.TestCase):

    def setUp(self):
        self.app = create_app(make_test_config())
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        role_coord = make_role('program_admin')
        role_student = make_role('student')
        # The codename is necessary but never sufficient — the scope check runs
        # afterwards. That is exactly the pairing under test.
        grant_permission(role_coord, 'students.api.view_record')

        self.coordinator = make_user(role_coord, suffix='_coord')
        # A real row the coordinator has no UserProgram overlap with.
        self.outsider = make_user(role_student, suffix='_outsider')
        db.session.commit()

        self.client = self.app.test_client()
        self._login(self.coordinator)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self, user):
        resp = self.client.post(
            '/api/v1/auth/login',
            json={'username': user.username, 'password': PASSWORD},
        )
        self.assertEqual(resp.status_code, 200, 'el login de la prueba falló')

    def test_malformed_uuid_uses_the_project_envelope(self):
        resp = self.client.get('/api/v1/students/not-a-uuid/record')
        self.assertEqual(resp.status_code, 404)
        body = resp.get_json()
        self.assertIsNone(body['data'])
        self.assertEqual(body['error']['code'], 'NOT_FOUND')

    def test_unknown_and_out_of_scope_are_byte_identical(self):
        unknown = self.client.get(
            '/api/v1/students/00000000-0000-7000-8000-000000000000/record'
        )
        forbidden = self.client.get(
            f'/api/v1/students/{self.outsider.uuid}/record'
        )

        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(forbidden.status_code, 404)
        # Same status is not enough: a different message rebuilds the oracle
        # one layer down, which is how it survived several review passes.
        self.assertEqual(
            unknown.data, forbidden.data,
            'un UUID inexistente se distingue de uno fuera de alcance',
        )


if __name__ == '__main__':
    unittest.main()
