# tests/deferral/test_deferral_file_cleanup.py
"""
Deferring an applicant must actually delete the files it drops from the DB.

`AcceptanceDocument.file_path` is relative to USER_DOCS_FOLDER (that is what
`save_user_doc` returns and what `/files/doc/...` serves). The service used to
call `os.remove(doc.file_path)` directly, resolving it against the process CWD,
so the row disappeared while the PDF stayed on disk forever — the applicant's
course schedule and enrolment receipt piled up unreferenced.
"""

import unittest
from datetime import date, timedelta
from pathlib import Path

from flask import current_app

from app import create_app, db
from app.models.academic_period import AcademicPeriod
from app.models.acceptance_document import AcceptanceDocument
from app.services import deferral_service as dsvc

from tests.program_scope.conftest import (
    make_test_config, make_role, make_user, make_program, make_period,
    make_user_program,
)


class DeferralFileCleanupTest(unittest.TestCase):

    def setUp(self):
        self.app = create_app(make_test_config())
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.role_applicant = make_role('applicant')
        self.role_coord = make_role('program_admin')
        self.coord = make_user(self.role_coord, suffix='_c')
        self.applicant = make_user(self.role_applicant, suffix='_a')

        self.period = make_period(code='20261')
        today = date.today()
        self.next_period = AcademicPeriod(
            code='20262',
            name='Period 20262',
            start_date=today + timedelta(days=150),
            end_date=today + timedelta(days=300),
            admission_start_date=today + timedelta(days=120),
            admission_end_date=today + timedelta(days=145),
            is_active=False,
            status='planned',
        )
        db.session.add(self.next_period)
        db.session.flush()

        self.program = make_program(self.coord, slug='prog-def')
        self.up = make_user_program(
            self.applicant, self.program, self.period, status='accepted',
        )

        # Real file on disk, stored exactly as save_user_doc would store it.
        base = Path(current_app.config['USER_DOCS_FOLDER'])
        folder = base / str(self.applicant.id) / 'acceptance'
        folder.mkdir(parents=True, exist_ok=True)
        self.absolute_file = folder / 'tira_de_materias.pdf'
        self.absolute_file.write_bytes(b'%PDF-1.4 fake')
        self.relative_path = f'{self.applicant.id}/acceptance/tira_de_materias.pdf'

        db.session.add(AcceptanceDocument(
            user_program_id=self.up.id,
            document_type='course_schedule',
            file_path=self.relative_path,
            status='uploaded',
            uploaded_by_id=self.coord.id,
        ))
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_deferring_deletes_the_physical_file(self):
        self.assertTrue(self.absolute_file.exists())

        dsvc.defer_applicant(
            user_id=self.applicant.id,
            program_id=self.program.id,
            coordinator_id=self.coord.id,
            reason='Motivo',
        )

        self.assertFalse(
            self.absolute_file.exists(),
            'el archivo físico debe borrarse al diferir',
        )
        self.assertIsNone(
            AcceptanceDocument.query.filter_by(
                user_program_id=self.up.id, document_type='course_schedule',
            ).first()
        )

    def test_path_resolution_refuses_traversal(self):
        self.assertIsNone(dsvc._absolute_doc_path(''))
        self.assertIsNone(dsvc._absolute_doc_path('../../etc/passwd'))


if __name__ == '__main__':
    unittest.main()
