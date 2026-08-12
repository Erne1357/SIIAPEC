# app/routes/pages/student_record_pages.py
"""
Page routes for the Student Record (Expediente Completo).
"""
from flask import Blueprint, abort, current_app, render_template
from flask_login import login_required, current_user

from app.models.user import User
from app.services import student_record_service as svc
from app.utils.permissions import permission_required

pages_student_record = Blueprint(
    'pages_student_record',
    __name__,
    url_prefix='/students',
)


@pages_student_record.route('/<uuid:user_uuid>/record')
@login_required
@permission_required('students.page.view_record')
def student_record(user_uuid):
    # 404 para las DOS negativas —usuario inexistente y usuario fuera del
    # alcance—: el 403 anterior confirmaba qué ids de usuario existen y dejaba
    # censar la tabla de cuentas recorriendo /students/1..N. El operador
    # conserva la diferencia en el log, no en la respuesta.
    # Un identificador desconocido o malformado entra por la MISMA puerta que
    # un usuario fuera del alcance: `load_record_target(None, ...)` lanza
    # StudentNotFound y ambos acaban en el mismo abort(404).
    target = User.by_uuid(user_uuid)
    user_id = target.id if target else None

    try:
        user = svc.load_record_target(user_id, current_user)
    except svc.StudentRecordError as e:
        reason = 'unknown_user' if isinstance(e, svc.StudentNotFound) else 'out_of_scope'
        current_app.logger.warning(
            "[student_record] page denied (%s): requester=%s target=%s — %s",
            reason, current_user.id, user_id, e,
        )
        abort(404)

    return render_template(
        'coordinator/student_record/index.html',
        target_user=user,
    )
