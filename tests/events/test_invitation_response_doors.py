# tests/events/test_invitation_response_doors.py
"""
F27 — the invitation state machine has exactly ONE home.

There are two doors into "respond to an invitation":

    POST /api/v1/invitations/<invitation_id>/respond        (sanctioned)
    POST /api/v1/notifications/<notification_id>/respond-invitation

The second one used to be a full second implementation: it wrote
`EventInvitation.status` and inserted an `EventAttendance` row by hand, without
consulting `invitation_block_reason` or `register_to_event`. So an invitee could
accept into states the sanctioned door forbids — draft, hidden, cancelled,
completed, over capacity — and walk away with a registration no rule had
granted. The rows belonged to their own event, so it never widened their reach;
what it broke is that the state machine had two versions and only one was right.

These tests pin the shape of the fix: for every state, BOTH doors must agree —
same acceptance, same refusal, and no `EventAttendance` row left behind when the
answer is no.
"""

import json
import unittest

from app import create_app, db
from app.models.event import Event, EventInvitation, EventAttendance
from app.models.notification import Notification
from app.models.user_program import UserProgram
from tests.events.conftest import (
    make_test_config, make_role, make_user, make_program,
    make_academic_period, login,
)


class InvitationDoorsBase(unittest.TestCase):
    """One published event, one invited student of its own programme."""

    def setUp(self):
        self.app = create_app(make_test_config())
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.role_admin = make_role('program_admin')
        self.admin = make_user(self.role_admin, suffix='_adm')
        self.prog = make_program(self.admin)
        self.period = make_academic_period()

        self.role_student = make_role('student')
        self.student = make_user(self.role_student, suffix='_stu')
        db.session.flush()

        db.session.add(UserProgram(
            user_id=self.student.id,
            program_id=self.prog.id,
            admission_period_id=self.period.id,
            admission_status='in_progress',
        ))
        db.session.commit()

        self.ev = Event(
            program_id=self.prog.id,
            type='conference',
            title='Evento con dos puertas',
            description='',
            location='',
            created_by=self.admin.id,
            visible_to_students=True,
            capacity_type='multiple',
            max_capacity=50,
            requires_registration=True,
            allows_attendance_tracking=False,
            reminders_enabled=True,
            status='published',
            visibility='private',
        )
        db.session.add(self.ev)
        db.session.commit()

        self.inv = EventInvitation(
            event_id=self.ev.id,
            user_id=self.student.id,
            invited_by=self.admin.id,
            status='pending',
        )
        db.session.add(self.inv)
        db.session.flush()

        self.notif = Notification(
            user_id=self.student.id,
            type='event_invitation',
            title='Invitación',
            message='Te invitaron a un evento.',
            is_actionable=True,
            related_invitation_id=self.inv.id,
        )
        db.session.add(self.notif)
        db.session.commit()

        self.client = self.app.test_client()
        self.csrf = login(self.client, self.student)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    # ── doors ────────────────────────────────────────────────────────────

    def _notification_door(self, response='accepted'):
        return self.client.post(
            f'/api/v1/notifications/{self.notif.id}/respond-invitation',
            data=json.dumps({'response': response}),
            content_type='application/json',
            headers={'X-CSRFToken': self.csrf},
        )

    def _sanctioned_door(self, accept=True):
        return self.client.post(
            f'/api/v1/invitations/{self.inv.id}/respond',
            data=json.dumps({'accept': accept}),
            content_type='application/json',
            headers={'X-CSRFToken': self.csrf},
        )

    # ── assertions ───────────────────────────────────────────────────────

    def _attendance_count(self):
        return EventAttendance.query.filter_by(
            event_id=self.ev.id, user_id=self.student.id
        ).count()

    def assertRefusedByBothDoors(self):
        """Neither door may move the invitation nor leave an attendance row."""
        for door in (self._sanctioned_door, self._notification_door):
            resp = door()
            self.assertEqual(
                resp.status_code, 400,
                f'{door.__name__} aceptó una respuesta que la regla prohíbe',
            )
            db.session.expire_all()
            self.assertEqual(
                db.session.get(EventInvitation, self.inv.id).status, 'pending',
                f'{door.__name__} movió el estado de la invitación',
            )
            self.assertEqual(
                self._attendance_count(), 0,
                f'{door.__name__} dejó un registro de asistencia sin autorizar',
            )


