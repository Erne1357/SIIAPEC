# tests/events/test_events_service.py
"""
Unit tests for EventsService core methods:
  - create_event
  - update_event
  - list_admin_events
  - add_window / generate_slots
  - delete_slot / delete_window
  - register_to_event / unregister_from_event / mark_attendance
  - count_new_events / mark_events_seen
  - update_event_dates
"""

import unittest
from datetime import date, time, datetime, timedelta

from app import create_app, db
from app.models.event import Event, EventWindow, EventSlot, EventAttendance
from app.services.events_service import EventsService
from tests.events.conftest import (
    make_test_config, make_role, make_user, make_program, make_academic_period,
    enroll_in_program,
)


class TestCreateEvent(unittest.TestCase):

    def setUp(self):
        self.app = create_app(make_test_config())
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.role = make_role('program_admin')
        self.admin = make_user(self.role)
        self.prog = make_program(self.admin)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_create_basic_event_returns_event(self):
        ev = EventsService.create_event(
            program_id=self.prog.id,
            type_='conference',
            title='My Event',
            description='Desc',
            location='Hall A',
            created_by=self.admin.id,
            capacity_type='multiple',
            max_capacity=50,
        )
        self.assertIsNotNone(ev.id)
        self.assertEqual(ev.title, 'My Event')
        self.assertEqual(ev.capacity_type, 'multiple')

    def test_create_event_invalid_capacity_type_raises(self):
        with self.assertRaises(ValueError) as ctx:
            EventsService.create_event(
                program_id=None,
                type_='conference',
                title='Bad',
                description=None,
                location=None,
                created_by=self.admin.id,
                capacity_type='invalid_type',
            )
        self.assertIn('capacity_type', str(ctx.exception))

    def test_create_multiple_event_without_max_capacity_raises(self):
        with self.assertRaises(ValueError):
            EventsService.create_event(
                program_id=None,
                type_='conference',
                title='Bad Multiple',
                description=None,
                location=None,
                created_by=self.admin.id,
                capacity_type='multiple',
                max_capacity=None,
            )

    def test_create_event_invalid_visibility_raises(self):
        with self.assertRaises(ValueError):
            EventsService.create_event(
                program_id=None,
                type_='conference',
                title='Bad Vis',
                description=None,
                location=None,
                created_by=self.admin.id,
                capacity_type='unlimited',
                visibility='secret',
            )

    def test_create_event_unlimited_no_max_capacity(self):
        ev = EventsService.create_event(
            program_id=None,
            type_='seminar',
            title='Unlimited',
            description=None,
            location=None,
            created_by=self.admin.id,
            capacity_type='unlimited',
        )
        self.assertIsNone(ev.max_capacity)

    def test_create_draft_event(self):
        ev = EventsService.create_event(
            program_id=None,
            type_='seminar',
            title='Draft',
            description=None,
            location=None,
            created_by=self.admin.id,
            capacity_type='unlimited',
            status='draft',
        )
        self.assertEqual(ev.status, 'draft')

    def test_create_event_auto_picks_active_period(self):
        period = make_academic_period(is_active=True)
        db.session.commit()
        ev = EventsService.create_event(
            program_id=None,
            type_='seminar',
            title='AutoPeriod',
            description=None,
            location=None,
            created_by=self.admin.id,
            capacity_type='unlimited',
        )
        self.assertEqual(ev.academic_period_id, period.id)

    def test_create_event_explicit_period_overrides_active(self):
        period = make_academic_period(is_active=True)
        period2 = make_academic_period(is_active=False, code='20252')
        db.session.commit()
        ev = EventsService.create_event(
            program_id=None,
            type_='seminar',
            title='ExplicitPeriod',
            description=None,
            location=None,
            created_by=self.admin.id,
            capacity_type='unlimited',
            academic_period_id=period2.id,
        )
        self.assertEqual(ev.academic_period_id, period2.id)

    def test_create_public_published_multiple_does_not_crash_without_roles(self):
        # No roles seeded -> broadcast finds no users, does not raise
        ev = EventsService.create_event(
            program_id=None,
            type_='conference',
            title='Broadcast Test',
            description=None,
            location=None,
            created_by=self.admin.id,
            capacity_type='multiple',
            max_capacity=100,
            status='published',
            visibility='public',
            visible_to_students=True,
        )
        self.assertIsNotNone(ev.id)


