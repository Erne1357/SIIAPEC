# tests/events/test_invitations_api.py
"""
Integration tests for /api/v1/invitations endpoints:
  POST   /event/<id>/invite
  GET    /event/<id>/list
  POST   /<inv_id>/respond
  GET    /my-invitations
  DELETE /<inv_id>
  PUT    /event/<id>/dates
"""

import json
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

from app import create_app, db
from app.models.event import Event, EventInvitation, EventAttendance
from app.models.user_program import UserProgram
from tests.events.conftest import (
    make_test_config, make_role, make_user, make_program,
    make_academic_period, grant_permission, login, inject_csrf,
)


class TestInvitationsApiBase(unittest.TestCase):

    def setUp(self):
        self.app = create_app(make_test_config())
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.role_admin = make_role('program_admin')
        grant_permission(self.role_admin, 'invitations.api.send')
        grant_permission(self.role_admin, 'invitations.api.list')
        grant_permission(self.role_admin, 'invitations.api.manage')

        self.admin = make_user(self.role_admin, suffix='_adm')
        self.prog = make_program(self.admin)
        self.period = make_academic_period()

        self.role_student = make_role('student')
        self.student = make_user(self.role_student, suffix='_stu')
        db.session.flush()

        # El evento cuelga del programa que coordina el admin y el estudiante
        # pertenece a ese mismo programa: es el mundo legítimo del módulo.
        # Antes el fixture usaba un evento SIN programa, que es precisamente el
        # hueco cerrado — un coordinador no gestiona eventos institucionales.
        db.session.add(UserProgram(
            user_id=self.student.id,
            program_id=self.prog.id,
            admission_period_id=self.period.id,
            admission_status='in_progress',
        ))
        db.session.commit()

        self.ev = Event(
            program_id=self.prog.id,
            type='conference',
            title='Invite Test Event',
            description='',
            location='',
            created_by=self.admin.id,
            visible_to_students=True,
            capacity_type='multiple',
            max_capacity=50,
            requires_registration=True,
            allows_attendance_tracking=False,
            reminders_enabled=True,
            status='published',
            visibility='private',
        )
        db.session.add(self.ev)
        db.session.commit()

        self.admin_client = self.app.test_client()
        self.admin_csrf = login(self.admin_client, self.admin)

        self.student_client = self.app.test_client()
        self.student_csrf = login(self.student_client, self.student)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _admin_post(self, url, data):
        return self.admin_client.post(
            url,
            data=json.dumps(data),
            content_type='application/json',
            headers={'X-CSRFToken': self.admin_csrf},
        )

    def _student_post(self, url, data):
        return self.student_client.post(
            url,
            data=json.dumps(data),
            content_type='application/json',
            headers={'X-CSRFToken': self.student_csrf},
        )

    def _admin_delete(self, url):
        return self.admin_client.delete(url, headers={'X-CSRFToken': self.admin_csrf})


class TestInviteStudents(TestInvitationsApiBase):

    @patch('app.services.user_history_service.UserHistoryService.log_event_invitation')
    @patch('app.services.notification_service.NotificationService.notify_event_invitation')
    def test_invite_students_success(self, mock_notif, mock_log):
        mock_notif.return_value = MagicMock(id=1)
        resp = self._admin_post(
            f'/api/v1/invitations/event/{self.ev.id}/invite',
            {'user_ids': [str(self.student.uuid)]},
        )
        self.assertEqual(resp.status_code, 201)
        data = json.loads(resp.data)
        self.assertTrue(data['ok'])
        self.assertGreater(data['invited'], 0)

    def test_invite_empty_user_ids_returns_400(self):
        resp = self._admin_post(
            f'/api/v1/invitations/event/{self.ev.id}/invite',
            {'user_ids': []},
        )
        self.assertEqual(resp.status_code, 400)

    def test_invite_event_not_found_returns_404(self):
        resp = self._admin_post(
            '/api/v1/invitations/event/99999/invite',
            {'user_ids': [str(self.student.uuid)]},
        )
        self.assertEqual(resp.status_code, 404)

    def test_invite_requires_permission(self):
        role2 = make_role('applicant')
        u2 = make_user(role2, suffix='_app')
        db.session.commit()
        client2 = self.app.test_client()
        login(client2, u2)
        inject_csrf(client2)
        resp = client2.post(
            f'/api/v1/invitations/event/{self.ev.id}/invite',
            data=json.dumps({'user_ids': [str(self.student.uuid)]}),
            content_type='application/json',
            headers={'X-CSRFToken': 'test-csrf-token'},
        )
        self.assertEqual(resp.status_code, 403)


class TestListEventInvitations(TestInvitationsApiBase):

    def test_list_invitations_success(self):
        inv = EventInvitation(
            event_id=self.ev.id,
            user_id=self.student.id,
            invited_by=self.admin.id,
            status='pending',
        )
        db.session.add(inv)
        db.session.commit()
        resp = self.admin_client.get(f'/api/v1/invitations/event/{self.ev.id}/list')
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertTrue(data['ok'])
        self.assertGreater(data['total'], 0)

    def test_list_invitations_not_found_event(self):
        resp = self.admin_client.get('/api/v1/invitations/event/99999/list')
        self.assertEqual(resp.status_code, 404)


