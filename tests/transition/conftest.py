# tests/transition/conftest.py
"""
Fixtures compartidos para los tests de la tarea "Pasar Semestre".
Crean roles, permisos, programas, periodos académicos, archives, ventanas,
estudiantes y aspirantes para cubrir las ramas de evaluate_student y la
ejecución masiva.
"""

from datetime import date, datetime, timedelta
from pathlib import Path
import tempfile

import pytest

from app import create_app, db
from app.models.role import Role
from app.models.user import User
from app.models.program import Program
from app.models.academic_period import AcademicPeriod
from app.models.user_program import UserProgram
from app.models.permission import Permission
from app.models.role_permission import RolePermission
from app.models.phase import Phase
from app.models.step import Step
from app.models.archive import Archive
from app.models.program_step import ProgramStep
from app.models.semester_enrollment import SemesterEnrollment
from app.models.document_deadline import DocumentDeadline
from app.models.submission import Submission
from app.models.enrollment_deferral import EnrollmentDeferral


# ---------------------------------------------------------------------------
# App + DB lifecycle
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    """Crea una app Flask con SQLite en memoria. Una nueva por test."""
    upload_dir = Path(tempfile.mkdtemp(prefix='siiap_test_'))
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
        'ALLOWED_DOC_EXT': {'pdf', 'doc', 'docx'},
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


# ---------------------------------------------------------------------------
# Roles + permisos
# ---------------------------------------------------------------------------

@pytest.fixture
def roles(app):
    """Crea los 5 roles del proyecto."""
    out = {}
    for name in ('applicant', 'program_admin', 'postgraduate_admin',
                 'social_service', 'student'):
        r = Role(name=name, description=f'Test role: {name}')
        db.session.add(r)
        out[name] = r
    db.session.flush()
    return out


