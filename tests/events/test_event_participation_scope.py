# tests/events/test_event_participation_scope.py
"""
The PARTICIPANT tier of the events module — one rule, every door.

`EventsService.user_may_participate_in_event` is the single predicate for
"may this authenticated user see or join this event". Before it existed the
module answered that question in four different ways:

  * `POST /api/v1/attendance/event/<id>/register` did not ask it at all
    (`@login_required` and nothing else), so anybody could plant an
    `EventAttendance` row on any event id in the institution;
  * `GET /api/v1/events/public/<id>` and `EventsService.list_public_events`
    then READ that self-granted row back as proof of access;
  * the hosts, images and image-bytes routes used a predicate that ignored
    `visibility` and the event's program entirely;
  * `GET /events/<id>` rendered `event.title` server-side with no check at all.

These tests pin both halves: the exploit is closed, and the ordinary student
flow (institutional + own-program published public events: list, detail,
hosts, images, register) still works end to end.
"""

import json
import tempfile
import unittest
from pathlib import Path

from flask import g

from app import create_app, db
from app.models.event import Event, EventAttendance, EventHost, EventImage, EventInvitation
from app.services import file_access_service
from app.services.events_service import EventsService
from tests.events.conftest import (
    make_test_config, make_role, make_user, make_program, make_academic_period,
    make_event, enroll_in_program, login,
)