class TestRespondToInvitation(TestInvitationsApiBase):

    def _make_invitation(self, status='pending'):
        inv = EventInvitation(
            event_id=self.ev.id,
            user_id=self.student.id,
            invited_by=self.admin.id,
            status=status,
        )
        db.session.add(inv)
        db.session.commit()
        return inv

    def test_accept_invitation(self):
        inv = self._make_invitation()
        resp = self._student_post(
            f'/api/v1/invitations/{inv.id}/respond',
            {'accept': True},
        )
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data['status'], 'accepted')

    def test_reject_invitation(self):
        inv = self._make_invitation()
        resp = self._student_post(
            f'/api/v1/invitations/{inv.id}/respond',
            {'accept': False},
        )
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data['status'], 'rejected')

    def test_respond_wrong_user_returns_400(self):
        inv = self._make_invitation()
        # Admin tries to respond to student's invitation
        resp = self._admin_post(
            f'/api/v1/invitations/{inv.id}/respond',
            {'accept': True},
        )
        self.assertEqual(resp.status_code, 400)

    def test_respond_cancelled_invitation_returns_400(self):
        inv = self._make_invitation(status='cancelled')
        resp = self._student_post(
            f'/api/v1/invitations/{inv.id}/respond',
            {'accept': True},
        )
        self.assertEqual(resp.status_code, 400)

    def test_respond_not_found_returns_400(self):
        resp = self._student_post(
            '/api/v1/invitations/99999/respond',
            {'accept': True},
        )
        self.assertEqual(resp.status_code, 400)


class TestMyInvitations(TestInvitationsApiBase):

    def test_my_invitations_returns_pending(self):
        inv = EventInvitation(
            event_id=self.ev.id,
            user_id=self.student.id,
            invited_by=self.admin.id,
            status='pending',
        )
        db.session.add(inv)
        db.session.commit()
        resp = self.student_client.get('/api/v1/invitations/my-invitations')
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertTrue(data['ok'])
        self.assertEqual(data['total'], 1)

    def test_my_invitations_does_not_include_non_pending(self):
        """GET /my-invitations only returns pending invitations."""
        inv = EventInvitation(
            event_id=self.ev.id,
            user_id=self.student.id,
            invited_by=self.admin.id,
            status='accepted',
        )
        db.session.add(inv)
        db.session.commit()
        resp = self.student_client.get('/api/v1/invitations/my-invitations')
        data = json.loads(resp.data)
        self.assertEqual(data['total'], 0)

    def test_my_invitations_empty(self):
        resp = self.student_client.get('/api/v1/invitations/my-invitations')
        data = json.loads(resp.data)
        self.assertEqual(data['total'], 0)

    def test_my_invitations_requires_auth(self):
        anon = self.app.test_client()
        resp = anon.get('/api/v1/invitations/my-invitations')
        self.assertIn(resp.status_code, (401, 302, 403))


class TestCancelInvitation(TestInvitationsApiBase):

    def test_cancel_pending_invitation(self):
        inv = EventInvitation(
            event_id=self.ev.id,
            user_id=self.student.id,
            invited_by=self.admin.id,
            status='pending',
        )
        db.session.add(inv)
        db.session.commit()
        resp = self._admin_delete(f'/api/v1/invitations/{inv.id}')
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertTrue(data['ok'])

    def test_cancel_accepted_invitation_returns_400(self):
        inv = EventInvitation(
            event_id=self.ev.id,
            user_id=self.student.id,
            invited_by=self.admin.id,
            status='accepted',
        )
        db.session.add(inv)
        db.session.commit()
        resp = self._admin_delete(f'/api/v1/invitations/{inv.id}')
        self.assertEqual(resp.status_code, 400)


class TestUpdateEventDates(TestInvitationsApiBase):

    def test_update_event_dates_success(self):
        future = (datetime.now() + timedelta(days=7)).isoformat()
        resp = self.admin_client.put(
            f'/api/v1/invitations/event/{self.ev.id}/dates',
            data=json.dumps({'event_date': future}),
            content_type='application/json',
            headers={'X-CSRFToken': self.admin_csrf},
        )
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertTrue(data['ok'])

    def test_update_dates_event_not_found(self):
        resp = self.admin_client.put(
            '/api/v1/invitations/event/99999/dates',
            data=json.dumps({'event_date': '2030-01-01T00:00:00'}),
            content_type='application/json',
            headers={'X-CSRFToken': self.admin_csrf},
        )
        self.assertEqual(resp.status_code, 404)


