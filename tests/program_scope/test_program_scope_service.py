# tests/program_scope/test_program_scope_service.py
"""
Tests for app/services/program_scope_service.py — the shared program-scope
predicate every scoped route must call.

TRUTH TABLE (target = a student enrolled in program A; "other" = program B)
─────────────────────────────────────────────────────────────────────────────
caller                                  | user_in_scope | may_view_cross_
                                        | (full record  | program_summary
                                        |  + writes)    | (name/mail/status)
────────────────────────────────────────┼───────────────┼───────────────────
postgraduate_admin (global, scope=None) | True (anyone) | True
program_admin of A, target in A         | True          | True
program_admin of B, target in A         | False         | True
social_service delegated on A, target A | True          | True (if it holds
                                        |               | the listing perm)
social_service delegated on B, target A | False         | idem
social_service with no delegation       | False         | idem
applicant                               | only self     | False (no perm)
student                                 | only self     | False (no perm)
target is a STAFF account with NO       | False for     | n/a
UserProgram row                         | every non-    |
                                        | global caller |
                                        | (self excl.)  |
─────────────────────────────────────────────────────────────────────────────
Notes:
  * scope None  = ALL programs (global). scope set() = NO programs. They must
    never be conflated; several tests below pin that distinction.
  * `user_in_scope(caller, caller)` is always True (allow_self default), so a
    student reaches their own expediente without a program lookup.
  * Failing `user_in_scope` never grants personal data through another door:
    the only cross-program payload is the `CROSS_PROGRAM_SUMMARY_FIELDS`
    projection, which contains no PII, no documents, no photo and no history.
"""

import unittest

from app import create_app, db
from app.services import program_scope_service as scope

from tests.program_scope.conftest import (
    make_test_config, make_role, make_user, make_program, make_period,
    grant_permission, delegate_permission, make_user_program,
)


LIST_PERM = scope.CROSS_PROGRAM_SUMMARY_PERMISSION  # coordinator.api.list_students


class ProgramScopeTestCase(unittest.TestCase):
    """
    World:
      program_a — coordinated by coord_a
      program_b — coordinated by coord_b
      student_a in program_a, student_b in program_b
      pg_admin  — global (academic_periods.api.create)
      social_a  — social_service with a delegation on program_a
      social_none — social_service with no delegation
      staff_user — program_admin account with NO UserProgram row
    """

    def setUp(self):
        self.app = create_app(make_test_config())
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.role_student = make_role('student')
        self.role_applicant = make_role('applicant')
        self.role_coord = make_role('program_admin')
        self.role_pg = make_role('postgraduate_admin')
        self.role_social = make_role('social_service')

        grant_permission(self.role_pg, 'academic_periods.api.create')
        grant_permission(self.role_pg, LIST_PERM)
        grant_permission(self.role_coord, LIST_PERM)
        grant_permission(self.role_social, LIST_PERM)

        self.coord_a = make_user(self.role_coord, suffix='_a')
        self.coord_b = make_user(self.role_coord, suffix='_b')
        self.pg_admin = make_user(self.role_pg, suffix='_pg')
        self.social_a = make_user(self.role_social, suffix='_deleg')
        self.social_none = make_user(self.role_social, suffix='_nodeleg')
        self.staff_user = make_user(self.role_coord, suffix='_staff')

        self.student_a = make_user(self.role_student, suffix='_a')
        self.student_b = make_user(self.role_student, suffix='_b')
        self.applicant = make_user(self.role_applicant, suffix='_ap')

        self.period = make_period()
        self.program_a = make_program(self.coord_a, slug='prog-a')
        self.program_b = make_program(self.coord_b, slug='prog-b')

        make_user_program(self.student_a, self.program_a, self.period)
        make_user_program(self.student_b, self.program_b, self.period)
        make_user_program(self.applicant, self.program_a, self.period,
                          status='in_progress')

        # social_a gets the listing permission delegated ONLY on program_a
        delegate_permission(self.social_a, LIST_PERM, self.program_a,
                            granted_by=self.coord_a)

        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()


class TestAccessibleProgramIds(ProgramScopeTestCase):

    def test_global_admin_returns_none_meaning_all(self):
        self.assertIsNone(scope.accessible_program_ids(self.pg_admin))
        self.assertTrue(scope.is_global_scope(self.pg_admin))

    def test_coordinator_returns_own_programs(self):
        self.assertEqual(scope.accessible_program_ids(self.coord_a),
                         {self.program_a.id})
        self.assertEqual(scope.accessible_program_ids(self.coord_b),
                         {self.program_b.id})

    def test_delegated_social_service_returns_delegated_program(self):
        self.assertEqual(scope.accessible_program_ids(self.social_a),
                         {self.program_a.id})

    def test_undelegated_social_service_returns_empty_set_not_none(self):
        pids = scope.accessible_program_ids(self.social_none)
        self.assertEqual(pids, set())
        self.assertIsNotNone(pids)  # empty != global
        self.assertFalse(scope.is_global_scope(self.social_none))

    def test_student_and_applicant_have_no_scope(self):
        self.assertEqual(scope.accessible_program_ids(self.student_a), set())
        self.assertEqual(scope.accessible_program_ids(self.applicant), set())


