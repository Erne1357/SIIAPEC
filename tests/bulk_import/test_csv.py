# tests/bulk_import/test_csv.py
"""Tests de validate_csv + execute_csv + get_csv_template."""

from io import StringIO
import pytest
from app import db
from app.models.user import User
from app.services import student_bulk_service as svc


def _csv_text(rows):
    """Helper para construir un CSV con headers + filas dadas."""
    out = [','.join(svc.CSV_HEADERS)]
    for r in rows:
        out.append(','.join(str(r.get(h, '')) for h in svc.CSV_HEADERS))
    return '\n'.join(out)


def test_csv_template_has_headers_and_example():
    template = svc.get_csv_template()
    lines = template.strip().split('\n')
    assert len(lines) == 2  # header + ejemplo
    assert all(h in lines[0] for h in svc.CSV_HEADERS)


def test_validate_csv_empty_returns_error(app, periods, program):
    result = svc.validate_csv('')
    assert result['summary']['total'] == 0
    assert 'error' in result


def test_validate_csv_missing_headers(app, periods, program):
    bad_csv = 'first_name,last_name\nJuan,Lopez'
    result = svc.validate_csv(bad_csv)
    assert 'error' in result
    assert 'faltantes' in result['error'].lower()


def test_validate_csv_all_valid(app, periods, program):
    rows = [
        {
            'first_name': 'Ana', 'last_name': 'Soto', 'mother_last_name': 'M',
            'email': 'ana@test.local', 'control_number': 'M22110001',
            'program_slug': program.slug, 'current_semester': '2',
            'admission_period_code': '20223', 'has_conacyt': 'no',
        },
        {
            'first_name': 'Beto', 'last_name': 'Diaz', 'mother_last_name': 'N',
            'email': 'beto@test.local', 'control_number': 'M22110002',
            'program_slug': program.slug, 'current_semester': '3',
            'admission_period_code': '20221', 'has_conacyt': 'sí',
        },
    ]
    result = svc.validate_csv(_csv_text(rows))
    assert result['summary']['total'] == 2
    assert result['summary']['valid'] == 2
    assert result['summary']['invalid'] == 0
    assert result['rows'][1]['data']['has_conacyt'] is True


def test_validate_csv_intra_csv_duplicate_emails(app, periods, program):
    rows = [
        {
            'first_name': 'A', 'last_name': 'B', 'mother_last_name': '',
            'email': 'dup@test.local', 'control_number': 'M22110001',
            'program_slug': program.slug, 'current_semester': '2',
            'admission_period_code': '20223', 'has_conacyt': 'no',
        },
        {
            'first_name': 'C', 'last_name': 'D', 'mother_last_name': '',
            'email': 'dup@test.local', 'control_number': 'M22110002',
            'program_slug': program.slug, 'current_semester': '2',
            'admission_period_code': '20223', 'has_conacyt': 'no',
        },
    ]
    result = svc.validate_csv(_csv_text(rows))
    assert result['summary']['valid'] == 0
    assert result['summary']['invalid'] == 2
    assert any('duplicado' in e for r in result['rows'] for e in r['errors'])


def test_validate_csv_invalid_program_in_one_row(app, periods, program):
    rows = [
        {
            'first_name': 'A', 'last_name': 'B', 'mother_last_name': '',
            'email': 'a@test.local', 'control_number': 'M22110001',
            'program_slug': program.slug, 'current_semester': '2',
            'admission_period_code': '20223', 'has_conacyt': 'no',
        },
        {
            'first_name': 'C', 'last_name': 'D', 'mother_last_name': '',
            'email': 'c@test.local', 'control_number': 'M22110002',
            'program_slug': 'no-existe', 'current_semester': '2',
            'admission_period_code': '20223', 'has_conacyt': 'no',
        },
    ]
    result = svc.validate_csv(_csv_text(rows))
    assert result['summary']['valid'] == 1
    assert result['summary']['invalid'] == 1
    assert result['rows'][0]['valid'] is True
    assert result['rows'][1]['valid'] is False


def test_execute_csv_creates_only_valid(app, periods, program, postgrad_admin):
    rows_data = [
        {
            'first_name': 'Eli', 'last_name': 'Rios', 'mother_last_name': '',
            'email': 'eli@test.local', 'control_number': 'M22110010',
            'program_slug': program.slug, 'current_semester': '2',
            'admission_period_code': '20223', 'has_conacyt': 'no',
        },
        {
            'first_name': 'Bad', 'last_name': 'Row', 'mother_last_name': '',
            'email': 'bad@test.local', 'control_number': 'M22110011',
            'program_slug': 'no-existe', 'current_semester': '2',
            'admission_period_code': '20223', 'has_conacyt': 'no',
        },
    ]
    preview = svc.validate_csv(_csv_text(rows_data))
    assert preview['summary']['valid'] == 1

    out = svc.execute_csv(preview['rows'], created_by_id=postgrad_admin.id)
    assert out['created'] == 1
    assert len(out['failed']) == 0
    assert len(out['created_users']) == 1

    # Solo el primer email debe haber sido creado
    assert User.query.filter_by(email='eli@test.local').first() is not None
    assert User.query.filter_by(email='bad@test.local').first() is None


def test_execute_csv_isolated_failures(app, periods, program, postgrad_admin, roles):
    """Si una fila falla en runtime, el resto continúa."""
    # Primera fila válida en preview, pero hacemos que en execute falle por
    # email duplicado creado entre preview y execute.
    rows_data = [
        {
            'first_name': 'Fernanda', 'last_name': 'Lara', 'mother_last_name': '',
            'email': 'race@test.local', 'control_number': 'M22110020',
            'program_slug': program.slug, 'current_semester': '2',
            'admission_period_code': '20223', 'has_conacyt': 'no',
        },
        {
            'first_name': 'Joaquin', 'last_name': 'Mendoza', 'mother_last_name': '',
            'email': 'ok@test.local', 'control_number': 'M22110021',
            'program_slug': program.slug, 'current_semester': '2',
            'admission_period_code': '20223', 'has_conacyt': 'no',
        },
    ]
    preview = svc.validate_csv(_csv_text(rows_data))
    assert preview['summary']['valid'] == 2

    # Race condition: alguien crea usuario con email 'race@test.local' antes de execute
    racing = User(
        first_name='Fernanda', last_name='Lara', mother_last_name='',
        username='racing', password='pw',
        email='race@test.local', is_internal=False,
        role_id=roles['student'].id, must_change_password=False,
    )
    db.session.add(racing)
    db.session.commit()

    out = svc.execute_csv(preview['rows'], created_by_id=postgrad_admin.id)
    assert out['created'] == 1
    assert len(out['failed']) == 1
    assert out['failed'][0]['email'] == 'race@test.local'

    # La segunda fila sí se creó
    assert User.query.filter_by(email='ok@test.local').first() is not None
