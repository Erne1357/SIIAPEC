# tests/events/test_invitations_service.py
"""
Unit tests for EventsService invitation methods:
  - invite_students (single, batch, already invited, already registered, wrong program)
  - respond_to_invitation (accept, reject, reconsider, cancelled)
  - cancel_invitation
  - get_my_invitations
  - get_event_invitations
"""

import unittest
from unittest.mock import patch, MagicMock

from app import create_app, db
from app.models.event import Event, EventInvitation, EventAttendance
from app.models.user_program import UserProgram
from app.services.events_service import EventsService
from tests.events.conftest import (
    make_test_config, make_role, make_user, make_program,
)


def _make_event(admin_id, prog_id, capacity_type='multiple', max_capacity=50):
    ev = Event(
        program_id=prog_id,
        type='conference',
        title='Invitation Event',
        description='',
        location='',
        created_by=admin_id,
        visible_to_students=True,
        capacity_type=capacity_type,
        max_capacity=max_capacity,
        requires_registration=True,
        allows_attendance_tracking=False,
        reminders_enabled=True,
        status='published',
        visibility='private',
    )
    db.session.add(ev)
    db.session.flush()
    return ev


class TestInviteStudents(unittest.TestCase):

    def setUp(self):
        self.app = create_app(make_test_config())
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        role_admin = make_role('program_admin')
        self.admin = make_user(role_admin, suffix='_adm')
        self.prog = make_program(self.admin)

        role_student = make_role('student')
        self.s1 = make_user(role_student, suffix='_s1')
        self.s2 = make_user(role_student, suffix='_s2')
        db.session.commit()

        self.ev = _make_event(self.admin.id, None)  # global event
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    @patch('app.services.events_service.EventsService.get_my_invitations')
    @patch('app.services.notification_service.NotificationService.notify_event_invitation')
    @patch('app.services.user_history_service.UserHistoryService.log_event_invitation')
    def test_invite_single_student(self, mock_log, mock_notif, mock_get_my):
        mock_notif.return_value = MagicMock(id=1)
        with self.app.test_request_context('/'):
            results = EventsService.invite_students(
                event_id=self.ev.id,
                user_ids=[self.s1.id],
                invited_by=self.admin.id,
            )
        self.assertIn(self.s1.id, results['invited'])

    @patch('app.services.notification_service.NotificationService.notify_event_invitation')
    @patch('app.services.user_history_service.UserHistoryService.log_event_invitation')
    def test_invite_already_invited_returns_already_invited(self, mock_log, mock_notif):
        mock_notif.return_value = MagicMock(id=1)
        with self.app.test_request_context('/'):
            # First invite
            EventsService.invite_students(
                event_id=self.ev.id,
                user_ids=[self.s1.id],
                invited_by=self.admin.id,
            )
            # Second invite (pending status)
            results = EventsService.invite_students(
                event_id=self.ev.id,
                user_ids=[self.s1.id],
                invited_by=self.admin.id,
            )
        self.assertIn(self.s1.id, results['already_invited'])

    @patch('app.services.notification_service.NotificationService.notify_event_invitation')
    @patch('app.services.user_history_service.UserHistoryService.log_event_invitation')
    def test_invite_already_registered_returns_already_registered(self, mock_log, mock_notif):
        # Register first
        att = EventAttendance(
            event_id=self.ev.id,
            user_id=self.s1.id,
            status='registered',
        )
        db.session.add(att)
        db.session.commit()

        with self.app.test_request_context('/'):
            results = EventsService.invite_students(
                event_id=self.ev.id,
                user_ids=[self.s1.id],
                invited_by=self.admin.id,
            )
        self.assertIn(self.s1.id, results['already_registered'])

    def test_invite_to_single_event_raises(self):
        self.ev.capacity_type = 'single'
        db.session.commit()
        with self.app.test_request_context('/'):
            with self.assertRaises(ValueError):
                EventsService.invite_students(
                    event_id=self.ev.id,
                    user_ids=[self.s1.id],
                    invited_by=self.admin.id,
                )

    @patch('app.services.notification_service.NotificationService.notify_event_invitation')
    @patch('app.services.user_history_service.UserHistoryService.log_event_invitation')
    def test_invite_wrong_program_returns_wrong_program(self, mock_log, mock_notif):
        """Student not enrolled in event's program goes to wrong_program."""
        # Assign event to a specific program but don't link student to that program
        self.ev.program_id = self.prog.id
        db.session.commit()

        with self.app.test_request_context('/'):
            results = EventsService.invite_students(
                event_id=self.ev.id,
                user_ids=[self.s1.id],
                invited_by=self.admin.id,
            )
        self.assertIn(self.s1.id, results['wrong_program'])

    @patch('app.services.notification_service.NotificationService.notify_event_invitation')
    @patch('app.services.user_history_service.UserHistoryService.log_event_invitation')
    def test_reinvite_rejected_becomes_pending(self, mock_log, mock_notif):
        mock_notif.return_value = MagicMock(id=1)
        # Seed a rejected invitation
        inv = EventInvitation(
            event_id=self.ev.id,
            user_id=self.s1.id,
            invited_by=self.admin.id,
            status='rejected',
        )
        db.session.add(inv)
        db.session.commit()

        with self.app.test_request_context('/'):
            results = EventsService.invite_students(
                event_id=self.ev.id,
                user_ids=[self.s1.id],
                invited_by=self.admin.id,
            )
        self.assertIn(self.s1.id, results['invited'])
        db.session.refresh(inv)
        self.assertEqual(inv.status, 'pending')

    def test_invite_nonexistent_event_raises(self):
        with self.app.test_request_context('/'):
            with self.assertRaises(ValueError):
                EventsService.invite_students(
                    event_id=99999,
                    user_ids=[self.s1.id],
                    invited_by=self.admin.id,
                )


