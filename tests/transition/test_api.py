# tests/transition/test_api.py
"""
Tests de los endpoints de la transición y del self-pago del estudiante.
"""

from io import BytesIO
import pytest

from app import db
from app.models.user import User
from app.models.user_program import UserProgram
from app.models.semester_enrollment import SemesterEnrollment


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
        token = resp.get_json().get('data', {}).get('csrf_token', '')
    except Exception:
        token = ''
    return token


def _csrf(token):
    return {'X-CSRFToken': token}


# ---------------------------------------------------------------------------
# /transition/preview
# ---------------------------------------------------------------------------

def test_preview_endpoint_requires_advance_bulk_perm(app, client, periods, permissions, coordinator):
    """Coordinador (no postgrad) NO puede ver preview."""
    _login(client, coordinator)
    resp = client.get(
        f'/api/v1/permanence/transition/preview'
        f'?source_period_id={periods["20263"].id}&target_period_id={periods["20271"].id}'
    )
    assert resp.status_code == 403


def test_preview_endpoint_postgrad_succeeds(app, client, periods, permissions, postgrad_admin, program):
    """postgraduate_admin recibe shape correcto del preview."""
    _login(client, postgrad_admin)
    resp = client.get(
        f'/api/v1/permanence/transition/preview'
        f'?program_id={program.id}'
        f'&source_period_id={periods["20263"].id}'
        f'&target_period_id={periods["20271"].id}'
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert 'data' in body
    data = body['data']
    for key in ('will_advance', 'will_block', 'on_leave',
                'admission_migrate', 'admission_expire',
                'admission_to_cleanup', 'deferred_reactivate', 'stats'):
        assert key in data


def test_preview_endpoint_global(app, client, periods, permissions, postgrad_admin):
    """Sin program_id → preview_global. Devuelve 'programs' adicional."""
    _login(client, postgrad_admin)
    resp = client.get(
        f'/api/v1/permanence/transition/preview'
        f'?source_period_id={periods["20263"].id}'
        f'&target_period_id={periods["20271"].id}'
    )
    assert resp.status_code == 200
    data = resp.get_json()['data']
    assert 'programs' in data


# ---------------------------------------------------------------------------
# Contrato de permisos de la transición
#
#   permanence.api.advance_bulk    → puedo correr una transición
#   permanence.api.transition_all  → puedo correrla sobre TODOS los programas
#
# Ninguna de las dos cosas se deduce de `academic_periods.api.create`: quien
# tiene `advance_bulk` delegado corre el cambio de semestre de su programa sin
# necesitar un permiso de otro recurso.
# ---------------------------------------------------------------------------

def test_preview_own_program_needs_only_advance_bulk(app, client, periods,
                                                     coordinator_advance_bulk,
                                                     coordinator, program):
    """`advance_bulk` + alcance sobre el programa basta: no hace falta nada más."""
    _login(client, coordinator)
    resp = client.get(
        f'/api/v1/permanence/transition/preview'
        f'?program_id={program.id}'
        f'&source_period_id={periods["20263"].id}'
        f'&target_period_id={periods["20271"].id}'
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)[:300]


def test_preview_global_requires_transition_all(app, client, periods,
                                                coordinator_advance_bulk,
                                                coordinator, program):
    """
    Sin `transition_all`, el modo global se deniega — y el mensaje nombra el
    permiso que falta, para que el 403 sea diagnosticable desde el catálogo.
    """
    _login(client, coordinator)
    resp = client.get(
        f'/api/v1/permanence/transition/preview'
        f'?source_period_id={periods["20263"].id}'
        f'&target_period_id={periods["20271"].id}'
    )
    assert resp.status_code == 403
    assert 'permanence.api.transition_all' in resp.get_json()['error']['message']


def test_preview_foreign_program_is_denied(app, client, periods,
                                           coordinator_advance_bulk,
                                           coordinator, program, foreign_program):
    """`advance_bulk` es capacidad, no alcance: el programa ajeno sigue cerrado."""
    _login(client, coordinator)
    resp = client.get(
        f'/api/v1/permanence/transition/preview'
        f'?program_id={foreign_program.id}'
        f'&source_period_id={periods["20263"].id}'
        f'&target_period_id={periods["20271"].id}'
    )
    assert resp.status_code == 403


def test_execute_own_program_needs_only_advance_bulk(app, client, periods,
                                                     coordinator_advance_bulk,
                                                     coordinator, program):
    token = _login(client, coordinator)
    resp = client.post(
        '/api/v1/permanence/transition/execute',
        json={
            'program_id': program.id,
            'source_period_id': periods['20263'].id,
            'target_period_id': periods['20271'].id,
        },
        headers=_csrf(token),
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)[:300]


def test_execute_global_requires_transition_all(app, client, periods,
                                                coordinator_advance_bulk,
                                                coordinator, program):
    token = _login(client, coordinator)
    resp = client.post(
        '/api/v1/permanence/transition/execute',
        json={
            'source_period_id': periods['20263'].id,
            'target_period_id': periods['20271'].id,
        },
        headers=_csrf(token),
    )
    assert resp.status_code == 403
    assert 'permanence.api.transition_all' in resp.get_json()['error']['message']


# ---------------------------------------------------------------------------
# /transition/execute
# ---------------------------------------------------------------------------

def test_execute_endpoint_requires_advance_bulk_perm(app, client, periods, permissions, coordinator):
    token = _login(client, coordinator)
    resp = client.post(
        '/api/v1/permanence/transition/execute',
        json={
            'source_period_id': periods['20263'].id,
            'target_period_id': periods['20271'].id,
        },
        headers=_csrf(token),
    )
    assert resp.status_code == 403


def test_execute_endpoint_runs(app, client, periods, permissions, postgrad_admin, program):
    """Endpoint ejecuta y devuelve stats sin errores en caso vacío."""
    token = _login(client, postgrad_admin)
    resp = client.post(
        '/api/v1/permanence/transition/execute',
        json={
            'program_id': program.id,
            'source_period_id': periods['20263'].id,
            'target_period_id': periods['20271'].id,
        },
        headers=_csrf(token),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body.get('error') is None
    stats = body['data']
    for key in ('advanced', 'blocked', 'on_leave',
                'admission_migrated', 'admission_expired',
                'deferred_reactivated'):
        assert key in stats


def test_execute_endpoint_missing_periods_returns_400(app, client, permissions, postgrad_admin):
    token = _login(client, postgrad_admin)
    resp = client.post('/api/v1/permanence/transition/execute', json={}, headers=_csrf(token))
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# /my-enrollment GET
# ---------------------------------------------------------------------------

def test_my_enrollment_returns_active_period_se(app, client, periods, permissions,
                                                program, student_factory):
    """Estudiante autenticado obtiene su SE del periodo activo + program info."""
    user, up, se = student_factory(suffix='myenr')
    db.session.commit()
    _login(client, user)

    resp = client.get('/api/v1/permanence/my-enrollment')
    assert resp.status_code == 200
    data = resp.get_json()['data']
    assert data is not None
    # Estructura observada del endpoint
    assert 'program' in data
    assert 'payment_reference' in data
    # Datos de la inscripción están bajo current_enrollment o equivalente
    assert ('current_enrollment' in data or 'enrollment' in data
            or 'semester_number' in data or 'enrollment_confirmed' in data)


def test_my_enrollment_requires_view_perm(app, client, periods, permissions, postgrad_admin):
    """Sin permiso `view_my_enrollment`, devuelve 403."""
    _login(client, postgrad_admin)  # postgrad no tiene view_my_enrollment
    resp = client.get('/api/v1/permanence/my-enrollment')
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# /my-enrollment/payment-proof POST
# ---------------------------------------------------------------------------

def test_upload_payment_proof_success(app, client, periods, permissions, program, student_factory):
    """Estudiante sube PDF → SE.payment_proof_path se setea, no marca confirmed."""
    user, up, se = student_factory(suffix='paypdf', enrollment_confirmed=False)
    db.session.commit()
    token = _login(client, user)

    pdf_bytes = b'%PDF-1.4\n%test\n'
    resp = client.post(
        '/api/v1/permanence/my-enrollment/payment-proof',
        data={'payment_proof': (BytesIO(pdf_bytes), 'comprobante.pdf')},
        content_type='multipart/form-data',
        headers=_csrf(token),
    )
    assert resp.status_code == 200, f'body={resp.get_data(as_text=True)[:500]}'
    db.session.expire_all()
    se_re = SemesterEnrollment.query.get(se.id)
    assert se_re.payment_proof_path is not None
    # NO se marca enrollment_confirmed automáticamente
    assert se_re.enrollment_confirmed is False


def test_upload_payment_proof_rejects_non_pdf(app, client, periods, permissions,
                                              program, student_factory):
    """Archivo no-PDF rechazado con 400."""
    user, up, se = student_factory(suffix='paydoc', enrollment_confirmed=False)
    db.session.commit()
    token = _login(client, user)

    resp = client.post(
        '/api/v1/permanence/my-enrollment/payment-proof',
        data={'payment_proof': (BytesIO(b'plain text'), 'archivo.txt')},
        content_type='multipart/form-data',
        headers=_csrf(token),
    )
    assert resp.status_code == 400


def test_upload_payment_proof_requires_perm(app, client, periods, permissions, coordinator):
    token = _login(client, coordinator)
    resp = client.post(
        '/api/v1/permanence/my-enrollment/payment-proof',
        data={'payment_proof': (BytesIO(b'%PDF-1.4'), 'a.pdf')},
        content_type='multipart/form-data',
        headers=_csrf(token),
    )
    assert resp.status_code == 403
