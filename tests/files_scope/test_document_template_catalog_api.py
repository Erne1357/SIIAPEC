# tests/files_scope/test_document_template_catalog_api.py
"""
The document-template CATALOGUE must be scoped exactly like its BYTES.

`/files/template/<filename>` was closed in the previous pass through
`template_access_service.may_download_document_template`, but
`/api/admin/document-templates` kept listing EVERY `DocumentTemplate` in the
institution — name, description, `program_name` and `file_path` — behind
`admin_templates.api.list` alone, a codename every `program_admin` holds. An
index that names files you may not open is the same disclosure one indirection
away, and the service written for exactly this object sat unused by the two GET
routes.

The invariant these tests pin is not "the list is filtered somehow" but the
stronger one the next audit pass would check: for every template and every
caller, appearing in the catalogue and being served the bytes are the SAME
answer. `test_catalog_and_bytes_agree_exactly` is that assertion; the rest name
the individual cases so a regression says which one broke.

The GLOBAL template (`program_id IS NULL`) is the decided case: it is the
institution-wide fallback `DocumentTemplate.get_for_program` serves to every
program, so it belongs to the Jefatura and needs global scope — in the
catalogue exactly as in the download. A coordinator therefore sees an empty
catalogue when their program has no template of its own, and generating a
document still works, because `generate_document` resolves the template
server-side.

Writes are covered too: `program_id` on upload is chosen by the caller, and
PATCH/DELETE took an id with no scope check at all. The seeded roles give
`create`/`manage`/`delete` only to the Jefatura, but those codenames are
delegatable, and a delegated permission never widens its holder's scope.
"""

import io
import unittest

from flask import g

from app import create_app, db
from app.models.document_template import DocumentTemplate

from tests.program_scope.conftest import (
    make_test_config, make_role, make_user, make_program, grant_permission,
)


