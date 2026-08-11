# tests/bulk_import/conftest.py
"""
Fixtures para los tests del módulo de alta masiva de estudiantes
(student_bulk_service + endpoints).
"""

from datetime import date, timedelta
from pathlib import Path
import tempfile

import pytest

from app import create_app, db
from app.models.role import Role
from app.models.user import User
from app.models.program import Program
from app.models.academic_period import AcademicPeriod
from app.models.permission import Permission
from app.models.role_permission import RolePermission


@pytest.fixture
def app():
    upload_dir = Path(tempfile.mkdtemp(prefix='siiap_test_bulk_'))
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
        'MAX_CONTENT_LENGTH': 10 * 1024 * 1024,
        'ALLOWED_DOC_EXT': {'pdf', 'doc', 'docx', 'csv'},
        'UPLOAD_FOLDER': upload_dir,
        'AVATAR_FOLDER': upload_dir / 'avatars',
        'USER_DOCS_FOLDER': upload_dir / 'documents',
        'EVENTS_FOLDER': upload_dir / 'events',
        'TEMPLATE_STORE': upload_dir / 'templates_sys',
    }
    application = create_app(test_config=cfg)
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def roles(app):
    out = {}
    for name in ('applicant', 'program_admin', 'postgraduate_admin',
                 'social_service', 'student'):
        r = Role(name=name, description=f'Test role: {name}')
        db.session.add(r)
        out[name] = r
    db.session.flush()
    return out


def _grant(role, codename):
    parts = codename.split('.')
    perm = Permission.query.filter_by(codename=codename).first()
    if not perm:
        perm = Permission(
            codename=codename,
            display_name=codename,
            resource=parts[0],
            perm_type=parts[1] if len(parts) > 1 else 'api',
            action='.'.join(parts[2:]) if len(parts) > 2 else 'action',
        )
        db.session.add(perm)
        db.session.flush()
    if not RolePermission.query.filter_by(role_id=role.id, permission_id=perm.id).first():
        db.session.add(RolePermission(role_id=role.id, permission_id=perm.id))
        db.session.flush()


@pytest.fixture
def permissions(app, roles):
    """
    Mapea permisos del módulo a roles correctos.

    `social_service` sigue recibiendo los permisos AQUÍ aunque la semilla real
    ya no se los dé: así los tests pueden comprobar la capa de alcance por sí
    sola (tiene el permiso y aun así se le niega por no administrar ningún
    programa), en vez de que el 403 lo produzca `permission_required`.
    """
    for code in ('student_bulk.page.view',
                 'student_bulk.api.create_one',
                 'student_bulk.api.csv_preview',
                 'student_bulk.api.csv_execute'):
        _grant(roles['postgraduate_admin'], code)
        _grant(roles['program_admin'], code)
        _grant(roles['social_service'], code)

    # El jefe de posgrado es global porque tiene este permiso: es la definición
    # que usa User.get_accessible_program_ids(). Sin él, el fixture
    # postgrad_admin no tendría alcance sobre ningún programa.
    _grant(roles['postgraduate_admin'], 'academic_periods.api.create')
    return True


@pytest.fixture
def periods(app):
    """
    Crea 4 periodos cronológicos:
      P1=20221, P2=20223, P3=20231, P4=20233 (activo)
    """
    seq = [
        ('20221', '2022-01-15', '2022-06-15', False),
        ('20223', '2022-08-15', '2022-12-15', False),
        ('20231', '2023-01-15', '2023-06-15', False),
        ('20233', '2023-08-15', '2023-12-15', True),
    ]
    out = {}
    for code, sd, ed, active in seq:
        ap = AcademicPeriod(
            code=code,
            name=f'Period {code}',
            start_date=date.fromisoformat(sd),
            end_date=date.fromisoformat(ed),
            admission_start_date=date.fromisoformat(sd) - timedelta(days=60),
            admission_end_date=date.fromisoformat(sd) - timedelta(days=10),
            is_active=active,
            status='active' if active else 'completed',
        )
        db.session.add(ap)
        db.session.flush()
        out[code] = ap
    return out


@pytest.fixture
def coordinator(app, roles):
    u = User(
        first_name='Coord', last_name='Test', mother_last_name='',
        username='coord_bulk', password='Test1234!',
        email='coordbulk@test.local', is_internal=True,
        role_id=roles['program_admin'].id, must_change_password=False,
    )
    db.session.add(u)
    db.session.flush()
    return u


@pytest.fixture
def postgrad_admin(app, roles):
    u = User(
        first_name='Postgrad', last_name='Bulk', mother_last_name='',
        username='postgrad_bulk', password='Test1234!',
        email='postgradbulk@test.local', is_internal=True,
        role_id=roles['postgraduate_admin'].id, must_change_password=False,
    )
    db.session.add(u)
    db.session.flush()
    return u


@pytest.fixture
def social_user(app, roles):
    u = User(
        first_name='Social', last_name='Service', mother_last_name='',
        username='social_bulk', password='Test1234!',
        email='socialbulk@test.local', is_internal=True,
        role_id=roles['social_service'].id, must_change_password=False,
    )
    db.session.add(u)
    db.session.flush()
    return u


@pytest.fixture
def applicant_user(app, roles):
    u = User(
        first_name='App', last_name='Test', mother_last_name='',
        username='applicant_bulk', password='Test1234!',
        email='applicantbulk@test.local', is_internal=False,
        role_id=roles['applicant'].id, must_change_password=False,
    )
    db.session.add(u)
    db.session.flush()
    return u


@pytest.fixture
def program(app, coordinator):
    p = Program(
        name='Maestria Test',
        description='Test',
        coordinator_id=coordinator.id,
        slug='maestria-test',
        is_active=True,
        duration_semesters=4,
    )
    db.session.add(p)
    db.session.flush()
    return p


@pytest.fixture
def other_coordinator(app, roles):
    """Coordinador de OTRO programa — el vecino cuyo padrón no debe tocarse."""
    u = User(
        first_name='Otro', last_name='Coord', mother_last_name='',
        username='coord_other', password='Test1234!',
        email='coordother@test.local', is_internal=True,
        role_id=roles['program_admin'].id, must_change_password=False,
    )
    db.session.add(u)
    db.session.flush()
    return u


@pytest.fixture
def other_program(app, other_coordinator):
    p = Program(
        name='Doctorado Ajeno',
        description='Test',
        coordinator_id=other_coordinator.id,
        slug='doctorado-ajeno',
        is_active=True,
        duration_semesters=6,
    )
    db.session.add(p)
    db.session.flush()
    return p


@pytest.fixture
def valid_payload(program, periods):
    """Payload válido base para tests."""
    return {
        'first_name': 'Pedro',
        'last_name': 'Lopez',
        'mother_last_name': 'Soto',
        'email': 'pedro.lopez@test.local',
        'control_number': 'M22110099',
        'program_slug': program.slug,
        'current_semester': 3,
        'admission_period_code': '20221',  # 1er periodo, 3 sem cubren 20221, 20223, 20231
        'has_conacyt': False,
    }
