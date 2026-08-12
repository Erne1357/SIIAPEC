# tests/profile/test_profile_photo_service.py
"""
Tests for profile_photo_service:
  - First upload allowed regardless of photo_change_allowed
  - Subsequent upload blocked unless flag is set
  - upload_photo resets flag to False
  - request_photo_change sets timestamp + notifies coordinators
  - enable_photo_change(approve=True) sets flag, (False) clears request
  - list_pending_photo_requests returns only the coordinator's program students
"""

import io
import unittest
from PIL import Image

from app import create_app, db
from app.models.notification import Notification
from app.models.user_history import UserHistory
from app.models.user_program import UserProgram
from app.services import profile_photo_service as svc

from tests.profile.conftest import (
    make_test_config, make_role, make_user, make_program,
)


def _make_jpeg_filestorage(filename='photo.jpg', size=(800, 600), color=(120, 140, 160)):
    """Build a FileStorage-compatible object backed by a JPEG bytes stream."""
    from werkzeug.datastructures import FileStorage
    img = Image.new('RGB', size, color)
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=90)
    buf.seek(0)
    return FileStorage(stream=buf, filename=filename, content_type='image/jpeg')


class TestUploadPhoto(unittest.TestCase):

    def setUp(self):
        self.app = create_app(make_test_config())
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.role_student = make_role('student')
        self.role_coord = make_role('program_admin')
        self.coord = make_user(self.role_coord, suffix='_coord')
        self.student = make_user(self.role_student, suffix='_st')
        self.program = make_program(self.coord)
        db.session.add(UserProgram(
            user_id=self.student.id,
            program_id=self.program.id,
            admission_status='enrolled',
        ))
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_first_upload_allowed(self):
        fs = _make_jpeg_filestorage()
        user = svc.upload_photo(self.student.id, fs, requester_id=self.student.id, is_self=True)
        self.assertEqual(user.avatar, 'avatar.jpg')

    def test_second_upload_blocked_without_authorization(self):
        # First
        svc.upload_photo(self.student.id, _make_jpeg_filestorage(), requester_id=self.student.id)
        with self.assertRaises(svc.PhotoChangeNotAllowed):
            svc.upload_photo(self.student.id, _make_jpeg_filestorage(), requester_id=self.student.id)

    def test_second_upload_allowed_after_authorization(self):
        svc.upload_photo(self.student.id, _make_jpeg_filestorage(), requester_id=self.student.id)
        # Coordinator authorizes
        svc.enable_photo_change(self.student.id, self.coord.id, approve=True)
        # Student uploads again
        user = svc.upload_photo(self.student.id, _make_jpeg_filestorage(), requester_id=self.student.id)
        self.assertEqual(user.avatar, 'avatar.jpg')
        self.assertFalse(user.photo_change_allowed)
        self.assertIsNone(user.photo_change_requested_at)

    def test_coordinator_upload_bypasses_block(self):
        svc.upload_photo(self.student.id, _make_jpeg_filestorage(), requester_id=self.student.id)
        # Coordinator uploads directly, no auth flag needed
        user = svc.upload_photo(
            self.student.id, _make_jpeg_filestorage(),
            requester_id=self.coord.id, is_self=False,
        )
        self.assertEqual(user.avatar, 'avatar.jpg')
        # Notification to student must exist
        notifs = Notification.query.filter_by(
            user_id=self.student.id,
            type='profile_photo_uploaded_by_coordinator',
        ).count()
        self.assertEqual(notifs, 1)

    def test_upload_logs_history(self):
        svc.upload_photo(self.student.id, _make_jpeg_filestorage(), requester_id=self.student.id)
        h = UserHistory.query.filter_by(
            user_id=self.student.id, action='profile_photo_uploaded',
        ).count()
        self.assertEqual(h, 1)


