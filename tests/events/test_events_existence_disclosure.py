# tests/events/test_events_existence_disclosure.py
"""
F29 — the events module must not answer "does this id exist?".

A denial carries two answers: "may you?" and, through its status code, "does it
exist?". The management routes used to answer 403 when the caller lacked scope
and 404 when the row was absent, while `invitations_api.cancel_invitation`
twenty lines away had deliberately chosen 404 for both. That mismatch is an
enumeration oracle: a `program_admin` of program A sweeps the ids and every 403
marks a real event of somebody else's programme — how many the institution runs
and in what id range — without ever seeing one.

The rule now applied across the module:

  * 404 when the caller CANNOT already know the object exists. `GET
    /api/v1/events` is scope-filtered, so an event they cannot manage was never
    on any list they received. Absence and denial must be indistinguishable —
    same status AND same message, including for child ids (windows, slots,
    images), where two different 404 texts rebuild the same oracle one level
    down.
  * 403 when the caller DOES already know: the participation reads
    (`/slots`, `/hosts`, `/images`, `/public/<id>`) reached from a listing they
    legitimately received, where "existe pero no es para ti" is the useful
    answer.
"""

import json
import unittest
from datetime import date, datetime, time, timedelta

from app import create_app, db
from app.models.event import Event, EventImage, EventWindow, EventSlot
from tests.events.conftest import (
    make_test_config, make_role, make_user, make_program, grant_permission,
    login,
)

#: A high id that certainly does not exist in a freshly created schema.
ABSENT_ID = 987654


MANAGEMENT_PERMISSIONS = (
    'events.api.list',
    'events.api.manage',
    'events.api.create',
    'events.api.create_window',
    'events.api.generate_slots',
    'events.api.conclude',
    'events.api.archive',
    'events.api.manage_hosts',
    'events.api.manage_images',
)


class ExistenceDisclosureBase(unittest.TestCase):
    """
    Two programmes, two coordinators. `coord_a` holds every management
    permission — the point is that CAPABILITY is not SCOPE — and probes the
    event of programme B.
    """

    def setUp(self):
        self.app = create_app(make_test_config())
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.role_admin = make_role('program_admin')
        for codename in MANAGEMENT_PERMISSIONS:
            grant_permission(self.role_admin, codename)

        self.coord_a = make_user(self.role_admin, suffix='_a')
        self.coord_b = make_user(self.role_admin, suffix='_b')
        db.session.flush()

        self.prog_a = make_program(self.coord_a, slug='prog-a')
        self.prog_b = make_program(self.coord_b, slug='prog-b')
        db.session.commit()

        self.ev_b = Event(
            program_id=self.prog_b.id,
            type='conference',
            title='Evento del programa B',
            description='',
            location='',
            created_by=self.coord_b.id,
            visible_to_students=True,
            capacity_type='single',
            max_capacity=None,
            requires_registration=True,
            allows_attendance_tracking=False,
            reminders_enabled=True,
            status='published',
            visibility='public',
        )
        db.session.add(self.ev_b)
        db.session.commit()

        self.client_a = self.app.test_client()
        self.csrf_a = login(self.client_a, self.coord_a)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    # ── helpers ──────────────────────────────────────────────────────────

    def _send(self, method, url):
        return getattr(self.client_a, method)(
            url, headers={'X-CSRFToken': self.csrf_a}
        )

    def _assertIndistinguishable(self, method, url_foreign, url_absent):
        """
        The foreign object and the absent one must be answered identically:
        same status AND same message. A different message is the same oracle
        wearing a different hat.
        """
        foreign = self._send(method, url_foreign)
        absent = self._send(method, url_absent)

        self.assertEqual(
            foreign.status_code, 404,
            f'{method.upper()} {url_foreign} delata que el id existe',
        )
        self.assertEqual(absent.status_code, 404)
        self.assertEqual(
            json.loads(foreign.data)['error']['message'],
            json.loads(absent.data)['error']['message'],
            f'{method.upper()} {url_foreign} usa un mensaje distinto al del id inexistente',
        )


