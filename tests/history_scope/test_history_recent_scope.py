# tests/history_scope/test_history_recent_scope.py
"""
Tests for the scope and the limit of the institutional history API.

Why these exist
───────────────
`GET /api/v1/admin/history/recent` used to call
`UserHistoryService.get_recent_activity(limit=...)`, a bare
`SELECT ... ORDER BY timestamp DESC LIMIT n` over `user_history` with no scope
argument at all, gated on `admin_history.api.statistics` — a codename
`program_admin` holds by role. Every row serialises its raw `details` JSON
(control number, student name, document and program names), all of which are
on the forbidden cross-program list. `?limit=1000000` also handed a
single-worker deployment a denial of service.

TRUTH TABLE pinned below
─────────────────────────────────────────────────────────────────────────────
caller                          | program_ids passed | rows returned
────────────────────────────────┼────────────────────┼──────────────────────
postgraduate_admin (global)     | None               | every program
program_admin of A              | {a}                | A's users + itself
social_service, no delegation   | set()              | itself only
nobody (no scope, no viewer)    | set(), viewer=None | none
─────────────────────────────────────────────────────────────────────────────
`None` means ALL programs and only a global caller may pass it; `set()` means
NONE and must never be read as "all". Both branches are asserted.
"""

import unittest

from flask import g

from app import create_app, db
from app.models.user_history import UserHistory
from app.services.user_history_service import UserHistoryService

from tests.program_scope.conftest import (
    make_test_config, make_role, make_user, make_program, make_period,
    grant_permission, make_user_program,
)


def add_history(user, action='profile_completed', details='x'):
    entry = UserHistory(user_id=user.id, admin_id=None, action=action, details=details)
    db.session.add(entry)
    db.session.flush()
    return entry


class HistoryWorldMixin:
    """
    World shared by both cases:
      program_a — coordinated by coord_a; student_a enrolled
      program_b — coordinated by coord_b; student_b enrolled
      pg_admin  — global scope (role grants academic_periods.api.create)
      Every one of them has one history row.
    """

    def setUp(self):
        self.app = create_app(make_test_config())
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.role_student = make_role('student')
        self.role_coord = make_role('program_admin')
        self.role_pg = make_role('postgraduate_admin')

        grant_permission(self.role_pg, 'academic_periods.api.create')
        for codename in ('admin_history.api.view', 'admin_history.api.statistics'):
            grant_permission(self.role_coord, codename)
            grant_permission(self.role_pg, codename)

        self.coord_a = make_user(self.role_coord, suffix='_a')
        self.coord_b = make_user(self.role_coord, suffix='_b')
        self.pg_admin = make_user(self.role_pg, suffix='_pg')
        self.student_a = make_user(self.role_student, suffix='_a')
        self.student_b = make_user(self.role_student, suffix='_b')

        self.period = make_period()
        self.program_a = make_program(self.coord_a, slug='prog-a')
        self.program_b = make_program(self.coord_b, slug='prog-b')
        make_user_program(self.student_a, self.program_a, self.period)
        make_user_program(self.student_b, self.program_b, self.period)

        for user in (self.coord_a, self.coord_b, self.pg_admin,
                     self.student_a, self.student_b):
            add_history(user)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()


