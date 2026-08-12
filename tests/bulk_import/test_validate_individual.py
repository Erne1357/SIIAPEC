# tests/bulk_import/test_validate_individual.py
"""Tests de validate_individual — todas las ramas de validación."""

import pytest
from app import db
from app.models.user import User
from app.services import student_bulk_service as svc


def test_valid_payload_returns_valid_true(app, periods, program, valid_payload):
    result = svc.validate_individual(valid_payload)
    assert result['valid'] is True
    assert result['errors'] == []
    assert result['normalized']['email'] == 'pedro.lopez@test.local'


def test_missing_first_name(app, periods, program, valid_payload):
    valid_payload['first_name'] = ''
    result = svc.validate_individual(valid_payload)
    assert result['valid'] is False
    assert any('nombre' in e.lower() for e in result['errors'])


def test_missing_last_name(app, periods, program, valid_payload):
    valid_payload['last_name'] = ''
    result = svc.validate_individual(valid_payload)
    assert result['valid'] is False
    assert any('apellido paterno' in e.lower() for e in result['errors'])


def test_missing_email(app, periods, program, valid_payload):
    valid_payload['email'] = ''
    result = svc.validate_individual(valid_payload)
    assert result['valid'] is False
    assert any('correo' in e.lower() for e in result['errors'])


def test_duplicate_email_rejected(app, periods, program, roles, valid_payload):
    existing = User(
        first_name='X', last_name='Y', mother_last_name='',
        username='other_user', password='pw',
        email=valid_payload['email'], is_internal=False,
        role_id=roles['student'].id, must_change_password=False,
    )
    db.session.add(existing)
    db.session.commit()

    result = svc.validate_individual(valid_payload)
    assert result['valid'] is False
    assert any('ya está registrado' in e for e in result['errors'])


def test_duplicate_control_number_rejected(app, periods, program, roles, valid_payload):
    existing = User(
        first_name='X', last_name='Y', mother_last_name='',
        username='other_ctrl', password='pw',
        email='other@test.local', is_internal=False,
        role_id=roles['student'].id, must_change_password=False,
    )
    db.session.add(existing)
    db.session.flush()
    existing.control_number = valid_payload['control_number']
    db.session.commit()

    result = svc.validate_individual(valid_payload)
    assert result['valid'] is False
    assert any('número de control' in e.lower() for e in result['errors'])


def _student_with_control_number(roles, control_number, *, username, email):
    """Crea un usuario ya dueño de ese número de control."""
    u = User(
        first_name='Ya', last_name='Existe', mother_last_name='',
        username=username, password='pw', email=email, is_internal=False,
        role_id=roles['student'].id, must_change_password=False,
    )
    db.session.add(u)
    db.session.flush()
    u.control_number = control_number
    db.session.commit()
    return u


def test_control_number_collision_uses_the_shared_message(app, periods, program,
                                                          roles, valid_payload):
    """
    F18 — el rechazo por colisión no nombra el número ni afirma que exista, y
    usa LITERALMENTE la misma cadena que los otros tres puntos de rechazo
    (`acceptance_service.assign_control_number`,
    `assign_control_number_admin` y `admin/users_api.assign_control_number`).
    Si alguien reescribe uno de los cuatro textos, este test cae.
    """
    from app.services.acceptance_service import CONTROL_NUMBER_REJECTED_MESSAGE

    _student_with_control_number(
        roles, valid_payload['control_number'],
        username='dueno_ctrl', email='dueno@test.local',
    )

    result = svc.validate_individual(
        valid_payload, creator_program_ids={program.id}
    )
    assert result['valid'] is False
    assert CONTROL_NUMBER_REJECTED_MESSAGE in result['errors']
    # Ni el número ni la palabra "registrado": el mensaje anterior confirmaba
    # la existencia del dato que se estaba sondeando.
    assert not any(valid_payload['control_number'] in e for e in result['errors'])