class TestUpdateEvent(unittest.TestCase):

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
            title='Original',
            description='Desc',
            location='Loc',
            created_by=self.admin.id,
            visible_to_students=True,
            capacity_type='multiple',
            max_capacity=20,
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

    def test_update_title(self):
        updated = EventsService.update_event(self.ev.id, {'title': 'New Title'})
        self.assertEqual(updated.title, 'New Title')

    def test_update_status(self):
        updated = EventsService.update_event(self.ev.id, {'status': 'cancelled'})
        self.assertEqual(updated.status, 'cancelled')

    def test_update_not_found_raises(self):
        with self.assertRaises(ValueError):
            EventsService.update_event(9999, {'title': 'Ghost'})

    def test_update_capacity_type_with_no_slots_allowed(self):
        updated = EventsService.update_event(self.ev.id, {'capacity_type': 'unlimited'})
        self.assertEqual(updated.capacity_type, 'unlimited')

    def test_update_capacity_type_with_existing_slots_raises(self):
        # Add a window and slot first
        win = EventWindow(
            event_id=self.ev.id,
            date=date.today(),
            start_time=time(9, 0),
            end_time=time(10, 0),
            slot_minutes=30,
        )
        db.session.add(win)
        db.session.flush()
        slot = EventSlot(
            event_window_id=win.id,
            starts_at=datetime.now(),
            ends_at=datetime.now() + timedelta(minutes=30),
            status='free',
        )
        db.session.add(slot)
        db.session.commit()

        with self.assertRaises(ValueError):
            EventsService.update_event(self.ev.id, {'capacity_type': 'unlimited'})


class TestListAdminEvents(unittest.TestCase):

    def setUp(self):
        self.app = create_app(make_test_config())
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        role = make_role('program_admin')
        self.admin = make_user(role)
        self.prog = make_program(self.admin)

        self.ev1 = Event(
            program_id=self.prog.id,
            type='conference',
            title='Published Event',
            description='',
            location='',
            created_by=self.admin.id,
            visible_to_students=True,
            capacity_type='multiple',
            max_capacity=10,
            requires_registration=True,
            allows_attendance_tracking=False,
            reminders_enabled=True,
            status='published',
            visibility='public',
        )
        self.ev2 = Event(
            program_id=self.prog.id,
            type='seminar',
            title='Draft Event',
            description='',
            location='',
            created_by=self.admin.id,
            visible_to_students=False,
            capacity_type='single',
            max_capacity=None,
            requires_registration=True,
            allows_attendance_tracking=False,
            reminders_enabled=True,
            status='draft',
            visibility='public',
        )
        db.session.add_all([self.ev1, self.ev2])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_global_admin_sees_all_events(self):
        # accessible_pids=None means global access
        events = EventsService.list_admin_events(accessible_pids=None)
        ids = [e.id for e in events]
        self.assertIn(self.ev1.id, ids)
        self.assertIn(self.ev2.id, ids)

    def test_scoped_admin_sees_program_events(self):
        events = EventsService.list_admin_events(accessible_pids={self.prog.id})
        ids = [e.id for e in events]
        self.assertIn(self.ev1.id, ids)
        self.assertIn(self.ev2.id, ids)

    def test_filter_by_status(self):
        events = EventsService.list_admin_events(
            accessible_pids=None, filters={'status': 'draft'}
        )
        for e in events:
            self.assertEqual(e.status, 'draft')

    def test_archived_hidden_by_default(self):
        archived = Event(
            program_id=self.prog.id,
            type='conference',
            title='Archived',
            description='',
            location='',
            created_by=self.admin.id,
            visible_to_students=False,
            capacity_type='unlimited',
            max_capacity=None,
            requires_registration=False,
            allows_attendance_tracking=False,
            reminders_enabled=False,
            status='archived',
            visibility='public',
        )
        db.session.add(archived)
        db.session.commit()
        events = EventsService.list_admin_events(accessible_pids=None)
        for e in events:
            self.assertNotEqual(e.status, 'archived')

    def test_filter_by_search_title(self):
        events = EventsService.list_admin_events(
            accessible_pids=None, filters={'search': 'Published'}
        )
        self.assertTrue(any(e.title == 'Published Event' for e in events))

    def test_empty_scope_shows_global_events_only(self):
        # program set to empty -> only global (program_id IS NULL)
        global_ev = Event(
            program_id=None,
            type='conference',
            title='Global',
            description='',
            location='',
            created_by=self.admin.id,
            visible_to_students=True,
            capacity_type='unlimited',
            max_capacity=None,
            requires_registration=False,
            allows_attendance_tracking=False,
            reminders_enabled=False,
            status='published',
            visibility='public',
        )
        db.session.add(global_ev)
        db.session.commit()
        events = EventsService.list_admin_events(accessible_pids=set())
        ids = [e.id for e in events]
        self.assertIn(global_ev.id, ids)
        self.assertNotIn(self.ev1.id, ids)


