# tests/appointments_scope/test_appointment_event_read_acl.py
"""
F19 — an Appointment is not a self-granted authorisation.

Two things are pinned here:

  * BOOKING goes through the single participant rule
    (`EventsService.user_may_participate_in_event`). The local copy that used
    to live in `appointments_api` read `visible_to_students` without reading
    `visibility`, so an applicant could book a slot of a PRIVATE event — of
    their own program or of anybody's — and the API answered 201.

  * READING the event behind an appointment is re-derived at read time
    (`AppointmentsService.user_may_read_event_of_appointment`). Owning the
    appointment is not enough: a booking the applicant wrote for themselves
    stops publishing the event's title / type / location / description as soon
    as the organiser closes the event, exactly like an `EventAttendance` row.
    An appointment ASSIGNED BY A COORDINATOR is a third-party grant and keeps
    working — that is the admission interview, the core flow of the system.
"""

import json
import unittest
from datetime import datetime, timedelta

from app import create_app, db
from app.models.appointment import Appointment
from app.models.event import Event, EventWindow, EventSlot, EventInvitation
from app.models.user_program import UserProgram

from tests.events.conftest import (
    make_test_config, make_role, make_user, make_program,
    make_academic_period, grant_permission, login,
)


class AppointmentEventReadACLTestCase(unittest.TestCase):

    def setUp(self):
        self.app = create_app(make_test_config())
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        role_admin = make_role('program_admin')
        for code in ('appointments.api.assign', 'events.api.manage'):
            grant_permission(role_admin, code)

        role_applicant = make_role('applicant')
        for code in ('appointments.api.book', 'appointments.api.cancel',
                     'appointments.api.change_request', 'appointments.api.list_own'):
            grant_permission(role_applicant, code)

        self.coord_a = make_user(role_admin, suffix='_a')
        db.session.flush()

        self.program_a = make_program(self.coord_a, slug='prog-a')
        self.period = make_academic_period()

        self.applicant_a = make_user(role_applicant, suffix='_a')
        db.session.flush()

        db.session.add(UserProgram(
            user_id=self.applicant_a.id,
            program_id=self.program_a.id,
            admission_period_id=self.period.id,
            admission_status='in_progress',
        ))

        self.event_public, self.slot_public = self._make_interview_event(
            title='Entrevistas de admisión', visibility='public', hour=9,
        )
        self.event_private, self.slot_private = self._make_interview_event(
            title='Entrevistas cerradas', visibility='private', hour=13,
        )
        db.session.commit()

        self.client_coord_a = self.app.test_client()
        self.csrf_coord_a = login(self.client_coord_a, self.coord_a)
        self.client_applicant_a = self.app.test_client()
        self.csrf_applicant_a = login(self.client_applicant_a, self.applicant_a)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    # ── world helpers ────────────────────────────────────────────────────

    def _make_interview_event(self, title: str, visibility: str, hour: int):
        """A 1:1 interview event of program_a with one window and one slot."""
        event = Event(
            program_id=self.program_a.id,
            type='interview',
            title=title,
            description='Descripción interna',
            location='Sala 1',
            created_by=self.coord_a.id,
            visible_to_students=True,
            capacity_type='single',
            requires_registration=True,
            allows_attendance_tracking=False,
            reminders_enabled=True,
            status='published',
            visibility=visibility,
        )
        db.session.add(event)
        db.session.flush()

        window = EventWindow(
            event_id=event.id,
            date=(datetime.now() + timedelta(days=3)).date(),
            start_time=datetime.strptime(f'{hour:02d}:00', '%H:%M').time(),
            end_time=datetime.strptime(f'{hour + 1:02d}:00', '%H:%M').time(),
            slot_minutes=30,
            timezone='America/Ciudad_Juarez',
            slots_generated=True,
        )
        db.session.add(window)
        db.session.flush()

        base = datetime.combine(window.date, window.start_time)
        slot = EventSlot(
            event_window_id=window.id, starts_at=base,
            ends_at=base + timedelta(minutes=30), status='free',
        )
        db.session.add(slot)
        db.session.flush()
        return event, slot

    @staticmethod
    def _clear_login_cache():
        from flask import g
        if hasattr(g, '_login_user'):
            del g._login_user

    def _post(self, client, csrf, url, payload):
        self._clear_login_cache()
        return client.post(
            url,
            data=json.dumps(payload),
            content_type='application/json',
            headers={'X-CSRFToken': csrf},
        )

    def _get(self, client, url):
        self._clear_login_cache()
        return client.get(url)

    def _book_as_applicant(self, event, slot):
        return self._post(
            self.client_applicant_a, self.csrf_applicant_a, '/api/v1/appointments',
            {'event_id': event.id, 'slot_id': slot.id},
        )

    def _assign_as_coordinator(self, event, slot):
        return self._post(
            self.client_coord_a, self.csrf_coord_a, '/api/v1/appointments',
            {'event_id': event.id, 'slot_id': slot.id,
             'applicant_id': self.applicant_a.id},
        )

    def _assert_created(self, resp):
        """201 or fail with the API's own error message, not a bare 400."""
        self.assertEqual(resp.status_code, 201, resp.data.decode('utf-8'))

    def _close_event(self, event):
        """The organiser closes the event after the booking exists."""
        event.visibility = 'private'
        db.session.commit()

    # ── booking: the fifth answer is gone ────────────────────────────────

    def test_applicant_cannot_book_private_event_of_own_program(self):
        resp = self._book_as_applicant(self.event_private, self.slot_private)
        self.assertEqual(resp.status_code, 403)
        self.assertIsNone(Appointment.query.first())
        db.session.refresh(self.slot_private)
        self.assertEqual(self.slot_private.status, 'free')

    def test_applicant_can_book_their_public_admission_interview(self):
        # The core flow of the system must keep working: a 1:1 ('single')
        # event is admitted by the shared participant predicate.
        resp = self._book_as_applicant(self.event_public, self.slot_public)
        self._assert_created(resp)
        appt = Appointment.query.first()
        self.assertIsNotNone(appt)
        self.assertEqual(appt.applicant_id, self.applicant_a.id)

    def test_invited_applicant_can_book_private_event(self):
        # An EventInvitation is a third-party grant, so it opens the private
        # event exactly as it does everywhere else in the module.
        db.session.add(EventInvitation(
            event_id=self.event_private.id,
            user_id=self.applicant_a.id,
            invited_by=self.coord_a.id,
            status='pending',
        ))
        db.session.commit()

        self._assert_created(self._book_as_applicant(self.event_private, self.slot_private))

    def test_applicant_cannot_book_unpublished_event(self):
        self.event_public.status = 'draft'
        db.session.commit()
        resp = self._book_as_applicant(self.event_public, self.slot_public)
        self.assertEqual(resp.status_code, 403)
        self.assertIsNone(Appointment.query.first())

    # ── reading: the appointment is not the key ──────────────────────────

    def test_self_booked_appointment_stops_serving_event_once_closed(self):
        self._assert_created(self._book_as_applicant(self.event_public, self.slot_public))
        appt = Appointment.query.first()

        # While the event is participable the applicant reads it normally.
        resp = self._get(self.client_applicant_a, f'/api/v1/appointments/{appt.id}/details')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            json.loads(resp.data)['appointment']['event']['title'],
            'Entrevistas de admisión',
        )

        self._close_event(self.event_public)

        resp = self._get(self.client_applicant_a, f'/api/v1/appointments/{appt.id}/details')
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(json.loads(resp.data)['error']['code'], 'FORBIDDEN')

        resp = self._get(self.client_applicant_a, '/api/v1/appointments/mine/active')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json.loads(resp.data)['appointments'], [])

    def test_coordinator_assigned_appointment_survives_the_event_closing(self):
        # This is the shipped shape of every real interview: assigned by the
        # coordinator, and the event is hidden again once the round is over.
        self._assert_created(self._assign_as_coordinator(self.event_public, self.slot_public))
        appt = Appointment.query.first()
        self.assertNotEqual(appt.assigned_by, appt.applicant_id)

        self._close_event(self.event_public)
        self.event_public.visible_to_students = False
        self.event_public.status = 'completed'
        db.session.commit()

        resp = self._get(self.client_applicant_a, f'/api/v1/appointments/{appt.id}/details')
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.data)
        self.assertEqual(body['appointment']['event']['title'], 'Entrevistas de admisión')
        self.assertEqual(body['appointment']['event']['location'], 'Sala 1')

    def test_mine_active_keeps_the_coordinator_assigned_interview(self):
        self._assert_created(self._assign_as_coordinator(self.event_public, self.slot_public))
        self._close_event(self.event_public)

        resp = self._get(self.client_applicant_a, '/api/v1/appointments/mine/active')
        self.assertEqual(resp.status_code, 200)
        items = json.loads(resp.data)['appointments']
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['event_title'], 'Entrevistas de admisión')
        self.assertEqual(items[0]['location'], 'Sala 1')

    def test_coordinator_reads_the_event_of_an_appointment_they_manage(self):
        self._assert_created(self._assign_as_coordinator(self.event_public, self.slot_public))
        appt = Appointment.query.first()
        self._close_event(self.event_public)

        resp = self._get(self.client_coord_a, f'/api/v1/appointments/{appt.id}/details')
        self.assertEqual(resp.status_code, 200)


if __name__ == '__main__':
    unittest.main()
