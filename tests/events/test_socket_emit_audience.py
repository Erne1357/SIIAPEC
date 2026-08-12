"""
Audiencia de los emits de Socket.IO (F14 / F15).

Una sala es una decisión de divulgación, no un detalle de transporte: lo que
entra en `role:coordinator` lo lee CADA titular de `coordinator.page.view`, que
en el seed es cada program_admin de la institución. Estas pruebas fijan a qué
sala va cada payload, porque una regresión aquí no rompe nada visible — sólo
manda datos de más.

No tocan la base del dueño: SQLite en memoria, igual que el resto de tests/events.
"""

import unittest
from unittest.mock import patch

from app import create_app, db
from app.models.user_program import UserProgram
from app.models.event import EventInvitation
from app.sockets import emitters

from tests.events.conftest import (
    make_test_config,
    make_role,
    make_user,
    make_program,
    make_event,
)


class EmitRoomsTestCase(unittest.TestCase):
    """Base: levanta la app y captura (event, payload, room) de cada emit."""

    def setUp(self):
        self.app = create_app(test_config=make_test_config())
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.role_admin = make_role('program_admin')
        self.role_student = make_role('student')

        self.coordinator = make_program_coordinator = make_user(self.role_admin)
        self.program_a = make_program(make_program_coordinator, slug='prog-a')
        self.program_b = make_program(make_program_coordinator, slug='prog-b')

        self.student_a = make_user(self.role_student, suffix='_a')
        self.student_b = make_user(self.role_student, suffix='_b')
        self.outsider = make_user(self.role_student, suffix='_out')

        db.session.add(UserProgram(
            user_id=self.student_a.id, program_id=self.program_a.id,
            admission_status='enrolled',
        ))
        db.session.add(UserProgram(
            user_id=self.student_b.id, program_id=self.program_b.id,
            admission_status='enrolled',
        ))
        db.session.commit()

        self.sent = []

        def _record(event, payload=None, room=None, **kwargs):
            self.sent.append((event, payload, room))

        patcher = patch('app.extensions.socketio.emit', side_effect=_record)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    # -- helpers ------------------------------------------------------------

    def rooms(self):
        return [room for (_e, _p, room) in self.sent]

    def payload_for(self, room):
        for (_e, payload, r) in self.sent:
            if r == room:
                return payload
        return None


class TestEventChangeAudience(EmitRoomsTestCase):
    """F15 — `event:changed` ya no es un broadcast incondicional."""

    def _payload(self, ev, action='created'):
        return {
            'action': action,
            'event_id': ev.id,
            'program_id': ev.program_id,
            'title': ev.title,
        }

    def test_draft_event_never_reaches_a_participant(self):
        """El defecto original: create_event emitía el título de un borrador."""
        ev = make_event(self.coordinator.id, program_id=self.program_a.id,
                        status='draft', title='Borrador confidencial')
        db.session.commit()

        emitters.emit_event_change(self._payload(ev), event=ev)

        self.assertNotIn(None, self.rooms(), 'no debe haber broadcast sin sala')
        self.assertNotIn(f'user:{self.student_a.id}', self.rooms())
        self.assertIn(f'coordinator:program:{self.program_a.id}', self.rooms())

    def test_private_event_never_reaches_a_participant(self):
        ev = make_event(self.coordinator.id, program_id=self.program_a.id,
                        status='published', visibility='private')
        db.session.commit()

        emitters.emit_event_change(self._payload(ev), event=ev)

        self.assertNotIn(None, self.rooms())
        self.assertNotIn(f'user:{self.student_a.id}', self.rooms())

    def test_private_event_reaches_its_invitee(self):
        """Una invitación la escribe quien gestiona: es concesión de tercero."""
        ev = make_event(self.coordinator.id, program_id=self.program_a.id,
                        status='published', visibility='private')
        db.session.add(EventInvitation(event_id=ev.id, user_id=self.outsider.id))
        db.session.commit()

        emitters.emit_event_change(self._payload(ev), event=ev)

        self.assertIn(f'user:{self.outsider.id}', self.rooms())
        self.assertNotIn(f'user:{self.student_a.id}', self.rooms())

    def test_public_program_event_reaches_only_its_own_program(self):
        ev = make_event(self.coordinator.id, program_id=self.program_a.id,
                        status='published', visibility='public')
        db.session.commit()

        emitters.emit_event_change(self._payload(ev), event=ev)

        self.assertIn(f'user:{self.student_a.id}', self.rooms())
        self.assertNotIn(f'user:{self.student_b.id}', self.rooms())
        self.assertNotIn(None, self.rooms())

    def test_public_institutional_event_is_the_only_broadcast(self):
        """program_id NULL + publicado + público: todo autenticado puede verlo."""
        ev = make_event(self.coordinator.id, program_id=None,
                        status='published', visibility='public')
        db.session.commit()

        emitters.emit_event_change(self._payload(ev), event=ev)

        self.assertIn(None, self.rooms())
        self.assertIn('coordinator:programs:all', self.rooms())
        self.assertNotIn(f'coordinator:program:{self.program_a.id}', self.rooms())

    def test_hidden_from_students_reaches_nobody_outside_management(self):
        ev = make_event(self.coordinator.id, program_id=self.program_a.id,
                        status='published', visibility='public',
                        visible_to_students=False)
        db.session.commit()

        emitters.emit_event_change(self._payload(ev), event=ev)

        self.assertNotIn(None, self.rooms())
        self.assertNotIn(f'user:{self.student_a.id}', self.rooms())

    def test_archived_sends_a_titleless_refresh_to_former_audience(self):
        ev = make_event(self.coordinator.id, program_id=self.program_a.id,
                        status='archived', visibility='public')
        db.session.commit()

        emitters.emit_event_change(self._payload(ev, action='archived'), event=ev)

        payload = self.payload_for(f'user:{self.student_a.id}')
        self.assertIsNotNone(payload, 'el participante necesita refrescar su lista')
        self.assertNotIn('title', payload)
        self.assertEqual(payload['action'], 'archived')

    def test_deleted_without_event_stays_on_management_only(self):
        emitters.emit_event_change({
            'action': 'deleted',
            'event_id': 999,
            'program_id': self.program_a.id,
            'title': 'Lo que fuera',
        })

        self.assertNotIn(None, self.rooms())
        self.assertEqual(
            set(self.rooms()),
            {'coordinator:programs:all', f'coordinator:program:{self.program_a.id}'},
        )


class TestAdminUserChangeAudience(EmitRoomsTestCase):
    """F14 — nombre y correo de una cuenta de personal no cruzan de programa."""

    def test_staff_account_only_reaches_global_scope(self):
        emitters.emit_admin_user_change(
            {'action': 'updated', 'user_id': self.coordinator.id,
             'role': 'program_admin', 'email': 'x@y.z', 'full_name': 'Staff'},
            None,
        )
        self.assertEqual(self.rooms(), ['role:postgraduate_admin'])

    def test_student_account_also_reaches_their_program(self):
        emitters.emit_admin_user_change(
            {'action': 'updated', 'user_id': self.student_a.id,
             'role': 'student', 'email': 'a@y.z', 'full_name': 'Alumno A'},
            {self.program_a.id},
        )
        self.assertEqual(
            self.rooms(),
            ['role:postgraduate_admin', f'coordinator:program:{self.program_a.id}'],
        )
        self.assertNotIn('role:coordinator', self.rooms())


if __name__ == '__main__':
    unittest.main()
