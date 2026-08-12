import hmac
import secrets

from flask import Blueprint, render_template, redirect, url_for, request, jsonify, flash, session
from flask_login import login_required
from markupsafe import escape
from app.utils.permissions import permission_required
from app.utils.ms_graph import build_auth_url, process_auth_code, clear_account_and_cache, read_account_info
from app.services.email_service import EmailService

pages_emails = Blueprint('pages_emails', __name__)

# Session key holding the single-use OAuth `state` minted by ms_login and
# verified by ms_callback. Without it the callback accepts any authorization
# code from anyone: redeeming a code there rebinds the mailbox identity the
# whole system sends from, and the queue flush that follows would deliver the
# pending mail — password-reset links included — to that account's tenant.
_OAUTH_STATE_KEY = 'ms_oauth_state'


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
        # Per-attempt random state, kept server-side in the session. It is the
        # only thing that ties the code arriving at /emails/callback to a
        # consent flow this admin actually started.
        state = secrets.token_urlsafe(32)
        session[_OAUTH_STATE_KEY] = state
        return redirect(build_auth_url(state))
    except (RuntimeError, ValueError) as e:
        session.pop(_OAUTH_STATE_KEY, None)
        flash(str(e), 'danger')
        return redirect(url_for('.email_config'))


@pages_emails.route('/emails/callback')
@login_required
@permission_required('admin_emails.api.manage')
def ms_callback():
    """Callback de Microsoft después de autenticación"""
    # The state is single use: consume it before anything else, whether or not
    # it matches, so a leaked state cannot be replayed on a second attempt.
    expected_state = session.pop(_OAUTH_STATE_KEY, None)
    received_state = request.args.get('state') or ''

    if not expected_state or not hmac.compare_digest(expected_state, received_state):
        flash('La solicitud de autenticación con Microsoft no es válida o expiró. '
              'Inténtalo de nuevo desde esta página.', 'danger')
        return redirect(url_for('.email_config'))

    code = request.args.get('code')
    error = request.args.get('error')

    # Los mensajes se escapan: el cuerpo se sirve como HTML y estos valores
    # vienen de la query string.
    if error:
        return f"Error de autenticación: {escape(error)}", 400

    if not code:
        return "Falta código de autorización", 400

    result = process_auth_code(code)

    if result.get('error'):
        return f"Error MSAL: {escape(result['error_description'])}", 400

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
    """
    Obtiene el estado actual de la cola de correos.

    Superficie duplicada de GET /api/v1/emails/queue/pending: devuelve los
    mismos metadatos de entrega y nunca el cuerpo del correo. Ver la nota en
    `app/routes/api/emails_api.py::queue_pending`.
    """
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