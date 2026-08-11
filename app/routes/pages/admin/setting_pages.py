from flask import Blueprint, render_template, abort
from flask_login import login_required, current_user
from app.utils.permissions import permission_required
from app.services import programs_service as svc

pages_settings = Blueprint('pages_settings', __name__, url_prefix='/settings')

@pages_settings.route('/', methods=['GET'])
@login_required
@permission_required('archives.api.list')
def index():
    return render_template('admin/settings/archives.html')

@pages_settings.route('/retention', methods=['GET'])
@login_required
@permission_required('admin_retention.api.manage')
def retention():
    return render_template('admin/settings/retention.html')

@pages_settings.route('/users', methods=['GET'])
@login_required
@permission_required('admin_users.page.view')
def users():
    return render_template('admin/settings/users.html')

@pages_settings.route('/program/<string:slug>', methods=['GET'])
@login_required
@permission_required('coordinator.page.view')
def config_program(slug):
    """
    Configuración del programa.
    - postgraduate_admin: puede editar todos los programas
    - program_admin: solo puede editar los programas que coordina
    """
    try:
        program = svc.get_program_by_slug(slug)
    except Exception:
        return render_template('404.html'), 404

    # Verificar permisos
    if not current_user.has_global_program_scope():
        # Verificar que sea coordinador del programa
        if program.coordinator_id != current_user.id:
            abort(403)  # Forbidden

    # postgraduate_admin puede editar cualquier programa
    return render_template('admin/settings/program_config.html', program=program)


@pages_settings.route('/academic-periods', methods=['GET'])
@login_required
@permission_required('academic_periods.api.create')
def academic_periods():
    """Gestión de periodos académicos."""
    return render_template('admin/settings/academic_periods.html')


@pages_settings.route('/document-templates', methods=['GET'])
@login_required
@permission_required('admin_templates.page.view')
def document_templates():
    """Gestión de plantillas de documentos institucionales."""
    return render_template(
        'admin/settings/document_templates.html',
        is_postgrad_admin=current_user.has_global_program_scope(),
    )


@pages_settings.route('/worker', methods=['GET'])
@login_required
@permission_required('admin_celery.api.manage')
def celery_worker():
    """Panel de monitoreo y control del worker Celery."""
    return render_template('admin/settings/celery_worker.html')


@pages_settings.route('/student-bulk-import', methods=['GET'])
@login_required
@permission_required('student_bulk.page.view')
def student_bulk_import():
    """Alta masiva e individual de estudiantes existentes."""
    from app.models.program import Program
    from app.models.academic_period import AcademicPeriod
    programs = Program.query.filter_by(is_active=True).order_by(Program.name).all()
    periods = AcademicPeriod.query.order_by(AcademicPeriod.id.desc()).all()
    return render_template(
        'admin/settings/student_bulk_import.html',
        programs=programs,
        periods=periods,
    )


@pages_settings.route('/data-cleanup', methods=['GET'])
@login_required
@permission_required('admin.page.purge')
def data_cleanup():
    """Limpieza de aspirantes/estudiantes con respaldo ZIP previo."""
    return render_template('admin/settings/data_cleanup.html')


@pages_settings.route('/permissions', methods=['GET'])
@login_required
@permission_required('permissions.page.manage_roles')
def permissions_manager():
    """Gestión de permisos de roles (solo jefe de posgrado)."""
    from app.models.role import Role
    from app.models.permission import Permission
    roles = Role.query.order_by(Role.name).all()
    resources = (
        Permission.query
        .with_entities(Permission.resource)
        .distinct()
        .order_by(Permission.resource)
        .all()
    )
    return render_template(
        'admin/settings/permissions.html',
        roles=roles,
        resources=[r[0] for r in resources],
    )