class TestRespondToInvitation(unittest.TestCase):

    def setUp(self):
        self.app = create_app(make_test_config())
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        role_admin = make_role('program_admin')
        self.admin = make_user(role_admin, suffix='_adm')
        self.prog = make_program(self.admin)

        role_student = make_role('student')
        self.student = make_user(role_student, suffix='_stu')
        db.session.commit()

        self.ev = _make_event(self.admin.id, None)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _make_invitation(self, status='pending'):
        inv = EventInvitation(
            event_id=self.ev.id,
            user_id=self.student.id,
            invited_by=self.admin.id,
            status=status,
        )
        db.session.add(inv)
        db.session.commit()
        return inv

    def test_accept_invitation_creates_attendance(self):
        inv = self._make_invitation()
        with self.app.test_request_context('/'):
            result = EventsService.respond_to_invitation(inv.id, self.student.id, accept=True)
        self.assertEqual(result.status, 'accepted')
        att = EventAttendance.query.filter_by(
            event_id=self.ev.id, user_id=self.student.id
        ).first()
        self.assertIsNotNone(att)

    def test_reject_invitation(self):
        inv = self._make_invitation()
        with self.app.test_request_context('/'):
            result = EventsService.respond_to_invitation(inv.id, self.student.id, accept=False)
        self.assertEqual(result.status, 'rejected')

    def test_accept_already_accepted_returns_same(self):
        inv = self._make_invitation(status='accepted')
        # Pre-create attendance so register_to_event does not fail
        att = EventAttendance(
            event_id=self.ev.id,
            user_id=self.student.id,
            status='registered',
        )
        db.session.add(att)
        db.session.commit()
        with self.app.test_request_context('/'):
            result = EventsService.respond_to_invitation(inv.id, self.student.id, accept=True)
        self.assertEqual(result.status, 'accepted')

    def test_respond_to_cancelled_invitation_raises(self):
        inv = self._make_invitation(status='cancelled')
        with self.app.test_request_context('/'):
            with self.assertRaises(ValueError):
                EventsService.respond_to_invitation(inv.id, self.student.id, accept=True)

    def test_respond_wrong_user_raises(self):
        inv = self._make_invitation()
        role_other = make_role('student2')
        other = make_user(role_other, suffix='_oth')
        db.session.commit()
        with self.app.test_request_context('/'):
            with self.assertRaises(ValueError):
                EventsService.respond_to_invitation(inv.id, other.id, accept=True)

    def test_respond_invitation_not_found_raises(self):
        with self.app.test_request_context('/'):
            with self.assertRaises(ValueError):
                EventsService.respond_to_invitation(99999, self.student.id, accept=True)

    def test_reconsider_rejected_to_accepted(self):
        inv = self._make_invitation(status='rejected')
        with self.app.test_request_context('/'):
            result = EventsService.respond_to_invitation(inv.id, self.student.id, accept=True)
        self.assertEqual(result.status, 'accepted')


