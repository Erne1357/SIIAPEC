# tests/events/test_event_host_authorship.py
"""
F26 — the third self-granted authorisation: an `EventHost` row naming anybody.

Managing an event let its organiser write a host (ponente) row pointing at ANY
account in the institution. `file_access_service._hosts_event_visible_to` then
correctly found that the caller managed an event naming that person and served
`/files/avatar/<victim>/<file>` — the face photograph of students of other
programmes and of staff accounts no `user_in_scope` call would ever cover. The
ACL was right; its input was attacker-written.

Two independent halves are pinned here:

  1. WRITING — `EventsService.user_may_be_named_host`: an internal host must be
     the actor themselves, somebody inside the actor's program scope, or
     somebody who already has authority over THAT event (the coordinator of its
     programme, the Jefatura). Everybody else goes through `external_name`.

  2. READING — the host exception of `may_view_avatar` is honoured only through
     an event the viewer ATTENDS and does NOT manage, so the row can never be
     one the viewer wrote. A legacy row minted before (1) shipped cashes in for
     nobody.

And the third thing that must remain true: an ordinary attendee still sees the
ponente chips — names and photographs — of an event they may participate in.
"""

import json
import tempfile
import unittest
from pathlib import Path

from flask import g

from app import create_app, db
from app.models.event import EventHost
from app.services import file_access_service
from app.services.events_service import EventsService
from tests.events.conftest import (
    make_test_config, make_role, make_user, make_program, make_academic_period,
    make_event, enroll_in_program, grant_permission, login,
)


class HostAuthorshipBase(unittest.TestCase):
    """
    Programme A (coord_a) and programme B (coord_b), one student each, an
    unrelated staff account, and the Jefatura. The attack surface is the public
    event of A, which coord_a legitimately manages.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(make_test_config(upload_folder=self.tmp.name))
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.role_admin = make_role('program_admin')
        for code in ('events.api.create', 'events.api.manage_hosts'):
            grant_permission(self.role_admin, code)

        self.coord_a = make_user(self.role_admin, suffix='_coord_a')
        self.coord_b = make_user(self.role_admin, suffix='_coord_b')
        self.staff_other = make_user(self.role_admin, suffix='_coord_c')
        self.prog_a = make_program(self.coord_a, slug='prog-a')
        self.prog_b = make_program(self.coord_b, slug='prog-b')
        self.prog_c = make_program(self.staff_other, slug='prog-c')

        self.role_student = make_role('student')
        self.student_a = make_user(self.role_student, suffix='_stu_a')
        self.student_b = make_user(self.role_student, suffix='_stu_b')
        enroll_in_program(self.student_a, self.prog_a)
        enroll_in_program(self.student_b, self.prog_b)

        # Jefatura de Posgrado: global scope through a ROLE permission.
        self.role_head = make_role('postgraduate_admin')
        grant_permission(self.role_head, 'academic_periods.api.create')
        self.head = make_user(self.role_head, suffix='_head')

        self.period = make_academic_period(is_active=True)

        self.ev_a = make_event(
            created_by=self.coord_a.id, program_id=self.prog_a.id,
            title='Seminario de A',
        )
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        self.tmp.cleanup()

    @staticmethod
    def _clear_login_cache():
        if hasattr(g, '_login_user'):
            del g._login_user


class TestWhoMayBeNamedHost(HostAuthorshipBase):
    """Half 1 — the write side."""

    def test_actor_may_name_themselves(self):
        hosts = EventsService.set_event_hosts(
            self.ev_a.id,
            [{'user_id': self.coord_a.id, 'role_label': 'Moderador'}],
            acting_user=self.coord_a,
        )
        self.assertEqual(len(hosts), 1)

    def test_actor_may_name_a_student_of_their_own_programme(self):
        hosts = EventsService.set_event_hosts(
            self.ev_a.id,
            [{'user_id': self.student_a.id, 'role_label': 'Ponente'}],
            acting_user=self.coord_a,
        )
        self.assertEqual(hosts[0].user_id, self.student_a.id)

    def test_actor_may_name_the_jefatura(self):
        """The staff door: the Jefatura has authority over every event."""
        hosts = EventsService.set_event_hosts(
            self.ev_a.id,
            [{'user_id': self.head.id, 'role_label': 'Presentacion'}],
            acting_user=self.coord_a,
        )
        self.assertEqual(hosts[0].user_id, self.head.id)

    def test_actor_cannot_name_a_student_of_another_programme(self):
        with self.assertRaises(ValueError):
            EventsService.set_event_hosts(
                self.ev_a.id,
                [{'user_id': self.student_b.id, 'role_label': 'Ponente'}],
                acting_user=self.coord_a,
            )

    def test_actor_cannot_name_unrelated_staff(self):
        """A coordinator of another programme has no authority over this event."""
        with self.assertRaises(ValueError):
            EventsService.set_event_hosts(
                self.ev_a.id,
                [{'user_id': self.staff_other.id, 'role_label': 'Ponente'}],
                acting_user=self.coord_a,
            )

    def test_actor_cannot_name_an_unknown_user_id(self):
        with self.assertRaises(ValueError):
            EventsService.set_event_hosts(
                self.ev_a.id,
                [{'user_id': 999999, 'role_label': 'Ponente'}],
                acting_user=self.coord_a,
            )

    def test_refusal_does_not_wipe_the_existing_list(self):
        """Validation runs before the delete: a refused save changes nothing."""
        EventsService.set_event_hosts(
            self.ev_a.id,
            [{'user_id': self.student_a.id, 'role_label': 'Ponente'}],
            acting_user=self.coord_a,
        )
        with self.assertRaises(ValueError):
            EventsService.set_event_hosts(
                self.ev_a.id,
                [
                    {'user_id': self.student_a.id, 'role_label': 'Ponente'},
                    {'user_id': self.student_b.id, 'role_label': 'Invitado'},
                ],
                acting_user=self.coord_a,
            )
        rows = EventHost.query.filter_by(event_id=self.ev_a.id).all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].user_id, self.student_a.id)

    def test_external_hosts_are_untouched(self):
        """The door for people who are not SIIAP users stays wide open."""
        hosts = EventsService.set_event_hosts(
            self.ev_a.id,
            [
                {'external_name': 'Dra. Externa', 'external_bio': 'Bio',
                 'role_label': 'Conferencista'},
                {'external_name': 'Mtro. Invitado', 'role_label': 'Panelista'},
            ],
            acting_user=self.coord_a,
        )
        self.assertEqual(len(hosts), 2)
        self.assertTrue(all(h.user_id is None for h in hosts))

    def test_no_actor_refuses_internal_hosts(self):
        """Fail closed: without an actor nobody's identity may be published."""
        with self.assertRaises(ValueError):
            EventsService.set_event_hosts(
                self.ev_a.id,
                [{'user_id': self.student_a.id, 'role_label': 'Ponente'}],
            )


