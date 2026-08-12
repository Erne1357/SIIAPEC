from app import db
from app.models.email_queue import EmailQueue
from app.models.user import User
from app.utils.ms_graph import graph_send_mail, acquire_token_silent, is_connected
from app.utils.datetime_utils import now_local
from sqlalchemy.orm import defer
from datetime import timedelta
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# Value written over `html_content` once a mail is no longer sendable. The
# column is NOT NULL, so the body is blanked rather than nulled.
PURGED_BODY = ''

# How long a rendered body survives after the mail stopped being sendable.
# See EmailService.purge_delivered_bodies for the reasoning behind 24 h.
BODY_RETENTION_HOURS = 24


class EmailService:
    """Servicio para gestión y envío de correos con cola"""

    @staticmethod
    def queue_email(user_id: int, subject: str, html_content: str, 
                   notification_id: Optional[int] = None) -> EmailQueue:
        """
        Agrega un correo a la cola.
        Si hay sesión activa de Microsoft, intenta enviarlo inmediatamente.
        """
        user = User.query.get(user_id)
        if not user or not user.email:
            raise ValueError(f"Usuario {user_id} no tiene email configurado")
        
        email_item = EmailQueue(
            user_id=user_id,
            notification_id=notification_id,
            recipient_email=user.email,
            subject=subject,
            html_content=html_content,
            status='pending',
            attempts=0
        )
        
        db.session.add(email_item)
        db.session.flush()

        # Delegar el envío a Celery para evitar bloqueos síncronos en la UI
        # Usamos countdown=1 para dar tiempo a que la transacción principal haga commit
        try:
            from app.tasks.notifications import send_email_async
            send_email_async.apply_async(args=[email_item.id], countdown=1)
        except Exception as err:
            logger.warning(f"No se pudo encolar la tarea de email async: {err}")

        # Notificar en tiempo real al panel de administración
        EmailService._emit_queue_update()

        return email_item

    @staticmethod
    def _emit_queue_update():
        """Emite el estado actual de la cola al panel de admin vía WebSocket."""
        try:
            from app.extensions import socketio
            pending = EmailQueue.query.filter_by(status='pending').count()
            failed = EmailQueue.query.filter_by(status='failed').count()
            socketio.emit(
                'email:queue_update',
                {'pending': pending, 'failed': failed},
                room='role:postgraduate_admin',
            )
        except Exception:
            pass
    
    @staticmethod
    def _try_send_email(email_item: EmailQueue) -> bool:
        """
        Intenta enviar un email de la cola.
        Retorna True si se envió exitosamente.
        """
        # A blanked body means the retention purge already reclaimed this row,
        # which only happens once the mail is no longer sendable ('sent', or
        # 'failed' past max_attempts). Sending it would deliver an empty
        # message; fail it loudly instead of silently mailing nothing.
        if not email_item.html_content:
            logger.error(
                f"Email {email_item.id} sin cuerpo (purgado por retención); no se envía"
            )
            email_item.status = 'failed'
            email_item.error_message = (
                'El cuerpo del correo fue purgado por la política de retención; '
                'no se puede reenviar.'
            )
            db.session.flush()
            return False

        try:
            token = acquire_token_silent()
            if not token:
                logger.warning(f"No hay token para enviar email {email_item.id}")
                return False

            response = graph_send_mail(
                access_token=token,
                subject=email_item.subject,
                content_html=email_item.html_content,
                to_list=[email_item.recipient_email],
                save_to_sent=True
            )
            
            if response.status_code in (200, 202):
                email_item.status = 'sent'
                email_item.sent_at = now_local()
                email_item.error_message = None
                db.session.flush()
                logger.info(f"Email {email_item.id} enviado exitosamente")
                return True
            else:
                raise Exception(f"HTTP {response.status_code}: {response.text}")
                
        except Exception as e:
            logger.error(f"Error enviando email {email_item.id}: {str(e)}")
            email_item.attempts += 1
            email_item.error_message = str(e)
            
            if email_item.attempts >= email_item.max_attempts:
                email_item.status = 'failed'
            else:
                # Reintentar en 5 minutos
                email_item.next_retry_at = now_local() + timedelta(minutes=5)
            
            db.session.flush()
            return False
    
    @staticmethod
    def process_queue(limit: int = 50) -> dict:
        """
        Procesa la cola de correos pendientes.
        Retorna estadísticas del procesamiento.
        """
        if not is_connected():
            return {
                'processed': 0,
                'sent': 0,
                'failed': 0,
                'error': 'No hay sesión activa de Microsoft'
            }
        
        # Obtener correos pendientes
        pending = EmailQueue.query.filter_by(status='pending').order_by(
            EmailQueue.created_at.asc()
        ).limit(limit).all()
        
        sent_count = 0
        failed_count = 0
        
        for email_item in pending:
            if EmailService._try_send_email(email_item):
                sent_count += 1
            else:
                failed_count += 1
        
        db.session.commit()
        
        return {
            'processed': len(pending),
            'sent': sent_count,
            'failed': failed_count
        }
    
    @staticmethod
    def retry_failed(limit: int = 20) -> dict:
        """Reintenta enviar correos fallidos que no han superado max_attempts"""
        if not is_connected():
            return {
                'processed': 0,
                'sent': 0,
                'error': 'No hay sesión activa de Microsoft'
            }
        
        failed = EmailQueue.query.filter(
            EmailQueue.status == 'pending',
            EmailQueue.attempts > 0,
            EmailQueue.attempts < EmailQueue.max_attempts
        ).limit(limit).all()
        
        sent_count = 0
        for email_item in failed:
            if EmailService._try_send_email(email_item):
                sent_count += 1
        
        db.session.commit()
        
        return {
            'processed': len(failed),
            'sent': sent_count
        }
    
    @staticmethod
    def get_queue_stats() -> dict:
        """Obtiene estadísticas de la cola"""
        pending = EmailQueue.query.filter_by(status='pending').count()
        sent = EmailQueue.query.filter_by(status='sent').count()
        failed = EmailQueue.query.filter_by(status='failed').count()
        
        return {
            'pending': pending,
            'sent': sent,
            'failed': failed,
            'total': pending + sent + failed,
        }
    
    @staticmethod
    def get_pending_emails(limit: int = 50, offset: int = 0):
        """
        Obtiene correos pendientes con paginación (sin el cuerpo del correo).

        `defer` keeps `html_content` out of the SELECT: the rendered body — which
        for password-reset and activation mail is a live one-time token link —
        never reaches the application layer on a listing, let alone the client.
        """
        query = EmailQueue.query.filter_by(status='pending').order_by(
            EmailQueue.created_at.desc()
        )
        total = query.count()
        items = (
            query.options(defer(EmailQueue.html_content))
            .limit(limit)
            .offset(offset)
            .all()
        )

        return {
            'items': [item.to_dict() for item in items],
            'total': total
        }

    @staticmethod
    def purge_delivered_bodies(hours: int = BODY_RETENTION_HOURS) -> dict:
        """
        Blanks `html_content` on mail that can no longer be sent.

        Rationale for the window (24 h by default): once a mail is 'sent' the
        rendered body has no operational use — the sender already consumed it
        and no code path re-reads it (`retry_failed` only picks 'pending' rows).
        What it does keep is a copy of whatever the template rendered, including
        password-reset and staff-activation token links, which stay valid for up
        to 7 days (`generate_token` default `ttl_days=7`). Keeping the body for
        24 h leaves one working day to diagnose "what did we actually send?"
        while ensuring a live credential stops existing in a second place long
        before it expires. 'failed' rows are purged on the same clock measured
        from `created_at`: nothing can ever resend them.

        The rows themselves are kept — subject, recipient, status, attempts and
        timestamps are the delivery audit trail and carry no secret.
        """
        cutoff = now_local() - timedelta(hours=hours)

        sent = EmailQueue.query.filter(
            EmailQueue.status == 'sent',
            EmailQueue.sent_at.isnot(None),
            EmailQueue.sent_at < cutoff,
            EmailQueue.html_content != PURGED_BODY,
        ).update({'html_content': PURGED_BODY}, synchronize_session=False)

        failed = EmailQueue.query.filter(
            EmailQueue.status == 'failed',
            EmailQueue.created_at < cutoff,
            EmailQueue.html_content != PURGED_BODY,
        ).update({'html_content': PURGED_BODY}, synchronize_session=False)

        db.session.commit()

        return {
            'sent_purged': sent,
            'failed_purged': failed,
            'total': sent + failed,
            'hours': hours,
        }