class TestRequestPhotoChange(unittest.TestCase):

    def setUp(self):
        self.app = create_app(make_test_config())
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.role_student = make_role('student')
        self.role_coord = make_role('program_admin')
        self.coord = make_user(self.role_coord, suffix='_coord')
        self.student = make_user(self.role_student, suffix='_st')
        self.program = make_program(self.coord)
        db.session.add(UserProgram(
            user_id=self.student.id,
            program_id=self.program.id,
            admission_status='enrolled',
        ))
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_request_sets_timestamp(self):
        user = svc.request_photo_change(self.student.id, reason='Cambio de imagen')
        self.assertIsNotNone(user.photo_change_requested_at)

    def test_request_notifies_coordinator(self):
        svc.request_photo_change(self.student.id, reason='Test')
        n = Notification.query.filter_by(
            user_id=self.coord.id,
            type='profile_photo_change_requested',
        ).count()
        self.assertEqual(n, 1)

    def test_duplicate_pending_request_raises(self):
        svc.request_photo_change(self.student.id, reason='primera')
        with self.assertRaises(svc.ProfilePhotoError):
            svc.request_photo_change(self.student.id, reason='segunda')

    def test_request_when_already_allowed_raises(self):
        # Manually set the flag
        self.student.photo_change_allowed = True
        db.session.commit()
        with self.assertRaises(svc.ProfilePhotoError):
            svc.request_photo_change(self.student.id)


class TestEnablePhotoChange(unittest.TestCase):

    def setUp(self):
        self.app = create_app(make_test_config())
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.role_student = make_role('student')
        self.role_coord = make_role('program_admin')
        self.coord = make_user(self.role_coord, suffix='_coord')
        self.student = make_user(self.role_student, suffix='_st')
        self.program = make_program(self.coord)
        db.session.add(UserProgram(
            user_id=self.student.id,
            program_id=self.program.id,
            admission_status='enrolled',
        ))
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_approve_sets_flag(self):
        svc.request_photo_change(self.student.id)
        user = svc.enable_photo_change(self.student.id, self.coord.id, approve=True)
        self.assertTrue(user.photo_change_allowed)
        self.assertIsNone(user.photo_change_requested_at)

    def test_approve_notifies_student(self):
        svc.request_photo_change(self.student.id)
        svc.enable_photo_change(self.student.id, self.coord.id, approve=True)
        n = Notification.query.filter_by(
            user_id=self.student.id,
            type='profile_photo_change_enabled',
        ).count()
        self.assertEqual(n, 1)

    def test_reject_clears_request(self):
        svc.request_photo_change(self.student.id)
        user = svc.enable_photo_change(
            self.student.id, self.coord.id, approve=False, reason='Innecesario',
        )
        self.assertFalse(user.photo_change_allowed)
        self.assertIsNone(user.photo_change_requested_at)
        n = Notification.query.filter_by(
            user_id=self.student.id,
            type='profile_photo_change_rejected',
        ).count()
        self.assertEqual(n, 1)


class TestListPendingPhotoRequests(unittest.TestCase):

    def setUp(self):
        self.app = create_app(make_test_config())
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.role_student = make_role('student')
        self.role_coord = make_role('program_admin')
        self.coord_a = make_user(self.role_coord, suffix='_a')
        self.coord_b = make_user(self.role_coord, suffix='_b')
        self.program_a = make_program(self.coord_a, slug='prog-a')
        self.program_b = make_program(self.coord_b, slug='prog-b')

        self.student_a = make_user(self.role_student, suffix='_sa')
        self.student_b = make_user(self.role_student, suffix='_sb')
        db.session.add(UserProgram(
            user_id=self.student_a.id, program_id=self.program_a.id,
            admission_status='enrolled',
        ))
        db.session.add(UserProgram(
            user_id=self.student_b.id, program_id=self.program_b.id,
            admission_status='enrolled',
        ))
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_returns_only_own_program_requests(self):
        svc.request_photo_change(self.student_a.id)
        svc.request_photo_change(self.student_b.id)

        items_a = svc.list_pending_photo_requests(self.coord_a.id)
        items_b = svc.list_pending_photo_requests(self.coord_b.id)

        self.assertEqual(len(items_a), 1)
        self.assertEqual(items_a[0]['user_id'], str(self.student_a.uuid))
        self.assertEqual(len(items_b), 1)
        self.assertEqual(items_b[0]['user_id'], str(self.student_b.uuid))


if __name__ == '__main__':
    unittest.main()
