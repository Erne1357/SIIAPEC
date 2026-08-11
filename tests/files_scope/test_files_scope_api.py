# tests/files_scope/test_files_scope_api.py
"""
HTTP-level tests for the program scope of served files and profile photos.

Covers:
  * GET /files/doc/<uid>/<phase>/<file>
      - the owner downloads their own document
      - the coordinator of the owner's program downloads it
      - a coordinator of ANOTHER program gets 404 (not 403: a 403 would confirm
        which documents a student of another program uploaded)
      - a caller without `files.api.view_doc_others` still gets 403 (unchanged)
      - a path no database row references is 404 even for the owner
  * GET /files/avatar/<uid>/<file>
      - self and in-scope staff get the photo
      - out-of-scope staff and other students get 404 (no enumeration)
  * POST /api/v1/users/<uid>/photo and .../photo/enable-change
      - out-of-scope coordinator gets the standard 403 JSON envelope and the
        victim's avatar is left untouched
"""

import io
import unittest

from PIL import Image

from app import create_app, db
from app.models.archive import Archive
from app.models.phase import Phase
from app.models.program_step import ProgramStep
from app.models.step import Step
from app.models.submission import Submission

from tests.program_scope.conftest import (
    make_test_config, make_role, make_user, make_program, make_period,
    make_user_program, grant_permission,
)


def _jpeg_bytes(size=(64, 64)):
    buf = io.BytesIO()
    Image.new('RGB', size, (10, 20, 30)).save(buf, format='JPEG')
    return buf.getvalue()


class FilesScopeBase(unittest.TestCase):

    def setUp(self):
        self.app = create_app(make_test_config())
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        r_student = make_role('student')
        r_admin = make_role('program_admin')
        r_postgrad = make_role('postgraduate_admin')

        for codename in ('files.api.view_doc_others',
                         'profile.api.upload_photo_for_student',
                         'profile.api.enable_photo_change'):
            grant_permission(r_admin, codename)
            grant_permission(r_postgrad, codename)
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

        # Minimal admission document chain for student_b
        phase = Phase(name='admission', description='Admisión')
        db.session.add(phase)
        db.session.flush()
        step = Step(name='Documentos', description='Docs', phase_id=phase.id)
        db.session.add(step)
        db.session.flush()
        archive = Archive(name='Acta', description='Acta de nacimiento',
                          file_path=None, step_id=step.id)
        db.session.add(archive)
        pstep = ProgramStep(sequence=1, program_id=self.program_b.id,
                            step_id=step.id)
        db.session.add(pstep)
        db.session.flush()

        self.doc_rel = f'{self.student_b.id}/admission/acta.pdf'
        db.session.add(Submission(
            file_path=self.doc_rel, status='pending',
            user_id=self.student_b.id, archive_id=archive.id,
            program_step_id=pstep.id, semester=None,
        ))
        db.session.commit()

        # Physical files
        docs_dir = self.app.config['USER_DOCS_FOLDER'] / str(self.student_b.id) / 'admission'
        docs_dir.mkdir(parents=True, exist_ok=True)
        (docs_dir / 'acta.pdf').write_bytes(b'%PDF-1.4 test')
        (docs_dir / 'huerfano.pdf').write_bytes(b'%PDF-1.4 orphan')

        avatar_dir = self.app.config['AVATAR_FOLDER'] / str(self.student_b.id)
        avatar_dir.mkdir(parents=True, exist_ok=True)
        (avatar_dir / 'avatar.jpg').write_bytes(_jpeg_bytes())
        self.student_b.avatar = 'avatar.jpg'
        db.session.commit()

        self.doc_url = f'/files/doc/{self.doc_rel}'
        self.orphan_url = f'/files/doc/{self.student_b.id}/admission/huerfano.pdf'
        self.avatar_url = f'/files/avatar/{self.student_b.id}/avatar.jpg'

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login_as(self, user):
        """Authenticated session + a valid CSRF token (mutations need both)."""
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(user.id)
            sess['_fresh'] = True
            sess['_csrf_token'] = 'test-csrf-token'
        self.csrf_headers = {'X-CSRFToken': 'test-csrf-token'}


