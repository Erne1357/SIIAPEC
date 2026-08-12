# tests/student_record/test_student_record_service.py
"""
Tests for student_record_service:
  - get_full_record returns expected sections
  - access control: only allowed coordinators / postgrad_admin / self
  - update_personal_info updates only whitelisted fields, logs + notifies
"""

import unittest
from datetime import date

from app import create_app, db
from app.models.user_history import UserHistory
from app.models.notification import Notification
from app.services import student_record_service as svc

from tests.student_record.conftest import (
    make_test_config, make_role, make_user, make_program, make_period,
    grant_permission, make_user_program,
)


class TestGetFullRecord(unittest.TestCase):

    def setUp(self):
        self.app = create_app(make_test_config())
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.role_student = make_role('student')
        self.role_coord = make_role('program_admin')
        self.role_pg = make_role('postgraduate_admin')
        self.role_other = make_role('applicant')

        # postgraduate_admin needs the global flag
        grant_permission(self.role_pg, 'academic_periods.api.create')
        grant_permission(self.role_pg, 'students.api.view_record')
        grant_permission(self.role_coord, 'students.api.view_record')

        self.coord = make_user(self.role_coord, suffix='_coord')
        self.pg_admin = make_user(self.role_pg, suffix='_pg')
        self.student = make_user(self.role_student, suffix='_st')
        self.other_student = make_user(self.role_student, suffix='_other')

        self.period = make_period()
        self.program = make_program(self.coord)
        self.up = make_user_program(self.student, self.program, self.period)

        # Other student in a DIFFERENT program (different coordinator)
        another_coord = make_user(self.role_coord, suffix='_coord2')
        another_program = make_program(another_coord, slug='other-prog')
        make_user_program(self.other_student, another_program, self.period)

        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_record_has_expected_sections(self):
        rec = svc.get_full_record(self.student.id, requester=self.pg_admin)
        self.assertIn('user', rec)
        self.assertIn('programs', rec)
        self.assertIn('acceptance_documents', rec)
        self.assertIn('documents_by_phase', rec)
        self.assertIn('semester_enrollments', rec)
        self.assertIn('interview', rec)
        self.assertIn('events_attended', rec)
        self.assertIn('upcoming_events', rec)
        self.assertIn('deferrals', rec)
        self.assertIn('history', rec)
        self.assertIn('editable_fields', rec)
        self.assertEqual(rec['user']['id'], str(self.student.uuid))
        self.assertEqual(rec['user']['email'], self.student.email)

    def test_postgraduate_admin_sees_any_student(self):
        rec = svc.get_full_record(self.other_student.id, requester=self.pg_admin)
        self.assertEqual(rec['user']['id'], str(self.other_student.uuid))

    def test_program_admin_sees_only_their_students(self):
        rec = svc.get_full_record(self.student.id, requester=self.coord)
        self.assertEqual(rec['user']['id'], str(self.student.uuid))

    def test_program_admin_blocked_for_other_program(self):
        with self.assertRaises(svc.AccessDenied):
            svc.get_full_record(self.other_student.id, requester=self.coord)

    def test_self_can_see_own_record(self):
        rec = svc.get_full_record(self.student.id, requester=self.student)
        self.assertEqual(rec['user']['id'], str(self.student.uuid))

    def test_unknown_user_raises(self):
        with self.assertRaises(svc.StudentNotFound):
            svc.get_full_record(99999, requester=self.pg_admin)


class TestUpdatePersonalInfo(unittest.TestCase):

    def setUp(self):
        self.app = create_app(make_test_config())
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.role_student = make_role('student')
        self.role_coord = make_role('program_admin')
        grant_permission(self.role_coord, 'students.api.view_record')
        grant_permission(self.role_coord, 'students.api.edit_personal_info')

        self.coord = make_user(self.role_coord, suffix='_coord')
        self.student = make_user(self.role_student, suffix='_st')
        self.period = make_period()
        self.program = make_program(self.coord)
        self.up = make_user_program(self.student, self.program, self.period)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_updates_whitelisted_fields(self):
        svc.update_personal_info(self.student.id, self.coord.id, {
            'phone': '6561234567',
            'curp': 'NEWA800101HJCXXX01',
        })
        db.session.refresh(self.student)
        self.assertEqual(self.student.phone, '6561234567')
        self.assertEqual(self.student.curp, 'NEWA800101HJCXXX01')

    def test_ignores_non_whitelisted_fields(self):
        original_email = self.student.email
        original_username = self.student.username
        svc.update_personal_info(self.student.id, self.coord.id, {
            'email': 'hacked@example.com',
            'username': 'pwned',
            'phone': '6569876543',
        })
        db.session.refresh(self.student)
        self.assertEqual(self.student.email, original_email)
        self.assertEqual(self.student.username, original_username)
        self.assertEqual(self.student.phone, '6569876543')

    def test_parses_birth_date(self):
        svc.update_personal_info(self.student.id, self.coord.id, {
            'birth_date': '1995-06-15',
        })
        db.session.refresh(self.student)
        self.assertEqual(self.student.birth_date, date(1995, 6, 15))

    def test_logs_user_history(self):
        svc.update_personal_info(self.student.id, self.coord.id, {
            'phone': '6561111111',
        })
        h = UserHistory.query.filter_by(
            user_id=self.student.id, action='personal_info_updated',
        ).count()
        self.assertEqual(h, 1)

    def test_notifies_student(self):
        svc.update_personal_info(self.student.id, self.coord.id, {
            'address': 'Calle Nueva 100',
        })
        n = Notification.query.filter_by(
            user_id=self.student.id, type='personal_info_updated',
        ).count()
        self.assertEqual(n, 1)

    def test_no_change_skips_log(self):
        svc.update_personal_info(self.student.id, self.coord.id, {
            'phone': self.student.phone,  # unchanged
        })
        h = UserHistory.query.filter_by(
            user_id=self.student.id, action='personal_info_updated',
        ).count()
        self.assertEqual(h, 0)


if __name__ == '__main__':
    unittest.main()
