# tests/bulk_import/test_create_individual.py
"""Tests de create_student_individual + _backfill_history."""

import pytest
from app import db
from app.models.user import User
from app.models.user_program import UserProgram
from app.models.semester_enrollment import SemesterEnrollment
from app.services import student_bulk_service as svc


def test_create_individual_creates_user_up_and_ses(app, periods, program,
                                                   postgrad_admin, valid_payload):
    """Happy path: User + UP + N SE creados, contadores correctos."""
    result = svc.create_student_individual(valid_payload, created_by_id=postgrad_admin.id)

    assert result['user_id'] is not None
    assert result['user_program_id'] is not None
    assert result['sems_created'] == 3

    # `user_id` del resultado es el identificador PÚBLICO (UUID).
    user = User.by_uuid(result['user_id'])
    assert user.email == 'pedro.lopez@test.local'
    assert user.control_number == 'M22110099'
    assert user.username == 'M22110099'
    assert user.must_change_password is True
    assert user.is_active is True

    up = UserProgram.query.get(result['user_program_id'])
    assert up.admission_status == 'enrolled'
    assert up.current_semester == 3
    assert up.has_conacyt_scholarship is False


def test_backfill_creates_correct_periods(app, periods, program,
                                          postgrad_admin, valid_payload):
    """SE 1..N en periodos cronológicos consecutivos desde admission_period."""
    result = svc.create_student_individual(valid_payload, created_by_id=postgrad_admin.id)
    up_id = result['user_program_id']

    ses = (
        SemesterEnrollment.query
        .filter_by(user_program_id=up_id)
        .order_by(SemesterEnrollment.semester_number.asc())
        .all()
    )
    assert len(ses) == 3

    # current_semester=3, admission=20221 → sem1=20221, sem2=20223, sem3=20231
    assert ses[0].semester_number == 1
    assert ses[0].academic_period.code == '20221'
    assert ses[0].status == 'completed'

    assert ses[1].semester_number == 2
    assert ses[1].academic_period.code == '20223'
    assert ses[1].status == 'completed'

    assert ses[2].semester_number == 3
    assert ses[2].academic_period.code == '20231'
    assert ses[2].status == 'active'

    for se in ses:
        assert se.enrollment_confirmed is True
        assert se.confirmed_by == postgrad_admin.id


def test_create_with_conacyt_flag(app, periods, program,
                                   postgrad_admin, valid_payload):
    valid_payload['has_conacyt'] = True
    result = svc.create_student_individual(valid_payload, created_by_id=postgrad_admin.id)
    up = UserProgram.query.get(result['user_program_id'])
    assert up.has_conacyt_scholarship is True


def test_create_invalid_payload_raises_validation_error(app, periods, program,
                                                        postgrad_admin, valid_payload):
    valid_payload['email'] = ''  # inválido
    with pytest.raises(svc.ValidationError):
        svc.create_student_individual(valid_payload, created_by_id=postgrad_admin.id)


def test_create_rolls_back_on_duplicate_email(app, periods, program, roles,
                                              postgrad_admin, valid_payload):
    # Pre-crear user con mismo email
    existing = User(
        first_name='X', last_name='Y', mother_last_name='',
        username='other', password='pw',
        email=valid_payload['email'], is_internal=False,
        role_id=roles['student'].id, must_change_password=False,
    )
    db.session.add(existing)
    db.session.commit()
    initial_count = User.query.count()

    with pytest.raises(svc.ValidationError):
        svc.create_student_individual(valid_payload, created_by_id=postgrad_admin.id)

    # No se creó ningún user adicional
    assert User.query.count() == initial_count


def test_create_logs_history(app, periods, program, postgrad_admin, valid_payload):
    from app.models.user_history import UserHistory
    result = svc.create_student_individual(valid_payload, created_by_id=postgrad_admin.id)

    log = UserHistory.query.filter_by(
        user_id=User.by_uuid(result['user_id']).id,
        action='enrolled_via_bulk_import',
    ).first()
    assert log is not None
    assert log.admin_id == postgrad_admin.id