class TestWindowsAndSlots(unittest.TestCase):

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
            type='interview',
            title='Interview Event',
            description='',
            location='',
            created_by=self.admin.id,
            visible_to_students=True,
            capacity_type='single',
            max_capacity=None,
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

    def test_add_window_returns_window(self):
        win = EventsService.add_window(
            event_id=self.ev.id,
            window_date=date.today(),
            start=time(9, 0),
            end=time(12, 0),
            slot_minutes=30,
        )
        self.assertIsNotNone(win.id)
        self.assertEqual(win.slot_minutes, 30)

    def test_add_window_invalid_time_raises(self):
        with self.assertRaises(ValueError):
            EventsService.add_window(
                event_id=self.ev.id,
                window_date=date.today(),
                start=time(12, 0),
                end=time(9, 0),
                slot_minutes=30,
            )

    def test_add_window_invalid_slot_minutes_raises(self):
        with self.assertRaises(ValueError):
            EventsService.add_window(
                event_id=self.ev.id,
                window_date=date.today(),
                start=time(9, 0),
                end=time(12, 0),
                slot_minutes=7,
            )

    def test_generate_slots_creates_correct_count(self):
        win = EventsService.add_window(
            event_id=self.ev.id,
            window_date=date.today(),
            start=time(9, 0),
            end=time(10, 0),
            slot_minutes=30,
        )
        result = EventsService.generate_slots(win.id)
        # 9:00-10:00 / 30 min = 2 slots
        self.assertEqual(result['created'], 2)
        self.assertTrue(db.session.get(EventWindow, win.id).slots_generated)

    def test_generate_slots_window_not_found_raises(self):
        with self.assertRaises(ValueError):
            EventsService.generate_slots(99999)

    def test_delete_slot_free(self):
        win = EventsService.add_window(
            event_id=self.ev.id,
            window_date=date.today(),
            start=time(9, 0),
            end=time(10, 0),
            slot_minutes=30,
        )
        EventsService.generate_slots(win.id)
        slots = EventSlot.query.filter_by(event_window_id=win.id).all()
        slot_id = slots[0].id
        result = EventsService.delete_slot(slot_id)
        self.assertTrue(result)
        self.assertIsNone(db.session.get(EventSlot, slot_id))

    def test_delete_slot_occupied_without_force_raises(self):
        win = EventsService.add_window(
            event_id=self.ev.id,
            window_date=date.today(),
            start=time(9, 0),
            end=time(10, 0),
            slot_minutes=30,
        )
        EventsService.generate_slots(win.id)
        slot = EventSlot.query.filter_by(event_window_id=win.id).first()
        slot.status = 'booked'
        db.session.commit()
        with self.assertRaises(ValueError):
            EventsService.delete_slot(slot.id)

    def test_delete_window_with_no_booked_slots(self):
        win = EventsService.add_window(
            event_id=self.ev.id,
            window_date=date.today(),
            start=time(9, 0),
            end=time(10, 0),
            slot_minutes=30,
        )
        win_id = win.id
        EventsService.generate_slots(win.id)
        result = EventsService.delete_window(win_id)
        self.assertTrue(result)
        self.assertIsNone(db.session.get(EventWindow, win_id))

    def test_delete_window_with_booked_slots_without_force_raises(self):
        win = EventsService.add_window(
            event_id=self.ev.id,
            window_date=date.today(),
            start=time(9, 0),
            end=time(10, 0),
            slot_minutes=30,
        )
        EventsService.generate_slots(win.id)
        slot = EventSlot.query.filter_by(event_window_id=win.id).first()
        slot.status = 'booked'
        db.session.commit()
        with self.assertRaises(ValueError):
            EventsService.delete_window(win.id)

    def test_list_slots_by_event(self):
        win = EventsService.add_window(
            event_id=self.ev.id,
            window_date=date.today(),
            start=time(9, 0),
            end=time(10, 0),
            slot_minutes=30,
        )
        EventsService.generate_slots(win.id)
        slots = EventsService.list_slots(event_id=self.ev.id)
        self.assertEqual(len(slots), 2)

    def test_list_slots_by_status_filter(self):
        win = EventsService.add_window(
            event_id=self.ev.id,
            window_date=date.today(),
            start=time(9, 0),
            end=time(10, 0),
            slot_minutes=30,
        )
        EventsService.generate_slots(win.id)
        free_slots = EventsService.list_slots(event_id=self.ev.id, status='free')
        self.assertEqual(len(free_slots), 2)
        booked_slots = EventsService.list_slots(event_id=self.ev.id, status='booked')
        self.assertEqual(len(booked_slots), 0)