class TestCancelInvitation(unittest.TestCase):

    def setUp(self):
        self.app = create_app(make_test_config())
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        role_admin = make_role('program_admin')
        self.admin = make_user(role_admin, suffix='_adm')
        self.prog = make_program(self.admin)

        role_student = make_role('student')
        self.student = make_user(role_student, suffix='_stu')
        db.session.commit()

        self.ev = _make_event(self.admin.id, None)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_cancel_pending_invitation(self):
        inv = EventInvitation(
            event_id=self.ev.id,
            user_id=self.student.id,
            invited_by=self.admin.id,
            status='pending',
        )
        db.session.add(inv)
        db.session.commit()
        with self.app.test_request_context('/'):
            result = EventsService.cancel_invitation(inv.id)
        self.assertTrue(result)
        self.assertIsNone(db.session.get(EventInvitation, inv.id))

    def test_cancel_non_pending_raises(self):
        inv = EventInvitation(
            event_id=self.ev.id,
            user_id=self.student.id,
            invited_by=self.admin.id,
            status='accepted',
        )
        db.session.add(inv)
        db.session.commit()
        with self.app.test_request_context('/'):
            with self.assertRaises(ValueError):
                EventsService.cancel_invitation(inv.id)

    def test_cancel_not_found_raises(self):
        with self.app.test_request_context('/'):
            with self.assertRaises(ValueError):
                EventsService.cancel_invitation(99999)


class TestGetMyInvitations(unittest.TestCase):

    def setUp(self):
        self.app = create_app(make_test_config())
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        role_admin = make_role('program_admin')
        self.admin = make_user(role_admin, suffix='_adm')
        self.prog = make_program(self.admin)

        role_student = make_role('student')
        self.student = make_user(role_student, suffix='_stu')
        db.session.commit()

        self.ev = _make_event(self.admin.id, None)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_get_my_invitations_returns_only_pending(self):
        pending = EventInvitation(
            event_id=self.ev.id,
            user_id=self.student.id,
            invited_by=self.admin.id,
            status='pending',
        )
        accepted = EventInvitation(
            event_id=self.ev.id,
            user_id=self.admin.id,  # different user
            invited_by=self.admin.id,
            status='accepted',
        )
        db.session.add_all([pending, accepted])
        db.session.commit()

        result = EventsService.get_my_invitations(self.student.id)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['invitation_id'], pending.id)

    def test_get_my_invitations_empty(self):
        result = EventsService.get_my_invitations(self.student.id)
        self.assertEqual(result, [])

    def test_get_event_invitations(self):
        inv = EventInvitation(
            event_id=self.ev.id,
            user_id=self.student.id,
            invited_by=self.admin.id,
            status='pending',
        )
        db.session.add(inv)
        db.session.commit()
        result = EventsService.get_event_invitations(self.ev.id)
        self.assertEqual(len(result), 1)
        # The listing publishes the PUBLIC handle, never the integer primary
        # key — the console posts this value straight back to the invitation
        # and attendance endpoints, which resolve UUIDs.
        self.assertEqual(result[0]['user_id'], str(self.student.uuid))


