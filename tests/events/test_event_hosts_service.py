# tests/events/test_event_hosts_service.py
"""
Unit tests for EventsService host-related methods:
  - set_event_hosts (internal, external, replace)
  - get_event_hosts
"""

import unittest

from app import create_app, db
from app.models.event import Event, EventHost
from app.services.events_service import EventsService
from tests.events.conftest import (
    make_test_config, make_role, make_user, make_program,
)


class TestSetEventHosts(unittest.TestCase):

    def setUp(self):
        self.app = create_app(make_test_config())
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        role = make_role('program_admin')
        self.admin = make_user(role)
        self.prog = make_program(self.admin)
        self.ev = Event(
            program_id=self.prog.id,
            type='conference',
            title='Host Test Event',
            description='',
            location='',
            created_by=self.admin.id,
            visible_to_students=True,
            capacity_type='multiple',
            max_capacity=30,
            requires_registration=True,
            allows_attendance_tracking=False,
            reminders_enabled=True,
            status='published',
            visibility='public',
        )
        db.session.add(self.ev)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_set_internal_host(self):
        hosts = EventsService.set_event_hosts(self.ev.id, [
            {'user_id': self.admin.id, 'role_label': 'Ponente'},
        ], acting_user=self.admin)
        self.assertEqual(len(hosts), 1)
        self.assertEqual(hosts[0].user_id, self.admin.id)

    def test_set_external_host(self):
        hosts = EventsService.set_event_hosts(self.ev.id, [
            {'external_name': 'Dr. Externo', 'external_bio': 'Bio', 'role_label': 'Invitado'},
        ], acting_user=self.admin)
        self.assertEqual(len(hosts), 1)
        self.assertIsNone(hosts[0].user_id)
        self.assertEqual(hosts[0].external_name, 'Dr. Externo')

    def test_set_multiple_hosts(self):
        hosts = EventsService.set_event_hosts(self.ev.id, [
            {'user_id': self.admin.id, 'role_label': 'Moderador'},
            {'external_name': 'Speaker', 'role_label': 'Ponente'},
        ], acting_user=self.admin)
        self.assertEqual(len(hosts), 2)

    def test_set_hosts_replaces_existing(self):
        EventsService.set_event_hosts(self.ev.id, [
            {'user_id': self.admin.id, 'role_label': 'Ponente'},
        ], acting_user=self.admin)
        # Replace with a single external host
        hosts = EventsService.set_event_hosts(self.ev.id, [
            {'external_name': 'New Speaker', 'role_label': 'Nuevo'},
        ], acting_user=self.admin)
        self.assertEqual(len(hosts), 1)
        total = EventHost.query.filter_by(event_id=self.ev.id).count()
        self.assertEqual(total, 1)

    def test_set_empty_hosts_clears_all(self):
        EventsService.set_event_hosts(self.ev.id, [
            {'user_id': self.admin.id, 'role_label': 'Ponente'},
        ], acting_user=self.admin)
        hosts = EventsService.set_event_hosts(self.ev.id, [], acting_user=self.admin)
        self.assertEqual(len(hosts), 0)
        self.assertEqual(EventHost.query.filter_by(event_id=self.ev.id).count(), 0)

    def test_set_host_without_identity_raises(self):
        with self.assertRaises(ValueError):
            EventsService.set_event_hosts(self.ev.id, [
                {'role_label': 'Ponente'},  # no user_id, no external_name
            ], acting_user=self.admin)

    def test_set_host_without_role_label_raises(self):
        with self.assertRaises(ValueError):
            EventsService.set_event_hosts(self.ev.id, [
                {'user_id': self.admin.id},  # no role_label
            ], acting_user=self.admin)

    def test_set_hosts_event_not_found_raises(self):
        with self.assertRaises(ValueError):
            EventsService.set_event_hosts(99999, [
                {'user_id': self.admin.id, 'role_label': 'Ponente'},
            ], acting_user=self.admin)

    def test_get_event_hosts_with_external(self):
        EventsService.set_event_hosts(self.ev.id, [
            {'external_name': 'Dr. X', 'external_bio': 'Bio X', 'role_label': 'Guest'},
        ], acting_user=self.admin)
        with self.app.test_request_context('/'):
            hosts = EventsService.get_event_hosts(self.ev.id, viewer=self.admin)
        self.assertEqual(len(hosts), 1)
        self.assertTrue(hosts[0]['is_external'])
        self.assertEqual(hosts[0]['name'], 'Dr. X')

    def test_get_event_hosts_empty(self):
        with self.app.test_request_context('/'):
            hosts = EventsService.get_event_hosts(self.ev.id, viewer=self.admin)
        self.assertEqual(hosts, [])

    def test_get_event_hosts_with_internal(self):
        EventsService.set_event_hosts(self.ev.id, [
            {'user_id': self.admin.id, 'role_label': 'Conductor'},
        ], acting_user=self.admin)
        with self.app.test_request_context('/'):
            hosts = EventsService.get_event_hosts(self.ev.id, viewer=self.admin)
        self.assertEqual(len(hosts), 1)
        self.assertFalse(hosts[0]['is_external'])