def test_control_number_oracle_closed_for_out_of_scope_row(app, periods, program,
                                                           other_program, roles,
                                                           valid_payload):
    """
    F18 — el oráculo batcheable.

    Un coordinador de A que valida una fila apuntando al programa B debe recibir
    EXACTAMENTE la misma respuesta tanto si el número de control existe como si
    no. El alcance se comprueba antes, así que la consulta global —que es
    institucional por diseño— nunca llega a ejecutarse para esa fila.
    """
    _student_with_control_number(
        roles, 'M22119999',
        username='ajeno_ctrl', email='ajeno@test.local',
    )

    scope = {program.id}          # coordinador de A, y sólo de A
    base = dict(valid_payload, program_slug=other_program.slug)

    hit = svc.validate_individual(
        dict(base, control_number='M22119999'), creator_program_ids=scope
    )
    miss = svc.validate_individual(
        dict(base, control_number='M22110000'), creator_program_ids=scope
    )

    assert hit['valid'] is False and miss['valid'] is False
    assert hit['errors'] == miss['errors']
    assert not any('M22119999' in e for e in hit['errors'])


def test_invalid_program_slug(app, periods, program, valid_payload):
    valid_payload['program_slug'] = 'no-existe'
    result = svc.validate_individual(valid_payload)
    assert result['valid'] is False
    assert any('programa' in e.lower() for e in result['errors'])


def test_inactive_program_rejected(app, periods, program, valid_payload):
    program.is_active = False
    db.session.commit()
    result = svc.validate_individual(valid_payload)
    assert result['valid'] is False
    assert any('no está activo' in e for e in result['errors'])


def test_invalid_admission_period(app, periods, program, valid_payload):
    valid_payload['admission_period_code'] = '99999'
    result = svc.validate_individual(valid_payload)
    assert result['valid'] is False
    assert any('periodo' in e.lower() for e in result['errors'])


def test_current_semester_not_int(app, periods, program, valid_payload):
    valid_payload['current_semester'] = 'abc'
    result = svc.validate_individual(valid_payload)
    assert result['valid'] is False
    assert any('semestre' in e.lower() for e in result['errors'])


def test_current_semester_zero_rejected(app, periods, program, valid_payload):
    valid_payload['current_semester'] = 0
    result = svc.validate_individual(valid_payload)
    assert result['valid'] is False
    assert any('al menos 1' in e for e in result['errors'])


def test_current_semester_above_max_rejected(app, periods, program, valid_payload):
    # max_sem = duration*2 = 8
    valid_payload['current_semester'] = 100
    result = svc.validate_individual(valid_payload)
    assert result['valid'] is False
    assert any('excede el máximo' in e for e in result['errors'])


def test_admission_period_after_active_rejected(app, periods, program, valid_payload):
    # Crear un periodo cronológicamente POSTERIOR al activo (20233)
    from app.models.academic_period import AcademicPeriod
    from datetime import date
    future = AcademicPeriod(
        code='20241', name='Future', start_date=date(2024, 1, 15),
        end_date=date(2024, 6, 15),
        admission_start_date=date(2023, 11, 1),
        admission_end_date=date(2023, 12, 15),
        is_active=False, status='upcoming',
    )
    db.session.add(future)
    db.session.commit()

    valid_payload['admission_period_code'] = '20241'
    valid_payload['current_semester'] = 1
    result = svc.validate_individual(valid_payload)
    assert result['valid'] is False
    assert any('posterior al' in e for e in result['errors'])


def test_insufficient_periods_for_backfill_rejected(app, periods, program, valid_payload):
    # Periodos: 20221, 20223, 20231, 20233 (4 total)
    # Si admission=20231 (idx=2), current_semester=5 → necesita idx 2..6 → 5 periodos
    # Solo hay 4 - 2 = 2 periodos disponibles → falla.
    valid_payload['admission_period_code'] = '20231'
    valid_payload['current_semester'] = 5
    result = svc.validate_individual(valid_payload)
    assert result['valid'] is False
    assert any('No hay suficientes periodos' in e for e in result['errors'])


def test_has_conacyt_string_truthy(app, periods, program, valid_payload):
    valid_payload['has_conacyt'] = 'sí'
    result = svc.validate_individual(valid_payload)
    assert result['normalized']['has_conacyt'] is True


def test_has_conacyt_string_falsy(app, periods, program, valid_payload):
    valid_payload['has_conacyt'] = 'no'
    result = svc.validate_individual(valid_payload)
    assert result['normalized']['has_conacyt'] is False
