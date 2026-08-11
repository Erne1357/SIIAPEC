# tests/program_transfer/test_transfer_rules.py
"""
Reglas del cambio de programa por autoservicio.

Fija lo que /execute dejó de permitir: mudarse con un dictamen ya tomado,
mudarse desde un programa ajeno, mudarse con la convocatoria cerrada, y —el
daño colateral más caro— perder los documentos de permanencia en la barrida.
"""

import pytest

from app import db
from app.models.submission import Submission
from app.models.program_change_request import (
    ProgramChangeRequest,
    COMPLETED_STATUSES,
    EXECUTED,
    VALID_STATUSES,
)
from app.services.program_changes_service import ProgramChangesService
from app.services.program_transfer_service import (
    ProgramTransferService,
    ProgramTransferNotFound,
    ProgramTransferNotAllowed,
)


# ─── Reglas de validación ───────────────────────────────────────────────────

def test_in_progress_applicant_passes(app, applicant, from_program, to_program,
                                      enrollment, open_period):
    up = ProgramTransferService.validate_transfer(
        applicant.id, from_program.id, to_program.id
    )
    assert up.id == enrollment.id


@pytest.mark.parametrize('status', [
    'interview_completed', 'deliberation', 'accepted',
    'rejected', 'deferred', 'enrolled', 'expired',
])
def test_non_transferable_status_is_denied(app, applicant, from_program, to_program,
                                           enrollment, open_period, status):
    """Sólo 'in_progress' se transfiere solo; todo lo demás necesita coordinador."""
    enrollment.admission_status = status
    db.session.flush()

    with pytest.raises(ProgramTransferNotAllowed):
        ProgramTransferService.validate_transfer(
            applicant.id, from_program.id, to_program.id
        )


def test_applicant_without_enrollment_in_origin_is_not_found(app, applicant,
                                                             from_program, to_program,
                                                             open_period):
    """Sin inscripción en el origen no hay nada que mover: 'no existe', no 403."""
    with pytest.raises(ProgramTransferNotFound):
        ProgramTransferService.validate_transfer(
            applicant.id, from_program.id, to_program.id
        )


def test_closed_admission_window_is_denied(app, applicant, from_program, to_program,
                                           enrollment, closed_period):
    with pytest.raises(ProgramTransferNotAllowed):
        ProgramTransferService.validate_transfer(
            applicant.id, from_program.id, to_program.id
        )


def test_same_program_is_denied(app, applicant, from_program, enrollment, open_period):
    with pytest.raises(ProgramTransferNotAllowed):
        ProgramTransferService.validate_transfer(
            applicant.id, from_program.id, from_program.id
        )


def test_inactive_destination_is_not_found(app, applicant, from_program, to_program,
                                           enrollment, open_period):
    to_program.is_active = False
    db.session.flush()

    with pytest.raises(ProgramTransferNotFound):
        ProgramTransferService.validate_transfer(
            applicant.id, from_program.id, to_program.id
        )


# ─── Barrida de documentos acotada a la fase de admisión ────────────────────

def test_execute_keeps_permanence_documents(app, applicant, from_program, to_program,
                                            enrollment, structure, submissions,
                                            open_period):
    """
    El mapeo sólo cubre la fase de admisión, así que la barrida también.
    El documento de permanencia debe seguir existiendo después del cambio.
    """
    result = ProgramTransferService.execute_transfer(
        applicant.id, from_program.id, to_program.id
    )

    assert result['success'] is True
    assert result['deleted_documents'] == 0

    surviving = db.session.get(Submission, submissions['permanence'].id)
    assert surviving is not None
    assert surviving.program_step_id == structure['ps_from_permanence'].id


def test_execute_moves_admission_documents(app, applicant, from_program, to_program,
                                           enrollment, structure, submissions,
                                           open_period):
    result = ProgramTransferService.execute_transfer(
        applicant.id, from_program.id, to_program.id
    )

    assert result['updated_documents'] == 1
    moved = db.session.get(Submission, submissions['admission'].id)
    assert moved.program_step_id == structure['ps_to_admission'].id


def test_execute_resets_the_admission_decision(app, applicant, from_program,
                                               to_program, enrollment, open_period):
    enrollment.decision_notes = 'Aceptado por el comité de origen'
    db.session.flush()

    ProgramTransferService.execute_transfer(
        applicant.id, from_program.id, to_program.id
    )

    assert enrollment.program_id == to_program.id
    assert enrollment.admission_status == 'in_progress'
    assert enrollment.decision_notes is None
    assert enrollment.decision_by is None


