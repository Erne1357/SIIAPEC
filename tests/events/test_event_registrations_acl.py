# tests/events/test_event_registrations_acl.py
"""
GET /api/v1/attendance/event/<id>/registrations — the roster is a MANAGEMENT view.

The route used to answer the ownership question by itself:

    manages = EventsService.user_may_manage_event(current_user, event)
    if not manages and event.program_id is not None:
        return 403

which is the seventh local copy of the participate/manage question in this
module, and the copy said yes to something the shared rule says no to: any
holder of `attendance.api.list_registrations` could read the full roster of any
INSTITUTIONAL event — including one still in `draft` — because the branch only
looked at `program_id`.

Name + e-mail + attendance status is inside the owner's permitted cross-program
summary tier, so this was never a leak of forbidden fields; it was a scope
inconsistency, and the local branch was the recurring defect itself. The roster
is now `EventsService.user_may_manage_event`, the same guard `mark-attendance`
uses: the only consumer of the endpoint is the admin console
(`app/static/js/admin/events/detail.js`), so no participant audience loses a
view it had.

These tests pin the rule from both sides: the manager still gets the whole list
with the organiser's notes, and permission-without-authority is turned away —
for a published institutional event, for a draft one, and for another
program's.

The denial is `404 "Evento no encontrado."`, byte for byte what a nonexistent
id returns, because the event listing this id comes from is already scope
filtered: an event the caller cannot manage was never on their list, so its
existence is not theirs to confirm by sweeping ids. The previous
`404 ausente / 403 ajeno` split was exactly that oracle.
"""

import unittest

from flask import g

from app import create_app, db
from app.models.event import EventAttendance
from tests.events.conftest import (
    make_test_config, make_role, make_user, make_program, make_event,
    enroll_in_program, grant_permission, login,
)


class EventRegistrationsAclTestCase(unittest.TestCase):
    """Two programs, a jefe de posgrado, and four events to ask about."""

    def setUp(self):
        self.app = create_app(make_test_config())
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        role_admin = make_role('program_admin')
        role_postgrad = make_role('postgraduate_admin')
        role_student = make_role('student')

        # Everybody in this test HOLDS the capability. The point of the suite is
        # that the capability is not the authority.
        for role in (role_admin, role_postgrad, role_student):
            grant_permission(role, 'attendance.api.list_registrations')
        # Role-level global scope marker: get_accessible_program_ids() -> None
        grant_permission(role_postgrad, 'academic_periods.api.create')

        self.coord_a = make_user(role_admin, suffix='_coord_a')
        self.coord_b = make_user(role_admin, suffix='_coord_b')
        self.postgrad = make_user(role_postgrad)
        self.student_a = make_user(role_student, suffix='_stu_a')

        self.prog_a = make_program(self.coord_a, slug='prog-a')
        self.prog_b = make_program(self.coord_b, slug='prog-b')
        enroll_in_program(self.student_a, self.prog_a)

        self.ev_institutional = make_event(
            created_by=self.postgrad.id, program_id=None,
            title='Feria institucional',
        )
        self.ev_institutional_draft = make_event(
            created_by=self.postgrad.id, program_id=None,
            title='Feria en borrador', status='draft',
        )
        self.ev_a = make_event(
            created_by=self.coord_a.id, program_id=self.prog_a.id,
            title='Seminario de A',
        )
        self.ev_b = make_event(
            created_by=self.coord_b.id, program_id=self.prog_b.id,
            title='Seminario de B',
        )

        # One registration per event, with an organiser note attached.
        for event in (self.ev_institutional, self.ev_institutional_draft,
                      self.ev_a, self.ev_b):
            db.session.add(EventAttendance(
                event_id=event.id, user_id=self.student_a.id,
                status='registered', notes='Nota del organizador',
            ))
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    # ── helpers ─────────────────────────────────────────────────────────────
    def _as(self, user):
        """Fresh client for `user`, with the app-context login cache cleared."""
        client = self.app.test_client()
        login(client, user)
        return client

    def _roster(self, user, event_id):
        client = self._as(user)
        if hasattr(g, '_login_user'):
            del g._login_user
        return client.get(f'/api/v1/attendance/event/{event_id}/registrations')

    # ── the manager still sees everything ───────────────────────────────────
    def test_coordinator_reads_the_roster_of_their_own_event(self):
        res = self._roster(self.coord_a, self.ev_a.id)
        self.assertEqual(res.status_code, 200, res.data.decode('utf-8'))
        body = res.get_json()
        self.assertEqual(body['total'], 1)
        self.assertTrue(body['can_manage'])
        self.assertEqual(body['registrations'][0]['email'], self.student_a.email)
        # A manager gets the organiser's notes — that is what managing means.
        self.assertEqual(body['registrations'][0]['notes'], 'Nota del organizador')

    def test_postgraduate_admin_reads_the_institutional_roster(self):
        for event in (self.ev_institutional, self.ev_institutional_draft,
                      self.ev_a, self.ev_b):
            res = self._roster(self.postgrad, event.id)
            self.assertEqual(res.status_code, 200,
                             msg=f'event {event.title}: {res.data.decode("utf-8")}')

    # ── the defect: institutional events were open to every permission holder
    def test_institutional_roster_is_not_open_to_a_mere_permission_holder(self):
        res = self._roster(self.coord_a, self.ev_institutional.id)
        self.assertEqual(res.status_code, 404, res.data.decode('utf-8'))

    def test_draft_institutional_roster_is_not_open_either(self):
        res = self._roster(self.coord_a, self.ev_institutional_draft.id)
        self.assertEqual(res.status_code, 404, res.data.decode('utf-8'))

    def test_student_registered_to_the_event_still_cannot_read_the_roster(self):
        """Being on the list is not authority to read the list."""
        res = self._roster(self.student_a, self.ev_institutional.id)
        self.assertEqual(res.status_code, 404, res.data.decode('utf-8'))

    def test_other_programs_roster_stays_closed(self):
        res = self._roster(self.coord_a, self.ev_b.id)
        self.assertEqual(res.status_code, 404, res.data.decode('utf-8'))

    def test_denial_is_indistinguishable_from_absence(self):
        """Same status AND same message, or the id sweep becomes an oracle."""
        foreign = self._roster(self.coord_a, self.ev_b.id)
        missing = self._roster(self.coord_a, 999999)
        self.assertEqual(foreign.status_code, missing.status_code)
        self.assertEqual(foreign.get_json()['error'], missing.get_json()['error'])

    def test_unknown_event_is_404(self):
        res = self._roster(self.postgrad, 999999)
        self.assertEqual(res.status_code, 404)

    def test_anonymous_is_rejected(self):
        client = self.app.test_client()
        if hasattr(g, '_login_user'):
            del g._login_user
        res = client.get(
            f'/api/v1/attendance/event/{self.ev_institutional.id}/registrations')
        self.assertIn(res.status_code, (302, 401))


if __name__ == '__main__':
    unittest.main()
