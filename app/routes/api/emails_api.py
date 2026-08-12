from flask import Blueprint, jsonify, request
from flask_login import login_required
from app.utils.permissions import permission_required
from app.services.email_service import EmailService
from app.utils.ms_graph import is_connected, acquire_token_silent, read_account_info, graph_send_mail
from app import db

api_emails = Blueprint('api_emails', __name__, url_prefix='/api/v1/emails')


@api_emails.get('/status')
@login_required
@permission_required('admin_emails.api.manage')
def email_status():
    """Verifica el estado de la conexión con Microsoft"""
    connected = is_connected()
    account = read_account_info()
    stats = EmailService.get_queue_stats()
    
    return jsonify({
        'data': {
            'connected': connected,
            'account': account,
            'stats': stats
        }
    }), 200


@api_emails.get('/queue/stats')
@login_required
@permission_required('admin_emails.api.manage')
def queue_stats():
    """Obtiene estadísticas de la cola de correos"""
    stats = EmailService.get_queue_stats()
    
    return jsonify({
        'data': stats
    }), 200


@api_emails.get('/queue/pending')
@login_required
@permission_required('admin_emails.api.manage')
def queue_pending():
    """
    Lista correos pendientes — sólo metadatos de entrega, nunca el cuerpo.

    `admin_emails.api.manage` lo tiene también program_admin, así que cualquier
    coordinador llega aquí. El cuerpo renderizado de un correo de
    restablecimiento o de activación contiene el enlace de un solo uso: servirlo
    en un listado equivale a entregar la cuenta destino, incluida la del
    postgraduate_admin. La proyección la garantiza EmailQueue.to_dict() y el
    `defer` de EmailService.get_pending_emails.

    No existe endpoint de vista previa del cuerpo, y es deliberado: el HTML es
    la salida determinista de una plantilla del repositorio
    (`app/templates/emails/`), así que para saber qué se envió se lee la
    plantilla; redactar "lo que parezca un enlace con token" sería una lista
    negra, y una lista negra falla abierta justo con el formato que no previó.
    """
    limit = min(int(request.args.get('limit', 50)), 100)
    offset = int(request.args.get('offset', 0))
    
    result = EmailService.get_pending_emails(limit, offset)
    
    return jsonify({
        'data': result,
        'meta': {
            'limit': limit,
            'offset': offset
        }
    }), 200


@api_emails.post('/queue/process')
@login_required
@permission_required('admin_emails.api.manage')
def process_queue():
    """Procesa la cola de correos pendientes manualmente"""
    limit = min(int(request.args.get('limit', 50)), 100)
    
    result = EmailService.process_queue(limit)
    
    if 'error' in result:
        return jsonify({
            'data': None,
            'flash': [{
                'level': 'error',
                'message': result['error']
            }]
        }), 400
    
    return jsonify({
        'data': result,
        'flash': [{
            'level': 'success',
            'message': f"{result['sent']} correos enviados exitosamente"
        }]
    }), 200


@api_emails.post('/queue/retry-failed')
@login_required
@permission_required('admin_emails.api.manage')
def retry_failed():
    """Reintenta enviar correos fallidos"""
    result = EmailService.retry_failed()
    
    if 'error' in result:
        return jsonify({
            'data': None,
            'flash': [{
                'level': 'error',
                'message': result['error']
            }]
        }), 400
    
    return jsonify({
        'data': result,
        'flash': [{
            'level': 'success',
            'message': f"{result['sent']} correos reenviados"
        }]
    }), 200


@api_emails.post('/test')
@login_required
@permission_required('admin_emails.api.manage')
def send_test_email():
    """
    Envía un correo de prueba al usuario que aprieta el botón
    (`current_user.email`). Envío DIRECTO con graph_send_mail — NO pasa por
    EmailQueue, NO deja fila en BD y NO se guarda en la carpeta "Enviados"
    de la cuenta MS (`save_to_sent=False`). Cero rastro.
    """
    from flask_login import current_user

    if not current_user.email:
        return jsonify({
            'data': None,
            'flash': [{'level': 'error', 'message': 'Tu usuario no tiene email configurado'}]
        }), 400

    if not is_connected():
        return jsonify({
            'data': None,
            'flash': [{'level': 'error', 'message': 'No hay conexión activa con Microsoft'}]
        }), 400

    token = acquire_token_silent()
    if not token:
        return jsonify({
            'data': None,
            'flash': [{'level': 'error', 'message': 'No se pudo obtener token de Microsoft'}]
        }), 400

    subject = "✅ Correo de prueba - SIIAP"
    html = f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family: Arial, sans-serif; padding: 20px;">
        <h2>🎉 ¡Correo de Prueba Exitoso!</h2>
        <p>Hola <strong>{current_user.first_name} {current_user.last_name}</strong>,</p>
        <p>Este es un correo de prueba del sistema SIIAP.</p>
        <p>Si recibes este mensaje, la configuración de correos está funcionando correctamente.</p>
        <hr>
        <p style="color: #666; font-size: 14px;">
            Instituto Tecnológico de Ciudad Juárez<br>
            Sistema Integral de Información Académica de Posgrado
        </p>
    </body>
    </html>
    """

    try:
        resp = graph_send_mail(
            access_token=token,
            subject=subject,
            content_html=html,
            to_list=[current_user.email],
            save_to_sent=False,  # no dejar copia en "Enviados" del buzón MS
        )
        if resp.status_code in (200, 202):
            return jsonify({
                'data': {'sent': True},
                'flash': [{
                    'level': 'success',
                    'message': f'Correo de prueba enviado a {current_user.email}'
                }]
            }), 200
        return jsonify({
            'data': None,
            'flash': [{
                'level': 'error',
                'message': f'Microsoft Graph devolvió {resp.status_code}: {resp.text[:200]}'
            }]
        }), 502
    except Exception as e:
        return jsonify({
            'data': None,
            'flash': [{'level': 'error', 'message': f'Error al enviar correo de prueba: {str(e)}'}]
        }), 500