# tests/events/test_events_admin_listing_scope.py
"""
Regression tests for the admin events listing and for the public listing's
program lookup.

F20 — `GET /api/v1/events` (and `/admin-stats`) is the MANAGEMENT listing. Its
      scope branch read an EMPTY accessible set as "every institutional event",
      with no filter on status, visibility or `visible_to_students`. Any caller
      with no programs — an applicant, once the mis-seeded `events.api.list`
      opened the door — read the Jefatura's DRAFT and PRIVATE institutional
      events. The empty set means NONE, never ALL.

F21 — `list_public_events` resolved the caller's program with
      `UserProgram.query.filter_by(user_id=...).first()`, one row where the
      shared predicate reads them all, so a user enrolled in two programs was
      denied their second program's events.
"""

import unittest

from app import create_app, db
from app.models.event import Event
from app.services.events_service import EventsService
from tests.events.conftest import (
    make_test_config, make_role, make_user, make_program, enroll_in_program,
)


def _event(**kwargs) -> Event:
    base = dict(
        type='conference',
        description='',
        location='',
        capacity_type='multiple',
        max_capacity=10,
        requires_registration=True,
        allows_attendance_tracking=False,
        reminders_enabled=True,
    )
    base.update(kwargs)
    return Event(**base)


class TestAdminListingInstitutionalScope(unittest.TestCase):
    """F20 — institutional rows a non-global caller may read."""

    def setUp(self):
        self.app = create_app(make_test_config())
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.role = make_role('program_admin')
        self.admin = make_user(self.role)
        self.program = make_program(self.admin)

        self.institutional_published = _event(
            program_id=None, title='INST publicado', created_by=self.admin.id,
            visible_to_students=True, status='published', visibility='public',
        )
        self.institutional_draft = _event(
            program_id=None, title='INST borrador', created_by=self.admin.id,
            visible_to_students=True, status='draft', visibility='public',
        )
        self.institutional_private = _event(
            program_id=None, title='INST privado', created_by=self.admin.id,
            visible_to_students=True, status='published', visibility='private',
        )
        self.institutional_hidden = _event(
            program_id=None, title='INST oculto', created_by=self.admin.id,
            visible_to_students=False, status='published', visibility='public',
        )
        self.own_draft = _event(
            program_id=self.program.id, title='PROG borrador', created_by=self.admin.id,
            visible_to_students=False, status='draft', visibility='private',
        )
        db.session.add_all([
            self.institutional_published, self.institutional_draft,
            self.institutional_private, self.institutional_hidden,
            self.own_draft,
        ])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_empty_scope_gets_published_public_institutional_only(self):
        titles = {e.title for e in EventsService.list_admin_events(accessible_pids=set())}
        self.assertEqual(titles, {'INST publicado'})

    def test_scoped_admin_keeps_own_drafts_but_not_institutional_drafts(self):
        titles = {
            e.title for e in
            EventsService.list_admin_events(accessible_pids={self.program.id})
        }
        self.assertEqual(titles, {'INST publicado', 'PROG borrador'})

    def test_global_scope_still_sees_everything(self):
        titles = {e.title for e in EventsService.list_admin_events(accessible_pids=None)}
        self.assertEqual(len(titles), 5)

    def test_stats_count_the_same_universe_as_the_listing(self):
        self.assertEqual(
            EventsService.get_admin_dashboard_stats(accessible_pids=set())['active'], 1
        )
        self.assertEqual(
            EventsService.get_admin_dashboard_stats(
                accessible_pids={self.program.id})['active'], 2
        )
        self.assertEqual(
            EventsService.get_admin_dashboard_stats(accessible_pids=None)['active'], 5
        )


class TestPublicListingReadsEveryProgram(unittest.TestCase):
    """F21 — a user enrolled in two programs sees both programs' events."""

    def setUp(self):
        self.app = create_app(make_test_config())
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.admin = make_user(make_role('program_admin'))
        self.program_a = make_program(self.admin, slug='program-a')
        self.program_b = make_program(self.admin, slug='program-b')

        self.student = make_user(make_role('student'), suffix='_dual')
        enroll_in_program(self.student, self.program_a)
        enroll_in_program(self.student, self.program_b)

        self.event_a = _event(
            program_id=self.program_a.id, title='EV A', created_by=self.admin.id,
            visible_to_students=True, status='published', visibility='public',
        )
        self.event_b = _event(
            program_id=self.program_b.id, title='EV B', created_by=self.admin.id,
            visible_to_students=True, status='published', visibility='public',
        )
        db.session.add_all([self.event_a, self.event_b])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_second_program_events_are_not_dropped(self):
        titles = {e.title for e in EventsService.list_public_events(self.student.id)}
        self.assertEqual(titles, {'EV A', 'EV B'})


if __name__ == '__main__':
    unittest.main()
