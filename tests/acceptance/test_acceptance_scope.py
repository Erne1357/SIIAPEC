# tests/acceptance/test_acceptance_scope.py
"""
HTTP-level program-scope tests for /api/v1/acceptance.

Holding `acceptance.api.review_doc` says WHAT a coordinator may do; it never
said on WHOSE document. Before this suite, the coordinator of program B could
approve program A's enrolment receipt and approve a deferral that deletes
program A's applicant files.

Two denial shapes are pinned on purpose:

  * a program id in the URL  → 403 with the standard scope envelope
    (a program is not a secret; the caller simply has no access to it),
  * an opaque object id      → 404, identical to a document that never existed,
    so a 403 never confirms the existence of another program's document.
"""

import unittest

from app import create_app, db
from app.models.acceptance_document import AcceptanceDocument
from app.utils.uuid7 import uuid7
from app.models.enrollment_deferral import EnrollmentDeferral
from app.models.permission import Permission
from app.models.role_permission import RolePermission

from tests.acceptance.conftest import (
    make_test_config, make_role, make_user, make_program, make_period,
    make_accepted_user_program,
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


class AcceptanceScopeTest(unittest.TestCase):

    def setUp(self):
        self.app = create_app(make_test_config())
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.role_applicant = make_role('applicant')
        self.role_coord = make_role('program_admin')
        self.role_boss = make_role('postgraduate_admin')

        perms = {
            c: _perm(c) for c in (
                'acceptance.api.list_applicants',
                'acceptance.api.review_doc',
                'acceptance.api.upload_doc',
                'acceptance.api.defer_applicant',
                'academic_periods.api.create',
            )
        }
        for codename, perm in perms.items():
            if codename != 'academic_periods.api.create':
                _grant(self.role_coord, perm)
            _grant(self.role_boss, perm)

        self.coord_a = make_user(self.role_coord, suffix='_a')
        self.coord_b = make_user(self.role_coord, suffix='_b')
        self.boss = make_user(self.role_boss, suffix='_boss')
        self.applicant = make_user(self.role_applicant, suffix='_app')

        self.period = make_period()
        self.program_a = make_program(self.coord_a, slug='prog-a')
        self.program_b = make_program(self.coord_b, slug='prog-b')

        self.up_a = make_accepted_user_program(
            self.applicant, self.program_a, self.period,
        )

        self.receipt = AcceptanceDocument(
            user_program_id=self.up_a.id,
            document_type='enrollment_receipt',
            file_path=f'{self.applicant.id}/acceptance/boleta.pdf',
            status='uploaded',
            uploaded_by_id=self.applicant.id,
        )
        db.session.add(self.receipt)

        self.deferral = EnrollmentDeferral(
            user_program_id=self.up_a.id,
            original_period_id=self.period.id,
            deferred_to_period_id=None,
            deferral_number=1,
            status='pending',
            requested_by='applicant',
        )
        db.session.add(self.deferral)
        db.session.commit()

        self.receipt_id = self.receipt.id
        self.deferral_id = self.deferral.id
        # URLs hablan UUID público; las comprobaciones contra la BD siguen
        # usando el id interno, que no cambió.
        self.receipt_uuid = str(self.receipt.uuid)
        self.applicant_uuid = str(self.applicant.uuid)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self, user):
        res = self.client.post('/api/v1/auth/login', json={
            'username': user.username,
            'password': 'Test1234!',
        })
        # Mutations go through validate_csrf_for_api: seed the session token
        # instead of scraping it from a rendered page.
        with self.client.session_transaction() as sess:
            sess['_csrf_token'] = 'test-csrf-token'
        return res

    @property
    def _csrf(self):
        return {'X-CSRFToken': 'test-csrf-token'}

    # ── Program id in the URL → 403 with the scope envelope ─────────────────

    def test_other_program_applicants_list_is_403(self):
        self._login(self.coord_b)
        res = self.client.get(
            f'/api/v1/acceptance/program/{self.program_a.id}/applicants'
        )
        self.assertEqual(res.status_code, 403)
        body = res.get_json()
        self.assertIsNone(body['data'])
        self.assertEqual(body['error']['code'], 'FORBIDDEN')

    def test_own_program_applicants_list_still_works(self):
        self._login(self.coord_a)
        res = self.client.get(
            f'/api/v1/acceptance/program/{self.program_a.id}/applicants'
        )
        self.assertEqual(res.status_code, 200)

    def test_global_admin_reaches_every_program(self):
        self._login(self.boss)
        res = self.client.get(
            f'/api/v1/acceptance/program/{self.program_a.id}/applicants'
        )
        self.assertEqual(res.status_code, 200)

    # ── Opaque object id → 404, never 403 ───────────────────────────────────

    def test_other_program_receipt_review_is_404_and_changes_nothing(self):
        self._login(self.coord_b)
        res = self.client.post(
            f'/api/v1/acceptance/document/{self.receipt_uuid}/review',
            json={'status': 'approved'}, headers=self._csrf,
        )
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.get_json()['error']['code'], 'NOT_FOUND')

        doc = db.session.get(AcceptanceDocument, self.receipt_id)
        self.assertEqual(doc.status, 'uploaded')
        self.assertIsNone(doc.reviewed_by_id)

    def test_own_program_receipt_review_still_works(self):
        self._login(self.coord_a)
        res = self.client.post(
            f'/api/v1/acceptance/document/{self.receipt_uuid}/review',
            json={'status': 'approved'}, headers=self._csrf,
        )
        self.assertEqual(res.status_code, 200)
        doc = db.session.get(AcceptanceDocument, self.receipt_id)
        self.assertEqual(doc.status, 'approved')

    def test_other_program_document_delete_is_404(self):
        self._login(self.coord_b)
        res = self.client.delete(
            f'/api/v1/acceptance/document/{self.receipt_uuid}',
            headers=self._csrf,
        )
        self.assertEqual(res.status_code, 404)
        self.assertIsNotNone(db.session.get(AcceptanceDocument, self.receipt_id))

    def test_other_program_deferral_approval_is_404(self):
        self._login(self.coord_b)
        res = self.client.post(
            f'/api/v1/acceptance/deferral/{self.deferral_id}/approve',
            json={'notes': 'ok'}, headers=self._csrf,
        )
        self.assertEqual(res.status_code, 404)

        deferral = db.session.get(EnrollmentDeferral, self.deferral_id)
        self.assertEqual(deferral.status, 'pending')
        # The applicant's documents survive: approving deletes them.
        self.assertIsNotNone(db.session.get(AcceptanceDocument, self.receipt_id))

    def test_other_program_deferral_rejection_is_404(self):
        self._login(self.coord_b)
        res = self.client.post(
            f'/api/v1/acceptance/deferral/{self.deferral_id}/reject',
            json={'notes': 'motivo'}, headers=self._csrf,
        )
        self.assertEqual(res.status_code, 404)
        deferral = db.session.get(EnrollmentDeferral, self.deferral_id)
        self.assertEqual(deferral.status, 'pending')

    def test_unknown_document_and_foreign_document_are_indistinguishable(self):
        self._login(self.coord_b)
        foreign = self.client.post(
            f'/api/v1/acceptance/document/{self.receipt_uuid}/review',
            json={'status': 'approved'}, headers=self._csrf,
        )
        missing = self.client.post(
            # Un UUID bien formado que no existe: mismo cuerpo y mismo código
            # que un documento de otro programa.
            f'/api/v1/acceptance/document/{uuid7()}/review',
            json={'status': 'approved'}, headers=self._csrf,
        )
        self.assertEqual(foreign.status_code, missing.status_code)
        self.assertEqual(foreign.get_json(), missing.get_json())

    # ── Writes keyed by (user_id, program_id) ───────────────────────────────

    def test_other_program_defer_applicant_is_403(self):
        self._login(self.coord_b)
        res = self.client.post(
            f'/api/v1/acceptance/user/{self.applicant_uuid}'
            f'/program/{self.program_a.id}/defer',
            json={'reason': 'motivo'}, headers=self._csrf,
        )
        self.assertEqual(res.status_code, 403)
        self.assertEqual(
            db.session.get(EnrollmentDeferral, self.deferral_id).status,
            'pending',
        )

    def test_other_program_acceptance_status_is_403(self):
        self._login(self.coord_b)
        res = self.client.get(
            f'/api/v1/acceptance/user/{self.applicant_uuid}'
            f'/program/{self.program_a.id}/status'
        )
        self.assertEqual(res.status_code, 403)

    def test_applicant_still_reads_own_acceptance_status(self):
        self._login(self.applicant)
        res = self.client.get(
            f'/api/v1/acceptance/user/{self.applicant_uuid}'
            f'/program/{self.program_a.id}/status'
        )
        self.assertEqual(res.status_code, 200)


if __name__ == '__main__':
    unittest.main()