def _make_perm(codename, role):
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
    Mapea los permisos de la transición a los roles correctos.

    Réplica de `database/DML/permissions/02_role_permissions.sql`: el rol
    postgraduate_admin lleva ahí `academic_periods.api.create` (el marcador de
    alcance global, sin el cual no alcanza ningún programa concreto) y
    `permanence.api.transition_all` (el modo "todos los programas"). Omitirlos
    daba un jefe de posgrado que no existe en producción.
    """
    _make_perm('academic_periods.api.create', roles['postgraduate_admin'])
    _make_perm('permanence.api.advance_bulk', roles['postgraduate_admin'])
    _make_perm('permanence.api.transition_all', roles['postgraduate_admin'])
    _make_perm('permanence.api.upload_my_payment', roles['student'])
    _make_perm('permanence.api.view_my_enrollment', roles['student'])
    _make_perm('permanence.api.list_students', roles['program_admin'])
    _make_perm('permanence.api.confirm_enrollment', roles['program_admin'])
    return True


@pytest.fixture
def coordinator_advance_bulk(app, roles, permissions):
    """
    Concede `permanence.api.advance_bulk` al rol program_admin, SIN
    `transition_all`: el despliegue que delega el avance masivo a las
    coordinaciones. Debe poder correr la transición de su propio programa.
    """
    _make_perm('permanence.api.advance_bulk', roles['program_admin'])
    return True


# ---------------------------------------------------------------------------
# Periodos académicos (4 secuenciales)
# ---------------------------------------------------------------------------

@pytest.fixture
def periods(app):
    """
    Crea 4 periodos académicos en orden cronológico:
      P0 = 20251 (2 atrás del activo actual)
      P1 = 20253 (1 atrás del activo actual)
      P2 = 20263 (activo / fuente de transición)
      P3 = 20271 (destino de transición)
    """
    today = date.today()
    out = {}
    seq = [
        ('20251', '2025-01-15', '2025-06-15', False),
        ('20253', '2025-08-15', '2025-12-15', False),
        ('20263', '2026-08-15', '2026-12-15', True),
        ('20271', '2027-01-15', '2027-06-15', False),
    ]
    for code, sd, ed, active in seq:
        ap = AcademicPeriod(
            code=code,
            name=f'Period {code}',
            start_date=date.fromisoformat(sd),
            end_date=date.fromisoformat(ed),
            admission_start_date=date.fromisoformat(sd) - timedelta(days=60),
            admission_end_date=date.fromisoformat(sd) - timedelta(days=10),
            is_active=active,
            status='active' if active else ('completed' if not active and code < '20263' else 'upcoming'),
        )
        db.session.add(ap)
        db.session.flush()
        out[code] = ap
    return out


# ---------------------------------------------------------------------------
# Programa + fases + archives + ventanas
# ---------------------------------------------------------------------------

@pytest.fixture
def coordinator(app, roles):
    u = User(
        first_name='Coord', last_name='Test', mother_last_name='',
        username='coord_test', password='Test1234!',
        email='coord@test.local', is_internal=True,
        role_id=roles['program_admin'].id, must_change_password=False,
    )
    db.session.add(u)
    db.session.flush()
    return u


@pytest.fixture
def postgrad_admin(app, roles):
    u = User(
        first_name='Postgrad', last_name='Admin', mother_last_name='',
        username='postgrad_test', password='Test1234!',
        email='postgrad@test.local', is_internal=True,
        role_id=roles['postgraduate_admin'].id, must_change_password=False,
    )
    db.session.add(u)
    db.session.flush()
    return u


@pytest.fixture
def program(app, coordinator):
    p = Program(
        name='Test MII', description='Test', coordinator_id=coordinator.id,
        slug='test-mii', is_active=True, duration_semesters=4,
    )
    db.session.add(p)
    db.session.flush()
    return p


@pytest.fixture
def foreign_program(app, postgrad_admin):
    """Programa ajeno: lo coordina el jefe de posgrado, no `coordinator`."""
    p = Program(
        name='Test MAI', description='Test', coordinator_id=postgrad_admin.id,
        slug='test-mai', is_active=True, duration_semesters=4,
    )
    db.session.add(p)
    db.session.flush()
    return p


@pytest.fixture
def archives(app, program):
    """
    Crea estructura mínima:
    - Phase permanence
    - Step 9 (regular) con archive 'Boleta Inscripcion'
    - Step 12 (CONACyT) con archive 'Formato Desempeno'
    - ProgramStep para cada step ligado al programa
    """
    phase = Phase(name='permanence', description='Permanencia')
    db.session.add(phase)
    db.session.flush()

    # El service identifica CONACyT por step_id == 12. Creamos 11 steps dummy
    # para forzar que step12 reciba id=12 al hacer flush autoincremental.
    for i in range(1, 12):
        s = Step(name=f'dummy{i}', description='dummy', phase_id=phase.id)
        db.session.add(s)
    db.session.flush()

    step12 = Step(name='Evaluacion Desempeno', description='Eval', phase_id=phase.id)
    db.session.add(step12)
    db.session.flush()
    assert step12.id == 12, f'Expected step_id 12, got {step12.id}'

    # step9: el primero de los dummies (step_id=1). Lo usaremos como step regular.
    step9 = Step.query.filter_by(name='dummy1').first()

    arch_boleta = Archive(
        name='Boleta de Inscripcion', description='boleta',
        file_path='', step_id=step9.id,
        is_downloadable=False, is_uploadable=True,
    )
    arch_conacyt = Archive(
        name='Formato Desempeno', description='conacyt',
        file_path='', step_id=step12.id,
        is_downloadable=False, is_uploadable=True,
    )
    db.session.add_all([arch_boleta, arch_conacyt])
    db.session.flush()
    arch_boleta.is_active = True
    arch_conacyt.is_active = True
    db.session.flush()

    ps9 = ProgramStep(program_id=program.id, step_id=step9.id, sequence=1)
    ps12 = ProgramStep(program_id=program.id, step_id=step12.id, sequence=2)
    db.session.add_all([ps9, ps12])
    db.session.flush()

    return {
        'phase': phase, 'step9': step9, 'step12': step12,
        'boleta': arch_boleta, 'conacyt': arch_conacyt,
        'ps9': ps9, 'ps12': ps12,
    }


@pytest.fixture
def deadlines_source(app, program, archives, periods):
    """
    Ventana 'Boleta Inscripcion' en el periodo origen (20263), CERRADA antes de
    ahora — bloquea avance si no se entregó.
    """
    now = datetime.now()
    dl = DocumentDeadline(
        archive_id=archives['boleta'].id,
        program_id=program.id,
        academic_period_id=periods['20263'].id,
        sequence=1,
        label='Boleta Inscripcion',
        opens_at=now - timedelta(days=60),
        closes_at=now - timedelta(days=1),  # ya cerrada vs now real
        is_open=False,
    )
    db.session.add(dl)
    db.session.flush()
    return dl


@pytest.fixture
def conacyt_deadline_source(app, program, archives, periods):
    """Ventana CONACyT mensual cerrada en periodo origen."""
    now = datetime.now()
    dl = DocumentDeadline(
        archive_id=archives['conacyt'].id,
        program_id=program.id,
        academic_period_id=periods['20263'].id,
        sequence=8,
        label='Formato CONACyT - Agosto',
        opens_at=now - timedelta(days=60),
        closes_at=now - timedelta(days=2),
        is_open=False,
    )
    db.session.add(dl)
    db.session.flush()
    return dl


# ---------------------------------------------------------------------------
# Helpers para crear estudiantes con SE en distintos estados
# ---------------------------------------------------------------------------

def _make_student(role, program_obj, period_active, suffix, semester=1,
                  se_status='active', enrollment_confirmed=True,
                  has_conacyt=False):
    """
    Crea un estudiante con UserProgram enrolled + SE en el periodo activo.
    """
    u = User(
        first_name='Stud', last_name=f'Test{suffix}', mother_last_name='',
        username=f'student_{suffix}', password='Test1234!',
        email=f'student{suffix}@test.local', is_internal=True,
        role_id=role.id, must_change_password=False,
    )
    db.session.add(u)
    db.session.flush()
    u.control_number = f'TEST{suffix}'
    db.session.flush()

    up = UserProgram(
        user_id=u.id, program_id=program_obj.id,
        admission_status='enrolled',
        admission_period_id=period_active.id,
        current_semester=semester,
        has_conacyt_scholarship=has_conacyt,
    )
    db.session.add(up)
    db.session.flush()

    se = SemesterEnrollment(
        user_program_id=up.id,
        academic_period_id=period_active.id,
        semester_number=semester,
        status=se_status,
        enrollment_confirmed=enrollment_confirmed,
    )
    db.session.add(se)
    db.session.flush()
    return u, up, se


@pytest.fixture
def student_factory(app, roles, program, periods):
    """Factory: ver firma _make_student."""
    def _factory(**kw):
        suffix = kw.pop('suffix', 'A')
        period = kw.pop('period', periods['20263'])
        return _make_student(roles['student'], program, period, suffix, **kw)
    return _factory


@pytest.fixture
def make_submission():
    """Helper para crear Submission con document_deadline_id (no aceptado en __init__)."""
    from datetime import datetime
    def _build(user_id, archive_id, program_step_id, deadline_id=None,
               status='approved', file_path='ok.pdf', semester=1):
        sub = Submission(
            file_path=file_path, status=status,
            user_id=user_id, archive_id=archive_id,
            program_step_id=program_step_id, semester=semester,
        )
        db.session.add(sub)
        db.session.flush()
        if deadline_id is not None:
            sub.document_deadline_id = deadline_id
            db.session.flush()
        return sub
    return _build