class TestInviteToDraftEvent(unittest.TestCase):
    """
    Preparing the guest list while the event is still a draft is legitimate,
    so `invite_students` accepts it — but the *response* has to wait, because
    `user_may_participate_in_event` refuses a draft on purpose and
    `register_to_event` enforces that rule.

    The dead end this covers: the invitee saw a pending invitation in the
    dashboard, pressed "Aceptar", and got "No tienes acceso a este evento"
    with nothing to do about it.
    """

    def setUp(self):
        self.app = create_app(make_test_config())
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        role_admin = make_role('program_admin')
        self.admin = make_user(role_admin, suffix='_adm')
        self.prog = make_program(self.admin)

        role_student = make_role('student')
        self.student = make_user(role_student, suffix='_stu')
        db.session.commit()

        # Institutional draft event: nobody may participate in it yet.
        self.ev = _make_event(self.admin.id, None)
        self.ev.status = 'draft'
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _invite(self, allow_external=False):
        with patch('app.services.notification_service.NotificationService'
                   '.notify_event_invitation') as mock_notif, \
             patch('app.services.user_history_service.UserHistoryService'
                   '.log_event_invitation'):
            mock_notif.return_value = MagicMock(id=1)
            with self.app.test_request_context('/'):
                return EventsService.invite_students(
                    event_id=self.ev.id,
                    user_ids=[self.student.id],
                    invited_by=self.admin.id,
                    allow_external=allow_external,
                )

    def _publish(self):
        self.ev.status = 'published'
        self.ev.visible_to_students = True
        db.session.commit()

    def _invitation(self):
        return EventInvitation.query.filter_by(
            event_id=self.ev.id, user_id=self.student.id
        ).first()

    # -- inviting is allowed while the event is a draft ------------------

    def test_invite_while_draft_creates_invitation(self):
        results = self._invite()
        self.assertIn(self.student.id, results['invited'])
        self.assertEqual(self._invitation().status, 'pending')

    def test_invite_while_draft_flags_pending_publication(self):
        results = self._invite()
        self.assertTrue(results['pending_publication'])

    def test_invite_to_published_event_does_not_flag_pending_publication(self):
        self._publish()
        results = self._invite()
        self.assertFalse(results['pending_publication'])

    def test_invite_to_hidden_event_flags_pending_publication(self):
        self.ev.status = 'published'
        self.ev.visible_to_students = False
        db.session.commit()
        results = self._invite()
        self.assertTrue(results['pending_publication'])

    def test_invite_to_closed_event_raises(self):
        """A cancelled/finished event could never resolve the invitation."""
        for closed in ('cancelled', 'completed'):
            with self.subTest(status=closed):
                self.ev.status = closed
                db.session.commit()
                with self.app.test_request_context('/'):
                    with self.assertRaises(ValueError):
                        EventsService.invite_students(
                            event_id=self.ev.id,
                            user_ids=[self.student.id],
                            invited_by=self.admin.id,
                        )

    # -- the invitation is not advertised until it can be answered -------

    def test_draft_invitation_hidden_from_my_invitations(self):
        self._invite()
        self.assertEqual(EventsService.get_my_invitations(self.student.id), [])

    def test_draft_invitation_hidden_from_dashboard_widget(self):
        self._invite()
        with self.app.test_request_context('/'):
            widget = EventsService.get_dashboard_widget(self.student.id)
        self.assertEqual(widget['pending_invitations'], [])

    def test_invitation_appears_after_publication(self):
        self._invite()
        self._publish()

        listed = EventsService.get_my_invitations(self.student.id)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]['invitation_id'], self._invitation().id)

        with self.app.test_request_context('/'):
            widget = EventsService.get_dashboard_widget(self.student.id)
        self.assertEqual(len(widget['pending_invitations']), 1)

    # -- accepting: before vs after publication --------------------------

    def test_accept_before_publication_fails_with_spanish_reason(self):
        self._invite()
        inv_id = self._invitation().id

        with self.app.test_request_context('/'):
            with self.assertRaises(ValueError) as cm:
                EventsService.respond_to_invitation(inv_id, self.student.id, accept=True)

        self.assertIn('no está publicado', str(cm.exception))
        # The invitation survives untouched: a draft is temporary, and a
        # temporary obstacle must not burn the invitation as 'rejected'.
        db.session.rollback()
        inv = db.session.get(EventInvitation, inv_id)
        self.assertEqual(inv.status, 'pending')
        self.assertIsNone(inv.responded_at)
        self.assertIsNone(
            EventAttendance.query.filter_by(
                event_id=self.ev.id, user_id=self.student.id
            ).first()
        )

    def test_invite_while_draft_then_publish_then_accept(self):
        self._invite()
        inv_id = self._invitation().id
        self._publish()

        with self.app.test_request_context('/'):
            result = EventsService.respond_to_invitation(inv_id, self.student.id, accept=True)

        self.assertEqual(result.status, 'accepted')
        self.assertIsNotNone(
            EventAttendance.query.filter_by(
                event_id=self.ev.id, user_id=self.student.id
            ).first()
        )

    def test_reject_before_publication_is_allowed(self):
        """Declining needs no participation right — only accepting does."""
        self._invite()
        inv_id = self._invitation().id

        with self.app.test_request_context('/'):
            result = EventsService.respond_to_invitation(inv_id, self.student.id, accept=False)

        self.assertEqual(result.status, 'rejected')

    # -- the shipped cross-program flow must keep working ----------------

    def test_external_invitee_of_another_program_can_still_accept(self):
        """`allow_external=True` on a program event, invitee from elsewhere."""
        other_prog = make_program(self.admin, slug='other-prog')
        db.session.add(UserProgram(
            user_id=self.student.id,
            program_id=other_prog.id,
            admission_status='enrolled',
        ))
        self.ev.program_id = self.prog.id      # student is NOT in this program
        db.session.commit()

        results = self._invite(allow_external=True)
        self.assertIn(self.student.id, results['invited'])

        self._publish()
        inv_id = self._invitation().id

        listed = EventsService.get_my_invitations(self.student.id)
        self.assertEqual(len(listed), 1)

        with self.app.test_request_context('/'):
            result = EventsService.respond_to_invitation(inv_id, self.student.id, accept=True)
        self.assertEqual(result.status, 'accepted')
