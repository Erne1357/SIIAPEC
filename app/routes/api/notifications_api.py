from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from app.services.notification_service import NotificationService
from app.services.events_service import EventsService
from app import db

api_notifications = Blueprint('api_notifications', __name__, url_prefix='/api/v1/notifications')


@api_notifications.get('')
@login_required
def get_notifications():
    """
    Obtiene las notificaciones del usuario actual.
    Query params:
        - unread_only: bool (default: false)
        - limit: int (default: 50, max: 100)
        - offset: int (default: 0)
    """
    unread_only = request.args.get('unread_only', 'false').lower() == 'true'
    limit = min(int(request.args.get('limit', 50)), 100)
    offset = int(request.args.get('offset', 0))
    
    notifications, total = NotificationService.get_user_notifications(
        user_id=current_user.id,
        include_read=not unread_only,
        limit=limit,
        offset=offset
    )
    
    return jsonify({
        'data': {
            'notifications': [n.to_dict() for n in notifications],
            'total': total,
            'unread_count': NotificationService.get_unread_count(current_user.id)
        },
        'meta': {
            'limit': limit,
            'offset': offset
        }
    }), 200


@api_notifications.get('/unread-count')
@login_required
def get_unread_count():
    """Obtiene solo el contador de notificaciones no leídas"""
    count = NotificationService.get_unread_count(current_user.id)
    
    return jsonify({
        'data': {
            'count': count
        }
    }), 200


@api_notifications.patch('/<int:notification_id>/read')
@login_required
def mark_notification_read(notification_id):
    """Marca una notificación como leída"""
    notification = NotificationService.mark_as_read(notification_id, current_user.id)
    
    if not notification:
        return jsonify({
            'data': None,
            'flash': [{
                'level': 'error',
                'message': 'Notificación no encontrada'
            }]
        }), 404
    
    db.session.commit()
    
    return jsonify({
        'data': notification.to_dict(),
        'flash': []
    }), 200


@api_notifications.post('/mark-all-read')
@login_required
def mark_all_notifications_read():
    """Marca todas las notificaciones como leídas"""
    count = NotificationService.mark_all_as_read(current_user.id)
    db.session.commit()
    
    return jsonify({
        'data': {
            'marked_count': count
        },
        'flash': [{
            'level': 'success',
            'message': f'{count} notificaciones marcadas como leídas'
        }]
    }), 200


@api_notifications.delete('/<int:notification_id>')
@login_required
def delete_notification(notification_id):
    """Elimina (soft delete) una notificación"""
    notification = NotificationService.delete_notification(notification_id, current_user.id)
    
    if not notification:
        return jsonify({
            'data': None,
            'flash': [{
                'level': 'error',
                'message': 'Notificación no encontrada'
            }]
        }), 404
    
    db.session.commit()
    
    return jsonify({
        'data': None,
        'flash': [{
            'level': 'success',
            'message': 'Notificación eliminada'
        }]
    }), 200


@api_notifications.post('/clear-read')
@login_required
def clear_read_notifications():
    """Elimina todas las notificaciones leídas"""
    count = NotificationService.clear_read_notifications(current_user.id)
    db.session.commit()
    
    return jsonify({
        'data': {
            'deleted_count': count
        },
        'flash': [{
            'level': 'success',
            'message': f'{count} notificaciones eliminadas'
        }]
    }), 200


@api_notifications.post('/<int:notification_id>/respond-invitation')
@login_required
def respond_invitation(notification_id):
    """
    Responde a una invitación desde una notificación.
    Body: { "response": "accepted" | "rejected" }

    Esta ruta NO implementa la transición: la delega en
    `EventsService.respond_to_invitation`, que es el único sitio donde vive la
    máquina de estados de las invitaciones. Aquí sólo se traduce la forma de la
    petición y de la respuesta —la notificación como punto de entrada, el
    cuerpo `{'response': ...}` y el marcado de leída—.

    Antes había una segunda implementación completa aquí: escribía
    `invitation.status` y creaba la fila `EventAttendance` a mano, sin pasar por
    `invitation_block_reason` ni por `register_to_event`. Con ella, un invitado
    aceptaba invitaciones a eventos en borrador, ocultos, cancelados,
    finalizados o ya llenos —estados que el camino sancionado
    (`POST /api/v1/invitations/<id>/respond`) rechaza—, y se quedaba con un
    registro de asistencia que ninguna regla había autorizado. No ampliaba el
    alcance (las filas son de su propio evento), pero dejaba la máquina de
    estados con dos versiones y sólo una correcta.
    """
    data = request.get_json(silent=True) or {}
    response_type = data.get('response')

    if response_type not in ('accepted', 'rejected'):
        return jsonify({
            'data': None,
            'flash': [{
                'level': 'error',
                'message': 'Respuesta inválida'
            }],
            'error': {'code': 'VALIDATION_ERROR', 'message': 'Respuesta inválida'},
            'meta': {}
        }), 400

    # La notificación se busca acotada al dueño: identifica la invitación sin
    # revelar las de nadie más. La autoría de la invitación la vuelve a
    # comprobar el servicio (`invitation.user_id != user_id`), que es la
    # comprobación que manda.
    from app.models.notification import Notification
    notification = Notification.query.filter_by(
        id=notification_id,
        user_id=current_user.id
    ).first()

    if not notification or not notification.related_invitation_id:
        return jsonify({
            'data': None,
            'flash': [{
                'level': 'error',
                'message': 'Notificación o invitación no encontrada'
            }],
            'error': {'code': 'NOT_FOUND', 'message': 'Notificación o invitación no encontrada'},
            'meta': {}
        }), 404

    try:
        invitation = EventsService.respond_to_invitation(
            invitation_id=notification.related_invitation_id,
            user_id=current_user.id,
            accept=(response_type == 'accepted'),
        )
    except ValueError as e:
        # El servicio ya explica el motivo en español (evento sin publicar,
        # invitación cancelada, sin cupo…). No se marca la notificación como
        # leída: la invitación sigue pendiente de respuesta.
        db.session.rollback()
        return jsonify({
            'data': None,
            'flash': [{'level': 'error', 'message': str(e)}],
            'error': {'code': 'BUSINESS_ERROR', 'message': str(e)},
            'meta': {}
        }), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'data': None,
            'flash': [{'level': 'error', 'message': 'No se pudo registrar tu respuesta.'}],
            'error': {'code': 'SERVER_ERROR', 'message': str(e)},
            'meta': {}
        }), 500

    # La transición ya está confirmada; marcar leída es un efecto de la ruta.
    NotificationService.mark_as_read(notification_id, current_user.id)
    db.session.commit()

    message = 'Invitación aceptada' if invitation.status == 'accepted' else 'Invitación rechazada'

    return jsonify({
        'data': {
            'invitation_status': invitation.status
        },
        'flash': [{
            'level': 'success',
            'message': message
        }],
        'error': None,
        'meta': {}
    }), 200