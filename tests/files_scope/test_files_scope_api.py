# tests/files_scope/test_files_scope_api.py
"""
HTTP-level tests for the program scope of served files and profile photos, and
for the opaque-identifier URL shape that replaced the path-mirroring one.

THE URL SHAPE THESE TESTS PIN DOWN
----------------------------------
    before                                        after
    /files/doc/<user_id>/<phase>/<filename>   ->  /files/doc/<row uuid>
                                                  /files/doc/<row uuid>/<slot>
    /files/avatar/<user_id>/<filename>        ->  /files/avatar/<user uuid>

The old shape published the student's integer id and the exact document name to
anybody who could read a payload, and it was enumerable. The new one names a
ROW; the server reads that row's stored path and streams the bytes from where
they actually are. NOTHING ON DISK MOVED — the assertions below check the file
is still at `<user_id>/<phase>/<human name>` while the URL no longer says so.

Covers:
  * GET /files/doc/<uuid>
      - the owner downloads their own document
      - the coordinator of the owner's program downloads it
      - a coordinator of ANOTHER program gets 404 (not 403: a 403 would confirm
        which documents a student of another program uploaded)
      - a caller without `files.api.view_doc_others` still gets 403 (unchanged)
      - an unknown, a malformed and an out-of-scope identifier are one answer
      - a row whose bytes are missing from disk is 404, never 500
      - a stored path that escapes the base directory is 404, never 500
      - the legacy `documents/` stored prefix still resolves
      - `Content-Disposition` carries the ORIGINAL human filename, accents
        included (RFC 5987)
  * GET /files/doc/<uuid>/<slot> — SemesterEnrollment owns TWO files
  * GET /files/avatar/<uuid>
      - self and in-scope staff get the photo
      - out-of-scope staff and other students get 404 (no enumeration)
  * POST /api/v1/users/<uuid>/photo and .../photo/enable-change
      - out-of-scope coordinator gets the standard 403 JSON envelope and the
        victim's avatar is left untouched
"""

import io
import unittest
import urllib.parse
import uuid as uuid_module

from PIL import Image

from app import create_app, db
from app.models.archive import Archive
from app.models.phase import Phase
from app.models.program_step import ProgramStep
from app.models.semester_enrollment import SemesterEnrollment
from app.models.step import Step
from app.models.submission import Submission

from tests.program_scope.conftest import (
    make_test_config, make_role, make_user, make_program, make_period,
    make_user_program, grant_permission,
)