class ParticipationBase(unittest.TestCase):
    """Two programs, one institutional event, and one student of program A."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(make_test_config(upload_folder=self.tmp.name))
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.role_admin = make_role('program_admin')
        self.coord_a = make_user(self.role_admin, suffix='_coord_a')
        self.coord_b = make_user(self.role_admin, suffix='_coord_b')
        self.prog_a = make_program(self.coord_a, slug='prog-a')
        self.prog_b = make_program(self.coord_b, slug='prog-b')

        self.role_student = make_role('student')
        self.student_a = make_user(self.role_student, suffix='_stu_a')
        enroll_in_program(self.student_a, self.prog_a)

        self.period = make_academic_period(is_active=True)

        # Institutional (no program), published, public.
        self.ev_institutional = make_event(
            created_by=self.coord_a.id, program_id=None,
            title='Feria institucional',
        )
        # Program A, published, public — the student's own programme.
        self.ev_a_public = make_event(
            created_by=self.coord_a.id, program_id=self.prog_a.id,
            title='Seminario de A',
        )
        # Program B, published, public — someone else's programme.
        self.ev_b_public = make_event(
            created_by=self.coord_b.id, program_id=self.prog_b.id,
            title='Seminario de B',
        )
        # Program B, published, PRIVATE — invitation only.
        self.ev_b_private = make_event(
            created_by=self.coord_b.id, program_id=self.prog_b.id,
            title='Comite secreto de B', visibility='private',
        )
        # Program A, but draft / hidden.
        self.ev_a_draft = make_event(
            created_by=self.coord_a.id, program_id=self.prog_a.id,
            title='Borrador de A', status='draft',
        )
        self.ev_a_hidden = make_event(
            created_by=self.coord_a.id, program_id=self.prog_a.id,
            title='Oculto de A', visible_to_students=False,
        )
        db.session.commit()

        self.client = self.app.test_client()
        self.csrf = login(self.client, self.student_a)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        self.tmp.cleanup()

    @staticmethod
    def _clear_login_cache():
        if hasattr(g, '_login_user'):
            del g._login_user

    def _get(self, url):
        self._clear_login_cache()
        return self.client.get(url)

    def _post(self, url, payload=None):
        self._clear_login_cache()
        return self.client.post(
            url,
            data=json.dumps(payload or {}),
            content_type='application/json',
            headers={'X-CSRFToken': self.csrf},
        )


class TestParticipantPredicate(ParticipationBase):
    """The predicate itself — the truth table of the participant tier."""

    def test_institutional_published_public_event_is_participable(self):
        self.assertTrue(EventsService.user_may_participate_in_event(
            self.student_a, self.ev_institutional))

    def test_own_program_published_public_event_is_participable(self):
        self.assertTrue(EventsService.user_may_participate_in_event(
            self.student_a, self.ev_a_public))

    def test_other_program_public_event_is_not_participable(self):
        self.assertFalse(EventsService.user_may_participate_in_event(
            self.student_a, self.ev_b_public))

    def test_private_event_without_invitation_is_not_participable(self):
        self.assertFalse(EventsService.user_may_participate_in_event(
            self.student_a, self.ev_b_private))

    def test_private_event_with_invitation_is_participable(self):
        db.session.add(EventInvitation(
            event_id=self.ev_b_private.id, user_id=self.student_a.id,
            invited_by=self.coord_b.id, status='pending',
        ))
        db.session.commit()
        self.assertTrue(EventsService.user_may_participate_in_event(
            self.student_a, self.ev_b_private))

    def test_draft_event_is_participable_by_nobody(self):
        self.assertFalse(EventsService.user_may_participate_in_event(
            self.student_a, self.ev_a_draft))
        db.session.add(EventInvitation(
            event_id=self.ev_a_draft.id, user_id=self.student_a.id,
            invited_by=self.coord_a.id, status='pending',
        ))
        db.session.commit()
        self.assertFalse(EventsService.user_may_participate_in_event(
            self.student_a, self.ev_a_draft))

    def test_event_hidden_from_students_is_not_participable(self):
        self.assertFalse(EventsService.user_may_participate_in_event(
            self.student_a, self.ev_a_hidden))

    def test_archived_event_is_not_participable(self):
        self.ev_a_public.status = 'archived'
        db.session.commit()
        self.assertFalse(EventsService.user_may_participate_in_event(
            self.student_a, self.ev_a_public))

    def test_registration_row_does_not_grant_participation(self):
        """A row the user can write for themselves is not an authorisation."""
        db.session.add(EventAttendance(
            event_id=self.ev_b_private.id, user_id=self.student_a.id,
            status='registered',
        ))
        db.session.commit()
        self.assertFalse(EventsService.user_may_participate_in_event(
            self.student_a, self.ev_b_private))

    def test_coordinator_of_other_program_is_not_a_participant(self):
        self.assertFalse(EventsService.user_may_participate_in_event(
            self.coord_a, self.ev_b_private))
        self.assertTrue(EventsService.user_may_manage_event(
            self.coord_b, self.ev_b_private))


class TestRegisterAuthorisation(ParticipationBase):
    """F11 — the self-service write that fed the other ACLs."""

    def test_service_refuses_private_event_without_invitation(self):
        with self.assertRaises(ValueError):
            EventsService.register_to_event(self.ev_b_private.id, self.student_a.id)

    def test_service_refuses_draft_event(self):
        with self.assertRaises(ValueError):
            EventsService.register_to_event(self.ev_a_draft.id, self.student_a.id)

    def test_service_refuses_event_hidden_from_students(self):
        with self.assertRaises(ValueError):
            EventsService.register_to_event(self.ev_a_hidden.id, self.student_a.id)

    def test_service_refuses_other_program_event(self):
        with self.assertRaises(ValueError):
            EventsService.register_to_event(self.ev_b_public.id, self.student_a.id)

    def test_service_accepts_own_program_and_institutional_events(self):
        att = EventsService.register_to_event(self.ev_a_public.id, self.student_a.id)
        self.assertEqual(att.status, 'registered')
        att = EventsService.register_to_event(self.ev_institutional.id, self.student_a.id)
        self.assertEqual(att.status, 'registered')

    def test_service_accepts_private_event_with_invitation(self):
        db.session.add(EventInvitation(
            event_id=self.ev_b_private.id, user_id=self.student_a.id,
            invited_by=self.coord_b.id, status='pending',
        ))
        db.session.commit()
        att = EventsService.register_to_event(self.ev_b_private.id, self.student_a.id)
        self.assertEqual(att.status, 'registered')

    def test_api_register_to_private_event_returns_404_and_writes_nothing(self):
        resp = self._post(f'/api/v1/attendance/event/{self.ev_b_private.id}/register')
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(EventAttendance.query.count(), 0)

    def test_api_register_to_draft_event_returns_404_and_writes_nothing(self):
        resp = self._post(f'/api/v1/attendance/event/{self.ev_a_draft.id}/register')
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(EventAttendance.query.count(), 0)

    def test_api_register_to_own_program_event_succeeds(self):
        resp = self._post(f'/api/v1/attendance/event/{self.ev_a_public.id}/register')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(EventAttendance.query.count(), 1)


class TestPlantedRegistrationGrantsNothing(ParticipationBase):
    """
    A row written before the guard existed (or by any other route) must not
    open the detail endpoint nor put the event on the student's list.
    """

    def setUp(self):
        super().setUp()
        db.session.add(EventAttendance(
            event_id=self.ev_b_private.id, user_id=self.student_a.id,
            status='registered',
        ))
        db.session.commit()

    def test_public_detail_still_forbidden(self):
        resp = self._get(f'/api/v1/events/public/{self.ev_b_private.id}')
        self.assertEqual(resp.status_code, 403)
        self.assertNotIn(b'Comite secreto', resp.data)

    def test_private_event_absent_from_public_listing(self):
        events = EventsService.list_public_events(self.student_a.id)
        self.assertNotIn(self.ev_b_private.id, [e.id for e in events])

    def test_page_still_404(self):
        resp = self._get(f'/events/{self.ev_b_private.id}')
        self.assertEqual(resp.status_code, 404)
        self.assertNotIn(b'Comite secreto', resp.data)


class TestHostsImagesAndBytes(ParticipationBase):
    """F12 — hosts, images and the bytes behind them share the one rule."""

    def setUp(self):
        super().setUp()
        self.host_user = make_user(self.role_admin, suffix='_ponente')
        db.session.add(EventHost(
            event_id=self.ev_b_private.id, user_id=self.host_user.id,
            role_label='Ponente', display_order=0,
        ))
        db.session.add(EventImage(
            event_id=self.ev_b_private.id, path=f'{self.ev_b_private.id}/cover.webp',
            is_cover=True, display_order=0,
        ))
        db.session.commit()

    def _write_cover(self, event_id: int) -> None:
        folder = Path(self.app.config['EVENTS_FOLDER']) / str(event_id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'cover.webp').write_bytes(b'not-really-an-image')

    def test_hosts_of_private_event_are_forbidden(self):
        resp = self._get(f'/api/v1/events/{self.ev_b_private.id}/hosts')
        self.assertEqual(resp.status_code, 403)

    def test_images_of_private_event_are_forbidden(self):
        resp = self._get(f'/api/v1/events/{self.ev_b_private.id}/images')
        self.assertEqual(resp.status_code, 403)

    def test_image_bytes_of_private_event_are_404(self):
        self._write_cover(self.ev_b_private.id)
        resp = self._get(f'/files/event/{self.ev_b_private.id}/cover/cover.webp')
        self.assertEqual(resp.status_code, 404)

    def test_hosts_and_images_of_own_program_event_are_readable(self):
        db.session.add(EventHost(
            event_id=self.ev_a_public.id, user_id=self.host_user.id,
            role_label='Ponente', display_order=0,
        ))
        db.session.commit()

        resp = self._get(f'/api/v1/events/{self.ev_a_public.id}/hosts')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(json.loads(resp.data)['hosts']), 1)

        resp = self._get(f'/api/v1/events/{self.ev_a_public.id}/images')
        self.assertEqual(resp.status_code, 200)

    def test_image_bytes_of_own_program_event_are_served(self):
        self._write_cover(self.ev_a_public.id)
        resp = self._get(f'/files/event/{self.ev_a_public.id}/cover/cover.webp')
        self.assertEqual(resp.status_code, 200)

    def test_host_avatar_of_private_event_is_not_visible(self):
        self.assertFalse(file_access_service.may_view_avatar(
            self.student_a, self.host_user.id))

    def test_host_avatar_of_participable_event_is_visible(self):
        db.session.add(EventHost(
            event_id=self.ev_institutional.id, user_id=self.host_user.id,
            role_label='Ponente', display_order=0,
        ))
        db.session.commit()
        self.assertTrue(file_access_service.may_view_avatar(
            self.student_a, self.host_user.id))


class TestEventPageObjectCheck(ParticipationBase):
    """F13 — /events/<id> printed the title of anything, to anybody."""

    def test_private_event_page_is_404_without_title(self):
        resp = self._get(f'/events/{self.ev_b_private.id}')
        self.assertEqual(resp.status_code, 404)
        self.assertNotIn(b'Comite secreto', resp.data)

    def test_draft_event_page_is_404_without_title(self):
        resp = self._get(f'/events/{self.ev_a_draft.id}')
        self.assertEqual(resp.status_code, 404)
        self.assertNotIn(b'Borrador de A', resp.data)

    def test_other_program_event_page_is_404(self):
        resp = self._get(f'/events/{self.ev_b_public.id}')
        self.assertEqual(resp.status_code, 404)
        self.assertNotIn(b'Seminario de B', resp.data)

    def test_own_program_event_page_renders(self):
        resp = self._get(f'/events/{self.ev_a_public.id}')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Seminario de A', resp.data)

    def test_institutional_event_page_renders(self):
        resp = self._get(f'/events/{self.ev_institutional.id}')
        self.assertEqual(resp.status_code, 200)

    def test_manager_sees_the_page_of_their_own_draft(self):
        coord_client = self.app.test_client()
        login(coord_client, self.coord_a)
        if hasattr(g, '_login_user'):
            del g._login_user
        resp = coord_client.get(f'/events/{self.ev_a_draft.id}')
        self.assertEqual(resp.status_code, 200)


class TestOrdinaryStudentFlowStillWorks(ParticipationBase):
    """The shipped public events UI, end to end, for an ordinary student."""

    def test_listing_shows_institutional_and_own_program_events_only(self):
        resp = self._get('/api/v1/events/public')
        self.assertEqual(resp.status_code, 200)
        ids = [item['id'] for item in json.loads(resp.data)['items']]
        self.assertIn(self.ev_institutional.id, ids)
        self.assertIn(self.ev_a_public.id, ids)
        self.assertNotIn(self.ev_b_public.id, ids)
        self.assertNotIn(self.ev_b_private.id, ids)
        self.assertNotIn(self.ev_a_draft.id, ids)
        self.assertNotIn(self.ev_a_hidden.id, ids)

    def test_detail_register_and_unregister(self):
        detail = self._get(f'/api/v1/events/public/{self.ev_a_public.id}')
        self.assertEqual(detail.status_code, 200)
        self.assertIsNone(json.loads(detail.data)['my_registration'])

        resp = self._post(f'/api/v1/attendance/event/{self.ev_a_public.id}/register')
        self.assertEqual(resp.status_code, 201)

        detail = self._get(f'/api/v1/events/public/{self.ev_a_public.id}')
        self.assertEqual(json.loads(detail.data)['my_registration']['status'], 'registered')

        resp = self._post(f'/api/v1/attendance/event/{self.ev_a_public.id}/unregister')
        self.assertEqual(resp.status_code, 200)

    def test_invited_student_of_another_program_completes_the_flow(self):
        db.session.add(EventInvitation(
            event_id=self.ev_b_private.id, user_id=self.student_a.id,
            invited_by=self.coord_b.id, status='pending',
        ))
        db.session.commit()

        self.assertEqual(
            self._get(f'/api/v1/events/public/{self.ev_b_private.id}').status_code, 200)
        self.assertEqual(
            self._get(f'/events/{self.ev_b_private.id}').status_code, 200)
        self.assertEqual(
            self._get(f'/api/v1/events/{self.ev_b_private.id}/hosts').status_code, 200)
        self.assertEqual(
            self._post(f'/api/v1/attendance/event/{self.ev_b_private.id}/register').status_code,
            201)


if __name__ == '__main__':
    unittest.main()
