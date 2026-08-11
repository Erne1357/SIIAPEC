# tests/program_transfer/conftest.py
"""
Fixtures del cambio de programa por autoservicio.

El escenario mínimo que hace falta para probar las reglas:
  - dos programas (origen y destino) con un paso de ADMISIÓN cada uno y un
    archive con el mismo nombre, para que el mapeo los considere equivalentes;
  - un paso de PERMANENCIA en el programa origen con su propio archive, que es
    justamente lo que la barrida no debe tocar;
  - un aspirante inscrito en el origen;
  - un periodo con la convocatoria abierta hoy.
"""

from datetime import date, timedelta
from pathlib import Path
import tempfile

import pytest

from app import create_app, db
from app.models.role import Role
from app.models.user import User
from app.models.program import Program
from app.models.program_step import ProgramStep
from app.models.phase import Phase
from app.models.step import Step
from app.models.archive import Archive
from app.models.submission import Submission
from app.models.user_program import UserProgram
from app.models.academic_period import AcademicPeriod


@pytest.fixture
def app():
    upload_dir = Path(tempfile.mkdtemp(prefix='siiap_test_transfer_'))
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
        'ALLOWED_DOC_EXT': {'pdf'},
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


@pytest.fixture
def phases(app):
    """Fase 1 = admisión (ADMISSION_PHASE_ID), fase 2 = permanencia."""
    admission = Phase(name='admission', description='Admisión')
    permanence = Phase(name='permanence', description='Permanencia')
    db.session.add_all([admission, permanence])
    db.session.flush()
    return {'admission': admission, 'permanence': permanence}


@pytest.fixture
def coordinator(app, roles):
    u = User(
        first_name='Coord', last_name='Transfer', mother_last_name='',
        username='coord_transfer', password='Test1234!',
        email='coordtransfer@test.local', is_internal=True,
        role_id=roles['program_admin'].id, must_change_password=False,
    )
    db.session.add(u)
    db.session.flush()
    return u


@pytest.fixture
def applicant(app, roles):
    u = User(
        first_name='Aspi', last_name='Rante', mother_last_name='Test',
        username='aspirante_transfer', password='Test1234!',
        email='aspirante@test.local', is_internal=False,
        role_id=roles['applicant'].id, must_change_password=False,
    )
    db.session.add(u)
    db.session.flush()
    return u


def _make_program(name, slug, coordinator_id):
    p = Program(
        name=name, description='Test', coordinator_id=coordinator_id,
        slug=slug, is_active=True, duration_semesters=4,
    )
    db.session.add(p)
    db.session.flush()
    return p


@pytest.fixture
def from_program(app, coordinator):
    return _make_program('Maestria Origen', 'maestria-origen', coordinator.id)


@pytest.fixture
def to_program(app, coordinator):
    return _make_program('Maestria Destino', 'maestria-destino', coordinator.id)


@pytest.fixture
def structure(app, phases, from_program, to_program):
    """
    Pasos y archives de ambos programas.

    'Acta de nacimiento' existe en el paso de admisión de los dos programas con
    el mismo nombre → el mapeo los considera equivalentes.
    'Reinscripción' vive en un paso de PERMANENCIA del origen → no está en
    ningún mapeo y por eso la barrida sin filtro de fase lo borraba.
    """
    adm_step = Step(name='Documentos', description='', phase_id=phases['admission'].id)
    perm_step = Step(name='Permanencia', description='', phase_id=phases['permanence'].id)
    db.session.add_all([adm_step, perm_step])
    db.session.flush()

    ps_from_adm = ProgramStep(sequence=1, program_id=from_program.id, step_id=adm_step.id)
    ps_from_perm = ProgramStep(sequence=2, program_id=from_program.id, step_id=perm_step.id)
    ps_to_adm = ProgramStep(sequence=1, program_id=to_program.id, step_id=adm_step.id)
    db.session.add_all([ps_from_adm, ps_from_perm, ps_to_adm])
    db.session.flush()

    birth = Archive(name='Acta de nacimiento', description='', file_path=None,
                    step_id=adm_step.id)
    reenroll = Archive(name='Reinscripción', description='', file_path=None,
                       step_id=perm_step.id)
    db.session.add_all([birth, reenroll])
    db.session.flush()

    return {
        'admission_step': adm_step,
        'permanence_step': perm_step,
        'ps_from_admission': ps_from_adm,
        'ps_from_permanence': ps_from_perm,
        'ps_to_admission': ps_to_adm,
        'admission_archive': birth,
        'permanence_archive': reenroll,
    }


@pytest.fixture
def open_period(app):
    """Periodo con la convocatoria de admisión ABIERTA hoy."""
    today = date.today()
    ap = AcademicPeriod(
        code='20261', name='Enero-Junio 2026',
        start_date=today + timedelta(days=60),
        end_date=today + timedelta(days=200),
        admission_start_date=today - timedelta(days=10),
        admission_end_date=today + timedelta(days=10),
        is_active=True, status='active',
    )
    db.session.add(ap)
    db.session.flush()
    return ap


@pytest.fixture
def closed_period(app):
    """Periodo con la convocatoria CERRADA (terminó ayer)."""
    today = date.today()
    ap = AcademicPeriod(
        code='20253', name='Agosto-Diciembre 2025',
        start_date=today + timedelta(days=30),
        end_date=today + timedelta(days=180),
        admission_start_date=today - timedelta(days=40),
        admission_end_date=today - timedelta(days=1),
        is_active=True, status='admission_closed',
    )
    db.session.add(ap)
    db.session.flush()
    return ap


@pytest.fixture
def enrollment(app, applicant, from_program):
    """Inscripción del aspirante en el programa de ORIGEN, en curso."""
    up = UserProgram(
        user_id=applicant.id,
        program_id=from_program.id,
        admission_status='in_progress',
    )
    db.session.add(up)
    db.session.flush()
    return up


@pytest.fixture
def submissions(app, applicant, structure):
    """Un documento de admisión y uno de permanencia del mismo aspirante."""
    admission_sub = Submission(
        file_path='acta.pdf', status='approved', user_id=applicant.id,
        archive_id=structure['admission_archive'].id,
        program_step_id=structure['ps_from_admission'].id, semester=None,
    )
    permanence_sub = Submission(
        file_path='reinscripcion.pdf', status='approved', user_id=applicant.id,
        archive_id=structure['permanence_archive'].id,
        program_step_id=structure['ps_from_permanence'].id, semester=1,
    )
    db.session.add_all([admission_sub, permanence_sub])
    db.session.flush()
    return {'admission': admission_sub, 'permanence': permanence_sub}