class TestHostAvatarIsNotSelfGranted(HostAuthorshipBase):
    """Half 2 — the read side, including rows minted before the fix."""

    def _plant_legacy_host(self, user):
        """A row as the exploit used to write it: straight into the table."""
        db.session.add(EventHost(
            event_id=self.ev_a.id, user_id=user.id,
            role_label='Ponente', display_order=0,
        ))
        db.session.commit()

    def test_manager_cannot_read_the_avatar_of_a_host_row_on_their_event(self):
        self._plant_legacy_host(self.student_b)
        self.assertFalse(
            file_access_service.may_view_avatar(self.coord_a, self.student_b.id)
        )

    def test_manager_cannot_read_the_avatar_of_unrelated_staff(self):
        self._plant_legacy_host(self.staff_other)
        self.assertFalse(
            file_access_service.may_view_avatar(self.coord_a, self.staff_other.id)
        )

    def test_attendee_still_reads_the_host_avatar(self):
        """The chip that the exception exists for: a staff ponente, seen by a
        student of the programme, who could not have written the row."""
        self._plant_legacy_host(self.staff_other)
        self.assertTrue(
            file_access_service.may_view_avatar(self.student_a, self.staff_other.id)
        )

    def test_student_of_another_programme_still_denied(self):
        self._plant_legacy_host(self.staff_other)
        self.assertTrue(
            file_access_service.may_view_avatar(self.student_a, self.staff_other.id)
        )
        self.assertFalse(
            file_access_service.may_view_avatar(self.student_b, self.staff_other.id)
        )

    def test_listing_hides_the_photo_url_the_viewer_cannot_fetch(self):
        """The index must not betray what the file server denies."""
        self.staff_other.avatar = 'foto.webp'
        self._plant_legacy_host(self.staff_other)

        with self.app.test_request_context('/'):
            as_manager = EventsService.get_event_hosts(
                self.ev_a.id, viewer=self.coord_a)
            as_attendee = EventsService.get_event_hosts(
                self.ev_a.id, viewer=self.student_a)

        self.assertIsNone(as_manager[0]['photo_url'])
        # El URL de la foto nombra la FILA del usuario, no su id entero ni el
        # nombre del archivo.
        self.assertIn(f'/files/avatar/{self.staff_other.uuid}',
                      as_attendee[0]['photo_url'])
        # The name and the role label — what the chip is for — survive for both.
        self.assertEqual(as_manager[0]['name'], as_attendee[0]['name'])
        self.assertEqual(as_manager[0]['role_label'], 'Ponente')


