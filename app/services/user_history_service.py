# app/services/user_history_service.py

from app import db
from app.models.user_history import UserHistory
from app.models.user import User
from flask_login import current_user
from typing import Optional, Dict, Any, List
from app.services.notification_service import NotificationService
import json


class UserHistoryService:
    """
    Servicio para gestionar el historial de acciones administrativas sobre usuarios.
    Centraliza toda la lógica relacionada con el registro y consulta del historial.
    
    REGLAS FUNDAMENTALES:
    =====================
    
    1. HISTORIAL SE GUARDA EN QUIEN HACE LA ACCIÓN:
       - Si un usuario sube un documento → historial en el usuario
       - Si un admin revisa un documento → historial en el admin
       - Si un admin asigna una cita → historial en el admin
    
    2. NOTIFICACIONES SE ENVÍAN A QUIEN SE AFECTA:
       - Si admin revisa documento de Juan → notificación a Juan
       - Si admin asigna cita a María → notificación a María
       - Si admin resetea password de Pedro → notificación a Pedro
    
    3. PARA VER ACCIONES SOBRE UN USUARIO:
       - get_user_history(user_id) → acciones que HIZO el usuario
       - get_actions_on_user(user_id) → acciones que se HICIERON SOBRE el usuario
       - get_admin_activity(admin_id) → acciones que HIZO el administrador
    """

    @staticmethod
    def log_action(
        user_id: int, 
        action: str, 
        details: Optional[str | Dict[str, Any]] = None, 
        admin_id: Optional[int] = None
    ) -> UserHistory:
        """
        Registra una acción en el historial del usuario.
        
        Args:
            user_id: ID del usuario afectado por la acción
            action: Tipo de acción realizada (ver ACTIONS para valores válidos)
            details: Detalles adicionales de la acción (string o dict)
            admin_id: ID del administrador que realizó la acción (opcional, usa current_user si no se especifica)
            
        Returns:
            UserHistory: La entrada de historial creada

        Raises:
            ValueError: Si el action no es válido o el user_id no existe

        ADVERTENCIA — admin_id=None NO significa "sin administrador".
        Si hay una sesión activa, el bloque de abajo lo rellena con
        current_user.id. Para una acción que el propio usuario ejecuta sobre sí
        mismo eso deja admin_id == user_id, es decir "fue su propio
        administrador", que es justo lo contrario de lo que esa columna
        comunica. Todo flujo que necesite un NULL real debe registrarse fuera
        de user_history (ver AuthAuditService) o pasar por aquí sabiendo esto.
        No se corrige en este cambio porque varias decenas de llamadas ya
        dependen del comportamiento actual.
        """
        # Validar que el usuario existe
        user = User.query.get(user_id)
        if not user:
            raise ValueError(f"Usuario con ID {user_id} no existe")
        
        # Validar acción
        if action not in UserHistoryService.ACTIONS:
            raise ValueError(f"Acción '{action}' no es válida. Acciones permitidas: {list(UserHistoryService.ACTIONS.keys())}")
        
        # Convertir details a JSON si es un diccionario
        if isinstance(details, dict):
            details = json.dumps(details, ensure_ascii=False)
        
        # Usar current_user si no se especifica admin_id
        if admin_id is None and current_user.is_authenticated:
            admin_id = current_user.id
        
        # Crear entrada de historial
        history_entry = UserHistory(
            user_id=user_id,
            admin_id=admin_id,
            action=action,
            details=details
        )
        
        db.session.add(history_entry)
        return history_entry

    @staticmethod
    def log_password_reset(user_id: int, admin_id: Optional[int] = None) -> UserHistory:
        """Registra un reset de contraseña - SE GUARDA EN EL HISTORIAL DEL ADMINISTRADOR"""
        # Obtener información del usuario afectado
        user = User.query.get(user_id)
        user_name = f"{user.first_name} {user.last_name}" if user else f"Usuario {user_id}"
        
        admin_name = "Sistema"
        if admin_id:
            admin = User.query.get(admin_id)
            if admin:
                admin_name = f"{admin.first_name} {admin.last_name}"
        elif current_user.is_authenticated:
            admin_name = f"{current_user.first_name} {current_user.last_name}"
            admin_id = current_user.id
        
        details = {
            'affected_user_id': user_id,
            'affected_user_name': user_name,
            'reset_by': admin_name,
            'action_type': 'password_reset'
        }
        
        # ✅ CORRECTO: El historial se guarda en quien HACE la acción (el administrador)
        history = UserHistoryService.log_action(
            user_id=admin_id or 1,  # Historial del ADMIN (o admin sistema si no hay admin_id)
            action='password_reset',
            details=details,
            admin_id=None  # Es su propia acción
        )
        
        # ✅ CORRECTO: La notificación se envía a quien se AFECTA (el usuario)
        NotificationService.notify_password_reset(user_id)
        
        return history

    @staticmethod
    def log_password_change(user_id: int, changed_by_user: bool = True) -> UserHistory:
        """Registra un cambio de contraseña"""
        details = "Contraseña cambiada por el usuario" if changed_by_user else "Contraseña cambiada por administrador"
        
        return UserHistoryService.log_action(
            user_id=user_id,
            action='password_changed',
            details=details,
            admin_id=None if changed_by_user else (current_user.id if current_user.is_authenticated else None)
        )

    @staticmethod
    def log_user_activation(user_id: int, is_active: bool, admin_id: Optional[int] = None) -> UserHistory:
        """Registra activación/desactivación de usuario - SE GUARDA EN EL HISTORIAL DEL ADMINISTRADOR"""
        # Obtener información del usuario afectado
        user = User.query.get(user_id)
        user_name = f"{user.first_name} {user.last_name}" if user else f"Usuario {user_id}"
        
        admin_name = "Sistema"
        if admin_id:
            admin = User.query.get(admin_id)
            if admin:
                admin_name = f"{admin.first_name} {admin.last_name}"
        elif current_user.is_authenticated:
            admin_name = f"{current_user.first_name} {current_user.last_name}"
            admin_id = current_user.id
        
        action = 'activated' if is_active else 'deactivated'
        status = 'activado' if is_active else 'desactivado'
        
        details = {
            'affected_user_id': user_id,
            'affected_user_name': user_name,
            'action_performed': status,
            'performed_by': admin_name,
            'action_type': 'user_activation'
        }
        
        # ✅ CORRECTO: El historial se guarda en quien HACE la acción (el administrador)
        history = UserHistoryService.log_action(
            user_id=admin_id or 1,  # Historial del ADMIN (o admin sistema si no hay admin_id)
            action=action,
            details=details,
            admin_id=None  # Es su propia acción
        )
        
        # ✅ CORRECTO: La notificación se envía a quien se AFECTA (el usuario)
        if not is_active:
            NotificationService.notify_account_deactivated(user_id)
        
        return history

    @staticmethod
    def log_control_number_assignment(
        user_id: int, 
        control_number: str, 
        old_username: str, 
        program_name: str, 
        admin_id: Optional[int] = None
    ) -> UserHistory:
        """Registra asignación de número de control - SE GUARDA EN EL HISTORIAL DEL ADMINISTRADOR"""
        # Obtener información del usuario afectado
        user = User.query.get(user_id)
        user_name = f"{user.first_name} {user.last_name}" if user else f"Usuario {user_id}"
        
        details = {
            'affected_user_id': user_id,
            'affected_user_name': user_name,
            'control_number': control_number,
            'old_username': old_username,
            'program': program_name,
            'action_type': 'control_number_assignment'
        }
        
        # ✅ CORRECTO: El historial se guarda en quien HACE la acción (el administrador)
        history = UserHistoryService.log_action(
            user_id=admin_id or current_user.id,  # Historial del ADMIN
            action='control_number_assigned',
            details=details,
            admin_id=None  # Es su propia acción
        )
        
        # ✅ CORRECTO: La notificación se envía a quien se AFECTA (el usuario)
        NotificationService.notify_control_number_assigned(user_id, control_number)
        
        return history

    @staticmethod
    def log_basic_info_update(user_id: int, changed_fields: Dict[str, Dict[str, str]], admin_id: Optional[int] = None) -> UserHistory:
        """Registra actualización de información básica"""
        if admin_id:
            # ✅ Si un admin actualiza info de otro usuario, el historial se guarda en el admin
            user = User.query.get(user_id)
            user_name = f"{user.first_name} {user.last_name}" if user else f"Usuario {user_id}"
            
            details = {
                'affected_user_id': user_id,
                'affected_user_name': user_name,
                'changed_fields': changed_fields,
                'action_type': 'basic_info_update'
            }
            
            return UserHistoryService.log_action(
                user_id=admin_id,  # Historial del ADMIN
                action='basic_info_updated',
                details=details,
                admin_id=None  # Es su propia acción
            )
        else:
            # ✅ Si el usuario actualiza su propia info, el historial se guarda en él
            return UserHistoryService.log_action(
                user_id=user_id,  # Historial del USUARIO
                action='basic_info_updated',
                details=changed_fields,
                admin_id=None  # Acción del usuario
            )

    @staticmethod
    def log_profile_completion(user_id: int) -> UserHistory:
        """Registra cuando un usuario completa su perfil"""
        return UserHistoryService.log_action(
            user_id=user_id,
            action='profile_completed',
            details="Perfil completado por el usuario",
            admin_id=None
        )

    @staticmethod
    def log_role_change(user_id: int, old_role: str, new_role: str, admin_id: Optional[int] = None) -> UserHistory:
        """Registra cambio de rol - SE GUARDA EN EL HISTORIAL DEL ADMINISTRADOR"""
        # Obtener información del usuario afectado
        user = User.query.get(user_id)
        user_name = f"{user.first_name} {user.last_name}" if user else f"Usuario {user_id}"
        
        details = {
            'affected_user_id': user_id,
            'affected_user_name': user_name,
            'old_role': old_role,
            'new_role': new_role,
            'action_type': 'role_change'
        }
        
        # ✅ CORRECTO: El historial se guarda en quien HACE la acción (el administrador)
        return UserHistoryService.log_action(
            user_id=admin_id or current_user.id,  # Historial del ADMIN
            action='role_changed',
            details=details,
            admin_id=None  # Es su propia acción
        )

    @staticmethod
    def log_user_creation(user_id: int, created_by_admin: bool = False, admin_id: Optional[int] = None) -> UserHistory:
        """Registra creación de usuario"""
        details = "Usuario creado por administrador" if created_by_admin else "Usuario registrado en el sistema"
        
        return UserHistoryService.log_action(
            user_id=user_id,
            action='created',
            details=details,
            admin_id=admin_id if created_by_admin else None
        )

    @staticmethod
    def log_user_deletion(user_id: int, user_name: str, admin_id: Optional[int] = None) -> UserHistory:
        """Registra eliminación de usuario - SE GUARDA EN EL HISTORIAL DEL ADMINISTRADOR (se debe llamar ANTES de eliminar)"""
        admin_name = "Sistema"
        if admin_id:
            admin = User.query.get(admin_id)
            if admin:
                admin_name = f"{admin.first_name} {admin.last_name}"
        elif current_user.is_authenticated:
            admin_name = f"{current_user.first_name} {current_user.last_name}"
            admin_id = current_user.id
        
        details = {
            'deleted_user_id': user_id,
            'deleted_user_name': user_name,
            'deleted_by': admin_name,
            'action_type': 'user_deletion'
        }
        
        # ✅ CORRECTO: El historial se guarda en quien HACE la acción (el administrador)
        return UserHistoryService.log_action(
            user_id=admin_id or 1,  # Historial del ADMIN (o admin sistema si no hay admin_id)
            action='deleted',
            details=details,
            admin_id=None  # Es su propia acción
        )

    @staticmethod
    def get_user_history(
        user_id: int, 
        limit: Optional[int] = None, 
        action_filter: Optional[str] = None,
        order_by: Optional[str] = None
    ) -> List[UserHistory]:
        """
        Obtiene el historial de un usuario.
        
        Args:
            user_id: ID del usuario
            limit: Número máximo de entradas a retornar
            action_filter: Filtrar por tipo de acción específica
            
        Returns:
            Lista de entradas del historial ordenadas por fecha (más reciente primero)
        """
        query = UserHistory.query.filter_by(user_id=user_id)
        if order_by:
            if order_by.lower() == 'desc':
                query = query.order_by(UserHistory.timestamp.desc())
            else:
                query = query.order_by(UserHistory.timestamp.asc())

        if action_filter:
            query = query.filter(UserHistory.action == action_filter)
        
        query = query.order_by(UserHistory.timestamp.desc())
        
        if limit:
            query = query.limit(limit)
        
        return query.all()

    @staticmethod
    def get_recent_activity(limit: int = 50) -> List[UserHistory]:
        """
        Obtiene la actividad reciente de todos los usuarios.
        
        Args:
            limit: Número máximo de entradas a retornar
            
        Returns:
            Lista de entradas del historial ordenadas por fecha (más reciente primero)
        """
        return UserHistory.query.order_by(UserHistory.timestamp.desc()).limit(limit).all()

    @staticmethod
    def get_admin_activity(admin_id: int, limit: Optional[int] = None) -> List[UserHistory]:
        """
        Obtiene todas las acciones realizadas por un administrador específico.
        
        Args:
            admin_id: ID del administrador
            limit: Número máximo de entradas a retornar
            
        Returns:
            Lista de entradas del historial ordenadas por fecha (más reciente primero)
        """
        query = UserHistory.query.filter_by(user_id=admin_id).order_by(UserHistory.timestamp.desc())
        
        if limit:
            query = query.limit(limit)
        
        return query.all()

    @staticmethod
    def get_actions_on_user(target_user_id: int, limit: Optional[int] = None) -> List[UserHistory]:
        """
        Obtiene todas las acciones administrativas realizadas SOBRE un usuario específico.
        Útil para que los administradores vean qué acciones se han realizado sobre un estudiante.
        
        Args:
            target_user_id: ID del usuario sobre el cual se realizaron acciones
            limit: Número máximo de entradas a retornar
            
        Returns:
            Lista de entradas del historial donde se menciona al usuario en los details
        """
        from sqlalchemy import or_, and_
        
        # Buscar en los detalles JSON donde se mencione al usuario
        query = UserHistory.query.filter(
            or_(
                UserHistory.details.like(f'%"student_id": {target_user_id}%'),
                UserHistory.details.like(f'%"affected_user_id": {target_user_id}%'),
                UserHistory.details.like(f'%"deleted_user_id": {target_user_id}%'),
                # También incluir acciones directas del usuario (para contexto completo)
                UserHistory.user_id == target_user_id
            )
        ).order_by(UserHistory.timestamp.desc())
        
        if limit:
            query = query.limit(limit)
        
        return query.all()

    # ==================== POSTULACIONES Y PROGRAMAS ====================
    @staticmethod
    def log_program_enrollment(user_id: int, program_name: str, program_id: int) -> 'UserHistory':
        """Registra postulación a un programa"""
        details = {
            'program_id': program_id,
            'program_name': program_name,
            'action_type': 'enrollment'
        }
        return UserHistoryService.log_action(
            user_id=user_id,
            action='program_enrolled',
            details=details,
            admin_id=None  # Acción del usuario
        )

    @staticmethod
    def log_program_transfer_request(user_id: int, from_program: str, to_program: str, reason: str) -> 'UserHistory':
        """Registra solicitud de cambio de programa"""
        details = {
            'from_program': from_program,
            'to_program': to_program,
            'reason': reason,
            'action_type': 'transfer_request'
        }
        return UserHistoryService.log_action(
            user_id=user_id,
            action='program_transfer_requested',
            details=details,
            admin_id=None
        )

    @staticmethod
    def log_program_transfer_execution(user_id: int, from_program: str, to_program: str, 
                                     documents_moved: int, documents_lost: int) -> 'UserHistory':
        """Registra ejecución de cambio de programa"""
        details = {
            'from_program': from_program,
            'to_program': to_program,
            'documents_moved': documents_moved,
            'documents_lost': documents_lost,
            'action_type': 'transfer_executed'
        }
        history = UserHistoryService.log_action(
            user_id=user_id,
            action='program_transferred',
            details=details,
            admin_id=None
        )
        
        # NUEVO: Crear notificación
        NotificationService.notify_program_changed(user_id, from_program, to_program)
        
        return history

    # ==================== DOCUMENTOS ====================
    @staticmethod
    def log_document_upload(user_id: int, archive_name: str, program_name: str, 
                          uploaded_by_admin: bool = False, admin_id: int = None, 
                          coordinator_name: str = None, submission_id: int = None) -> 'UserHistory':
        """Registra subida de documento"""
        details = {
            'archive_name': archive_name,
            'program_name': program_name,
            'uploaded_by_admin': uploaded_by_admin,
            'action_type': 'document_upload'
        }
        
        if uploaded_by_admin:
            # ✅ CORRECTO: Si lo sube un admin, el historial se guarda en el admin
            student = User.query.get(user_id)
            student_name = f"{student.first_name} {student.last_name}" if student else f"Usuario {user_id}"
            
            details['student_id'] = user_id
            details['student_name'] = student_name
            
            history = UserHistoryService.log_action(
                user_id=admin_id or current_user.id,  # Historial del ADMIN
                action='document_uploaded',
                details=details,
                admin_id=None  # Es su propia acción
            )
            
            # ✅ CORRECTO: Notificar al estudiante que el coordinador subió su documento
            if coordinator_name and submission_id:
                NotificationService.notify_coordinator_uploaded(user_id, archive_name, submission_id, coordinator_name)
        else:
            # ✅ CORRECTO: Si lo sube el usuario, el historial se guarda en el usuario
            history = UserHistoryService.log_action(
                user_id=user_id,  # Historial del USUARIO
                action='document_uploaded',
                details=details,
                admin_id=None  # Acción del usuario
            )
        
        return history

    @staticmethod
    def log_document_deletion(user_id: int, archive_name: str, program_name: str, 
                            deleted_by_admin: bool = False, admin_id: int = None) -> 'UserHistory':
        """Registra eliminación de documento"""
        details = {
            'archive_name': archive_name,
            'program_name': program_name,
            'deleted_by_admin': deleted_by_admin,
            'action_type': 'document_deletion'
        }
        
        if deleted_by_admin:
            # ✅ CORRECTO: Si lo elimina un admin, el historial se guarda en el admin
            student = User.query.get(user_id)
            student_name = f"{student.first_name} {student.last_name}" if student else f"Usuario {user_id}"
            
            details['student_id'] = user_id
            details['student_name'] = student_name
            
            return UserHistoryService.log_action(
                user_id=admin_id or current_user.id,  # Historial del ADMIN
                action='document_deleted',
                details=details,
                admin_id=None  # Es su propia acción
            )
        else:
            # ✅ CORRECTO: Si lo elimina el usuario, el historial se guarda en el usuario
            return UserHistoryService.log_action(
                user_id=user_id,  # Historial del USUARIO
                action='document_deleted',
                details=details,
                admin_id=None  # Acción del usuario
            )

    @staticmethod
    def log_document_review(user_id: int, archive_name: str, status: str, 
                          reviewer_comment: str = None, admin_id: int = None, submission_id: int = None) -> 'UserHistory':
        """Registra revisión de documento (aprobar/rechazar) - SE GUARDA EN EL HISTORIAL DEL REVISOR"""
        # Obtener información del estudiante afectado
        student = User.query.get(user_id)
        student_name = f"{student.first_name} {student.last_name}" if student else f"Usuario {user_id}"
        
        details = {
            'student_id': user_id,
            'student_name': student_name,
            'archive_name': archive_name,
            'review_status': status,
            'reviewer_comment': reviewer_comment,
            'action_type': 'document_review'
        }
        
        # ✅ CORRECTO: El historial se guarda en quien HACE la acción (el revisor/admin)
        history = UserHistoryService.log_action(
            user_id=admin_id or current_user.id,  # Historial del REVISOR
            action='document_reviewed',
            details=details,
            admin_id=None  # Es su propia acción
        )
        
        # ✅ CORRECTO: La notificación se envía a quien se AFECTA (el estudiante)
        if status == 'approved':
            NotificationService.notify_document_approved(user_id, archive_name, submission_id)
        elif status == 'rejected':
            NotificationService.notify_document_rejected(user_id, archive_name, submission_id, reviewer_comment)
        
        return history

    # ==================== PRÓRROGAS ====================
    @staticmethod
    def log_extension_request(user_id: int, archive_name: str, requested_until: str, 
                            reason: str, requested_by_admin: bool = False, admin_id: int = None) -> 'UserHistory':
        """Registra solicitud de prórroga"""
        details = {
            'archive_name': archive_name,
            'requested_until': requested_until,
            'reason': reason,
            'requested_by_admin': requested_by_admin,
            'action_type': 'extension_request'
        }
        return UserHistoryService.log_action(
            user_id=user_id,
            action='extension_requested',
            details=details,
            admin_id=admin_id if requested_by_admin else None
        )

    @staticmethod
    def log_extension_decision(user_id: int, archive_name: str, decision: str, 
                             granted_until: str = None, condition_text: str = None, admin_id: int = None) -> 'UserHistory':
        """Registra decisión sobre prórroga - SE GUARDA EN EL HISTORIAL DEL ADMINISTRADOR"""
        # Obtener información del estudiante afectado
        student = User.query.get(user_id)
        student_name = f"{student.first_name} {student.last_name}" if student else f"Usuario {user_id}"
        
        details = {
            'student_id': user_id,
            'student_name': student_name,
            'archive_name': archive_name,
            'decision': decision,
            'granted_until': granted_until,
            'condition_text': condition_text,
            'action_type': 'extension_decision'
        }
        
        # ✅ CORRECTO: El historial se guarda en quien HACE la acción (el administrador)
        history = UserHistoryService.log_action(
            user_id=admin_id or current_user.id,  # Historial del ADMIN
            action='extension_decided',
            details=details,
            admin_id=None  # Es su propia acción
        )
        
        # ✅ CORRECTO: La notificación se envía a quien se AFECTA (el estudiante)
        if decision == 'granted':  # ✅ CORREGIDO: usar 'granted' no 'approved'
            try:
                from flask import current_app
                current_app.logger.info(f"Enviando notificación de prórroga aprobada a usuario {user_id} para {archive_name}")
                NotificationService.notify_extension_approved(user_id, archive_name, granted_until)
            except Exception as e:
                # Log del error pero no fallar la operación
                from flask import current_app
                current_app.logger.error(f"Error al enviar notificación de prórroga aprobada: {e}")
        elif decision == 'rejected':
            try:
                from flask import current_app
                current_app.logger.info(f"Enviando notificación de prórroga rechazada a usuario {user_id} para {archive_name}")
                NotificationService.notify_extension_rejected(user_id, archive_name, condition_text)
            except Exception as e:
                # Log del error pero no fallar la operación
                from flask import current_app
                current_app.logger.error(f"Error al enviar notificación de prórroga rechazada: {e}")
        
        return history

    # ==================== EVENTOS Y CITAS ====================
    @staticmethod
    def log_event_registration(user_id: int, event_title: str, event_type: str) -> 'UserHistory':
        """Registra registro a evento"""
        details = {
            'event_title': event_title,
            'event_type': event_type,
            'action_type': 'event_registration'
        }
        return UserHistoryService.log_action(
            user_id=user_id,
            action='event_registered',
            details=details,
            admin_id=None
        )

    @staticmethod
    def log_appointment_assignment(user_id: int, event_title: str, appointment_datetime: str, 
                                 assigned_by_admin: int = None, appointment_id: int = None, 
                                 event_id: int = None) -> 'UserHistory':
        """Registra asignación de cita - SE GUARDA EN EL HISTORIAL DEL ADMINISTRADOR"""
        # Obtener información del estudiante afectado
        student = User.query.get(user_id)
        student_name = f"{student.first_name} {student.last_name}" if student else f"Usuario {user_id}"
        
        details = {
            'student_id': user_id,
            'student_name': student_name,
            'event_title': event_title,
            'appointment_datetime': appointment_datetime,
            'appointment_id': appointment_id,
            'event_id': event_id,
            'action_type': 'appointment_assignment'
        }
        
        # ✅ CORRECTO: El historial se guarda en quien HACE la acción (el administrador)
        return UserHistoryService.log_action(
            user_id=assigned_by_admin or current_user.id,  # Historial del ADMIN
            action='appointment_assigned',
            details=details,
            admin_id=None  # Es su propia acción
        )

    @staticmethod
    def log_appointment_cancellation(user_id: int, event_title: str, reason: str, 
                                   cancelled_by_admin: bool = False, admin_id: int = None) -> 'UserHistory':
        """Registra cancelación de cita"""
        details = {
            'event_title': event_title,
            'reason': reason,
            'cancelled_by_admin': cancelled_by_admin,
            'action_type': 'appointment_cancellation'
        }
        
        if cancelled_by_admin:
            # ✅ CORRECTO: Si lo cancela un admin, el historial se guarda en el admin
            student = User.query.get(user_id)
            student_name = f"{student.first_name} {student.last_name}" if student else f"Usuario {user_id}"
            
            details['student_id'] = user_id
            details['student_name'] = student_name
            
            history = UserHistoryService.log_action(
                user_id=admin_id or current_user.id,  # Historial del ADMIN
                action='appointment_cancelled',
                details=details,
                admin_id=None  # Es su propia acción
            )
        else:
            # ✅ CORRECTO: Si lo cancela el usuario, el historial se guarda en el usuario
            history = UserHistoryService.log_action(
                user_id=user_id,  # Historial del USUARIO
                action='appointment_cancelled',
                details=details,
                admin_id=None  # Acción del usuario
            )
        
        # ✅ CORRECTO: La notificación se envía apropiadamente
        if cancelled_by_admin:
            # Si lo canceló un admin, notificar al estudiante
            NotificationService.notify_appointment_cancelled(user_id, event_title, reason)
        # Si lo canceló el usuario, no hay necesidad de notificación (él ya sabe)
        
        return history

    # NUEVO método para invitaciones a eventos
    @staticmethod
    def log_event_invitation(user_id: int, event_title: str, event_id: int, 
                            invitation_id: int, event_date: str, invited_by: int = None) -> 'UserHistory':
        """Registra invitación a evento - SE GUARDA EN EL HISTORIAL DEL ADMINISTRADOR"""
        # Obtener información del usuario afectado
        student = User.query.get(user_id)
        student_name = f"{student.first_name} {student.last_name}" if student else f"Usuario {user_id}"
        
        details = {
            'student_id': user_id,
            'student_name': student_name,
            'event_title': event_title,
            'event_id': event_id,
            'invitation_id': invitation_id,
            'event_date': event_date,
            'action_type': 'event_invitation'
        }
        
        # ✅ CORRECTO: El historial se guarda en quien HACE la acción (el administrador)
        return UserHistoryService.log_action(
            user_id=invited_by or current_user.id,  # Historial del ADMIN
            action='event_invited',
            details=details,
            admin_id=None  # Es su propia acción
        )

    # ==================== GESTIÓN DE ARCHIVOS (ARCHIVES) ====================
    
    @staticmethod
    def log_archive_created(admin_id: int, archive_name: str, step_name: str) -> 'UserHistory':
        """Registra creación de un nuevo archivo/documento - SE GUARDA EN EL HISTORIAL DEL ADMINISTRADOR"""
        details = {
            'archive_name': archive_name,
            'step_name': step_name,
            'action_type': 'archive_creation'
        }
        
        # ✅ CORRECTO: El historial se guarda en quien HACE la acción (el administrador)
        return UserHistoryService.log_action(
            user_id=admin_id,  # Historial del ADMIN
            action='archive_created',
            details=details,
            admin_id=None  # Es su propia acción
        )

    @staticmethod
    def log_archive_updated(admin_id: int, archive_name: str, step_name: str, changes: dict) -> 'UserHistory':
        """Registra actualización de archivo/documento - SE GUARDA EN EL HISTORIAL DEL ADMINISTRADOR"""
        details = {
            'archive_name': archive_name,
            'step_name': step_name,
            'changes': changes,
            'action_type': 'archive_update'
        }
        
        # ✅ CORRECTO: El historial se guarda en quien HACE la acción (el administrador)
        return UserHistoryService.log_action(
            user_id=admin_id,  # Historial del ADMIN
            action='archive_updated',
            details=details,
            admin_id=None  # Es su propia acción
        )

    @staticmethod
    def log_archive_deleted(admin_id: int, archive_name: str, archive_description: str, force_used: bool) -> 'UserHistory':
        """Registra eliminación de archivo/documento - SE GUARDA EN EL HISTORIAL DEL ADMINISTRADOR"""
        details = {
            'archive_name': archive_name,
            'archive_description': archive_description,
            'force_used': force_used,
            'action_type': 'archive_deletion'
        }
        
        # ✅ CORRECTO: El historial se guarda en quien HACE la acción (el administrador)
        return UserHistoryService.log_action(
            user_id=admin_id,  # Historial del ADMIN
            action='archive_deleted',
            details=details,
            admin_id=None  # Es su propia acción
        )

    @staticmethod
    def log_template_uploaded(admin_id: int, archive_name: str, template_filename: str, was_replacement: bool) -> 'UserHistory':
        """Registra subida de plantilla para archivo - SE GUARDA EN EL HISTORIAL DEL ADMINISTRADOR"""
        details = {
            'archive_name': archive_name,
            'template_filename': template_filename,
            'was_replacement': was_replacement,
            'action_type': 'template_upload'
        }
        
        # ✅ CORRECTO: El historial se guarda en quien HACE la acción (el administrador)
        return UserHistoryService.log_action(
            user_id=admin_id,  # Historial del ADMIN
            action='template_uploaded',
            details=details,
            admin_id=None  # Es su propia acción
        )

    # ==================== RETENCIÓN Y LIMPIEZA ====================
    @staticmethod
    def log_document_purged(user_id: int, archive_name: str, reason: str, admin_id: int = None) -> 'UserHistory':
        """Registra eliminación de documento por políticas de retención"""
        details = {
            'archive_name': archive_name,
            'reason': reason,
            'action_type': 'document_purged'
        }
        return UserHistoryService.log_action(
            user_id=user_id,
            action='document_purged',
            details=details,
            admin_id=admin_id
        )

    # ==================== CRITERIOS PARA ACCIONES CRÍTICAS ====================
    
    # Acciones críticas que NUNCA se deben eliminar (tienen implicaciones legales/académicas)
    CRITICAL_ACTIONS = {
        # Gestión académica crítica
        'control_number_assigned',  # Asignación oficial de matrícula
        'program_transferred',      # Cambios de programa oficiales
        'program_enrolled',         # Inscripciones iniciales
        
        # Decisiones administrativas importantes
        'extension_decided',        # Decisiones de prórrogas (pueden ser legales)
        'document_purged',          # Eliminaciones por retención (auditoría)
        'role_changed',            # Cambios de permisos/acceso
        
        # Acciones irreversibles del sistema
        'deleted',                 # Eliminaciones de usuarios
        'document_reviewed',       # Decisiones de revisión de documentos
        'purge_run_confirmed',     # Purga física post-respaldo (irreversible)
        
        # Seguridad y acceso
        'deactivated',            # Desactivaciones (pueden ser disciplinarias)
        'activated'               # Reactivaciones importantes
    }
    
    # Acciones de alta importancia (conservar por más tiempo)
    HIGH_IMPORTANCE_ACTIONS = {
        'password_reset',         # Importante para seguridad
        'basic_info_updated',     # Cambios de datos personales
        'document_uploaded',      # Historial de documentación
        'document_deleted',       # Eliminaciones de documentos
        'appointment_assigned',   # Asignaciones de citas importantes
        'program_transfer_requested',  # Solicitudes de cambio
        'archive_created',        # Creación de archivos del sistema
        'archive_updated',        # Modificaciones de configuración
        'archive_deleted',        # Eliminaciones de archivos del sistema
        'template_uploaded'       # Subida de plantillas
    }
    
    # Acciones de importancia media (limpieza normal)
    MEDIUM_IMPORTANCE_ACTIONS = {
        'password_changed',       # Cambios rutinarios
        'profile_updated',        # Actualizaciones de perfil
        'profile_completed',      # Completar perfil
        'event_registered',       # Registros a eventos
        'extension_requested',    # Solicitudes de prórroga
        'appointment_cancelled'   # Cancelaciones de citas
    }

    # Constantes para los tipos de acciones válidas
    ACTIONS = {
        # Acciones originales (USER ACTIONS - se guardan en el historial del usuario)
        'password_changed': 'Contraseña cambiada',
        'profile_updated': 'Perfil actualizado',
        'created': 'Usuario creado',
        'profile_completed': 'Perfil completado',
        
        # Acciones del Usuario - Programas (se guardan en el historial del usuario)
        'program_enrolled': 'Postulado a programa',
        'program_transfer_requested': 'Solicitud de cambio de programa',
        'program_transferred': 'Cambio de programa ejecutado',
        
        # Acciones del Usuario - Documentos (se guardan en el historial del usuario)
        'document_uploaded': 'Documento subido (por usuario)',
        'document_deleted': 'Documento eliminado (por usuario)',
        
        # Acciones del Usuario - Prórrogas (se guardan en el historial del usuario)
        'extension_requested': 'Prórroga solicitada',
        
        # Acciones del Usuario - Eventos (se guardan en el historial del usuario)
        'event_registered': 'Registrado a evento',
        'appointment_cancelled': 'Cita cancelada (por usuario)',
        
        # ACCIONES ADMINISTRATIVAS (se guardan en el historial del administrador)
        'password_reset': 'Reseteó contraseña de usuario',
        'deactivated': 'Desactivó usuario',
        'activated': 'Activó usuario',
        'control_number_assigned': 'Asignó número de control',
        'role_changed': 'Cambió rol de usuario',
        'deleted': 'Eliminó usuario',
        'basic_info_updated': 'Actualizó información de usuario',
        'document_reviewed': 'Revisó documento de estudiante',
        'extension_decided': 'Decidió sobre prórroga',
        'appointment_assigned': 'Asignó cita a estudiante',
        'event_invited': 'Invitó estudiante a evento',
        'event_concluded': 'Concluyó evento',
        'event_archived': 'Archivó evento',
        'event_unarchived': 'Reactivó evento archivado',
        'document_purged': 'Eliminó documento por retención',
        
        # ACCIONES ADMINISTRATIVAS - Gestión de Archivos (se guardan en el historial del administrador)
        'archive_created': 'Creó nuevo archivo/documento',
        'archive_updated': 'Modificó configuración de archivo',
        'archive_deleted': 'Eliminó archivo/documento',
        'template_uploaded': 'Subió plantilla para archivo',

        # ACCIONES DE DELIBERACIÓN (se guardan en el historial del aspirante/coordinador)
        'deliberation_started': 'Inició proceso de deliberación',
        'admission_accepted': 'Aceptó aspirante al programa',
        'admission_rejected': 'Rechazó aspirante del programa',
        'correction_requested': 'Solicitó correcciones al aspirante',
        'admission_reset': 'Reinició estado de admisión a en proceso',

        # ACCIONES DE ACEPTACIÓN E INSCRIPCIÓN (Fase 4)
        'acceptance_docs_uploaded': 'Subió carta de aceptación y tira de materias',
        'acceptance_doc_deleted': 'Eliminó documento de aceptación',
        'enrollment_receipt_submitted': 'Subió carta de asignación de número de control',
        'enrollment_receipt_approved': 'Aprobó carta de asignación de número de control',
        'enrollment_receipt_rejected': 'Rechazó carta de asignación de número de control',

        # ACCIONES DE PERMANENCIA SEMESTRAL (Fase 6)
        'semester_enrollment_confirmed': 'Confirmó inscripción semestral del estudiante',
        'semester_enrollment_status_updated': 'Actualizó estado de inscripción semestral',
        'semester_enrollment_reinstated': 'Reincorporó estudiante de baja temporal',

        # ACCIONES DE TRANSICIÓN SEMESTRAL ("Pasar Semestre")
        'semester_advanced': 'Avanzó al siguiente semestre',
        'admission_period_migrated': 'Migró el periodo de admisión al siguiente',
        'admission_expired': 'Expiró el proceso de admisión por antigüedad',
        'deferral_reactivated_by_transition': 'Reactivó diferimiento durante transición semestral',
        'payment_proof_uploaded': 'Subió comprobante de pago de inscripción',

        # ACCIONES DE PURGA Y RESPALDO ZIP (Sección 15)
        'purge_run_created':   'Generó respaldo ZIP previo a purga',
        'purge_run_downloaded':'Descargó respaldo ZIP de purga',
        'purge_run_confirmed': 'Confirmó purga física post-respaldo',
        'purge_run_cancelled': 'Canceló respaldo ZIP de purga',

        # ACCIONES DEL SISTEMA DE PERMISOS GRANULARES (Fase 7-8)
        'social_service_created': 'Creó usuario de servicio social con delegación',
        'permission_delegated': 'Delegó permiso a usuario',
        'permission_revoked': 'Revocó delegación de permiso',
        'role_override_granted': 'Agregó override de permiso a rol',
        'role_override_reverted': 'Revirtió override de permiso de rol',

        # ALTA MASIVA DE ESTUDIANTES (student_bulk_service)
        'enrolled_via_bulk_import': 'Alta masiva — inscribió estudiante existente',

        # ACEPTACIÓN — documentos individuales (Sprint 1)
        'acceptance_acceptance_letter_uploaded': 'Subió carta de aceptación',
        'acceptance_course_schedule_uploaded':   'Subió tira de materias',

        # DELIBERACIÓN
        'admission_accepted_conditional': 'Aceptación condicionada (con dictamen)',
        'interview_completed':            'Marcó entrevista como completada',

        # PERMANENCIA — coordinación
        'document_deadline_created':   'Creó ventana de entrega de documento',
        'conacyt_scholarship_changed': 'Activó/desactivó beca CONACyT',

        # PERFIL — foto
        'profile_photo_uploaded':         'Subió foto de perfil',
        'profile_photo_change_requested': 'Solicitó cambio de foto de perfil',
        'profile_photo_change_enabled':   'Habilitó cambio de foto de perfil',
        'profile_photo_change_rejected':  'Rechazó solicitud de cambio de foto',

        # EXPEDIENTE COMPLETO — edición por coordinador
        'personal_info_updated': 'Actualizó información personal del estudiante',

        # PERMANENCIA — ventanas de entrega y submissions
        'deadline_opened':                'Abrió ventana de entrega',
        'deadline_closed':                'Cerró ventana de entrega',
        'deadline_deleted':               'Eliminó ventana de entrega',
        'deadline_archived':              'Archivó ventana de entrega',
        'deadline_restored':              'Restauró ventana de entrega archivada',
        'deadline_updated':               'Actualizó ventana de entrega',
        'permanence_document_submitted':  'Subió documento de permanencia',
        'permanence_document_approved':   'Aprobó documento de permanencia',
        'permanence_document_rejected':   'Rechazó documento de permanencia',
        'leave_request_rejected':         'Rechazó solicitud de baja temporal',
        'conacyt_deadlines_created':      'Creó ventanas mensuales CONACyT',
        'leave_request_submitted':        'Solicitó baja temporal',
        'leave_request_approved':         'Aprobó solicitud de baja temporal',

        # DIFERIMIENTOS
        'enrollment_deferred':            'Diferimiento aplicado',
        'deferral_requested':             'Solicitó diferimiento',
        'deferral_rejected':              'Rechazó solicitud de diferimiento',
        'deferral_reactivated':           'Reactivó aspirante diferido',

        # CONVOCATORIAS
        'admission_interest_registered':  'Pidió aviso de convocatoria',

        # AUTENTICACIÓN — NO VAN AQUÍ.
        # Los eventos de inicio de sesión se registran en la tabla `log` a
        # través de AuthAuditService (app/services/auth_audit_service.py).
        # No los vuelvas a agregar a este catálogo: user_history se muestra en
        # las consolas de coordinación/administración y se serializa completo
        # en User.to_dict(include_sensitive=True), su admin_id se rellena solo
        # desde current_user (un login quedaría como "su propio administrador")
        # y su volumen aquí lo controlaría una petición no autenticada.
    }

    @staticmethod
    def get_action_label(action: str) -> str:
        """
        Obtiene la etiqueta legible para una acción.
        
        Args:
            action: Código de la acción
            
        Returns:
            Etiqueta legible de la acción
        """
        return UserHistoryService.ACTIONS.get(action, action)

    @staticmethod
    def is_critical_action(action: str) -> bool:
        """
        Determina si una acción es crítica y nunca debe eliminarse.
        
        Args:
            action: Código de la acción
            
        Returns:
            True si es crítica, False en caso contrario
        """
        return action in UserHistoryService.CRITICAL_ACTIONS

    @staticmethod
    def get_action_importance_level(action: str) -> str:
        """
        Obtiene el nivel de importancia de una acción.
        
        Args:
            action: Código de la acción
            
        Returns:
            'critical', 'high', 'medium', o 'low'
        """
        if action in UserHistoryService.CRITICAL_ACTIONS:
            return 'critical'
        elif action in UserHistoryService.HIGH_IMPORTANCE_ACTIONS:
            return 'high'
        elif action in UserHistoryService.MEDIUM_IMPORTANCE_ACTIONS:
            return 'medium'
        else:
            return 'low'

    @staticmethod
    def get_retention_policy_for_action(action: str) -> dict:
        """
        Obtiene la política de retención recomendada para una acción.
        
        Args:
            action: Código de la acción
            
        Returns:
            Diccionario con la política de retención
        """
        importance = UserHistoryService.get_action_importance_level(action)
        
        policies = {
            'critical': {
                'retention_years': -1,  # Permanente
                'description': 'Conservar permanentemente por implicaciones legales/académicas'
            },
            'high': {
                'retention_years': 10,
                'description': 'Conservar 10 años por alta importancia'
            },
            'medium': {
                'retention_years': 5,
                'description': 'Conservar 5 años por importancia media'
            },
            'low': {
                'retention_years': 2,
                'description': 'Conservar 2 años por baja importancia'
            }
        }
        
        return policies.get(importance, policies['low'])