def test_execute_denies_accepted_applicant_before_touching_anything(
        app, applicant, from_program, to_program, enrollment, submissions, open_period):
    enrollment.admission_status = 'accepted'
    db.session.flush()

    with pytest.raises(ProgramTransferNotAllowed):
        ProgramTransferService.execute_transfer(
            applicant.id, from_program.id, to_program.id
        )

    assert enrollment.program_id == from_program.id
    assert db.session.get(Submission, submissions['admission'].id) is not None
    assert db.session.get(Submission, submissions['permanence'].id) is not None


# ─── Endpoint HTTP ──────────────────────────────────────────────────────────

def _login(client, user, password='Test1234!'):
    resp = client.post('/api/v1/auth/login',
                       json={'username': user.username, 'password': password})
    try:
        from flask import g as _g
        if hasattr(_g, '_login_user'):
            del _g._login_user
    except RuntimeError:
        pass
    try:
        return resp.get_json().get('data', {}).get('csrf_token', '')
    except Exception:
        return ''


def test_execute_endpoint_accepted_applicant_409(app, client, applicant, from_program,
                                                 to_program, enrollment, open_period):
    enrollment.admission_status = 'accepted'
    db.session.commit()

    token = _login(client, applicant)
    resp = client.post(
        '/api/v1/program-changes/execute',
        json={'from_program_id': from_program.id, 'to_program_id': to_program.id},
        headers={'X-CSRFToken': token},
    )

    assert resp.status_code == 409
    body = resp.get_json()
    assert body['ok'] is False
    assert 'coordinador' in body['error']
    # Ni siquiera se registró una solicitud: la denegación va antes.
    assert ProgramChangeRequest.query.count() == 0


def test_execute_endpoint_foreign_program_404(app, client, applicant, from_program,
                                              to_program, open_period):
    """Sin inscripción en el origen: 404, no 403 — no confirmamos que exista."""
    token = _login(client, applicant)
    resp = client.post(
        '/api/v1/program-changes/execute',
        json={'from_program_id': from_program.id, 'to_program_id': to_program.id},
        headers={'X-CSRFToken': token},
    )

    assert resp.status_code == 404
    assert resp.get_json()['ok'] is False


def test_execute_endpoint_does_not_self_approve(app, client, applicant, from_program,
                                                to_program, enrollment, structure,
                                                open_period):
    token = _login(client, applicant)
    resp = client.post(
        '/api/v1/program-changes/execute',
        json={'from_program_id': from_program.id, 'to_program_id': to_program.id},
        headers={'X-CSRFToken': token},
    )

    assert resp.status_code == 200
    req = ProgramChangeRequest.query.one()
    assert req.status == EXECUTED
    assert req.decided_by is None
    # 'executed' es parte de la enumeración documentada y cuenta como traslado
    # consumado: un reporte que sólo mire 'approved' se pierde el autoservicio.
    assert EXECUTED in VALID_STATUSES
    assert EXECUTED in COMPLETED_STATUSES


# ─── Enumeración de estados ─────────────────────────────────────────────────

def test_unknown_status_is_rejected_by_the_model(app, applicant, from_program,
                                                 to_program):
    """
    La columna es String(20) sin CHECK: si el modelo no valida, un estado
    inventado se persiste en silencio y los conteos dejan de cuadrar.
    """
    req = ProgramChangesService.create_request(
        applicant_id=applicant.id,
        from_program_id=from_program.id,
        to_program_id=to_program.id,
    )
    with pytest.raises(ValueError):
        req.status = 'aprobado'


def test_decide_request_refuses_executed(app, applicant, coordinator,
                                         from_program, to_program):
    """
    'executed' no es una decisión: lo fija el autoservicio, sin decisor. Un
    coordinador sólo puede aprobar, rechazar o cancelar.
    """
    req = ProgramChangesService.create_request(
        applicant_id=applicant.id,
        from_program_id=from_program.id,
        to_program_id=to_program.id,
    )
    with pytest.raises(ValueError):
        ProgramChangesService.decide_request(
            request_id=req.id, status=EXECUTED, decided_by=coordinator.id
        )