class TestRegistration(unittest.TestCase):

    def setUp(self):
        self.app = create_app(make_test_config())
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        role = make_role('student')
        self.student = make_user(role, suffix='_stu')
        role_admin = make_role('program_admin')
        self.admin = make_user(role_admin, suffix='_adm')
        self.prog = make_program(self.admin)
        # `register_to_event` now demands that the caller may participate in
        # the event, and the event below belongs to a program: the student has
        # to actually be a student of it.
        enroll_in_program(self.student, self.prog)
        self.ev = Event(
            program_id=self.prog.id,
            type='conference',
            title='Multiple Event',
            description='',
            location='',
            created_by=self.admin.id,
            visible_to_students=True,
            capacity_type='multiple',
            max_capacity=2,
            requires_registration=True,
            allows_attendance_tracking=True,
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

    def test_register_to_event_success(self):
        att = EventsService.register_to_event(self.ev.id, self.student.id)
        self.assertEqual(att.status, 'registered')
        self.assertEqual(att.event_id, self.ev.id)

    def test_register_to_single_event_raises(self):
        self.ev.capacity_type = 'single'
        db.session.commit()
        with self.assertRaises(ValueError):
            EventsService.register_to_event(self.ev.id, self.student.id)

    def test_register_twice_raises(self):
        EventsService.register_to_event(self.ev.id, self.student.id)
        with self.assertRaises(ValueError):
            EventsService.register_to_event(self.ev.id, self.student.id)

    def test_register_at_max_capacity_raises(self):
        role2 = make_role('student2')
        u1 = make_user(role2, suffix='_a')
        u2 = make_user(role2, suffix='_b')
        u3 = make_user(role2, suffix='_c')
        for u in (u1, u2, u3):
            enroll_in_program(u, self.prog)
        db.session.commit()
        EventsService.register_to_event(self.ev.id, u1.id)
        EventsService.register_to_event(self.ev.id, u2.id)
        with self.assertRaises(ValueError):
            EventsService.register_to_event(self.ev.id, u3.id)

    def test_unregister_success(self):
        EventsService.register_to_event(self.ev.id, self.student.id)
        result = EventsService.unregister_from_event(self.ev.id, self.student.id)
        self.assertTrue(result)
        self.assertIsNone(
            EventAttendance.query.filter_by(
                event_id=self.ev.id, user_id=self.student.id
            ).first()
        )

    def test_unregister_not_registered_raises(self):
        with self.assertRaises(ValueError):
            EventsService.unregister_from_event(self.ev.id, self.student.id)

    def test_mark_attendance_attended(self):
        EventsService.register_to_event(self.ev.id, self.student.id)
        att = EventsService.mark_attendance(self.ev.id, self.student.id, attended=True)
        self.assertEqual(att.status, 'attended')

    def test_mark_attendance_no_show(self):
        EventsService.register_to_event(self.ev.id, self.student.id)
        att = EventsService.mark_attendance(self.ev.id, self.student.id, attended=False)
        self.assertEqual(att.status, 'no_show')

    def test_mark_attendance_reset(self):
        EventsService.register_to_event(self.ev.id, self.student.id)
        EventsService.mark_attendance(self.ev.id, self.student.id, attended=True)
        att = EventsService.mark_attendance(self.ev.id, self.student.id, reset=True)
        self.assertEqual(att.status, 'registered')

    def test_mark_attendance_not_registered_raises(self):
        with self.assertRaises(ValueError):
            EventsService.mark_attendance(self.ev.id, self.student.id, attended=True)


class TestCountNewEventsAndMarkSeen(unittest.TestCase):

    def setUp(self):
        self.app = create_app(make_test_config())
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        role = make_role('student')
        self.student = make_user(role)
        role_admin = make_role('program_admin')
        self.admin = make_user(role_admin, suffix='_adm')
        self.prog = make_program(self.admin)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_count_returns_zero_when_no_events(self):
        count = EventsService.count_new_events(self.student.id)
        self.assertEqual(count, 0)

    def test_mark_events_seen_sets_timestamp(self):
        EventsService.mark_events_seen(self.student.id)
        from app.models.user import User
        u = db.session.get(User, self.student.id)
        self.assertIsNotNone(u.last_events_seen_at)

    def test_count_zero_after_mark_seen(self):
        # Create a public published multiple event first
        ev = Event(
            program_id=None,
            type='conference',
            title='New Event',
            description='',
            location='',
            created_by=self.admin.id,
            visible_to_students=True,
            capacity_type='multiple',
            max_capacity=100,
            requires_registration=False,
            allows_attendance_tracking=False,
            reminders_enabled=False,
            status='published',
            visibility='public',
        )
        db.session.add(ev)
        db.session.commit()

        # Mark seen now (after event creation)
        EventsService.mark_events_seen(self.student.id)
        count = EventsService.count_new_events(self.student.id)
        # The event was created before mark_seen, so count should be 0
        self.assertEqual(count, 0)

    def test_mark_seen_nonexistent_user_does_not_raise(self):
        # Should return silently
        EventsService.mark_events_seen(99999)


class TestUpdateEventDates(unittest.TestCase):

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
            title='Date Event',
            description='',
            location='',
            created_by=self.admin.id,
            visible_to_students=True,
            capacity_type='multiple',
            max_capacity=10,
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

    def test_update_event_date_success(self):
        future = datetime.now() + timedelta(days=30)
        updated = EventsService.update_event_dates(self.ev.id, event_date=future)
        self.assertIsNotNone(updated.event_date)

    def test_update_event_date_single_capacity_raises(self):
        self.ev.capacity_type = 'single'
        db.session.commit()
        with self.assertRaises(ValueError):
            EventsService.update_event_dates(self.ev.id, event_date=datetime.now())

    def test_update_event_date_not_found_raises(self):
        with self.assertRaises(ValueError):
            EventsService.update_event_dates(99999, event_date=datetime.now())
