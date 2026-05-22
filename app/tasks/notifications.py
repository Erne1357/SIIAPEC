"""
Tareas Celery para el envío masivo de notificaciones y correos.

Uso desde cualquier servicio Flask:
    from app.tasks.notifications import send_bulk_notification

    # Enviar a una lista de user_ids
    send_bulk_notification.delay(
        user_ids=[1, 2, 3],
        notification_type='event_announcement',
        title='Nuevo evento',
        message='Te invitamos al taller de tesis el viernes.',
        action_url='/events/42',
        priority='high',
    )

    # Enviar a un rol completo
    send_bulk_notification_by_filter.delay(
        filter_type='role',
        filter_value='applicant',
        notification_type='deadline_reminder',
        title='Recordatorio de fecha límite',
        message='Tu proceso de admisión vence en 7 días.',
        priority='high',
    )
"""

import logging
from typing import List, Optional

from app.celery_app import celery

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 1. ENVÍO MASIVO POR LISTA DE USER IDs
# ─────────────────────────────────────────────────────────────────────────────

@celery.task(
    name='app.tasks.notifications.send_bulk_notification',
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def send_bulk_notification(
    self,
    user_ids: List[int],
    notification_type: str,
    title: str,
    message: str,
    priority: str = 'medium',
    action_url: Optional[str] = None,
    data: Optional[dict] = None,
    send_email: bool = False,
    email_subject: Optional[str] = None,
    email_html: Optional[str] = None,
):
    """
    Crea notificaciones in-app para cada user_id de la lista.

    Args:
        user_ids:          IDs de los usuarios destinatarios.
        notification_type: Tipo de notificación (ej. 'event_announcement').
        title:             Título de la notificación.
        message:           Texto del mensaje.
        priority:          'low' | 'medium' | 'high' | 'critical'
        action_url:        URL a la que lleva el botón de acción (ej. '/events/42').
        data:              JSON extra que quieras adjuntar.
        send_email:        Si True, también encola un correo para cada usuario.
        email_subject:     Asunto del correo (requerido si send_email=True).
        email_html:        HTML del correo (requerido si send_email=True).
    """
    from app import db
    from app.services.notification_service import NotificationService
    from app.models.user import User

    logger.info(
        f"[send_bulk_notification] Enviando '{title}' a {len(user_ids)} usuarios..."
    )

    created = 0
    errors = 0

    try:
        for uid in user_ids:
            try:
                NotificationService.create_notification(
                    user_id=uid,
                    notification_type=notification_type,
                    title=title,
                    message=message,
                    priority=priority,
                    action_url=action_url,
                    data=data or {},
                )
                created += 1

                if send_email and email_subject and email_html:
                    try:
                        from app.services.email_service import EmailService
                        EmailService.queue_email(uid, email_subject, email_html)
                    except Exception as e:
                        logger.warning(f"Error al encolar correo para user {uid}: {e}")

            except Exception as e:
                errors += 1
                logger.warning(f"Error creando notificación para user {uid}: {e}")

        db.session.commit()

        logger.info(
            f"[send_bulk_notification] Completado. Creadas: {created}, errores: {errors}"
        )
        return {'created': created, 'errors': errors}

    except Exception as exc:
        db.session.rollback()
        logger.error(f"[send_bulk_notification] Error fatal: {exc}", exc_info=True)
        raise self.retry(exc=exc)


# ─────────────────────────────────────────────────────────────────────────────
# 2. ENVÍO MASIVO POR FILTRO (rol, programa, proceso, etc.)
# ─────────────────────────────────────────────────────────────────────────────

@celery.task(
    name='app.tasks.notifications.send_bulk_notification_by_filter',
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def send_bulk_notification_by_filter(
    self,
    filter_type: str,
    filter_value: str,
    notification_type: str,
    title: str,
    message: str,
    priority: str = 'medium',
    action_url: Optional[str] = None,
    data: Optional[dict] = None,
    send_email: bool = False,
    email_subject: Optional[str] = None,
    email_html: Optional[str] = None,
):
    """
    Resuelve la lista de user_ids según el filtro y delega a send_bulk_notification.

    filter_type puede ser:
      'role'      → filter_value = nombre del rol (ej. 'applicant')
      'program'   → filter_value = slug o id del programa
      'process'   → filter_value = estado del proceso (ej. 'in_progress')
      'all'       → notifica a todos los usuarios activos (filter_value ignorado)

    Ejemplo:
        send_bulk_notification_by_filter.delay(
            filter_type='role',
            filter_value='applicant',
            notification_type='deadline_reminder',
            title='Recordatorio',
            message='Tu proceso vence pronto.',
            action_url='/user/dashboard',
        )
    """
    from app.models.user import User
    from app.models.role import Role
    from app.models.user_program import UserProgram

    logger.info(
        f"[send_bulk_notification_by_filter] "
        f"Resolviendo filtro '{filter_type}={filter_value}'..."
    )

    try:
        user_ids = []

        if filter_type == 'role':
            role = Role.query.filter_by(name=filter_value).first()
            if role:
                users = User.query.filter(
                    User.roles.any(id=role.id),
                    User.is_active == True,
                ).all()
                user_ids = [u.id for u in users]

        elif filter_type == 'program':
            ups = UserProgram.query.filter_by(program_id=filter_value).all()
            user_ids = list({up.user_id for up in ups})

        elif filter_type == 'process':
            ups = UserProgram.query.filter_by(status=filter_value).all()
            user_ids = list({up.user_id for up in ups})

        elif filter_type == 'all':
            users = User.query.filter_by(is_active=True).all()
            user_ids = [u.id for u in users]

        else:
            logger.warning(f"filter_type desconocido: {filter_type}")
            return {'error': f'filter_type desconocido: {filter_type}'}

        logger.info(
            f"[send_bulk_notification_by_filter] "
            f"Encontrados {len(user_ids)} usuarios. Delegando a send_bulk_notification..."
        )

        # Delega el envío real a la tarea por lista
        send_bulk_notification.delay(
            user_ids=user_ids,
            notification_type=notification_type,
            title=title,
            message=message,
            priority=priority,
            action_url=action_url,
            data=data,
            send_email=send_email,
            email_subject=email_subject,
            email_html=email_html,
        )

        return {'user_ids_resolved': len(user_ids), 'filter': f'{filter_type}={filter_value}'}

    except Exception as exc:
        logger.error(
            f"[send_bulk_notification_by_filter] Error: {exc}", exc_info=True
        )
        raise self.retry(exc=exc)


# ─────────────────────────────────────────────────────────────────────────────
# 3. ENVÍO INDIVIDUAL DE CORREO ASÍNCRONO
# ─────────────────────────────────────────────────────────────────────────────

@celery.task(
    name='app.tasks.notifications.send_email_async',
    bind=True,
    max_retries=5,
    default_retry_delay=60,
)
def send_email_async(self, email_queue_id: int):
    """
    Intenta enviar un correo de la cola (EmailQueue) de forma asíncrona.
    Se llama desde EmailService.queue_email para no bloquear la petición HTTP.
    """
    from app import db
    from app.models.email_queue import EmailQueue
    from app.services.email_service import EmailService
    from app.utils.ms_graph import is_connected

    try:
        email_item = EmailQueue.query.get(email_queue_id)
        if not email_item:
            logger.warning(f"[send_email_async] EmailQueue {email_queue_id} no encontrado")
            return

        # Si ya se envió o falló definitivamente, no reintentar
        if email_item.status in ('sent', 'failed'):
            return

        # Sin sesión Microsoft activa NO tiene sentido quemar los 5 reintentos
        # con backoff exponencial: ninguno va a funcionar. El correo queda
        # 'pending' y el barrido periódico (process_email_queue) lo reintenta
        # cuando la sesión vuelva.
        if not is_connected():
            logger.warning(
                f"[send_email_async] Sin sesión Microsoft; email {email_queue_id} "
                f"queda pendiente para el barrido periódico"
            )
            return {'skipped': True, 'reason': 'no_session'}

        logger.info(f"[send_email_async] Intentando enviar email {email_queue_id}...")

        # Intentar enviar
        sent = EmailService._try_send_email(email_item)

        # Persistir el resultado. _try_send_email sólo hace flush(); sin este
        # commit el cambio (status='sent' o attempts++/error_message) se pierde
        # al cerrar el contexto de la task → el correo se reenvía (duplicados)
        # o se queda en 0 intentos sin rastro del error.
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise

        if not sent:
            # _try_send_email ya incrementó attempts y, si llegó al tope,
            # marcó 'failed'. Forzamos el reintento de Celery con backoff.
            raise Exception(f"Fallo al enviar email {email_queue_id}")

    except Exception as exc:
        logger.error(f"[send_email_async] Error enviando email {email_queue_id}: {exc}")
        # Reintentar con backoff exponencial
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


# ─────────────────────────────────────────────────────────────────────────────
# 4. BARRIDO PERIÓDICO DE LA COLA DE CORREOS (red de seguridad)
# ─────────────────────────────────────────────────────────────────────────────

@celery.task(
    name='app.tasks.notifications.process_email_queue',
    bind=True,
)
def process_email_queue(self, limit: int = 100):
    """
    Reintenta los correos 'pending' de la cola.

    Red de seguridad: si send_email_async agotó sus reintentos, o la sesión
    Microsoft estuvo caída cuando se encolaron los correos, esos quedan
    'pending' sin nadie que los reenvíe hasta que un admin presione el botón.
    Esta task (Celery Beat) los barre periódicamente.

    EmailService.process_queue ya hace su propio commit.
    """
    from app.models.email_queue import EmailQueue
    from app.services.email_service import EmailService
    from app.utils.ms_graph import is_connected

    # Salida barata en vacío: 1 COUNT indexado, sin tocar MSAL ni red.
    # Es el caso normal la mayoría de las corridas /10min.
    pending = EmailQueue.query.filter_by(status='pending').count()
    if pending == 0:
        return {'processed': 0, 'sent': 0, 'failed': 0, 'idle': True}

    if not is_connected():
        logger.info("[process_email_queue] Sin sesión Microsoft; barrido omitido")
        return {'processed': 0, 'sent': 0, 'failed': 0, 'skipped': 'no_session'}

    result = EmailService.process_queue(limit=limit)
    logger.info(f"[process_email_queue] {result}")
    return result