class RecentActivityScopeTest(HistoryWorldMixin, unittest.TestCase):
    """The service query: `UserHistoryService.get_recent_activity`."""

    def _user_ids(self, rows):
        return {row.user_id for row in rows}

    def test_global_scope_sees_every_program(self):
        rows = UserHistoryService.get_recent_activity(
            limit=100, program_ids=None, viewer_id=self.pg_admin.id,
        )
        self.assertEqual(
            self._user_ids(rows),
            {self.coord_a.id, self.coord_b.id, self.pg_admin.id,
             self.student_a.id, self.student_b.id},
        )

    def test_coordinator_sees_only_own_program_and_self(self):
        scope = self.coord_a.get_accessible_program_ids()
        self.assertEqual(scope, {self.program_a.id})

        rows = UserHistoryService.get_recent_activity(
            limit=100, program_ids=scope, viewer_id=self.coord_a.id,
        )
        self.assertEqual(self._user_ids(rows), {self.student_a.id, self.coord_a.id})
        self.assertNotIn(self.student_b.id, self._user_ids(rows))
        self.assertNotIn(self.coord_b.id, self._user_ids(rows))

    def test_empty_scope_is_not_every_program(self):
        """set() means NO program — the single most dangerous confusion here."""
        rows = UserHistoryService.get_recent_activity(
            limit=100, program_ids=set(), viewer_id=self.coord_a.id,
        )
        self.assertEqual(self._user_ids(rows), {self.coord_a.id})

    def test_no_scope_and_no_viewer_returns_nothing(self):
        rows = UserHistoryService.get_recent_activity(
            limit=100, program_ids=set(), viewer_id=None,
        )
        self.assertEqual(rows, [])

    def test_scope_argument_is_mandatory(self):
        with self.assertRaises(TypeError):
            UserHistoryService.get_recent_activity(limit=10)

    # ── limit ────────────────────────────────────────────────────────────────

    def test_limit_is_clamped(self):
        clamp = UserHistoryService.clamp_activity_limit
        self.assertEqual(clamp(1_000_000), UserHistoryService.MAX_ACTIVITY_LIMIT)
        self.assertEqual(clamp(-1), UserHistoryService.DEFAULT_ACTIVITY_LIMIT)
        self.assertEqual(clamp(0), UserHistoryService.DEFAULT_ACTIVITY_LIMIT)
        self.assertEqual(clamp(None), UserHistoryService.DEFAULT_ACTIVITY_LIMIT)
        self.assertEqual(clamp('abc'), UserHistoryService.DEFAULT_ACTIVITY_LIMIT)
        self.assertEqual(clamp(7), 7)

    def test_query_clamps_its_own_limit(self):
        rows = UserHistoryService.get_recent_activity(
            limit=1_000_000, program_ids=None, viewer_id=self.pg_admin.id,
        )
        self.assertLessEqual(len(rows), UserHistoryService.MAX_ACTIVITY_LIMIT)


class HistoryEndpointGuardTest(HistoryWorldMixin, unittest.TestCase):
    """The HTTP layer: permiso ≠ alcance on every endpoint of the blueprint."""

    def _client_as(self, user):
        # The test pushes ONE app context for the whole case, and Flask reuses
        # it for every test-client request, so `g` (flask_login's cached user,
        # the permission cache, the scope cache) would leak from one request to
        # the next. Production gets a fresh app context per request; clear it
        # by hand so the assertions describe production and not the harness.
        vars(g).clear()
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user.id)
            sess['_fresh'] = True
            sess['_csrf_token'] = 'test-token'
        return client

    def test_statistics_is_global_only(self):
        """program_admin holds admin_history.api.statistics by role — and is
        still refused, because the retention statistics cover the whole log."""
        self.assertTrue(self.coord_a.has_permission('admin_history.api.statistics'))
        res = self._client_as(self.coord_a).get('/api/v1/admin/history/statistics')
        self.assertEqual(res.status_code, 403)

        res = self._client_as(self.pg_admin).get('/api/v1/admin/history/statistics')
        self.assertEqual(res.status_code, 200)

    def test_recent_is_scoped_per_program(self):
        res = self._client_as(self.coord_a).get('/api/v1/admin/history/recent?limit=1000000')
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertTrue(body['meta']['scoped'])
        self.assertLessEqual(body['meta']['limit'], UserHistoryService.MAX_ACTIVITY_LIMIT)
        # El payload publica el identificador PÚBLICO del usuario, no el
        # entero: el id interno ya no sale de la aplicación.
        self.assertEqual(
            {row['user_id'] for row in body['data']},
            {str(self.student_a.uuid), str(self.coord_a.uuid)},
        )

    def test_recent_is_unscoped_for_the_global_admin(self):
        res = self._client_as(self.pg_admin).get('/api/v1/admin/history/recent')
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertFalse(body['meta']['scoped'])
        self.assertEqual(len(body['data']), 5)


if __name__ == '__main__':
    unittest.main()
