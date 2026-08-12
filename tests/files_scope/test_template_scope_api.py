# tests/files_scope/test_template_scope_api.py
"""
HTTP-level tests for GET /files/template/<filename>.

The route used to serve EVERY byte of TEMPLATE_STORE to any authenticated
account with no check at all. It now authorises from the database row that owns
the file (`Archive` or `DocumentTemplate`) through
`app/services/template_access_service.py`, and answers 404 for both "no owning
row" and "row out of your reach" so nobody can enumerate the store by name.

Covers:
  * published archive template (is_downloadable) → open to any authenticated user
  * internal archive template → only staff of that step / students of a program
    that includes it; everyone else 404
  * institutional DocumentTemplate → needs `admin_templates.api.list` AND the
    owning program in scope; a GLOBAL one needs global scope
  * a file in the store that no row references → 404 even for the jefe de posgrado
"""

import unittest

from flask import g

from app import create_app, db
from app.models.archive import Archive
from app.models.document_template import DocumentTemplate
from app.models.phase import Phase
from app.models.program_step import ProgramStep
from app.models.step import Step

from tests.program_scope.conftest import (
    make_test_config, make_role, make_user, make_program, make_period,
    make_user_program, grant_permission,
)


class TemplateDownloadScopeTest(unittest.TestCase):

    def setUp(self):
        self.app = create_app(make_test_config())
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        r_student = make_role('student')
        r_admin = make_role('program_admin')
        r_postgrad = make_role('postgraduate_admin')

        for role in (r_student, r_admin, r_postgrad):
            grant_permission(role, 'files.api.view_template')
        for role in (r_admin, r_postgrad):
            grant_permission(role, 'admin_templates.api.list')
        # Global scope marker: get_accessible_program_ids() returns None
        grant_permission(r_postgrad, 'academic_periods.api.create')

        self.coord_a = make_user(r_admin, suffix='_a')
        self.coord_b = make_user(r_admin, suffix='_b')
        self.postgrad = make_user(r_postgrad)
        self.student_a = make_user(r_student, suffix='_sa')
        self.student_b = make_user(r_student, suffix='_sb')

        self.program_a = make_program(self.coord_a, slug='prog-a')
        self.program_b = make_program(self.coord_b, slug='prog-b')
        period = make_period()
        make_user_program(self.student_a, self.program_a, period)
        make_user_program(self.student_b, self.program_b, period)

        phase = Phase(name='admission', description='Admisión')
        db.session.add(phase)
        db.session.flush()
        # Step used ONLY by program_b
        step = Step(name='Documentos DCI', description='Docs', phase_id=phase.id)
        db.session.add(step)
        db.session.flush()
        db.session.add(ProgramStep(sequence=1, program_id=self.program_b.id,
                                   step_id=step.id))
        db.session.flush()

        db.session.add(Archive(
            name='Formato publico', description='Descargable',
            file_path='uploads/templates/archives/1/formato_publico.pdf',
            step_id=step.id, is_downloadable=True,
        ))
        db.session.add(Archive(
            name='Formato interno', description='No descargable',
            file_path='uploads/templates/archives/2/formato_interno.pdf',
            step_id=step.id, is_downloadable=False,
        ))
        db.session.add(DocumentTemplate(
            program_id=self.program_b.id, document_type='acceptance_letter',
            name='Carta B', file_path='acceptance_letter/carta_b.docx',
            file_type='docx', is_active=True,
        ))
        db.session.add(DocumentTemplate(
            program_id=None, document_type='acceptance_letter',
            name='Carta global', file_path='acceptance_letter/carta_global.docx',
            file_type='docx', is_active=True,
        ))
        db.session.commit()

        store = self.app.config['TEMPLATE_STORE']
        store.mkdir(parents=True, exist_ok=True)
        for name in ('formato_publico.pdf', 'formato_interno.pdf',
                     'carta_b.docx', 'carta_global.docx', 'huerfano.pdf'):
            (store / name).write_bytes(b'%PDF-1.4 test')

        self.public_url = '/files/template/formato_publico.pdf'
        self.internal_url = '/files/template/formato_interno.pdf'
        self.program_template_url = '/files/template/carta_b.docx'
        self.global_template_url = '/files/template/carta_global.docx'
        self.orphan_url = '/files/template/huerfano.pdf'

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login_as(self, user):
        # A FRESH client per identity, AND `g._login_user` dropped: setUp keeps
        # one app context pushed for the whole test, so every request reuses its
        # `g` and Flask-Login would keep serving the FIRST user it resolved.
        # Without both, the loops below would pass without proving anything.
        self.client = self.app.test_client()
        g.pop('_login_user', None)
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(user.id)
            sess['_fresh'] = True

    # ── Published archive template: open to everybody authenticated ─────────
    def test_published_template_open_to_any_authenticated_user(self):
        for user in (self.student_a, self.student_b, self.coord_a,
                     self.coord_b, self.postgrad):
            self._login_as(user)
            self.assertEqual(self.client.get(self.public_url).status_code, 200,
                             msg=f'user {user.username}')

    def test_anonymous_is_redirected_to_login(self):
        self.assertIn(self.client.get(self.public_url).status_code, (302, 401))

    # ── Internal archive template: only the step's people ───────────────────
    def test_internal_template_served_to_student_of_that_step(self):
        self._login_as(self.student_b)
        self.assertEqual(self.client.get(self.internal_url).status_code, 200)

    def test_internal_template_served_to_coordinator_of_that_step(self):
        self._login_as(self.coord_b)
        self.assertEqual(self.client.get(self.internal_url).status_code, 200)

    def test_internal_template_served_to_postgraduate_admin(self):
        self._login_as(self.postgrad)
        self.assertEqual(self.client.get(self.internal_url).status_code, 200)

    def test_internal_template_hidden_from_other_program(self):
        for user in (self.student_a, self.coord_a):
            self._login_as(user)
            self.assertEqual(self.client.get(self.internal_url).status_code, 404,
                             msg=f'user {user.username}')

    # ── Institutional document templates ────────────────────────────────────
    def test_program_document_template_served_to_its_coordinator(self):
        self._login_as(self.coord_b)
        self.assertEqual(self.client.get(self.program_template_url).status_code, 200)

    def test_program_document_template_hidden_from_other_coordinator(self):
        self._login_as(self.coord_a)
        self.assertEqual(self.client.get(self.program_template_url).status_code, 404)

    def test_document_template_hidden_from_students(self):
        for user in (self.student_a, self.student_b):
            self._login_as(user)
            self.assertEqual(self.client.get(self.program_template_url).status_code, 404,
                             msg=f'user {user.username}')

    def test_global_document_template_needs_global_scope(self):
        self._login_as(self.postgrad)
        self.assertEqual(self.client.get(self.global_template_url).status_code, 200)
        for user in (self.coord_a, self.coord_b, self.student_b):
            self._login_as(user)
            self.assertEqual(self.client.get(self.global_template_url).status_code, 404,
                             msg=f'user {user.username}')

    # ── No owning row → the file does not exist for the application ─────────
    def test_orphan_file_is_404_even_for_postgraduate_admin(self):
        self._login_as(self.postgrad)
        self.assertEqual(self.client.get(self.orphan_url).status_code, 404)

    def test_traversal_attempt_is_not_served(self):
        self._login_as(self.postgrad)
        res = self.client.get('/files/template/../../config.py')
        self.assertIn(res.status_code, (400, 404))


if __name__ == '__main__':
    unittest.main()
