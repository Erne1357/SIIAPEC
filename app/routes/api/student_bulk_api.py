# app/routes/api/student_bulk_api.py
"""
API para el módulo de Alta Masiva de Estudiantes.

Permite dar de alta estudiantes que ya están en permanencia pero nunca
pasaron por el flujo de admisión en SIIAP (generaciones previas al sistema).

Endpoints:
  POST /api/v1/student-bulk/validate        — Valida payload individual sin crear
  POST /api/v1/student-bulk/create          — Alta individual de estudiante
  POST /api/v1/student-bulk/csv/preview     — Valida CSV y devuelve filas con errores
  POST /api/v1/student-bulk/csv/execute     — Ejecuta filas válidas del preview
  GET  /api/v1/student-bulk/csv/template    — Descarga plantilla CSV

Permiso ≠ alcance: `student_bulk.api.*` dice que el usuario puede dar de alta
estudiantes, no EN QUÉ PROGRAMA. El alcance se resuelve aquí y se pasa al
servicio como `creator_program_ids`; sin él, un coordinador podía fabricar
estudiantes con número de control y estatus `enrolled` dentro del programa de
otro coordinador.
"""

import io

from flask import Blueprint, jsonify, request, Response
from flask_login import login_required, current_user
from app.utils.permissions import permission_required
from app.services import program_scope_service as scope_service
import app.services.student_bulk_service as svc

api_student_bulk = Blueprint(
    'api_student_bulk',
    __name__,
    url_prefix='/api/v1/student-bulk',
)


def _creator_program_ids():
    """
    Programas sobre los que `current_user` puede dar de alta estudiantes.

    Devuelve `None` para el jefe de posgrado (TODOS) y un conjunto —posiblemente
    vacío— para el resto. Nunca conviertas `None` en `set()` ni al revés.
    """
    return scope_service.accessible_program_ids(current_user)


def _scope_denied(exc):
    """403 con el envoltorio estándar y el mensaje en español del servicio."""
    return jsonify({
        'data': None,
        'flash': [{'level': 'danger', 'message': str(exc)}],
        'error': {'code': 'FORBIDDEN', 'message': str(exc)},
        'meta': {},
    }), 403


# ---------------------------------------------------------------------------
# POST /validate — validar payload individual
# ---------------------------------------------------------------------------

@api_student_bulk.post('/validate')
@login_required
@permission_required('student_bulk.api.create_one')
def api_validate_individual():
    """Valida un payload de alta individual sin crear registros en la base de datos."""
    payload = request.get_json(silent=True) or {}

    try:
        result = svc.validate_individual(
            payload, creator_program_ids=_creator_program_ids()
        )
        return jsonify({
            'data': result,
            'error': None,
            'meta': {},
        }), 200

    except Exception as e:
        return jsonify({
            'data': None,
            'error': {'code': 'SERVER_ERROR', 'message': str(e)},
            'meta': {},
        }), 500


# ---------------------------------------------------------------------------
# POST /create — alta individual
# ---------------------------------------------------------------------------

@api_student_bulk.post('/create')
@login_required
@permission_required('student_bulk.api.create_one')
def api_create_individual():
    """Crea un estudiante individual: User + UserProgram + SemesterEnrollments + email."""
    payload = request.get_json(silent=True) or {}

    try:
        result = svc.create_student_individual(
            payload,
            created_by_id=current_user.id,
            creator_program_ids=_creator_program_ids(),
        )
        return jsonify({
            'data': result,
            'flash': [{'level': 'success', 'message': f'Estudiante creado exitosamente. Se envió un correo de bienvenida a {result["email"]}.'}],
            'error': None,
            'meta': {},
        }), 200

    except svc.ProgramScopeError as e:
        return _scope_denied(e)

    except svc.ValidationError as e:
        return jsonify({
            'data': None,
            'flash': [{'level': 'warning', 'message': str(e)}],
            'error': {'code': 'VALIDATION_ERROR', 'message': str(e)},
            'meta': {},
        }), 400

    except svc.StudentCreationError as e:
        return jsonify({
            'data': None,
            'flash': [{'level': 'danger', 'message': str(e)}],
            'error': {'code': 'CREATION_ERROR', 'message': str(e)},
            'meta': {},
        }), 400

    except Exception as e:
        return jsonify({
            'data': None,
            'flash': [{'level': 'danger', 'message': 'Error al crear el estudiante.'}],
            'error': {'code': 'SERVER_ERROR', 'message': str(e)},
            'meta': {},
        }), 500


# ---------------------------------------------------------------------------
# POST /csv/preview — validar CSV y devolver preview
# ---------------------------------------------------------------------------