class TestHappyPath(InvitationDoorsBase):

    def test_notification_door_accepts_and_registers(self):
        resp = self._notification_door('accepted')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            json.loads(resp.data)['data']['invitation_status'], 'accepted'
        )
        db.session.expire_all()
        self.assertEqual(
            db.session.get(EventInvitation, self.inv.id).status, 'accepted'
        )
        self.assertEqual(self._attendance_count(), 1)

    def test_notification_door_marks_notification_read(self):
        self._notification_door('accepted')
        db.session.expire_all()
        self.assertTrue(db.session.get(Notification, self.notif.id).is_read)

    def test_rejecting_never_creates_attendance(self):
        resp = self._notification_door('rejected')
        self.assertEqual(resp.status_code, 200)
        db.session.expire_all()
        self.assertEqual(
            db.session.get(EventInvitation, self.inv.id).status, 'rejected'
        )
        self.assertEqual(self._attendance_count(), 0)

    def test_invalid_response_value_is_rejected(self):
        resp = self._notification_door('maybe')
        self.assertEqual(resp.status_code, 400)
        db.session.expire_all()
        self.assertEqual(
            db.session.get(EventInvitation, self.inv.id).status, 'pending'
        )


class TestDraftEvent(InvitationDoorsBase):
    """Inviting into a draft is legal; ACCEPTING is what has to wait."""

    def setUp(self):
        super().setUp()
        self.ev.status = 'draft'
        db.session.commit()

    def test_both_doors_refuse(self):
        self.assertRefusedByBothDoors()

    def test_notification_door_leaves_notification_unread(self):
        self._notification_door('accepted')
        db.session.expire_all()
        self.assertFalse(db.session.get(Notification, self.notif.id).is_read)


class TestHiddenEvent(InvitationDoorsBase):

    def setUp(self):
        super().setUp()
        self.ev.visible_to_students = False
        db.session.commit()

    def test_both_doors_refuse(self):
        self.assertRefusedByBothDoors()


class TestCancelledEvent(InvitationDoorsBase):

    def setUp(self):
        super().setUp()
        self.ev.status = 'cancelled'
        db.session.commit()

    def test_both_doors_refuse(self):
        self.assertRefusedByBothDoors()


class TestCompletedEvent(InvitationDoorsBase):

    def setUp(self):
        super().setUp()
        self.ev.status = 'completed'
        db.session.commit()

    def test_both_doors_refuse(self):
        self.assertRefusedByBothDoors()


class TestFullEvent(InvitationDoorsBase):
    """Capacity is part of the state machine, not of the sanctioned door."""

    def setUp(self):
        super().setUp()
        self.ev.max_capacity = 1
        filler = make_user(self.role_student, suffix='_lleno')
        db.session.flush()
        db.session.add(EventAttendance(
            event_id=self.ev.id, user_id=filler.id, status='registered',
        ))
        db.session.commit()

    def test_notification_door_refuses_when_full(self):
        resp = self._notification_door('accepted')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self._attendance_count(), 0)


class TestCancelledInvitation(InvitationDoorsBase):

    def setUp(self):
        super().setUp()
        self.inv.status = 'cancelled'
        db.session.commit()

    def test_notification_door_refuses(self):
        resp = self._notification_door('accepted')
        self.assertEqual(resp.status_code, 400)
        db.session.expire_all()
        self.assertEqual(
            db.session.get(EventInvitation, self.inv.id).status, 'cancelled'
        )
        self.assertEqual(self._attendance_count(), 0)


class TestForeignNotification(InvitationDoorsBase):
    """The notification is the entry point, never the authorisation."""

    def setUp(self):
        super().setUp()
        self.other = make_user(self.role_student, suffix='_ajeno')
        db.session.commit()
        self.other_client = self.app.test_client()
        self.other_csrf = login(self.other_client, self.other)

    def test_another_users_notification_is_not_found(self):
        resp = self.other_client.post(
            f'/api/v1/notifications/{self.notif.id}/respond-invitation',
            data=json.dumps({'response': 'accepted'}),
            content_type='application/json',
            headers={'X-CSRFToken': self.other_csrf},
        )
        self.assertEqual(resp.status_code, 404)
        db.session.expire_all()
        self.assertEqual(
            db.session.get(EventInvitation, self.inv.id).status, 'pending'
        )
        self.assertEqual(self._attendance_count(), 0)


if __name__ == '__main__':
    unittest.main()