class TestInstitutionalEventScope(TestInvitationsApiBase):
    """
    El evento SIN programa (institucional) es de la Jefatura de Posgrado, no
    de todos los gestores.

    Antes, el predicado local de este módulo devolvía "en alcance" para
    cualquier evento con `program_id = NULL`, así que el coordinador de un
    programa reescribía las fechas del evento institucional, invitaba —con
    correo real— a estudiantes de otros posgrados y leía la lista completa de
    invitados de toda la institución. La regla vive ahora en
    `EventsService.user_may_manage_event`: sin programa, sólo alcance global.
    """

    def setUp(self):
        super().setUp()

        # Evento institucional (sin programa).
        self.inst_ev = Event(
            program_id=None,
            type='conference',
            title='EXANI institucional',
            description='',
            location='',
            created_by=self.admin.id,
            visible_to_students=True,
            capacity_type='multiple',
            max_capacity=50,
            requires_registration=True,
            allows_attendance_tracking=False,
            reminders_enabled=True,
            status='published',
            visibility='public',
        )
        db.session.add(self.inst_ev)

        # Jefe de posgrado: alcance global por permiso DE ROL.
        self.role_head = make_role('postgraduate_admin')
        for code in ('academic_periods.api.create', 'invitations.api.send',
                     'invitations.api.list', 'invitations.api.manage'):
            grant_permission(self.role_head, code)
        self.head = make_user(self.role_head, suffix='_head')
        db.session.commit()

        self.head_client = self.app.test_client()
        self.head_csrf = login(self.head_client, self.head)

    @staticmethod
    def _clear_login_cache():
        from flask import g
        if hasattr(g, '_login_user'):
            del g._login_user

    def test_coordinator_cannot_list_institutional_invitations(self):
        inv = EventInvitation(
            event_id=self.inst_ev.id,
            user_id=self.student.id,
            invited_by=self.admin.id,
            status='pending',
        )
        db.session.add(inv)
        db.session.commit()

        self._clear_login_cache()
        resp = self.admin_client.get(
            f'/api/v1/invitations/event/{self.inst_ev.id}/list')
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(json.loads(resp.data)['error']['code'], 'FORBIDDEN')

    def test_coordinator_cannot_invite_to_institutional_event(self):
        self._clear_login_cache()
        resp = self._admin_post(
            f'/api/v1/invitations/event/{self.inst_ev.id}/invite',
            {'user_ids': [str(self.student.uuid)]},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(EventInvitation.query.count(), 0)

    def test_coordinator_cannot_rewrite_institutional_dates(self):
        self._clear_login_cache()
        resp = self.admin_client.put(
            f'/api/v1/invitations/event/{self.inst_ev.id}/dates',
            data=json.dumps({'event_date': (datetime.now() + timedelta(days=9)).isoformat()}),
            content_type='application/json',
            headers={'X-CSRFToken': self.admin_csrf},
        )
        self.assertEqual(resp.status_code, 403)
        db.session.refresh(self.inst_ev)
        self.assertIsNone(self.inst_ev.event_date)

    def test_coordinator_cannot_cancel_institutional_invitation(self):
        inv = EventInvitation(
            event_id=self.inst_ev.id,
            user_id=self.student.id,
            invited_by=self.admin.id,
            status='pending',
        )
        db.session.add(inv)
        db.session.commit()

        self._clear_login_cache()
        resp = self._admin_delete(f'/api/v1/invitations/{inv.id}')
        # 404 deliberado: no se confirma qué ids de invitación existen.
        self.assertEqual(resp.status_code, 404)
        db.session.refresh(inv)
        self.assertEqual(inv.status, 'pending')

    def test_head_of_postgraduate_still_manages_institutional_event(self):
        self._clear_login_cache()
        resp = self.head_client.put(
            f'/api/v1/invitations/event/{self.inst_ev.id}/dates',
            data=json.dumps({'event_date': (datetime.now() + timedelta(days=9)).isoformat()}),
            content_type='application/json',
            headers={'X-CSRFToken': self.head_csrf},
        )
        self.assertEqual(resp.status_code, 200)


class TestInviteRecipientScope(TestInvitationsApiBase):
    """
    La comprobación de destinatarios ya no se ancla a que el evento tenga
    programa: `if event.program_id and not is_global_scope(...)` era el mismo
    hueco institucional en miniatura.
    """

    def setUp(self):
        super().setUp()

        self.other_admin = make_user(self.role_admin, suffix='_adm2')
        db.session.flush()
        self.other_prog = make_program(self.other_admin, slug='otro-prog')

        self.foreign_student = make_user(self.role_student, suffix='_stu2')
        db.session.flush()
        db.session.add(UserProgram(
            user_id=self.foreign_student.id,
            program_id=self.other_prog.id,
            admission_period_id=self.period.id,
            admission_status='in_progress',
        ))
        db.session.commit()

    def test_cannot_invite_student_of_another_program(self):
        from flask import g
        if hasattr(g, '_login_user'):
            del g._login_user
        resp = self._admin_post(
            f'/api/v1/invitations/event/{self.ev.id}/invite',
            {'user_ids': [str(self.foreign_student.uuid)]},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(EventInvitation.query.count(), 0)
