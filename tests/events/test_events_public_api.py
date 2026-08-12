# tests/events/test_events_public_api.py
"""
Integration tests for student-facing public event endpoints:
  GET  /api/v1/events/public
  GET  /api/v1/events/public/<id>
"""

import json
import unittest
from datetime import datetime, timedelta

from app import create_app, db
from app.models.event import Event, EventInvitation
from app.models.user_program import UserProgram
from tests.events.conftest import (
    make_test_config, make_role, make_user, make_program,
    make_academic_period, login, inject_csrf,
)


class TestPublicEventsApi(unittest.TestCase):

    def setUp(self):
        self.app = create_app(make_test_config())
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.role_admin = make_role('program_admin')
        self.admin = make_user(self.role_admin, suffix='_adm')
        self.prog = make_program(self.admin)
        self.role_student = make_role('student')
        self.student = make_user(self.role_student, suffix='_stu')
        self.period = make_academic_period(is_active=True)
        db.session.commit()

        self.client = self.app.test_client()
        self.csrf = login(self.client, self.student)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _make_public_multiple_event(self, status='published', title='Public Event',
                                    capacity_type='multiple', max_capacity=50,
                                    visible_to_students=True, visibility='public',
                                    event_date=None):
        ev = Event(
            program_id=None,
            type='conference',
            title=title,
            description='Desc',
            location='Hall A',
            created_by=self.admin.id,
            visible_to_students=visible_to_students,
            capacity_type=capacity_type,
            max_capacity=max_capacity,
            requires_registration=True,
            allows_attendance_tracking=False,
            reminders_enabled=True,
            status=status,
            visibility=visibility,
            event_date=event_date,
            academic_period_id=self.period.id,
        )
        db.session.add(ev)
        db.session.commit()
        return ev

    def test_list_public_events_returns_200(self):
        self._make_public_multiple_event()
        resp = self.client.get('/api/v1/events/public')
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertTrue(data['ok'])
        self.assertIn('items', data)

    def test_list_public_events_includes_multiple_events(self):
        ev = self._make_public_multiple_event()
        resp = self.client.get('/api/v1/events/public')
        data = json.loads(resp.data)
        ids = [i['id'] for i in data['items']]
        self.assertIn(ev.id, ids)

    def test_list_public_events_excludes_single_capacity(self):
        self._make_public_multiple_event(capacity_type='single', max_capacity=None)
        resp = self.client.get('/api/v1/events/public')
        data = json.loads(resp.data)
        # single events never appear in public list
        for item in data['items']:
            self.assertNotEqual(item['capacity_type'], 'single')

    def test_list_public_events_excludes_draft(self):
        self._make_public_multiple_event(status='draft')
        resp = self.client.get('/api/v1/events/public')
        data = json.loads(resp.data)
        for item in data['items']:
            self.assertEqual(item.get('status', 'published'), 'published')

    def test_list_public_events_excludes_not_visible_to_students(self):
        self._make_public_multiple_event(visible_to_students=False)
        resp = self.client.get('/api/v1/events/public')
        data = json.loads(resp.data)
        # Events with visible_to_students=False should not appear
        self.assertEqual(len(data['items']), 0)

    def test_list_public_events_unauthenticated_redirects(self):
        anon = self.app.test_client()
        resp = anon.get('/api/v1/events/public')
        self.assertIn(resp.status_code, (401, 302, 403))

    def test_get_public_event_detail_success(self):
        ev = self._make_public_multiple_event()
        resp = self.client.get(f'/api/v1/events/public/{ev.id}')
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertTrue(data['ok'])
        self.assertEqual(data['event']['id'], ev.id)

    def test_get_public_event_detail_not_found(self):
        resp = self.client.get('/api/v1/events/public/99999')
        self.assertEqual(resp.status_code, 404)

    def test_get_public_event_detail_draft_is_indistinguishable_from_missing(self):
        # 404, not 403. A draft never appears in any list this student
        # receives, so answering 403 would confirm the id exists and turn
        # 1..N into a census of the institution's unpublished events.
        ev = self._make_public_multiple_event(status='draft')
        resp = self.client.get(f'/api/v1/events/public/{ev.id}')
        self.assertEqual(resp.status_code, 404)
        missing = self.client.get('/api/v1/events/public/99999')
        self.assertEqual(resp.data, missing.data)

    def test_get_public_event_detail_not_visible_is_indistinguishable_from_missing(self):
        ev = self._make_public_multiple_event(visible_to_students=False)
        resp = self.client.get(f'/api/v1/events/public/{ev.id}')
        self.assertEqual(resp.status_code, 404)
        missing = self.client.get('/api/v1/events/public/99999')
        self.assertEqual(resp.data, missing.data)

    def test_get_public_event_includes_my_registration_none(self):
        ev = self._make_public_multiple_event()
        resp = self.client.get(f'/api/v1/events/public/{ev.id}')
        data = json.loads(resp.data)
        self.assertIsNone(data['my_registration'])

    def test_get_private_event_without_invitation_is_indistinguishable_from_missing(self):
        ev = self._make_public_multiple_event(visibility='private')
        # Student is not creator, not admin, no invitation. The denial must
        # not reveal that a private event with this id exists.
        resp = self.client.get(f'/api/v1/events/public/{ev.id}')
        self.assertEqual(resp.status_code, 404)
        missing = self.client.get('/api/v1/events/public/99999')
        self.assertEqual(resp.data, missing.data)

    def test_get_private_event_with_invitation_allowed(self):
        ev = self._make_public_multiple_event(visibility='private')
        inv = EventInvitation(
            event_id=ev.id,
            user_id=self.student.id,
            invited_by=self.admin.id,
            status='pending',
        )
        db.session.add(inv)
        db.session.commit()
        resp = self.client.get(f'/api/v1/events/public/{ev.id}')
        self.assertEqual(resp.status_code, 200)

    def test_list_events_includes_my_invitation_status(self):
        ev = self._make_public_multiple_event(visibility='private')
        inv = EventInvitation(
            event_id=ev.id,
            user_id=self.student.id,
            invited_by=self.admin.id,
            status='pending',
        )
        db.session.add(inv)
        db.session.commit()
        resp = self.client.get('/api/v1/events/public')
        data = json.loads(resp.data)
        found = next((i for i in data['items'] if i['id'] == ev.id), None)
        if found:
            self.assertEqual(found['my_invitation_status'], 'pending')