class TestProgramInScope(ProgramScopeTestCase):

    def test_global_admin_reaches_every_program(self):
        self.assertTrue(scope.program_in_scope(self.pg_admin, self.program_a.id))
        self.assertTrue(scope.program_in_scope(self.pg_admin, self.program_b.id))

    def test_coordinator_only_own_program(self):
        self.assertTrue(scope.program_in_scope(self.coord_a, self.program_a.id))
        self.assertFalse(scope.program_in_scope(self.coord_a, self.program_b.id))

    def test_missing_or_garbage_program_id_fails_closed(self):
        self.assertFalse(scope.program_in_scope(self.coord_a, None))
        self.assertFalse(scope.program_in_scope(self.coord_a, 'abc'))

    def test_bulk_programs_in_scope(self):
        both = [self.program_a.id, self.program_b.id]
        self.assertEqual(scope.programs_in_scope(self.coord_a, both),
                         {self.program_a.id})
        self.assertEqual(scope.programs_in_scope(self.pg_admin, both), set(both))
        self.assertEqual(scope.programs_in_scope(self.social_none, both), set())

    def test_require_program_in_scope_raises(self):
        scope.require_program_in_scope(self.coord_a, self.program_a.id)
        with self.assertRaises(scope.ProgramScopeDenied):
            scope.require_program_in_scope(self.coord_a, self.program_b.id)


class TestUserInScope(ProgramScopeTestCase):

    def test_postgraduate_admin_reaches_any_student(self):
        self.assertTrue(scope.user_in_scope(self.pg_admin, self.student_a))
        self.assertTrue(scope.user_in_scope(self.pg_admin, self.student_b))
        self.assertTrue(scope.user_in_scope(self.pg_admin, self.staff_user))

    def test_program_admin_in_scope(self):
        self.assertTrue(scope.user_in_scope(self.coord_a, self.student_a))

    def test_program_admin_out_of_scope(self):
        self.assertFalse(scope.user_in_scope(self.coord_a, self.student_b))
        self.assertFalse(scope.user_in_scope(self.coord_b, self.student_a))

    def test_social_service_delegated(self):
        self.assertTrue(scope.user_in_scope(self.social_a, self.student_a))
        self.assertFalse(scope.user_in_scope(self.social_a, self.student_b))

    def test_social_service_undelegated_reaches_nobody(self):
        self.assertFalse(scope.user_in_scope(self.social_none, self.student_a))
        self.assertFalse(scope.user_in_scope(self.social_none, self.student_b))

    def test_applicant_and_student_only_reach_themselves(self):
        self.assertTrue(scope.user_in_scope(self.student_a, self.student_a))
        self.assertFalse(scope.user_in_scope(self.student_a, self.student_b))
        self.assertTrue(scope.user_in_scope(self.applicant, self.applicant))
        self.assertFalse(scope.user_in_scope(self.applicant, self.student_a))

    def test_staff_target_without_user_program_is_out_of_scope(self):
        """A user with no UserProgram row belongs to nobody's program."""
        self.assertEqual(scope.program_ids_of_user(self.staff_user), set())
        self.assertFalse(scope.user_in_scope(self.coord_a, self.staff_user))
        self.assertFalse(scope.user_in_scope(self.coord_b, self.staff_user))
        self.assertFalse(scope.user_in_scope(self.social_a, self.staff_user))
        # …but the account itself and the global admin still reach it
        self.assertTrue(scope.user_in_scope(self.staff_user, self.staff_user))
        self.assertTrue(scope.user_in_scope(self.pg_admin, self.staff_user))

    def test_allow_self_false_denies_own_record(self):
        self.assertFalse(
            scope.user_in_scope(self.student_a, self.student_a, allow_self=False)
        )

    def test_accepts_plain_int_target(self):
        self.assertTrue(scope.user_in_scope(self.coord_a, self.student_a.id))
        self.assertFalse(scope.user_in_scope(self.coord_a, self.student_b.id))

    def test_unknown_target_id_fails_closed(self):
        self.assertFalse(scope.user_in_scope(self.coord_a, 999999))
        self.assertFalse(scope.user_in_scope(self.coord_a, None))

    def test_require_user_in_scope_raises(self):
        scope.require_user_in_scope(self.coord_a, self.student_a)
        with self.assertRaises(scope.ProgramScopeDenied):
            scope.require_user_in_scope(self.coord_a, self.student_b)