@api_student_bulk.post('/csv/preview')
@login_required
@permission_required('student_bulk.api.csv_preview')
def api_csv_preview():
    """
    Recibe un archivo CSV (multipart/form-data, campo 'csv_file') y devuelve
    la validación de cada fila con errores marcados, listo para confirmación.
    """
    csv_file = request.files.get('csv_file')
    if not csv_file or csv_file.filename == '':
        return jsonify({
            'data': None,
            'flash': [{'level': 'danger', 'message': 'No se recibió ningún archivo CSV.'}],
            'error': {'code': 'MISSING_FILE', 'message': 'Se requiere el archivo csv_file.'},
            'meta': {},
        }), 400

    try:
        csv_text = csv_file.read().decode('utf-8-sig')  # utf-8-sig maneja BOM de Excel
    except UnicodeDecodeError:
        return jsonify({
            'data': None,
            'flash': [{'level': 'danger', 'message': 'El archivo no es UTF-8 válido. Guárdalo como CSV UTF-8.'}],
            'error': {'code': 'ENCODING_ERROR', 'message': 'El archivo debe ser UTF-8.'},
            'meta': {},
        }), 400

    try:
        result = svc.validate_csv(
            csv_text, creator_program_ids=_creator_program_ids()
        )

        # Si el servicio retornó un error de parseo
        if 'error' in result and result.get('error'):
            return jsonify({
                'data': None,
                'flash': [{'level': 'danger', 'message': result['error']}],
                'error': {'code': 'PARSE_ERROR', 'message': result['error']},
                'meta': {},
            }), 400

        summary = result.get('summary', {})
        return jsonify({
            'data': result,
            'error': None,
            'meta': {
                'total': summary.get('total', 0),
                'valid': summary.get('valid', 0),
                'invalid': summary.get('invalid', 0),
            },
        }), 200

    except Exception as e:
        return jsonify({
            'data': None,
            'flash': [{'level': 'danger', 'message': 'Error al procesar el CSV.'}],
            'error': {'code': 'SERVER_ERROR', 'message': str(e)},
            'meta': {},
        }), 500


# ---------------------------------------------------------------------------
# POST /csv/execute — ejecutar filas válidas
# ---------------------------------------------------------------------------

@api_student_bulk.post('/csv/execute')
@login_required
@permission_required('student_bulk.api.csv_execute')
def api_csv_execute():
    """
    Recibe las filas previamente validadas (JSON {rows: [...]}) y ejecuta
    las que tienen valid=True. Cada fila es atómica; errores son aislados.
    """
    body = request.get_json(silent=True) or {}
    rows = body.get('rows')

    if not isinstance(rows, list) or len(rows) == 0:
        return jsonify({
            'data': None,
            'flash': [{'level': 'warning', 'message': 'No se recibieron filas para ejecutar.'}],
            'error': {'code': 'MISSING_ROWS', 'message': 'Se requiere "rows" como lista no vacía.'},
            'meta': {},
        }), 400

    try:
        result = svc.execute_csv(
            rows,
            created_by_id=current_user.id,
            creator_program_ids=_creator_program_ids(),
        )
        created = result['created']
        failed_count = len(result['failed'])

        if created > 0 and failed_count == 0:
            flash_level = 'success'
            flash_msg = f'Se crearon {created} estudiante(s) exitosamente.'
        elif created > 0 and failed_count > 0:
            flash_level = 'warning'
            flash_msg = f'Se crearon {created} estudiante(s). {failed_count} fila(s) fallaron — revisa los detalles.'
        else:
            flash_level = 'danger'
            flash_msg = f'No se pudo crear ningún estudiante. {failed_count} fila(s) fallaron.'

        return jsonify({
            'data': result,
            'flash': [{'level': flash_level, 'message': flash_msg}],
            'error': None,
            'meta': {
                'created': created,
                'failed': failed_count,
            },
        }), 200

    except Exception as e:
        return jsonify({
            'data': None,
            'flash': [{'level': 'danger', 'message': 'Error al ejecutar el CSV.'}],
            'error': {'code': 'SERVER_ERROR', 'message': str(e)},
            'meta': {},
        }), 500


# ---------------------------------------------------------------------------
# GET /csv/template — descargar plantilla CSV
# ---------------------------------------------------------------------------

@api_student_bulk.get('/csv/template')
@login_required
@permission_required('student_bulk.api.create_one')
def api_csv_template():
    """Devuelve la plantilla CSV con headers y una fila de ejemplo para descargar."""
    try:
        csv_content = svc.get_csv_template()
        return Response(
            csv_content,
            mimetype='text/csv',
            headers={
                'Content-Disposition': 'attachment; filename="plantilla_alta_estudiantes.csv"',
                'Content-Type': 'text/csv; charset=utf-8',
            },
        )
    except Exception as e:
        return jsonify({
            'data': None,
            'error': {'code': 'SERVER_ERROR', 'message': str(e)},
            'meta': {},
        }), 500
