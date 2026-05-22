from flask import Blueprint, render_template, redirect, url_for, request, jsonify, flash
from flask_login import login_required
from app.utils.permissions import permission_required
from app.utils.ms_graph import build_auth_url, process_auth_code, clear_account_and_cache, read_account_info
from app.services.email_service import EmailService

pages_emails = Blueprint('pages_emails', __name__)


@pages_emails.route('/emails')
@login_required
@permission_required('admin_emails.api.manage')
def email_config():
    """Página de configuración de correos"""
    account = read_account_info()
    stats = EmailService.get_queue_stats()
    
    return render_template('admin/settings/emails.html', 
                         ms_account=account,
                         stats=stats)


@pages_emails.route('/emails/login')
@login_required
@permission_required('admin_emails.api.manage')
def ms_login():
    """Inicia el flujo de autenticación con Microsoft"""
    try:
        return redirect(build_auth_url("email_config"))
    except RuntimeError as e:
        flash(str(e), 'danger')
        return redirect(url_for('.email_config'))


@pages_emails.route('/emails/callback')
def ms_callback():
    """Callback de Microsoft después de autenticación"""
    code = request.args.get('code')
    error = request.args.get('error')
    
    if error:
        return f"Error de autenticación: {error}", 400
    
    if not code:
        return "Falta código de autorización", 400
    
    result = process_auth_code(code)
    
    if result.get('error'):
        return f"Error MSAL: {result['error_description']}", 400
    
    # Procesar cola después de conectar
    EmailService.process_queue()
    
    return redirect(url_for('.email_config'))


@pages_emails.post('/emails/logout')
@login_required
@permission_required('admin_emails.api.manage')
def ms_logout():
    """Cierra sesión de Microsoft"""
    clear_account_and_cache()
    return jsonify({'ok': True})


@pages_emails.post('/emails/process-queue')
@login_required
@permission_required('admin_emails.api.manage')
def process_email_queue():
    """Procesa la cola de correos manualmente"""
    try:
        result = EmailService.process_queue()
        # process_queue devuelve {'error': ...} (p. ej. 'No hay sesión activa
        # de Microsoft') sin lanzar excepción. Hay que propagarlo arriba o el
        # front muestra "Procesados: 0" sin explicar el motivo real.
        if result.get('error'):
            return jsonify({
                'ok': False,
                'error': result['error'],
                'result': result
            }), 400
        return jsonify({
            'ok': True,
            'result': result
        })
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500


@pages_emails.get('/emails/queue')
@login_required
@permission_required('admin_emails.api.manage')
def get_email_queue():
    """Obtiene el estado actual de la cola de correos"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        
        offset = (page - 1) * per_page
        result = EmailService.get_pending_emails(limit=per_page, offset=offset)
        
        return jsonify({
            'ok': True,
            'emails': result['items'],
            'total': result['total'],
            'page': page,
            'per_page': per_page
        })
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500


@pages_emails.post('/emails/retry-failed')
@login_required
@permission_required('admin_emails.api.manage')
def retry_failed_emails():
    """Reintenta enviar correos fallidos"""
    try:
        result = EmailService.retry_failed()
        return jsonify({
            'ok': True,
            'result': result
        })
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500