class TestExploitOverHttp(HostAuthorshipBase):
    """The three requests of the report, end to end."""

    def setUp(self):
        super().setUp()
        # The victim HAS a profile photo on disk: without this the last request
        # would 404 for want of bytes and the test would pass even unfixed.
        self.student_b.avatar = 'foto.webp'
        db.session.commit()
        folder = Path(self.app.config['AVATAR_FOLDER']) / str(self.student_b.id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'foto.webp').write_bytes(b'not-really-an-image')

        self.client = self.app.test_client()
        self.csrf = login(self.client, self.coord_a)

    def _put_hosts(self, payload):
        self._clear_login_cache()
        return self.client.put(
            f'/api/v1/events/{self.ev_a.id}/hosts',
            data=json.dumps(payload),
            content_type='application/json',
            headers={'X-CSRFToken': self.csrf},
        )

    def test_naming_a_victim_is_rejected_and_the_avatar_stays_shut(self):
        resp = self._put_hosts({'hosts': [
            {'user_id': self.student_b.id, 'role_label': 'x'},
        ]})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            EventHost.query.filter_by(event_id=self.ev_a.id).count(), 0)

        self._clear_login_cache()
        resp = self.client.get(
            f'/api/v1/events/{self.ev_a.id}/hosts')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json.loads(resp.data)['hosts'], [])

        self._clear_login_cache()
        resp = self.client.get(
            f'/files/avatar/{self.student_b.uuid}')
        self.assertEqual(resp.status_code, 404)

    def test_a_planted_row_does_not_open_the_avatar_either(self):
        """Even with the row already in the table — a save from before the fix —
        the bytes stay shut for the manager who could have written it."""
        db.session.add(EventHost(
            event_id=self.ev_a.id, user_id=self.student_b.id,
            role_label='x', display_order=0,
        ))
        db.session.commit()

        self._clear_login_cache()
        resp = self.client.get(
            f'/files/avatar/{self.student_b.uuid}')
        self.assertEqual(resp.status_code, 404)

    def test_legitimate_host_list_still_saves(self):
        resp = self._put_hosts({'hosts': [
            {'user_id': self.student_a.id, 'role_label': 'Ponente'},
            {'external_name': 'Dra. Externa', 'role_label': 'Invitada'},
        ]})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json.loads(resp.data)['count'], 2)


class TestAttendeeStillSeesTheCartel(HostAuthorshipBase):
    """The public UI contract: chips render, photos load, for an attendee."""

    def setUp(self):
        super().setUp()
        self.staff_other.avatar = 'foto.webp'
        db.session.add(EventHost(
            event_id=self.ev_a.id, user_id=self.staff_other.id,
            role_label='Ponente', display_order=0,
        ))
        db.session.add(EventHost(
            event_id=self.ev_a.id, external_name='Dra. Externa',
            role_label='Invitada', display_order=1,
        ))
        db.session.commit()

        folder = Path(self.app.config['AVATAR_FOLDER']) / str(self.staff_other.id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'foto.webp').write_bytes(b'not-really-an-image')

        self.client = self.app.test_client()
        self.csrf = login(self.client, self.student_a)

    def _get(self, url):
        self._clear_login_cache()
        return self.client.get(url)

    def test_hosts_endpoint_returns_names_and_photos(self):
        resp = self._get(f'/api/v1/events/{self.ev_a.id}/hosts')
        self.assertEqual(resp.status_code, 200)
        hosts = json.loads(resp.data)['hosts']
        self.assertEqual(len(hosts), 2)
        # El URL termina en el handle de la fila; el nombre del archivo
        # dejó de viajar en el URL.
        self.assertTrue(
            hosts[0]['photo_url'].endswith(str(self.staff_other.uuid)))
        self.assertEqual(hosts[1]['name'], 'Dra. Externa')

    def test_host_avatar_bytes_are_served_to_the_attendee(self):
        resp = self._get(f'/files/avatar/{self.staff_other.uuid}')
        self.assertEqual(resp.status_code, 200)


if __name__ == '__main__':
    unittest.main()
