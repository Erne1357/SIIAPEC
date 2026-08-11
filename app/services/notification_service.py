from app import db
from app.models.notification import Notification
from app.models.user import User
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from app.utils.datetime_utils import now_local
from app.utils.urls import external_url


class NotificationService:
    """
    Servicio centralizado para gestionar notificaciones del sistema.
    """
    
    # Tipos de notificaciones que requieren acción del usuario
    ACTIONABLE_TYPES = {
        'document_rejected',
        'extension_rejected',
        'event_invitation',
        'event_cancelled',
        'event_archived',
        'event_reminder_24h',
        'event_reminder_2h',
        'password_reset',
        'profile_incomplete',
        'extension_deadline_near',
        'deliberation_accepted',
        'deliberation_rejected',
        'deliberation_corrections',
        'deliberation_reset',
        'acceptance_docs_ready',
        'enrollment_receipt_rejected',
        'enrollment_receipt_submitted',
        'permanence_doc_rejected',
        'permanence_doc_submitted',
        'leave_request_submitted',
        'leave_request_rejected',
        'deadline_created',
        'deadline_opened',
        'deferral_applied',
        'deferral_rejected',
        'deferral_reactivated',
        'deferral_request_received',
        'extension_request_submitted',
    }
    
    @staticmethod
    def create_notification(
        user_id: int,
        notification_type: str,
        title: str,
        message: str,
        priority: str = 'medium',
        data: Optional[Dict[str, Any]] = None,
        action_url: Optional[str] = None,
        related_invitation_id: Optional[int] = None,
        expires_at: Optional[datetime] = None
    ) -> Notification:
        """
        Crea una notificación para un usuario.

        Args:
            user_id:               ID del usuario que recibirá la notificación
            notification_type:     Tipo de notificación
            title:                 Título de la notificación
            message:               Mensaje de la notificación
            priority:              Prioridad (low, medium, high, critical)
            data:                  Datos adicionales en formato JSON
            action_url:            URL destino cuando el usuario hace clic (opcional)
            related_invitation_id: ID de invitación relacionada (si aplica)
            expires_at:            Fecha de expiración (opcional)
        """
        notification = Notification(
            user_id=user_id,
            type=notification_type,
            title=title,
            message=message,
            priority=priority,
            data=data or {},
            action_url=action_url,
            related_invitation_id=related_invitation_id,
            expires_at=expires_at,
            is_actionable=notification_type in NotificationService.ACTIONABLE_TYPES
        )
        
        db.session.add(notification)
        db.session.flush()

        # Emitir evento WebSocket al usuario en tiempo real
        try:
            from app.extensions import socketio
            socketio.emit(
                'notification:new',
                {'notification': notification.to_dict()},
                room=f'user:{user_id}',
            )
        except Exception:
            pass  # Si Redis/socket falla, la notificación DB ya está guardada

        return notification

    # ==================== DOCUMENTOS ====================
    
    @staticmethod
    def notify_document_approved(user_id: int, archive_name: str, submission_id: int,
                                  program_slug: str = None) -> Notification:
        """Notifica cuando un documento es aprobado"""
        action_url = f'/programs/admission/{program_slug}' if program_slug else '/user/dashboard'
        notification = NotificationService.create_notification(
            user_id=user_id,
            notification_type='document_approved',
            title='Documento aprobado',
            message=f'Tu documento "{archive_name}" ha sido aprobado.',
            priority='medium',
            action_url=action_url,
            data={
                'submission_id': submission_id,
                'archive_name': archive_name,
            }
        )
        
        # NUEVO: Enviar correo
        try:
            from app.models.user import User
            from app.services.email_service import EmailService
            from app.services.email_templates import EmailTemplates
            user = User.query.get(user_id)
            if user:
                slug = user.user_program[0].program.slug if user.user_program else 'general'
                dashboard_url = external_url('program.admission.admission_dashboard', slug=slug)
                subject, html = EmailTemplates.document_approved(
                    user_name=f"{user.first_name} {user.last_name}",
                    archive_name=archive_name,
                    dashboard_url=dashboard_url
                )
                EmailService.queue_email(user_id, subject, html, notification.id)
        except Exception as e:
            import logging
            logging.error(f"Error queueing email for document_approved: {e}")
        
        return notification
    
    @staticmethod
    def notify_document_rejected(user_id: int, archive_name: str, submission_id: int,
                                  reason: str = None, program_slug: str = None) -> Notification:
        """Notifica cuando un documento es rechazado"""
        message = f'Tu documento "{archive_name}" fue rechazado.'
        if reason:
            message += f' Motivo: {reason}'

        action_url = f'/programs/admission/{program_slug}' if program_slug else '/user/dashboard'
        notification = NotificationService.create_notification(
            user_id=user_id,
            notification_type='document_rejected',
            title='Documento rechazado',
            message=message,
            priority='high',
            action_url=action_url,
            data={
                'submission_id': submission_id,
                'archive_name': archive_name,
                'reason': reason,
            }
        )
        
        # NUEVO: Enviar correo
        try:
            from app.models.user import User
            from app.services.email_service import EmailService
            from app.services.email_templates import EmailTemplates
            user = User.query.get(user_id)
            if user:
                slug = user.user_program[0].program.slug if user.user_program else 'general'
                dashboard_url = external_url('program.admission.admission_dashboard', slug=slug)
                subject, html = EmailTemplates.document_rejected(
                    user_name=f"{user.first_name} {user.last_name}",
                    archive_name=archive_name,
                    reason=reason or "No especificado",
                    dashboard_url=dashboard_url
                )
                EmailService.queue_email(user_id, subject, html, notification.id)
        except Exception as e:
            import logging
            logging.error(f"Error queueing email for document_rejected: {e}")
        
        return notification
    
    @staticmethod
    def notify_coordinator_uploaded(user_id: int, archive_name: str, submission_id: int,
                                     coordinator_name: str, program_slug: str = None) -> Notification:
        """Notifica cuando el coordinador sube un documento por el estudiante"""
        action_url = f'/programs/admission/{program_slug}' if program_slug else '/user/dashboard'
        notification = NotificationService.create_notification(
            user_id=user_id,
            notification_type='coordinator_uploaded',
            title='Documento subido por coordinador',
            message=f'El coordinador {coordinator_name} ha subido el documento "{archive_name}" por ti.',
            priority='medium',
            action_url=action_url,
            data={
                'submission_id': submission_id,
                'archive_name': archive_name,
            }
        )
        
        # NUEVO: Enviar correo
        try:
            from app.models.user import User
            from app.services.email_service import EmailService
            from app.services.email_templates import EmailTemplates
            user = User.query.get(user_id)
            if user:
                slug = user.user_program[0].program.slug if user.user_program else 'general'
                dashboard_url = external_url('program.admission.admission_dashboard', slug=slug)
                subject, html = EmailTemplates.coordinator_uploaded(
                    user_name=f"{user.first_name} {user.last_name}",
                    archive_name=archive_name,
                    coordinator_name=coordinator_name,
                    dashboard_url=dashboard_url
                )
                EmailService.queue_email(user_id, subject, html, notification.id)
        except Exception as e:
            import logging
            logging.error(f"Error queueing email for coordinator_uploaded: {e}")
        
        return notification
    
    # ==================== PRÓRROGAS ====================
    
    @staticmethod
    def notify_extension_approved(user_id: int, archive_name: str, granted_until: str,
                                   program_slug: str = None) -> Notification:
        """Notifica cuando una prórroga es aprobada"""
        action_url = f'/programs/admission/{program_slug}' if program_slug else '/user/dashboard'
        notification = NotificationService.create_notification(
            user_id=user_id,
            notification_type='extension_approved',
            title='Prórroga aprobada',
            message=f'Tu solicitud de prórroga para "{archive_name}" ha sido aprobada hasta el {granted_until}.',
            priority='medium',
            action_url=action_url,
            data={
                'archive_name': archive_name,
                'granted_until': granted_until,
            }
        )
        
        # NUEVO: Enviar correo
        try:
            from app.models.user import User
            from app.services.email_service import EmailService
            from app.services.email_templates import EmailTemplates
            user = User.query.get(user_id)
            if user:
                slug = user.user_program[0].program.slug if user.user_program else 'general'
                dashboard_url = external_url('program.admission.admission_dashboard', slug=slug)
                subject, html = EmailTemplates.extension_approved(
                    user_name=f"{user.first_name} {user.last_name}",
                    archive_name=archive_name,
                    granted_until=granted_until,
                    dashboard_url=dashboard_url
                )
                EmailService.queue_email(user_id, subject, html, notification.id)
        except Exception as e:
            import logging
            logging.error(f"Error queueing email for extension_approved: {e}")
        
        return notification
    
    @staticmethod
    def notify_extension_rejected(user_id: int, archive_name: str, reason: str = None,
                                   program_slug: str = None) -> Notification:
        """Notifica cuando una prórroga es rechazada"""
        message = f'Tu solicitud de prórroga para "{archive_name}" ha sido rechazada.'
        if reason:
            message += f' Motivo: {reason}'

        action_url = f'/programs/admission/{program_slug}' if program_slug else '/user/dashboard'
        notification = NotificationService.create_notification(
            user_id=user_id,
            notification_type='extension_rejected',
            title='Prórroga rechazada',
            message=message,
            priority='high',
            action_url=action_url,
            data={
                'archive_name': archive_name,
                'reason': reason,
            }
        )
        
        # NUEVO: Enviar correo
        try:
            from app.models.user import User
            from app.services.email_service import EmailService
            from app.services.email_templates import EmailTemplates
            user = User.query.get(user_id)
            if user:
                slug = user.user_program[0].program.slug if user.user_program else 'general'
                dashboard_url = external_url('program.admission.admission_dashboard', slug=slug)
                subject, html = EmailTemplates.extension_rejected(
                    user_name=f"{user.first_name} {user.last_name}",
                    archive_name=archive_name,
                    reason=reason or "No especificado",
                    dashboard_url=dashboard_url
                )
                EmailService.queue_email(user_id, subject, html, notification.id)
        except Exception as e:
            import logging
            logging.error(f"Error queueing email for extension_rejected: {e}")
        
        return notification
    
    # ==================== EVENTOS Y CITAS ====================
    
    @staticmethod
    def notify_appointment_assigned(user_id: int, event_title: str, appointment_id: int, 
                                   slot_datetime: str, event_id: int, location: str = None) -> Notification:
        """Notifica cuando se asigna una cita"""
        notification = NotificationService.create_notification(
            user_id=user_id,
            notification_type='appointment_assigned',
            title='Cita asignada',
            message=f'Se te ha asignado una cita para "{event_title}" el {slot_datetime}.',
            priority='high',
            action_url=f'/events/{event_id}',
            data={
                'appointment_id': appointment_id,
                'event_id': event_id,
                'event_title': event_title,
                'slot_datetime': slot_datetime,
            }
        )
        
        # NUEVO: Enviar correo
        try:
            from app.models.user import User
            from app.services.email_service import EmailService
            from app.services.email_templates import EmailTemplates
            user = User.query.get(user_id)
            if user:
                dashboard_url = external_url('pages_user.dashboard')
                subject, html = EmailTemplates.appointment_assigned(
                    user_name=f"{user.first_name} {user.last_name}",
                    event_title=event_title,
                    slot_datetime=slot_datetime,
                    location=location or "Por confirmar",
                    dashboard_url=dashboard_url
                )
                EmailService.queue_email(user_id, subject, html, notification.id)
        except Exception as e:
            import logging
            logging.error(f"Error queueing email for appointment_assigned: {e}")
        
        return notification
    
    @staticmethod
    def notify_appointment_cancelled(user_id: int, event_title: str, reason: str) -> Notification:
        """Notifica cuando se cancela una cita"""
        notification = NotificationService.create_notification(
            user_id=user_id,
            notification_type='appointment_cancelled',
            title='Cita cancelada',
            message=f'Tu cita para "{event_title}" ha sido cancelada. Motivo: {reason}',
            priority='high',
            action_url='/events/',
            data={
                'event_title': event_title,
                'reason': reason,
            }
        )
        
        # NUEVO: Enviar correo
        try:
            from app.models.user import User
            from app.services.email_service import EmailService
            from app.services.email_templates import EmailTemplates
            user = User.query.get(user_id)
            if user:
                dashboard_url = external_url('pages_user.dashboard')
                subject, html = EmailTemplates.appointment_cancelled(
                    user_name=f"{user.first_name} {user.last_name}",
                    event_title=event_title,
                    reason=reason,
                    dashboard_url=dashboard_url
                )
                EmailService.queue_email(user_id, subject, html, notification.id)
        except Exception as e:
            import logging
            logging.error(f"Error queueing email for appointment_cancelled: {e}")
        
        return notification
    
    @staticmethod
    def notify_appointment_reassigned(user_id: int, event_title: str, appointment_id: int,
                                      new_slot_datetime: str, old_slot_datetime: str,
                                      event_id: int, location: str = None) -> Notification:
        """Notifica cuando se reasigna una cita por aprobación de solicitud de cambio"""
        notification = NotificationService.create_notification(
            user_id=user_id,
            notification_type='appointment_change_accepted',
            title='Cambio de horario aprobado',
            message=f'Tu solicitud de cambio para "{event_title}" fue aprobada. Tu nuevo horario es el {new_slot_datetime}.',
            priority='high',
            action_url=f'/events/{event_id}',
            data={
                'appointment_id': appointment_id,
                'event_id': event_id,
                'event_title': event_title,
                'slot_datetime': new_slot_datetime,
            }
        )

        try:
            from app.models.user import User
            from app.services.email_service import EmailService
            from app.services.email_templates import EmailTemplates
            user = User.query.get(user_id)
            if user:
                dashboard_url = external_url('pages_user.dashboard')
                subject, html = EmailTemplates.appointment_reassigned(
                    user_name=f"{user.first_name} {user.last_name}",
                    event_title=event_title,
                    new_slot_datetime=new_slot_datetime,
                    old_slot_datetime=old_slot_datetime,
                    location=location or "Por confirmar",
                    dashboard_url=dashboard_url
                )
                EmailService.queue_email(user_id, subject, html, notification.id)
        except Exception as e:
            import logging
            logging.error(f"Error queueing email for appointment_reassigned: {e}")

        return notification

    @staticmethod
    def notify_event_invitation(user_id: int, event_title: str, event_id: int,
                               invitation_id: int, event_date: str, description: str = None) -> Notification:
        """Notifica sobre una invitación a evento"""
        notification = NotificationService.create_notification(
            user_id=user_id,
            notification_type='event_invitation',
            title='Invitación a evento',
            message=f'Has sido invitado a "{event_title}" el {event_date}.',
            priority='high',
            action_url=f'/events/{event_id}',
            data={
                'event_id': event_id,
                'invitation_id': invitation_id,
                'event_title': event_title,
                'event_date': event_date,
            },
            related_invitation_id=invitation_id
        )

        try:
            from app.models.user import User
            from app.services.email_service import EmailService
            from app.services.email_templates import EmailTemplates
            user = User.query.get(user_id)
            if not user:
                raise ValueError(f"Usuario {user_id} no existe para notify_event_invitation")
            if not user.email:
                raise ValueError(f"Usuario {user_id} no tiene email configurado")

            try:
                event_url = external_url('pages_events_public.view_event', event_id=event_id)
            except RuntimeError:
                # Fuera de request context (p.ej. desde Celery): fallback relativo
                event_url = f"/events/{event_id}"

            subject, html = EmailTemplates.event_invitation(
                user_name=f"{user.first_name} {user.last_name}",
                event_title=event_title,
                event_date=event_date,
                description=description or "Evento importante del sistema de posgrado",
                event_url=event_url
            )
            EmailService.queue_email(user_id, subject, html, notification.id)
        except Exception as e:
            import logging
            logging.exception(f"Error queueing email for event_invitation (user_id={user_id}, event_id={event_id}): {e}")
            raise

        return notification

    @staticmethod
    def notify_event_cancelled_invitation(user_id: int, event_title: str, event_id: int) -> Notification:
        """Notifica al invitado que el evento fue cerrado y su invitación cancelada."""
        return NotificationService.create_notification(
            user_id=user_id,
            notification_type='event_cancelled',
            title='Evento cancelado',
            message=f'El evento "{event_title}" fue cerrado y tu invitación quedó sin efecto.',
            priority='normal',
            action_url=f'/events/{event_id}',
            data={'event_id': event_id, 'event_title': event_title}
        )

    @staticmethod
    def notify_event_reminder(user_id, event, reminder_type: str, slot_datetime: str) -> Notification:
        """
        Recordatorio automático de evento (24h o 2h antes).
        reminder_type: '24h' | '2h'
        slot_datetime: string ya formateado 'dd/mm/yyyy HH:MM'
        """
        label_map = {
            '24h': ('Recordatorio: evento mañana', 'mañana'),
            '2h':  ('Recordatorio: evento en 2 horas', 'en unas horas'),
        }
        title, when = label_map.get(reminder_type, ('Recordatorio de evento', 'pronto'))

        notification_type = f'event_reminder_{reminder_type}'
        message = f'Tu evento "{event.title}" comienza {when} ({slot_datetime}).'

        notification = NotificationService.create_notification(
            user_id=user_id,
            notification_type=notification_type,
            title=title,
            message=message,
            priority='high',
            action_url=f'/events/{event.id}',
            data={
                'event_id': event.id,
                'event_title': event.title,
                'reminder_type': reminder_type,
                'slot_datetime': slot_datetime,
            }
        )

        try:
            from app.models.user import User
            from app.services.email_service import EmailService
            from app.services.email_templates import EmailTemplates

            user = User.query.get(user_id)
            if not user or not user.email:
                return notification

            try:
                event_url = external_url('pages_events_public.view_event', event_id=event.id)
            except RuntimeError:
                event_url = f"/events/{event.id}"

            subject, html = EmailTemplates.event_reminder(
                user_name=f"{user.first_name} {user.last_name}",
                event_title=event.title,
                slot_datetime=slot_datetime,
                reminder_type=reminder_type,
                location=event.location or 'Por definir',
                event_url=event_url
            )
            EmailService.queue_email(user_id, subject, html, notification.id)
        except Exception as e:
            import logging
            logging.exception(f"Error queueing email for event_reminder: {e}")

        return notification

    @staticmethod
    def notify_event_archived(user_id: int, event_title: str, event_id: int) -> Notification:
        """Notifica a registrados que el evento fue archivado/retirado."""
        return NotificationService.create_notification(
            user_id=user_id,
            notification_type='event_archived',
            title='Evento archivado',
            message=f'El evento "{event_title}" fue archivado por la coordinación.',
            priority='normal',
            action_url=f'/events/{event_id}',
            data={'event_id': event_id, 'event_title': event_title}
        )
    
    # ==================== ADMINISTRATIVAS ====================
    
    @staticmethod
    def notify_password_reset(
        user_id: int,
        token_link: str | None = None,
        expires_at=None,
    ) -> Notification:
        """
        Notifica cuando un administrador restablece la contraseña.

        La notificación en pantalla NO lleva credencial ni enlace: se almacena
        en la base y se muestra en cada sesión del usuario, así que un enlace
        de un solo uso ahí sería una credencial persistida. El enlace viaja
        únicamente en el correo.
        """
        notification = NotificationService.create_notification(
            user_id=user_id,
            notification_type='password_reset',
            title='Contraseña restablecida',
            message=(
                'Un administrador restableció tu contraseña. Tu contraseña anterior '
                'ya no funciona: revisa tu correo, ahí te enviamos un enlace para '
                'que definas una nueva.'
            ),
            priority='critical',
            action_url='/login',
        )

        # Enviar correo con el enlace de un solo uso.
        try:
            from app.models.user import User
            from app.services.email_service import EmailService
            from app.services.email_templates import EmailTemplates
            user = User.query.get(user_id)
            if not token_link:
                # Sin enlace no hay nada que el usuario pueda hacer con el
                # correo: mejor no enviarlo que mandar instrucciones muertas.
                import logging
                logging.error(
                    "notify_password_reset sin token_link para el usuario %s: "
                    "no se envía correo.", user_id
                )
            elif user:
                subject, html = EmailTemplates.password_reset(
                    user_name=f"{user.first_name} {user.last_name}",
                    token_link=token_link,
                    expires_at=expires_at,
                )
                EmailService.queue_email(user_id, subject, html, notification.id)
        except Exception as e:
            import logging
            logging.error(f"Error queueing email for password_reset: {e}")

        return notification
    
    @staticmethod
    def notify_control_number_assigned(user_id: int, control_number: str) -> Notification:
        """Notifica cuando se asigna un número de control"""
        notification = NotificationService.create_notification(
            user_id=user_id,
            notification_type='control_number_assigned',
            title='Número de control asignado',
            message=f'Se te ha asignado el número de control: {control_number}',
            priority='high',
            action_url='/user/profile',
            data={
                'control_number': control_number,
            }
        )
        
        # NUEVO: Enviar correo
        try:
            from app.models.user import User
            from app.services.email_service import EmailService
            from app.services.email_templates import EmailTemplates
            user = User.query.get(user_id)
            if user:
                dashboard_url = external_url('pages_user.profile')
                subject, html = EmailTemplates.control_number_assigned(
                    user_name=f"{user.first_name} {user.last_name}",
                    control_number=control_number,
                    dashboard_url=dashboard_url
                )
                EmailService.queue_email(user_id, subject, html, notification.id)
        except Exception as e:
            import logging
            logging.error(f"Error queueing email for control_number_assigned: {e}")
        
        return notification
    
    @staticmethod
    def notify_account_deactivated(user_id: int, reason: str = None) -> Notification:
        """Notifica cuando una cuenta es desactivada"""
        message = 'Tu cuenta ha sido desactivada.'
        if reason:
            message += f' Motivo: {reason}'
        
        notification = NotificationService.create_notification(
            user_id=user_id,
            notification_type='account_deactivated',
            title='Cuenta desactivada',
            message=message,
            priority='critical',
            data={'reason': reason}
        )
        
        # NUEVO: Enviar correo
        try:
            from app.models.user import User
            from app.services.email_service import EmailService
            from app.services.email_templates import EmailTemplates
            user = User.query.get(user_id)
            if user:
                dashboard_url = external_url('pages_auth.login_page')
                subject, html = EmailTemplates.account_deactivated(
                    user_name=f"{user.first_name} {user.last_name}",
                    reason=reason or "No especificado",
                    dashboard_url=dashboard_url
                )
                EmailService.queue_email(user_id, subject, html, notification.id)
        except Exception as e:
            import logging
            logging.error(f"Error queueing email for account_deactivated: {e}")
        
        return notification
    
    @staticmethod
    def notify_program_changed(user_id: int, from_program: str, to_program: str) -> Notification:
        """Notifica cuando hay un cambio de programa"""
        notification = NotificationService.create_notification(
            user_id=user_id,
            notification_type='program_changed',
            title='Cambio de programa',
            message=f'Has sido cambiado del programa "{from_program}" a "{to_program}".',
            priority='high',
            action_url='/user/dashboard',
            data={
                'from_program': from_program,
                'to_program': to_program,
            }
        )
        
        # NUEVO: Enviar correo
        try:
            from app.models.user import User
            from app.services.email_service import EmailService
            from app.services.email_templates import EmailTemplates
            user = User.query.get(user_id)
            if user:
                slug = user.user_program[0].program.slug if user.user_program else 'general'
                dashboard_url = external_url('program.admission.admission_dashboard', slug=slug)
                subject, html = EmailTemplates.program_changed(
                    user_name=f"{user.first_name} {user.last_name}",
                    from_program=from_program,
                    to_program=to_program,
                    dashboard_url=dashboard_url
                )
                EmailService.queue_email(user_id, subject, html, notification.id)
        except Exception as e:
            import logging
            logging.error(f"Error queueing email for program_changed: {e}")
        
        return notification
    
    # ==================== DELIBERACIÓN ====================

    @staticmethod
    def notify_deliberation_accepted(user_id: int, program_name: str, dashboard_url: str) -> Notification:
        """Notifica cuando el aspirante es aceptado en deliberación (con email)"""
        notification = NotificationService.create_notification(
            user_id=user_id,
            notification_type='deliberation_accepted',
            title='¡Felicidades! Has sido aceptado',
            message=f'Has sido aceptado en el programa {program_name}. Revisa tu portal para los próximos pasos.',
            priority='high',
            action_url='/user/dashboard',
        )

        try:
            from app.models.user import User
            from app.services.email_service import EmailService
            from app.services.email_templates import EmailTemplates
            user = User.query.get(user_id)
            if user:
                subject, html = EmailTemplates.deliberation_accepted(
                    user_name=f"{user.first_name} {user.last_name}",
                    program_name=program_name,
                    dashboard_url=dashboard_url,
                )
                EmailService.queue_email(user_id, subject, html, notification.id)
        except Exception as e:
            import logging
            logging.error(f"Error queueing email for deliberation_accepted: {e}")

        return notification

    @staticmethod
    def notify_deliberation_rejected(user_id: int, program_name: str,
                                      rejection_type: str, notes: str,
                                      dashboard_url: str) -> Notification:
        """Notifica cuando el aspirante es rechazado o requiere correcciones (con email)"""
        if rejection_type == 'partial':
            notif_type = 'deliberation_corrections'
            title = 'Se requieren correcciones en tu expediente'
            message = (
                f'El comité de admisión de {program_name} ha solicitado algunas correcciones. '
                f'Revisa los detalles en tu portal.'
            )
        else:
            notif_type = 'deliberation_rejected'
            title = 'Resultado de tu proceso de admisión'
            message = (
                f'Lamentamos informarte que no has sido aceptado en el programa {program_name}. '
                f'Consulta tu portal para más detalles.'
            )

        notification = NotificationService.create_notification(
            user_id=user_id,
            notification_type=notif_type,
            title=title,
            message=message,
            priority='high',
            action_url='/user/dashboard',
        )

        try:
            from app.models.user import User
            from app.services.email_service import EmailService
            from app.services.email_templates import EmailTemplates
            user = User.query.get(user_id)
            if user:
                subject, html = EmailTemplates.deliberation_rejected(
                    user_name=f"{user.first_name} {user.last_name}",
                    program_name=program_name,
                    rejection_type=rejection_type,
                    notes=notes or '',
                    dashboard_url=dashboard_url,
                )
                EmailService.queue_email(user_id, subject, html, notification.id)
        except Exception as e:
            import logging
            logging.error(f"Error queueing email for deliberation_rejected: {e}")

        return notification

    @staticmethod
    def notify_acceptance_docs_ready(user_id: int, program_name: str, dashboard_url: str) -> Notification:
        """Notifica cuando el coordinador sube carta de aceptación + tira de materias (con email)"""
        notification = NotificationService.create_notification(
            user_id=user_id,
            notification_type='acceptance_docs_ready',
            title='Tus documentos de aceptación están disponibles',
            message=(
                f'Tu carta de aceptación y tira de materias para {program_name} ya están disponibles. '
                f'Ingresa al portal para descargarlos y seguir las instrucciones.'
            ),
            priority='high',
            action_url='/user/dashboard',
        )

        try:
            from app.models.user import User
            from app.services.email_service import EmailService
            from app.services.email_templates import EmailTemplates
            user = User.query.get(user_id)
            if user:
                subject, html = EmailTemplates.acceptance_docs_ready(
                    user_name=f"{user.first_name} {user.last_name}",
                    program_name=program_name,
                    dashboard_url=dashboard_url,
                )
                EmailService.queue_email(user_id, subject, html, notification.id)
        except Exception as e:
            import logging
            logging.error(f"Error queueing email for acceptance_docs_ready: {e}")

        return notification

    @staticmethod
    def notify_acceptance_doc_uploaded(user_id: int, program_name: str,
                                        document_type: str, document_label: str,
                                        dashboard_url: str = '/user/dashboard') -> Notification:
        """
        Notifica al aspirante cada vez que el coordinador sube uno de los documentos
        de aceptación (carta de aceptación o tira de materias) por separado.
        """
        notification = NotificationService.create_notification(
            user_id=user_id,
            notification_type=f'acceptance_{document_type}_uploaded',
            title=f'{document_label} disponible',
            message=(
                f'Tu {document_label.lower()} para {program_name} '
                f'ya fue subida por el coordinador. Ingresa a tu dashboard para revisarla.'
            ),
            priority='high',
            action_url=dashboard_url,
            data={
                'document_type': document_type,
                'document_label': document_label,
                'program_name': program_name,
            }
        )
        return notification

    # ==================== GESTIÓN DE NOTIFICACIONES ====================

    @staticmethod
    def mark_as_read(notification_id: int, user_id: int) -> Optional[Notification]:
        """Marca una notificación como leída"""
        notification = Notification.query.filter_by(
            id=notification_id,
            user_id=user_id
        ).first()
        
        if notification and not notification.is_read:
            notification.is_read = True
            notification.read_at = now_local()
            db.session.flush()
        
        return notification
    
    @staticmethod
    def mark_all_as_read(user_id: int) -> int:
        """Marca todas las notificaciones de un usuario como leídas"""
        count = Notification.query.filter_by(
            user_id=user_id,
            is_read=False,
            is_deleted=False
        ).update({
            'is_read': True,
            'read_at': now_local()
        })
        db.session.flush()
        return count
    
    @staticmethod
    def delete_notification(notification_id: int, user_id: int) -> Optional[Notification]:
        """Soft delete de una notificación"""
        notification = Notification.query.filter_by(
            id=notification_id,
            user_id=user_id
        ).first()
        
        if notification:
            notification.is_deleted = True
            db.session.flush()
        
        return notification
    
    @staticmethod
    def get_user_notifications(
        user_id: int,
        include_read: bool = True,
        limit: int = 50,
        offset: int = 0
    ) -> tuple[List[Notification], int]:
        """Obtiene notificaciones de un usuario con paginación"""
        query = Notification.query.filter_by(
            user_id=user_id,
            is_deleted=False
        )
        
        if not include_read:
            query = query.filter_by(is_read=False)
        
        total = query.count()
        
        notifications = query.order_by(
            Notification.created_at.desc()
        ).limit(limit).offset(offset).all()
        
        return notifications, total
    
    @staticmethod
    def get_unread_count(user_id: int) -> int:
        """Obtiene el contador de notificaciones no leídas"""
        return Notification.query.filter_by(
            user_id=user_id,
            is_read=False,
            is_deleted=False
        ).count()
    
    @staticmethod
    def clear_read_notifications(user_id: int) -> int:
        """Elimina todas las notificaciones leídas de un usuario"""
        count = Notification.query.filter_by(
            user_id=user_id,
            is_read=True,
            is_deleted=False
        ).update({'is_deleted': True})
        db.session.flush()
        return count
    
    @staticmethod
    def cleanup_old_notifications(days: int = 30) -> int:
        """Limpia notificaciones antiguas y leídas"""
        cutoff_date = now_local() - timedelta(days=days)

        count = Notification.query.filter(
            Notification.created_at < cutoff_date,
            Notification.is_read == True,
            Notification.is_deleted == False
        ).update({'is_deleted': True})

        db.session.flush()
        return count

    # ==================== EVENTOS — BROADCAST ====================

    @staticmethod
    def notify_event_published(user_ids: list, event_title: str, event_id: int) -> int:
        """
        Crea notificación in-app para múltiples usuarios cuando un evento se publica.
        NO encola correos (broadcast de alto volumen).

        Returns:
            Cantidad de notificaciones creadas exitosamente.
        """
        created = 0
        for uid in user_ids:
            try:
                NotificationService.create_notification(
                    user_id=uid,
                    notification_type='event_published',
                    title='Nuevo evento disponible',
                    message=f'Se publicó "{event_title}". Revísalo en la sección de eventos.',
                    priority='normal',
                    action_url=f'/events/{event_id}',
                    data={'event_id': event_id, 'event_title': event_title}
                )
                created += 1
            except Exception as e:
                import logging
                logging.exception(f"notify_event_published fallo user_id={uid}: {e}")
        return created

    # ==================== NOTIFICACIONES MASIVAS (VÍA CELERY) ====================

    @staticmethod
    def send_bulk(
        user_ids: List[int],
        notification_type: str,
        title: str,
        message: str,
        priority: str = 'medium',
        action_url: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
        send_email: bool = False,
        email_subject: Optional[str] = None,
        email_html: Optional[str] = None,
    ) -> str:
        """
        Encola una tarea Celery para enviar notificaciones masivas a una lista de usuarios.

        Returns:
            task_id (str): ID de la tarea Celery para seguimiento.

        Ejemplo:
            task_id = NotificationService.send_bulk(
                user_ids=[1, 2, 3],
                notification_type='event_announcement',
                title='Nuevo evento',
                message='Te invitamos al taller de tesis.',
                action_url='/events/42',
                priority='high',
            )
        """
        from app.tasks.notifications import send_bulk_notification
        task = send_bulk_notification.delay(
            user_ids=user_ids,
            notification_type=notification_type,
            title=title,
            message=message,
            priority=priority,
            action_url=action_url,
            data=data or {},
            send_email=send_email,
            email_subject=email_subject,
            email_html=email_html,
        )
        return task.id

    @staticmethod
    def send_bulk_by_filter(
        filter_type: str,
        filter_value: str,
        notification_type: str,
        title: str,
        message: str,
        priority: str = 'medium',
        action_url: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
        send_email: bool = False,
        email_subject: Optional[str] = None,
        email_html: Optional[str] = None,
    ) -> str:
        """
        Encola una tarea Celery para enviar notificaciones a un grupo filtrado.

        filter_type: 'role' | 'program' | 'process' | 'all'
        filter_value: nombre del rol, id/slug del programa, estado del proceso, etc.

        Returns:
            task_id (str): ID de la tarea Celery para seguimiento.

        Ejemplo:
            task_id = NotificationService.send_bulk_by_filter(
                filter_type='role',
                filter_value='applicant',
                notification_type='deadline_reminder',
                title='Recordatorio',
                message='Tu proceso de admisión vence en 7 días.',
                action_url='/user/dashboard',
            )
        """
        from app.tasks.notifications import send_bulk_notification_by_filter
        task = send_bulk_notification_by_filter.delay(
            filter_type=filter_type,
            filter_value=filter_value,
            notification_type=notification_type,
            title=title,
            message=message,
            priority=priority,
            action_url=action_url,
            data=data or {},
            send_email=send_email,
            email_subject=email_subject,
            email_html=email_html,
        )
        return task.id