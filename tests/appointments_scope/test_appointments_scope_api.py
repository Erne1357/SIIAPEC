# tests/appointments_scope/test_appointments_scope_api.py
"""
HTTP-level tests for the program scope of /api/v1/appointments.

World:
    program_a — coordinated by coord_a, with applicant_a enrolled
    program_b — coordinated by coord_b, with applicant_b enrolled
    event_a   — 1:1 interview event of program_a, one window, two free slots

What is pinned here:
  * a coordinator of another program cannot assign, cancel, mark or read an
    appointment of program_a (403 with the shared scope envelope);
  * an applicant cannot cancel or request a change on somebody else's
    appointment, and gets 404 — the existence of the id is not leaked;
  * cancelling really frees the slot: the same slot can be booked again
    afterwards (the UNIQUE on Appointment.slot_id used to make it dead).
  * /by-slot no longer publishes the institutional e-mail nor the private
    notes of the coordinator.
"""

import json
import unittest
from datetime import datetime, timedelta

from app import create_app, db
from app.models.appointment import Appointment
from app.models.event import Event, EventWindow, EventSlot
from app.models.user_program import UserProgram

from tests.events.conftest import (
    make_test_config, make_role, make_user, make_program,
    make_academic_period, grant_permission, login,
)


class AppointmentsScopeTestCase(unittest.TestCase):

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
        self.coord_b = make_user(role_admin, suffix='_b')
        db.session.flush()

        self.program_a = make_program(self.coord_a, slug='prog-a')
        self.program_b = make_program(self.coord_b, slug='prog-b')
        self.period = make_academic_period()

        self.applicant_a = make_user(role_applicant, suffix='_a')
        self.applicant_a2 = make_user(role_applicant, suffix='_a2')
        self.applicant_b = make_user(role_applicant, suffix='_b')
        db.session.flush()

        for user, program in (
            (self.applicant_a, self.program_a),
            (self.applicant_a2, self.program_a),
            (self.applicant_b, self.program_b),
        ):
            db.session.add(UserProgram(
                user_id=user.id,
                program_id=program.id,
                admission_period_id=self.period.id,
                admission_status='in_progress',
            ))

        self.event_a = Event(
            program_id=self.program_a.id,
            type='interview',
            title='Entrevistas Programa A',
            description='',
            location='Sala 1',
            created_by=self.coord_a.id,
            visible_to_students=True,
            capacity_type='single',
            requires_registration=True,
            allows_attendance_tracking=False,
            reminders_enabled=True,
            status='published',
            visibility='public',
        )
        db.session.add(self.event_a)
        db.session.flush()

        window = EventWindow(
            event_id=self.event_a.id,
            date=(datetime.now() + timedelta(days=3)).date(),
            start_time=datetime.strptime('09:00', '%H:%M').time(),
            end_time=datetime.strptime('10:00', '%H:%M').time(),
            slot_minutes=30,
            timezone='America/Ciudad_Juarez',
            slots_generated=True,
        )
        db.session.add(window)
        db.session.flush()

        base = datetime.combine(window.date, window.start_time)
        self.slot_1 = EventSlot(
            event_window_id=window.id, starts_at=base,
            ends_at=base + timedelta(minutes=30), status='free',
        )
        self.slot_2 = EventSlot(
            event_window_id=window.id, starts_at=base + timedelta(minutes=30),
            ends_at=base + timedelta(minutes=60), status='free',
        )
        db.session.add_all([self.slot_1, self.slot_2])
        db.session.commit()

        self.client_coord_a = self.app.test_client()
        self.csrf_coord_a = login(self.client_coord_a, self.coord_a)
        self.client_coord_b = self.app.test_client()
        self.csrf_coord_b = login(self.client_coord_b, self.coord_b)
        self.client_applicant_a = self.app.test_client()
        self.csrf_applicant_a = login(self.client_applicant_a, self.applicant_a)
        self.client_applicant_b = self.app.test_client()
        self.csrf_applicant_b = login(self.client_applicant_b, self.applicant_b)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _clear_login_cache():
        """
        Flask-Login cachea el usuario en `g`, y aquí el app-context vive todo
        el test: sin limpiarlo, la segunda petición se ejecutaría como el
        usuario de la primera.
        """
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

    def _delete(self, client, csrf, url):
        self._clear_login_cache()
        return client.delete(url, headers={'X-CSRFToken': csrf})

    def _get(self, client, url):
        self._clear_login_cache()
        return client.get(url)

    def _book(self, slot, applicant):
        """Assign a slot as coord_a (the legitimate path)."""
        return self._post(
            self.client_coord_a, self.csrf_coord_a, '/api/v1/appointments',
            {'event_id': self.event_a.id, 'slot_id': slot.id,
             'applicant_id': applicant.id, 'notes': 'Notas privadas'},
        )

    # ── scope: another program's coordinator ─────────────────────────────

    def test_foreign_coordinator_cannot_assign(self):
        resp = self._post(
            self.client_coord_b, self.csrf_coord_b, '/api/v1/appointments',
            {'event_id': self.event_a.id, 'slot_id': self.slot_1.id,
             'applicant_id': self.applicant_a.id},
        )
        self.assertEqual(resp.status_code, 403)
        body = json.loads(resp.data)
        self.assertEqual(body['error']['code'], 'FORBIDDEN')
        self.assertIsNone(Appointment.query.first())

    def test_foreign_coordinator_cannot_cancel(self):
        self.assertEqual(self._book(self.slot_1, self.applicant_a).status_code, 201)
        appt = Appointment.query.first()

        resp = self._post(
            self.client_coord_b, self.csrf_coord_b,
            f'/api/v1/appointments/{appt.id}/cancel', {'reason': 'porque sí'},
        )
        self.assertEqual(resp.status_code, 403)
        db.session.refresh(appt)
        self.assertEqual(appt.status, 'scheduled')

    def test_foreign_coordinator_cannot_read_slot_occupant(self):
        self.assertEqual(self._book(self.slot_1, self.applicant_a).status_code, 201)
        resp = self._get(self.client_coord_b, f'/api/v1/appointments/by-slot/{self.slot_1.id}')
        self.assertEqual(resp.status_code, 403)

    # ── scope: another applicant ─────────────────────────────────────────

    def test_applicant_cannot_cancel_someone_elses_appointment(self):
        self.assertEqual(self._book(self.slot_1, self.applicant_a).status_code, 201)
        appt = Appointment.query.first()

        resp = self._delete(
            self.client_applicant_b, self.csrf_applicant_b,
            f'/api/v1/appointments/{appt.id}',
        )
        # 404 y no 403: el id de otra persona no debe poder confirmarse.
        self.assertEqual(resp.status_code, 404)
        db.session.refresh(appt)
        self.assertEqual(appt.status, 'scheduled')
        db.session.refresh(self.slot_1)
        self.assertEqual(self.slot_1.status, 'booked')

    def test_applicant_cannot_request_change_on_someone_elses_appointment(self):
        self.assertEqual(self._book(self.slot_1, self.applicant_a).status_code, 201)
        appt = Appointment.query.first()

        resp = self._post(
            self.client_applicant_b, self.csrf_applicant_b,
            f'/api/v1/appointments/{appt.id}/change-requests',
            {'reason': 'no me sirve'},
        )
        self.assertEqual(resp.status_code, 404)

    def test_owner_can_cancel_own_appointment(self):
        self.assertEqual(self._book(self.slot_1, self.applicant_a).status_code, 201)
        appt = Appointment.query.first()

        resp = self._delete(
            self.client_applicant_a, self.csrf_applicant_a,
            f'/api/v1/appointments/{appt.id}',
        )
        self.assertEqual(resp.status_code, 200)
        db.session.refresh(appt)
        self.assertEqual(appt.status, 'cancelled')

    # ── availability: cancelling really frees the slot ───────────────────

    def test_cancelled_slot_can_be_booked_again(self):
        self.assertEqual(self._book(self.slot_1, self.applicant_a).status_code, 201)
        appt = Appointment.query.first()

        self.assertEqual(
            self._delete(self.client_applicant_a, self.csrf_applicant_a,
                         f'/api/v1/appointments/{appt.id}').status_code,
            200,
        )
        db.session.refresh(self.slot_1)
        self.assertEqual(self.slot_1.status, 'free')

        # El slot vuelve a ser reservable: antes la fila cancelada seguía
        # ocupando el UNIQUE de slot_id y el horario quedaba muerto.
        second = self._book(self.slot_1, self.applicant_a2)
        self.assertEqual(second.status_code, 201)
        db.session.refresh(self.slot_1)
        self.assertEqual(self.slot_1.status, 'booked')

    def test_double_cancel_is_rejected(self):
        self.assertEqual(self._book(self.slot_1, self.applicant_a).status_code, 201)
        appt = Appointment.query.first()
        url = f'/api/v1/appointments/{appt.id}'

        self.assertEqual(
            self._delete(self.client_applicant_a, self.csrf_applicant_a, url).status_code, 200)
        # El segundo DELETE no debe volver a notificar ni a mandar correo.
        self.assertEqual(
            self._delete(self.client_applicant_a, self.csrf_applicant_a, url).status_code, 400)

    # ── payload: /by-slot no longer leaks e-mail nor notes ───────────────

    def test_by_slot_payload_drops_email_and_notes(self):
        self.assertEqual(self._book(self.slot_1, self.applicant_a).status_code, 201)

        resp = self._get(self.client_coord_a, f'/api/v1/appointments/by-slot/{self.slot_1.id}')
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.data)
        appointment = body['appointment']
        self.assertIsNotNone(appointment)
        self.assertNotIn('notes', appointment)
        self.assertIn('full_name', appointment['student'])
        self.assertNotIn('email', appointment['student'])


if __name__ == '__main__':
    unittest.main()