#: A name with the accents and the space a real Spanish document carries. It is
#: the whole point of the RFC 5987 assertion: the URL stopped carrying the
#: filename, so `Content-Disposition` is now the ONLY thing that keeps a
#: download named correctly.
ACCENTED_NAME = 'Título de la tesis (versión final).pdf'


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
        self.period = make_period()
        make_user_program(self.student_a, self.program_a, self.period)
        self.up_b = make_user_program(self.student_b, self.program_b, self.period)

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
        self.archive = archive
        self.pstep = pstep

        # ── The document rows ────────────────────────────────────────────────
        # DISK LAYOUT IS UNCHANGED: every stored path is still
        # '<user_id>/<phase>/<human filename>'. Only the URL stopped saying so.
        self.doc_rel = f'{self.student_b.id}/admission/acta.pdf'
        self.submission = self._make_submission(self.doc_rel)

        # Same shape, accented human name — pins the Content-Disposition
        # round-trip.
        self.accented_rel = f'{self.student_b.id}/admission/{ACCENTED_NAME}'
        self.accented_submission = self._make_submission(self.accented_rel)

        # A row in the LEGACY stored spelling ('documents/<user>/<phase>/<f>').
        # 121 of 227 production Submission rows carry it; `path_variants` is
        # what keeps them reachable and this row is what proves it still is.
        self.legacy_rel = f'{self.student_b.id}/admission/legado.pdf'
        self.legacy_submission = self._make_submission(f'documents/{self.legacy_rel}')

        # A row whose bytes are NOT on disk (deleted behind the app's back).
        self.missing_submission = self._make_submission(
            f'{self.student_b.id}/admission/desaparecido.pdf')

        # A row whose stored path tries to escape USER_DOCS_FOLDER. Defence in
        # depth on a STORED value: the client cannot supply a path any more.
        self.traversal_submission = self._make_submission('../../../etc/passwd')

        # SemesterEnrollment — the only row that owns TWO served files.
        self.enrollment = SemesterEnrollment(
            user_program_id=self.up_b.id,
            academic_period_id=self.period.id,
            semester_number=1,
            status='active',
            payment_proof_path=f'{self.student_b.id}/permanence/comprobante.pdf',
            schedule_path=f'{self.student_b.id}/permanence/horario.pdf',
        )
        db.session.add(self.enrollment)
        db.session.commit()

        # ── Physical files ───────────────────────────────────────────────────
        docs_dir = (self.app.config['USER_DOCS_FOLDER']
                    / str(self.student_b.id) / 'admission')
        docs_dir.mkdir(parents=True, exist_ok=True)
        (docs_dir / 'acta.pdf').write_bytes(b'%PDF-1.4 test')
        (docs_dir / ACCENTED_NAME).write_bytes(b'%PDF-1.4 accented')
        (docs_dir / 'legado.pdf').write_bytes(b'%PDF-1.4 legacy')
        # 'desaparecido.pdf' is deliberately NOT written.
        # Orphan bytes: a file no database row references. Under the new URL
        # shape there is no way to even ask for it — kept to prove that.
        (docs_dir / 'huerfano.pdf').write_bytes(b'%PDF-1.4 orphan')

        perm_dir = (self.app.config['USER_DOCS_FOLDER']
                    / str(self.student_b.id) / 'permanence')
        perm_dir.mkdir(parents=True, exist_ok=True)
        (perm_dir / 'comprobante.pdf').write_bytes(b'%PDF-1.4 proof')
        (perm_dir / 'horario.pdf').write_bytes(b'%PDF-1.4 schedule')

        avatar_dir = self.app.config['AVATAR_FOLDER'] / str(self.student_b.id)
        avatar_dir.mkdir(parents=True, exist_ok=True)
        (avatar_dir / 'avatar.jpg').write_bytes(_jpeg_bytes())
        self.student_b.avatar = 'avatar.jpg'
        db.session.commit()

        # ── The URLs under test: a row handle, and nothing else ──────────────
        self.doc_url = f'/files/doc/{self.submission.uuid}'
        self.accented_url = f'/files/doc/{self.accented_submission.uuid}'
        self.legacy_url = f'/files/doc/{self.legacy_submission.uuid}'
        self.missing_url = f'/files/doc/{self.missing_submission.uuid}'
        self.traversal_url = f'/files/doc/{self.traversal_submission.uuid}'
        self.proof_url = f'/files/doc/{self.enrollment.uuid}/payment-proof'
        self.schedule_url = f'/files/doc/{self.enrollment.uuid}/schedule'
        self.avatar_url = f'/files/avatar/{self.student_b.uuid}'

        #: A well-formed identifier that names no row. Replaces the old
        #: "pick a bigger integer" sentinel — with UUIDv7 you cannot count up
        #: to a real one, which is the property being tested.
        self.absent_url = f'/files/doc/{uuid_module.uuid4()}'
        self.absent_avatar_url = f'/files/avatar/{uuid_module.uuid4()}'

    def _make_submission(self, file_path: str) -> Submission:
        sub = Submission(
            file_path=file_path, status='pending',
            user_id=self.student_b.id, archive_id=self.archive.id,
            program_step_id=self.pstep.id, semester=None,
        )
        db.session.add(sub)
        db.session.flush()
        return sub

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

    def test_legacy_documents_prefix_still_resolves(self):
        """
        Las dos ortografías almacenadas conviven ('documents/x/y' y 'x/y') y
        `path_variants` las sigue aceptando. Nada se reescribió en disco ni en
        la columna.
        """
        self._login_as(self.student_b)
        resp = self.client.get(self.legacy_url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, b'%PDF-1.4 legacy')

    # ── Ninguna de estas tres debe poder distinguirse de las otras ───────────

    def test_unknown_identifier_is_404(self):
        self._login_as(self.student_b)
        self.assertEqual(self.client.get(self.absent_url).status_code, 404)

    def test_malformed_identifier_is_404(self):
        """
        El convertidor `uuid` de Werkzeug rechaza el segmento antes de que
        corra ningún decorador, así que un identificador mal formado contesta
        igual que uno inexistente.
        """
        self._login_as(self.student_b)
        self.assertEqual(self.client.get('/files/doc/no-es-un-uuid').status_code, 404)

    def test_old_path_shaped_url_is_gone(self):
        """
        La forma vieja `/files/doc/<user_id>/<fase>/<archivo>` ya no existe:
        publicaba el id entero del alumno y el nombre exacto del documento.
        """
        self._login_as(self.student_b)
        self.assertEqual(
            self.client.get(f'/files/doc/{self.doc_rel}').status_code, 404)
        self.assertEqual(
            self.client.get(
                f'/files/avatar/{self.student_b.id}/avatar.jpg').status_code, 404)

    def test_orphan_bytes_are_unreachable(self):
        """
        Bytes que ninguna fila referencia: antes se pedían por su ruta y el
        404 lo daba `user_doc_owner_id`. Ahora ni siquiera hay URL con la que
        nombrarlos, porque el URL nombra una fila.
        """
        self._login_as(self.student_b)
        self.assertEqual(
            self.client.get(
                f'/files/doc/{self.student_b.id}/admission/huerfano.pdf'
            ).status_code, 404)

    # ── El archivo que no está, y la ruta que se sale ────────────────────────

    def test_row_whose_file_is_missing_is_404_not_500(self):
        self._login_as(self.student_b)
        self.assertEqual(self.client.get(self.missing_url).status_code, 404)

    def test_stored_path_escaping_the_base_is_404_not_500(self):
        """
        `safe_join` DEVUELVE None (no lanza) cuando la ruta se sale de la
        carpeta base; `_send_safe` lo comprueba explícitamente. Sin esa
        comprobación esto era `Path(None)` -> TypeError -> 500, es decir una
        respuesta distinta de la de un archivo inexistente.
        """
        self._login_as(self.student_b)
        self.assertEqual(self.client.get(self.traversal_url).status_code, 404)

    # ── Content-Disposition: el nombre humano vuelve por la cabecera ─────────

    def test_ascii_name_round_trips_in_content_disposition(self):
        self._login_as(self.student_b)
        resp = self.client.get(self.doc_url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('acta.pdf', resp.headers['Content-Disposition'])

    def test_accented_name_round_trips_rfc5987(self):
        """
        El URL ya no lleva el nombre del archivo, así que `Content-Disposition`
        es lo único que mantiene la descarga bien nombrada. Werkzeug emite la
        forma RFC 5987 `filename*=UTF-8''…` en cuanto el nombre deja de ser
        ASCII puro, y decodificarla tiene que devolver el nombre EXACTO que
        está en disco.
        """
        self._login_as(self.student_b)
        resp = self.client.get(self.accented_url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, b'%PDF-1.4 accented')

        disposition = resp.headers['Content-Disposition']
        self.assertIn("filename*=UTF-8''", disposition)

        encoded = disposition.split("filename*=UTF-8''", 1)[1].split(';')[0].strip()
        self.assertEqual(urllib.parse.unquote(encoded), ACCENTED_NAME)

        # Y el URL, por su parte, no dice nada del nombre ni de la ruta.
        # (No se comprueba "el id entero no aparece": un id de un dígito
        # coincide por azar con cualquier dígito hexadecimal del UUID, así que
        # esa aserción no distingue una fuga de una casualidad. Lo que sí se
        # comprueba es que el segmento ES el handle de la fila y nada más.)
        self.assertNotIn('Título', self.accented_url)
        self.assertNotIn('admission', self.accented_url)
        self.assertEqual(self.accented_url.rsplit('/', 1)[1],
                         str(self.accented_submission.uuid))


class UrlOpacityTest(FilesScopeBase):
    """El URL servido no debe contener ni el id entero ni el nombre humano."""

    def test_served_urls_carry_only_the_row_handle(self):
        from app.services import file_access_service

        doc_url = file_access_service.submission_file_url(self.submission)
        self.assertEqual(doc_url, f'/files/doc/{self.submission.uuid}')
        self.assertNotIn('admission', doc_url)
        self.assertNotIn('acta.pdf', doc_url)

        avatar_url = file_access_service.avatar_url(self.student_b)
        self.assertEqual(avatar_url, f'/files/avatar/{self.student_b.uuid}')
        self.assertNotIn('avatar.jpg', avatar_url)

    def test_disk_layout_is_untouched(self):
        """
        Requisito explícito del dueño: en el servidor y dentro de un ZIP de
        retención los archivos se siguen leyendo con su nombre humano y en su
        carpeta de siempre.
        """
        base = self.app.config['USER_DOCS_FOLDER']
        self.assertTrue(
            (base / str(self.student_b.id) / 'admission' / 'acta.pdf').is_file())
        self.assertTrue(
            (base / str(self.student_b.id) / 'admission' / ACCENTED_NAME).is_file())
        # Y la columna conserva su valor, incluido el prefijo heredado.
        self.assertEqual(self.submission.file_path, self.doc_rel)
        self.assertEqual(self.legacy_submission.file_path,
                         f'documents/{self.legacy_rel}')

    def test_avatar_url_is_none_without_a_photo(self):
        from app.services import file_access_service

        self.student_a.avatar = 'default.jpg'
        self.assertIsNone(file_access_service.avatar_url(self.student_a))


class SemesterEnrollmentSlotTest(FilesScopeBase):
    """
    Una fila con DOS archivos: el handle solo no basta, por eso lleva un
    segmento discriminador explícito.
    """

    def test_payment_proof_slot_serves_the_payment_proof(self):
        self._login_as(self.student_b)
        resp = self.client.get(self.proof_url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, b'%PDF-1.4 proof')
        self.assertIn('comprobante.pdf', resp.headers['Content-Disposition'])

    def test_schedule_slot_serves_the_schedule(self):
        self._login_as(self.student_b)
        resp = self.client.get(self.schedule_url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, b'%PDF-1.4 schedule')
        self.assertIn('horario.pdf', resp.headers['Content-Disposition'])

    def test_unknown_slot_is_404(self):
        self._login_as(self.student_b)
        self.assertEqual(
            self.client.get(f'/files/doc/{self.enrollment.uuid}/otro').status_code,
            404)

    def test_enrollment_without_a_slot_is_404(self):
        """
        Sin slot la búsqueda va contra Submission y AcceptanceDocument, no
        contra SemesterEnrollment: un handle de inscripción no resuelve nada.
        """
        self._login_as(self.student_b)
        self.assertEqual(
            self.client.get(f'/files/doc/{self.enrollment.uuid}').status_code, 404)

    def test_slot_on_a_submission_handle_is_404(self):
        self._login_as(self.student_b)
        self.assertEqual(
            self.client.get(f'{self.doc_url}/payment-proof').status_code, 404)

    def test_out_of_scope_coordinator_gets_404_on_both_slots(self):
        self._login_as(self.coord_a)
        self.assertEqual(self.client.get(self.proof_url).status_code, 404)
        self.assertEqual(self.client.get(self.schedule_url).status_code, 404)

    def test_coordinator_of_the_program_reads_both_slots(self):
        self._login_as(self.coord_b)
        self.assertEqual(self.client.get(self.proof_url).status_code, 200)
        self.assertEqual(self.client.get(self.schedule_url).status_code, 200)


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

    def test_unknown_user_handle_is_404(self):
        """
        "No existe la cuenta", "no puedes verla" y "no tiene foto" contestan
        las tres lo mismo.
        """
        self._login_as(self.postgrad)
        self.assertEqual(self.client.get(self.absent_avatar_url).status_code, 404)

    def test_user_without_photo_is_404(self):
        self._login_as(self.postgrad)
        self.assertEqual(
            self.client.get(f'/files/avatar/{self.student_a.uuid}').status_code,
            404)


class PhotoWriteScopeTest(FilesScopeBase):

    def _upload_url(self, user):
        # La ruta habla UUID público.
        return f'/api/v1/users/{user.uuid}/photo'

    def _enable_url(self, user):
        return f'/api/v1/users/{user.uuid}/photo/enable-change'

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