class UserDocScopeTest(FilesScopeBase):

    def test_owner_downloads_own_document(self):
        self._login_as(self.student_b)
        self.assertEqual(self.client.get(self.doc_url).status_code, 200)

    def test_coordinator_of_the_program_downloads_it(self):
        self._login_as(self.coord_b)
        self.assertEqual(self.client.get(self.doc_url).status_code, 200)

    def test_postgraduate_admin_downloads_any_document(self):
        self._login_as(self.postgrad)
        self.assertEqual(self.client.get(self.doc_url).status_code, 200)

    def test_coordinator_of_another_program_gets_404(self):
        """Documento fuera del alcance: 404, no 403 (no revelar existencia)."""
        self._login_as(self.coord_a)
        self.assertEqual(self.client.get(self.doc_url).status_code, 404)

    def test_student_without_permission_still_gets_403(self):
        """Sin `files.api.view_doc_others` la puerta gruesa sigue dando 403."""
        self._login_as(self.student_a)
        resp = self.client.get(
            self.doc_url, headers={'X-Requested-With': 'XMLHttpRequest'})
        self.assertEqual(resp.status_code, 403)

    def test_orphan_file_without_db_row_is_404_even_for_owner(self):
        self._login_as(self.student_b)
        self.assertEqual(self.client.get(self.orphan_url).status_code, 404)


class AvatarScopeTest(FilesScopeBase):

    def test_owner_sees_own_avatar(self):
        self._login_as(self.student_b)
        self.assertEqual(self.client.get(self.avatar_url).status_code, 200)

    def test_coordinator_of_the_program_sees_the_avatar(self):
        self._login_as(self.coord_b)
        self.assertEqual(self.client.get(self.avatar_url).status_code, 200)

    def test_coordinator_of_another_program_gets_404(self):
        self._login_as(self.coord_a)
        self.assertEqual(self.client.get(self.avatar_url).status_code, 404)

    def test_student_of_another_program_cannot_enumerate(self):
        self._login_as(self.student_a)
        self.assertEqual(self.client.get(self.avatar_url).status_code, 404)

    def test_postgraduate_admin_sees_the_avatar(self):
        self._login_as(self.postgrad)
        self.assertEqual(self.client.get(self.avatar_url).status_code, 200)


class PhotoWriteScopeTest(FilesScopeBase):

    def _upload_url(self, user):
        return f'/api/v1/users/{user.id}/photo'

    def _enable_url(self, user):
        return f'/api/v1/users/{user.id}/photo/enable-change'

    def test_out_of_scope_coordinator_cannot_overwrite_photo(self):
        self._login_as(self.coord_a)
        resp = self.client.post(
            self._upload_url(self.student_b),
            data={'photo': (io.BytesIO(_jpeg_bytes()), 'nueva.jpg')},
            content_type='multipart/form-data',
            headers=self.csrf_headers,
        )
        self.assertEqual(resp.status_code, 403)
        body = resp.get_json()
        self.assertEqual(body['error']['code'], 'FORBIDDEN')
        self.assertIsNone(body['data'])
        # La foto de la víctima sigue intacta
        db.session.refresh(self.student_b)
        self.assertEqual(self.student_b.avatar, 'avatar.jpg')

    def test_coordinator_of_the_program_can_overwrite_photo(self):
        self._login_as(self.coord_b)
        resp = self.client.post(
            self._upload_url(self.student_b),
            data={'photo': (io.BytesIO(_jpeg_bytes()), 'nueva.jpg')},
            content_type='multipart/form-data',
            headers=self.csrf_headers,
        )
        self.assertEqual(resp.status_code, 200)

    def test_out_of_scope_coordinator_cannot_enable_photo_change(self):
        self._login_as(self.coord_a)
        resp = self.client.post(
            self._enable_url(self.student_b), json={'approve': True},
            headers=self.csrf_headers)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.get_json()['error']['code'], 'FORBIDDEN')
        db.session.refresh(self.student_b)
        self.assertFalse(self.student_b.photo_change_allowed)

    def test_coordinator_of_the_program_can_enable_photo_change(self):
        self._login_as(self.coord_b)
        resp = self.client.post(
            self._enable_url(self.student_b), json={'approve': True},
            headers=self.csrf_headers)
        self.assertEqual(resp.status_code, 200)
        db.session.refresh(self.student_b)
        self.assertTrue(self.student_b.photo_change_allowed)


if __name__ == '__main__':
    unittest.main()
