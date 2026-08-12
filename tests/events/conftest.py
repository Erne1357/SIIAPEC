# tests/events/conftest.py
"""
Shared test configuration, helpers, and fixtures for the events test suite.

All test classes import from here to avoid duplication. Each test file is
responsible for its own setUp / tearDown — nothing here is auto-used.
"""

import tempfile
from pathlib import Path
from datetime import date, time, datetime, timedelta

from app import create_app, db
from app.models.role import Role
from app.models.user import User
from app.models.program import Program
from app.models.academic_period import AcademicPeriod
from app.models.permission import Permission
from app.models.role_permission import RolePermission
from app.models.user_program import UserProgram
from app.models.event import Event, EventInvitation, EventAttendance

# ---------------------------------------------------------------------------
# App config used by every test in this package
# ---------------------------------------------------------------------------

def make_test_config(upload_folder: str | None = None) -> dict:
    """Return a config dict suitable for create_app(test_config=...)."""
    cfg = {
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
        'SECRET_KEY': 'test-secret-key',
        'WTF_CSRF_ENABLED': False,
        'CELERY_BROKER_URL': 'memory://',
        'CELERY_RESULT_BACKEND': 'cache+memory://',
        'SERVER_NAME': 'localhost.test',
        'PUBLIC_BASE_URL': 'http://localhost.test',
        'PREFERRED_URL_SCHEME': 'http',
        # Keep Max content length small for tests
        'MAX_CONTENT_LENGTH': 10 * 1024 * 1024,
        'MAX_EVENT_IMAGE_BYTES': 5 * 1024 * 1024,
        'ALLOWED_IMAGE_EXT': {'jpg', 'jpeg', 'png', 'webp'},
        'ALLOWED_DOC_EXT': {'pdf', 'doc', 'docx'},
    }
    if upload_folder:
        p = Path(upload_folder)
        cfg['UPLOAD_FOLDER'] = p
        cfg['AVATAR_FOLDER'] = p / 'avatars'
        cfg['USER_DOCS_FOLDER'] = p / 'documents'
        cfg['EVENTS_FOLDER'] = p / 'events'
        cfg['TEMPLATE_STORE'] = p / 'templates_sys'
    return cfg


# ---------------------------------------------------------------------------
# Model factories
# ---------------------------------------------------------------------------

def make_role(name: str) -> Role:
    r = Role(name=name, description=f'Test role: {name}')
    db.session.add(r)
    db.session.flush()
    return r


def make_user(role: Role, suffix: str = '', must_change_password: bool = False) -> User:
    username = f'user_{role.name}{suffix}'
    u = User(
        first_name='Test',
        last_name='User',
        mother_last_name='',
        username=username,
        password='Test1234!',
        email=f'{username}@siiap.test',
        is_internal=True,
        role_id=role.id,
        must_change_password=must_change_password,
    )
    db.session.add(u)
    db.session.flush()
    return u


def make_program(coordinator: User, slug: str = 'test-prog') -> Program:
    p = Program(
        name='Test Program',
        description='Test',
        coordinator_id=coordinator.id,
        slug=slug,
        is_active=True,
    )
    db.session.add(p)
    db.session.flush()
    return p


def enroll_in_program(user: User, program: Program,
                      admission_status: str = 'enrolled') -> UserProgram:
    """
    Link a user to a program.

    This is the row `program_scope_service.program_ids_of_user()` reads, i.e.
    what makes an event of that program *participable* for the user. A student
    without it is nobody's student: institutional events only.
    """
    up = UserProgram(
        user_id=user.id,
        program_id=program.id,
        admission_status=admission_status,
    )
    db.session.add(up)
    db.session.flush()
    return up


def make_academic_period(is_active: bool = True, code: str = '20251') -> AcademicPeriod:
    today = date.today()
    ap = AcademicPeriod(
        code=code,
        name=f'Period {code}',
        start_date=today - timedelta(days=30),
        end_date=today + timedelta(days=150),
        admission_start_date=today - timedelta(days=60),
        admission_end_date=today + timedelta(days=30),
        is_active=is_active,
        status='active' if is_active else 'upcoming',
    )
    db.session.add(ap)
    db.session.flush()
    return ap


def make_permission(codename: str) -> Permission:
    parts = codename.split('.')
    resource = parts[0]
    perm_type = parts[1] if len(parts) > 1 else 'api'
    action = '.'.join(parts[2:]) if len(parts) > 2 else 'action'
    perm = Permission(
        codename=codename,
        display_name=codename,
        resource=resource,
        perm_type=perm_type,
        action=action,
    )
    db.session.add(perm)
    db.session.flush()
    return perm


def grant_permission(role: Role, codename: str) -> None:
    """Grant a permission codename to a role, creating it if needed."""
    perm = Permission.query.filter_by(codename=codename).first()
    if not perm:
        perm = make_permission(codename)
    existing = RolePermission.query.filter_by(
        role_id=role.id, permission_id=perm.id
    ).first()
    if not existing:
        db.session.add(RolePermission(role_id=role.id, permission_id=perm.id))
        db.session.flush()


def make_event(
    created_by: int,
    program_id: int | None = None,
    title: str = 'Test Event',
    status: str = 'published',
    capacity_type: str = 'multiple',
    max_capacity: int | None = 20,
    visibility: str = 'public',
    visible_to_students: bool = True,
    event_date: datetime | None = None,
) -> Event:
    ev = Event(
        program_id=program_id,
        type='conference',
        title=title,
        description='Test description',
        location='Test Location',
        created_by=created_by,
        visible_to_students=visible_to_students,
        capacity_type=capacity_type,
        max_capacity=max_capacity,
        requires_registration=True,
        allows_attendance_tracking=False,
        reminders_enabled=True,
        status=status,
        visibility=visibility,
        event_date=event_date,
    )
    db.session.add(ev)
    db.session.flush()
    return ev


def login(client, user: User, password: str = 'Test1234!') -> str:
    """
    Log in a test user via the JSON API login endpoint.
    Returns the CSRF token issued by the server on successful login.
    The token should be used as the X-CSRFToken header for mutation requests.

    After the login request completes, ``g._login_user`` is cleared so that
    the *next* request from *any* client will re-resolve ``current_user``
    from the session cookie (via ``load_user``).  Without this, Flask keeps
    the last user returned by ``login_user()`` in the app-context-level ``g``
    and every subsequent request acts as that user regardless of which client
    cookie is sent.
    """
    import json as _json
    resp = client.post(
        '/api/v1/auth/login',
        json={'username': user.username, 'password': password},
    )
    # Clear the app-context login cache so each subsequent request re-loads
    # current_user from its own session cookie.
    try:
        from flask import g as _g
        if hasattr(_g, '_login_user'):
            del _g._login_user
    except RuntimeError:
        pass  # no app context active — ignore
    try:
        data = _json.loads(resp.data)
        return data.get('data', {}).get('csrf_token', 'test-csrf-token')
    except Exception:
        return 'test-csrf-token'


def add_csrf_header(csrf_token: str = 'test-csrf-token') -> dict:
    """Return headers dict with a CSRF token."""
    return {'X-CSRFToken': csrf_token}


def inject_csrf(client) -> str:
    """
    Injects a known CSRF token directly into the test client's session.
    Use this when you need a CSRF token without going through login
    (e.g. for already-authenticated clients or non-login endpoints).
    Returns the token string.
    """
    token = 'test-csrf-token'
    with client.session_transaction() as sess:
        sess['_csrf_token'] = token
    return token