class DocumentTemplateCatalogScopeTest(unittest.TestCase):

    def setUp(self):
        self.app = create_app(make_test_config())
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        r_admin = make_role('program_admin')
        r_postgrad = make_role('postgraduate_admin')
        r_student = make_role('student')

        for role in (r_admin, r_postgrad, r_student):
            grant_permission(role, 'files.api.view_template')
        # The capability the catalogue asks for — held by every coordinator.
        for role in (r_admin, r_postgrad):
            grant_permission(role, 'admin_templates.api.list')
        # Management codenames granted to a LIMITED-scope role on purpose: this
        # is the delegation case, and it must not become authority over another
        # program's templates.
        for codename in ('admin_templates.api.create',
                         'admin_templates.api.manage',
                         'admin_templates.api.delete'):
            grant_permission(r_admin, codename)
            grant_permission(r_postgrad, codename)
        # Role-level global scope marker: get_accessible_program_ids() -> None
        grant_permission(r_postgrad, 'academic_periods.api.create')

        self.coord_a = make_user(r_admin, suffix='_a')
        self.coord_b = make_user(r_admin, suffix='_b')
        self.postgrad = make_user(r_postgrad)
        self.student = make_user(r_student)

        self.program_a = make_program(self.coord_a, slug='prog-a')
        self.program_b = make_program(self.coord_b, slug='prog-b')

        self.tpl_a = DocumentTemplate(
            program_id=self.program_a.id, document_type='acceptance_letter',
            name='Carta A', file_path='acceptance_letter/carta_a.docx',
            file_type='docx', is_active=True,
        )
        self.tpl_b = DocumentTemplate(
            program_id=self.program_b.id, document_type='acceptance_letter',
            name='Carta B', file_path='acceptance_letter/carta_b.docx',
            file_type='docx', is_active=True,
        )
        self.tpl_global = DocumentTemplate(
            program_id=None, document_type='acceptance_letter',
            name='Carta global', file_path='acceptance_letter/carta_global.docx',
            file_type='docx', is_active=True,
        )
        db.session.add_all([self.tpl_a, self.tpl_b, self.tpl_global])
        db.session.commit()

        store = self.app.config['TEMPLATE_STORE']
        store.mkdir(parents=True, exist_ok=True)
        for name in ('carta_a.docx', 'carta_b.docx', 'carta_global.docx'):
            (store / name).write_bytes(b'%PDF-1.4 test')

        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    # ── helpers ─────────────────────────────────────────────────────────────
    CSRF = 'test-csrf-token'

    def _login_as(self, user):
        # A FRESH client per identity AND `g._login_user` dropped: setUp keeps
        # one app context pushed for the whole test, so Flask-Login would
        # otherwise keep serving the first user it resolved. The CSRF token
        # goes into the session by hand because `app.utils.csrf` guards every
        # /api/ mutation independently of WTF_CSRF_ENABLED.
        self.client = self.app.test_client()
        g.pop('_login_user', None)
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(user.id)
            sess['_fresh'] = True
            sess['_csrf_token'] = self.CSRF

    @property
    def _csrf_headers(self):
        return {'X-CSRFToken': self.CSRF}

    def _listed_names(self, query=''):
        res = self.client.get(f'/api/admin/document-templates{query}')
        self.assertEqual(res.status_code, 200, res.data.decode('utf-8'))
        body = res.get_json()
        self.assertEqual(body['total'], len(body['data']))
        return {row['name'] for row in body['data']}

    # ── the invariant the next pass would check ─────────────────────────────
    def test_catalog_and_bytes_agree_exactly(self):
        """Listed ⇔ downloadable, for every caller and every template."""
        cases = {
            'Carta A': 'carta_a.docx',
            'Carta B': 'carta_b.docx',
            'Carta global': 'carta_global.docx',
        }
        for user in (self.coord_a, self.coord_b, self.postgrad):
            self._login_as(user)
            listed = self._listed_names()
            for name, filename in cases.items():
                bytes_res = self.client.get(f'/files/template/{filename}')
                self.assertEqual(
                    name in listed, bytes_res.status_code == 200,
                    msg=(f'{user.username} — «{name}»: listada={name in listed} '
                         f'bytes={bytes_res.status_code}'),
                )

    # ── listing ─────────────────────────────────────────────────────────────
    def test_coordinator_sees_only_their_own_programs_template(self):
        self._login_as(self.coord_a)
        self.assertEqual(self._listed_names(), {'Carta A'})
        self._login_as(self.coord_b)
        self.assertEqual(self._listed_names(), {'Carta B'})

    def test_global_template_needs_global_scope_to_be_listed(self):
        self._login_as(self.postgrad)
        self.assertEqual(
            self._listed_names(), {'Carta A', 'Carta B', 'Carta global'})

    def test_program_id_filter_cannot_widen_the_scope(self):
        """The query string chooses a SUBSET; it never grants reach."""
        self._login_as(self.coord_a)
        self.assertEqual(self._listed_names(f'?program_id={self.program_b.id}'),
                         set())

    def test_student_without_the_capability_is_403(self):
        self._login_as(self.student)
        res = self.client.get('/api/admin/document-templates')
        self.assertEqual(res.status_code, 403)

    # ── detail ──────────────────────────────────────────────────────────────
    def test_detail_of_another_programs_template_is_404(self):
        self._login_as(self.coord_a)
        res = self.client.get(f'/api/admin/document-templates/{self.tpl_b.uuid}')
        self.assertEqual(res.status_code, 404)

    def test_detail_of_the_global_template_needs_global_scope(self):
        self._login_as(self.coord_a)
        self.assertEqual(
            self.client.get(
                f'/api/admin/document-templates/{self.tpl_global.uuid}'
            ).status_code, 404)
        self._login_as(self.postgrad)
        self.assertEqual(
            self.client.get(
                f'/api/admin/document-templates/{self.tpl_global.uuid}'
            ).status_code, 200)

    def test_detail_of_own_template_still_works(self):
        self._login_as(self.coord_a)
        res = self.client.get(f'/api/admin/document-templates/{self.tpl_a.uuid}')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()['data']['name'], 'Carta A')

    # ── writes ──────────────────────────────────────────────────────────────
    def test_cannot_patch_another_programs_template(self):
        self._login_as(self.coord_a)
        res = self.client.patch(
            f'/api/admin/document-templates/{self.tpl_b.uuid}',
            json={'name': 'Secuestrada'},
            headers=self._csrf_headers,
        )
        self.assertEqual(res.status_code, 404)
        db.session.expire_all()
        self.assertEqual(db.session.get(DocumentTemplate, self.tpl_b.id).name,
                         'Carta B')

    def test_cannot_delete_the_global_template_without_global_scope(self):
        self._login_as(self.coord_a)
        res = self.client.delete(
            f'/api/admin/document-templates/{self.tpl_global.uuid}',
            headers=self._csrf_headers)
        self.assertEqual(res.status_code, 404)
        self.assertIsNotNone(db.session.get(DocumentTemplate, self.tpl_global.id))
        self.assertTrue(
            (self.app.config['TEMPLATE_STORE'] / 'carta_global.docx').exists())

    def test_cannot_upload_a_template_for_another_program(self):
        self._login_as(self.coord_a)
        res = self.client.post(
            '/api/admin/document-templates',
            data={
                'file': (io.BytesIO(b'<html></html>'), 'nueva.html'),
                'name': 'Intrusa',
                'document_type': 'acceptance_letter',
                'program_id': str(self.program_b.id),
            },
            content_type='multipart/form-data',
            headers=self._csrf_headers,
        )
        self.assertEqual(res.status_code, 403, res.data.decode('utf-8'))
        self.assertIsNone(
            DocumentTemplate.query.filter_by(name='Intrusa').first())

    def test_cannot_upload_a_global_template_without_global_scope(self):
        """Omitting program_id would make it the fallback of every program."""
        self._login_as(self.coord_a)
        res = self.client.post(
            '/api/admin/document-templates',
            data={
                'file': (io.BytesIO(b'<html></html>'), 'nueva.html'),
                'name': 'Global intrusa',
                'document_type': 'acceptance_letter',
            },
            content_type='multipart/form-data',
            headers=self._csrf_headers,
        )
        self.assertEqual(res.status_code, 403, res.data.decode('utf-8'))
        self.assertIsNone(
            DocumentTemplate.query.filter_by(name='Global intrusa').first())

    def test_coordinator_can_still_patch_their_own_template(self):
        self._login_as(self.coord_a)
        res = self.client.patch(
            f'/api/admin/document-templates/{self.tpl_a.uuid}',
            json={'name': 'Carta A (v2)'},
            headers=self._csrf_headers,
        )
        self.assertEqual(res.status_code, 200, res.data.decode('utf-8'))
        self.assertEqual(res.get_json()['data']['name'], 'Carta A (v2)')


if __name__ == '__main__':
    unittest.main()