class TestBulkVariants(ProgramScopeTestCase):

    def test_users_in_scope_filters_the_page(self):
        ids = [self.student_a.id, self.student_b.id, self.staff_user.id]
        self.assertEqual(scope.users_in_scope(self.coord_a, ids),
                         {self.student_a.id})
        self.assertEqual(scope.users_in_scope(self.coord_b, ids),
                         {self.student_b.id})
        self.assertEqual(scope.users_in_scope(self.social_a, ids),
                         {self.student_a.id})
        self.assertEqual(scope.users_in_scope(self.social_none, ids), set())
        self.assertEqual(scope.users_in_scope(self.pg_admin, ids), set(ids))

    def test_users_in_scope_matches_single_check_row_by_row(self):
        ids = [self.student_a.id, self.student_b.id,
               self.applicant.id, self.staff_user.id]
        for caller in (self.coord_a, self.coord_b, self.social_a,
                       self.social_none, self.pg_admin):
            bulk = scope.users_in_scope(caller, ids)
            for uid in ids:
                self.assertEqual(
                    uid in bulk,
                    scope.user_in_scope(caller, uid),
                    msg=f'caller={caller.username} target={uid}',
                )

    def test_users_in_scope_includes_self(self):
        ids = [self.student_a.id, self.student_b.id]
        self.assertIn(self.student_a.id,
                      scope.users_in_scope(self.student_a, ids))

    def test_users_in_scope_empty_input(self):
        self.assertEqual(scope.users_in_scope(self.coord_a, []), set())

    def test_program_ids_by_user_returns_entry_for_every_id(self):
        ids = [self.student_a.id, self.student_b.id, self.staff_user.id]
        mapping = scope.program_ids_by_user(ids)
        self.assertEqual(set(mapping.keys()), set(ids))
        self.assertEqual(mapping[self.student_a.id], {self.program_a.id})
        self.assertEqual(mapping[self.student_b.id], {self.program_b.id})
        self.assertEqual(mapping[self.staff_user.id], set())


class TestCrossProgramSummaryTier(ProgramScopeTestCase):

    def test_coordinator_may_see_summary_of_other_programs(self):
        self.assertFalse(scope.user_in_scope(self.coord_a, self.student_b))
        self.assertTrue(scope.may_view_cross_program_summary(self.coord_a))

    def test_students_may_not(self):
        self.assertFalse(scope.may_view_cross_program_summary(self.student_a))
        self.assertFalse(scope.may_view_cross_program_summary(self.applicant))

    def test_projection_drops_every_forbidden_field(self):
        payload = {
            'id': self.student_b.id,
            'full_name': 'Ana Pérez',
            'email': 'ana@siiap.test',
            'current_phase': 'admission',
            'progress_percentage': 42,
            'overall_status': 'pending',
            'can_manage': True,
            # forbidden cross-program
            'curp': 'XXXX000000XXXXXX00',
            'rfc': 'XAXX010101000',
            'nss': '12345678901',
            'address': 'Calle Falsa 123',
            'birth_date': '1999-01-01',
            'birth_place': 'Cuernavaca',
            'cedula_profesional': '1234567',
            'emergency_contact_name': 'Mamá',
            'emergency_contact_phone': '7771234567',
            'avatar_url': '/files/avatar/1/foto.jpg',
            'control_number': 'M21111182',
            'documents_by_phase': {'admission': ['acta.pdf']},
            'history': [{'action': 'login'}],
        }
        out = scope.to_cross_program_summary(payload)

        self.assertEqual(out['full_name'], 'Ana Pérez')
        self.assertEqual(out['email'], 'ana@siiap.test')
        self.assertEqual(out['progress_percentage'], 42)
        self.assertEqual(out['overall_status'], 'pending')
        for forbidden in ('curp', 'rfc', 'nss', 'address', 'birth_date',
                          'birth_place', 'cedula_profesional',
                          'emergency_contact_name', 'emergency_contact_phone',
                          'avatar_url', 'control_number',
                          'documents_by_phase', 'history'):
            self.assertNotIn(forbidden, out, msg=f'{forbidden} leaked')

    def test_projection_forces_can_manage_false(self):
        out = scope.to_cross_program_summary({'id': 1, 'can_manage': True})
        self.assertFalse(out['can_manage'])

    def test_unknown_keys_are_dropped_by_default(self):
        """A field added later cannot leak by being forgotten here."""
        out = scope.to_cross_program_summary({'id': 1, 'brand_new_pii': 'x'})
        self.assertNotIn('brand_new_pii', out)

    def test_allow_list_contains_no_pii(self):
        pii = {'curp', 'rfc', 'nss', 'address', 'birth_date', 'birth_place',
               'cedula_profesional', 'emergency_contact_name',
               'emergency_contact_phone', 'emergency_contact_relationship',
               'avatar_url', 'avatar', 'control_number', 'phone',
               'mobile_phone', 'history', 'documents_by_phase',
               'acceptance_documents', 'file_url', 'file_path'}
        self.assertEqual(scope.CROSS_PROGRAM_SUMMARY_FIELDS & pii, set())


if __name__ == '__main__':
    unittest.main()