class TestEventIdsAreNotEnumerable(ExistenceDisclosureBase):
    """Routes addressed by an EVENT id."""

    def test_get_event_details(self):
        self._assertIndistinguishable(
            'get', f'/api/v1/events/{self.ev_b.id}', f'/api/v1/events/{ABSENT_ID}',
        )

    def test_delete_event(self):
        self._assertIndistinguishable(
            'delete', f'/api/v1/events/{self.ev_b.id}', f'/api/v1/events/{ABSENT_ID}',
        )

    def test_update_event(self):
        self._assertIndistinguishable(
            'put', f'/api/v1/events/{self.ev_b.id}', f'/api/v1/events/{ABSENT_ID}',
        )

    def test_windows_list(self):
        self._assertIndistinguishable(
            'get',
            f'/api/v1/events/{self.ev_b.id}/windows-list',
            f'/api/v1/events/{ABSENT_ID}/windows-list',
        )

    def test_add_window(self):
        self._assertIndistinguishable(
            'post',
            f'/api/v1/events/{self.ev_b.id}/windows',
            f'/api/v1/events/{ABSENT_ID}/windows',
        )

    def test_conclude(self):
        self._assertIndistinguishable(
            'post',
            f'/api/v1/events/{self.ev_b.id}/conclude',
            f'/api/v1/events/{ABSENT_ID}/conclude',
        )

    def test_archive(self):
        self._assertIndistinguishable(
            'post',
            f'/api/v1/events/{self.ev_b.id}/archive',
            f'/api/v1/events/{ABSENT_ID}/archive',
        )

    def test_unarchive(self):
        self._assertIndistinguishable(
            'post',
            f'/api/v1/events/{self.ev_b.id}/unarchive',
            f'/api/v1/events/{ABSENT_ID}/unarchive',
        )

    def test_delete_event_does_not_delete(self):
        self._send('delete', f'/api/v1/events/{self.ev_b.id}')
        db.session.expire_all()
        self.assertIsNotNone(db.session.get(Event, self.ev_b.id))


class TestChildIdsAreNotEnumerable(ExistenceDisclosureBase):
    """
    Routes addressed by a WINDOW / SLOT / IMAGE id. These already answered 404
    for an absent child, so the leak here is the MESSAGE: returning the
    parent's "Evento no encontrado." for a foreign child and the child's own
    text for an absent one rebuilds the oracle one level down.
    """

    def setUp(self):
        super().setUp()
        day = date.today() + timedelta(days=7)
        self.win_b = EventWindow(
            event_id=self.ev_b.id,
            date=day,
            start_time=time(9, 0),
            end_time=time(10, 0),
            slot_minutes=30,
            timezone='America/Ciudad_Juarez',
        )
        db.session.add(self.win_b)
        db.session.flush()

        self.slot_b = EventSlot(
            event_window_id=self.win_b.id,
            starts_at=datetime.combine(day, time(9, 0)),
            ends_at=datetime.combine(day, time(9, 30)),
            status='free',
        )
        self.img_b = EventImage(
            event_id=self.ev_b.id,
            path=f'{self.ev_b.id}/cover.webp',
            is_cover=True,
            display_order=0,
        )
        db.session.add_all([self.slot_b, self.img_b])
        db.session.commit()

    def test_generate_slots(self):
        self._assertIndistinguishable(
            'post',
            f'/api/v1/events/windows/{self.win_b.id}/generate-slots',
            f'/api/v1/events/windows/{ABSENT_ID}/generate-slots',
        )

    def test_delete_window(self):
        self._assertIndistinguishable(
            'delete',
            f'/api/v1/events/windows/{self.win_b.id}',
            f'/api/v1/events/windows/{ABSENT_ID}',
        )

    def test_delete_slot(self):
        self._assertIndistinguishable(
            'delete',
            f'/api/v1/events/slots/{self.slot_b.id}',
            f'/api/v1/events/slots/{ABSENT_ID}',
        )

    def test_delete_image(self):
        self._assertIndistinguishable(
            'delete',
            f'/api/v1/events/images/{self.img_b.id}',
            f'/api/v1/events/images/{ABSENT_ID}',
        )

    def test_nothing_was_actually_deleted(self):
        self._send('delete', f'/api/v1/events/windows/{self.win_b.id}')
        self._send('delete', f'/api/v1/events/slots/{self.slot_b.id}')
        self._send('delete', f'/api/v1/events/images/{self.img_b.id}')
        db.session.expire_all()
        self.assertIsNotNone(db.session.get(EventWindow, self.win_b.id))
        self.assertIsNotNone(db.session.get(EventSlot, self.slot_b.id))
        self.assertIsNotNone(db.session.get(EventImage, self.img_b.id))


class TestParticipationReadsStay403(ExistenceDisclosureBase):
    """
    The other bucket. `ev_b` is public and published, so it reaches every
    listing the caller legitimately receives; its existence is already known
    and 403 is the more useful answer. This test exists so the 404 sweep above
    is never widened onto these routes by accident.
    """

    def test_slots_hosts_and_images_still_403(self):
        for url in (
            f'/api/v1/events/{self.ev_b.id}/slots',
            f'/api/v1/events/{self.ev_b.id}/hosts',
            f'/api/v1/events/{self.ev_b.id}/images',
        ):
            resp = self.client_a.get(url)
            self.assertIn(
                resp.status_code, (200, 403),
                f'{url} salió del bucket de participación',
            )


if __name__ == '__main__':
    unittest.main()
