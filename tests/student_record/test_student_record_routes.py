# tests/student_record/test_student_record_routes.py
"""
HTTP-level tests for the Expediente Completo routes.

The point of this file is the DENIAL POLICY: an unknown user id and a user who
exists but is outside the caller's program scope must be indistinguishable.
Before, the service's `StudentNotFound` became a 404 and its `AccessDenied` a
403, so walking /api/v1/students/1..N and reading the status code enumerated
which user ids exist — for any holder of `students.api.view_record`, i.e. every
program_admin and every social_service account by role.

Covers GET /students/<id>/record (page), GET /api/v1/students/<id>/record,
GET /api/v1/students/<id>/record/pdf and PATCH .../personal-info.
"""

import unittest

from flask import g

from app import create_app, db
from app.utils.uuid7 import uuid7
from app.models.user import User

from tests.student_record.conftest import (
    make_test_config, make_role, make_user, make_program, make_period,
    grant_permission, make_user_program,
)


#: Identificador que no nombra ninguna fila. Con enteros bastaba "un número
#: más grande"; con UUIDv7 no existe tal cosa, así que se genera uno. Es la
#: pieza que sostiene toda la suite de no-divulgación: sin un identificador
#: ausente pero BIEN FORMADO no se puede comprobar que "no existe" y "no es
#: tuyo" responden lo mismo (un UUID malformado lo rechaza el enrutador antes
#: de llegar a la vista, que es otro caso distinto).
UNKNOWN_ID = str(uuid7())


class StudentRecordRoutesTest(unittest.TestCase):

    def setUp(self):
        self.app = create_app(make_test_config())
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        r_student = make_role('student')
        r_admin = make_role('program_admin')
        r_postgrad = make_role('postgraduate_admin')

        for role in (r_admin, r_postgrad):
            for codename in ('students.page.view_record',
                             'students.api.view_record',
                             'students.api.edit_personal_info',
                             'students.api.export_record_pdf'):
                grant_permission(role, codename)
        # Global scope marker: get_accessible_program_ids() returns None
        grant_permission(r_postgrad, 'academic_periods.api.create')

        self.coord_a = make_user(r_admin, suffix='_a')
        self.coord_b = make_user(r_admin, suffix='_b')
        self.postgrad = make_user(r_postgrad)
        self.student_b = make_user(r_student, suffix='_sb')

        self.program_a = make_program(self.coord_a, slug='prog-a')
        self.program_b = make_program(self.coord_b, slug='prog-b')
        period = make_period()
        make_user_program(self.student_b, self.program_b, period)
        db.session.commit()

        # Las URLs hablan UUID público; las comprobaciones contra la BD
        # siguen usando el id interno.
        student_b_uuid = str(self.student_b.uuid)
        self.page_url = f'/students/{student_b_uuid}/record'
        self.api_url = f'/api/v1/students/{student_b_uuid}/record'
        self.pdf_url = f'{self.api_url}/pdf'
        self.patch_url = f'/api/v1/students/{student_b_uuid}/personal-info'

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login_as(self, user):
        # Fresh client + `g._login_user` dropped: setUp keeps one app context
        # for the whole test, so Flask-Login would otherwise keep serving the
        # first user it resolved.
        self.client = self.app.test_client()
        g.pop('_login_user', None)
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(user.id)
            sess['_fresh'] = True
            sess['_csrf_token'] = 'test-csrf-token'
        self.csrf_headers = {'X-CSRFToken': 'test-csrf-token'}

    # ── The record is still served to the people who should see it ─────────
    def test_coordinator_of_the_program_gets_the_record(self):
        self._login_as(self.coord_b)
        self.assertEqual(self.client.get(self.api_url).status_code, 200)
        self.assertEqual(self.client.get(self.page_url).status_code, 200)

    def test_postgraduate_admin_gets_any_record(self):
        self._login_as(self.postgrad)
        self.assertEqual(self.client.get(self.api_url).status_code, 200)

    # ── Denials are indistinguishable ──────────────────────────────────────
    def test_out_of_scope_coordinator_gets_the_same_404_as_unknown_id(self):
        self._login_as(self.coord_a)
        denied = self.client.get(self.api_url)
        unknown = self.client.get(f'/api/v1/students/{UNKNOWN_ID}/record')
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(denied.get_json()['error'], unknown.get_json()['error'])

    def test_page_answers_404_for_both_causes(self):
        self._login_as(self.coord_a)
        self.assertEqual(self.client.get(self.page_url).status_code, 404)
        self.assertEqual(
            self.client.get(f'/students/{UNKNOWN_ID}/record').status_code, 404)

    def test_pdf_export_answers_404_for_both_causes(self):
        self._login_as(self.coord_a)
        denied = self.client.get(self.pdf_url)
        unknown = self.client.get(f'/api/v1/students/{UNKNOWN_ID}/record/pdf')
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(denied.get_json()['error'], unknown.get_json()['error'])

    def test_patch_answers_404_for_both_causes_and_writes_nothing(self):
        self._login_as(self.coord_a)
        denied = self.client.patch(self.patch_url, json={'curp': 'HACK800101HJCXXX01'},
                                   headers=self.csrf_headers)
        unknown = self.client.patch(
            f'/api/v1/students/{UNKNOWN_ID}/personal-info',
            json={'curp': 'HACK800101HJCXXX01'}, headers=self.csrf_headers)
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(denied.get_json()['error'], unknown.get_json()['error'])

        db.session.expire_all()
        victim = db.session.get(User, self.student_b.id)
        self.assertIsNone(victim.curp)

    def test_no_response_says_forbidden(self):
        """No route may leak the 403/404 distinction through the error code."""
        self._login_as(self.coord_a)
        for res in (self.client.get(self.api_url),
                    self.client.get(self.pdf_url),
                    self.client.patch(self.patch_url, json={'phone': '000'},
                                      headers=self.csrf_headers)):
            self.assertEqual(res.status_code, 404)
            self.assertEqual(res.get_json()['error']['code'], 'NOT_FOUND')


if __name__ == '__main__':
    unittest.main()
