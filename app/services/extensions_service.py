# app/services/extensions_service.py - Actualizado
from datetime import datetime, timezone
from app.utils.datetime_utils import now_local
from sqlalchemy.exc import IntegrityError
from app import db
from app.services import public_id_service
from app.models.extension_request import ExtensionRequest
from app.models.archive import Archive
from app.models.program_step import ProgramStep
from app.models.user_program import UserProgram

class ExtensionsService:
    @staticmethod
    def create_request(user_id: int, archive_id: int, requested_by: int, reason: str, requested_until, role: str = 'student') -> ExtensionRequest:
        """
        Crea una solicitud de prórroga para un archivo específico.
        Ya no requiere que exista una submission previa.
        """
        # Validar que el archivo existe
        archive = db.session.get(Archive, archive_id)
        if not archive:
            raise ValueError("Archivo no encontrado")
        
        # Encontrar el program_step correspondiente al usuario
        # Asumimos que el usuario está inscrito en el programa donde está el step del archive
        user_program = UserProgram.query.filter_by(user_id=user_id).first()
        if not user_program:
            raise ValueError("Usuario no inscrito en ningún programa")
        
        program_step = ProgramStep.query.filter_by(
            program_id=user_program.program_id,
            step_id=archive.step_id
        ).first()
        
        if not program_step:
            raise ValueError("El archivo no pertenece al programa del usuario")
        
        # Verificar que no haya una solicitud pendiente para el mismo archivo
        existing = ExtensionRequest.query.filter_by(
            user_id=user_id,
            archive_id=archive_id,
            status='pending'
        ).first()
        
        if existing:
            raise ValueError("Ya tienes una solicitud pendiente para este archivo")
        
        er = ExtensionRequest(
            user_id=user_id,
            archive_id=archive_id,
            program_step_id=program_step.id,
            requested_by=requested_by,
            reason=reason,
            requested_until=requested_until,
            role=role
        )
        
        db.session.add(er)
        db.session.commit()

        # Notificar al coordinador del programa
        try:
            from app.services.notification_service import NotificationService
            from app.models.user import User
            from app.models.program import Program
            program = Program.query.get(user_program.program_id)
            if program and program.coordinator_id:
                user = User.query.get(user_id)
                student_name = f"{user.first_name} {user.last_name}" if user else f"Usuario {user_id}"
                NotificationService.create_notification(
                    user_id=program.coordinator_id,
                    notification_type='extension_request_submitted',
                    title='Nueva solicitud de prórroga',
                    message=f'{student_name} ha solicitado una prórroga para "{archive.name}" en {program.name}.',
                    priority='medium',
                    action_url='/coordinator/extensions',
                    data={'student_id': user_id, 'archive_id': archive_id, 'program_id': program.id},
                )
                db.session.commit()
        except Exception:
            pass

        return er

    @staticmethod
    def list_requests(user_id=None, archive_id=None, status=None, program_id=None):
        """Lista solicitudes de prórroga con filtros opcionales"""
        query = db.session.query(ExtensionRequest).join(Archive)
        
        if user_id:
            query = query.filter(ExtensionRequest.user_id == user_id)
        if archive_id:
            query = query.filter(ExtensionRequest.archive_id == archive_id)
        if status:
            query = query.filter(ExtensionRequest.status == status)
        if program_id:
            query = query.join(ProgramStep).filter(ProgramStep.program_id == program_id)
        
        return query.order_by(ExtensionRequest.created_at.desc()).all()

    @staticmethod
    def decide_request(request_id: int, status: str, decided_by: int, granted_until=None, condition_text=None) -> ExtensionRequest:
        """Decide sobre una solicitud de prórroga"""
        er = db.session.get(ExtensionRequest, request_id)
        if not er:
            raise ValueError("Solicitud de extensión no encontrada")

        if status not in ('granted', 'rejected', 'cancelled'):
            raise ValueError("Estado inválido")

        er.status = status
        er.decided_by = decided_by
        er.decided_at = now_local()
        er.updated_at = now_local()
        er.granted_until = granted_until
        er.condition_text = condition_text

        db.session.commit()

        # Notificar al solicitante + coordinadores del programa en tiempo real
        from app.sockets.emitters import emit_user_and_coordinators
        program_id = er.program_step.program_id if er.program_step else None
        emit_user_and_coordinators(
            'extension:decided',
            {
                'user_id': public_id_service.user_uuid(er.user_id),
                'extension_request_id': er.id,
                'archive_id': public_id_service.archive_uuid(er.archive_id),
                'program_id': program_id,
                'status': status,
                'granted_until': er.granted_until.isoformat() if er.granted_until else None,
            },
            user_id=er.user_id,
            program_id=program_id,
        )

        return er

    @staticmethod
    def get_active_extension(user_id: int, archive_id: int) -> ExtensionRequest | None:
        """Obtiene la prórroga activa (granted) para un usuario y archivo específico"""
        return ExtensionRequest.query.filter_by(
            user_id=user_id,
            archive_id=archive_id,
            status='granted'
        ).first()

    @staticmethod
    def has_pending_request(user_id: int, archive_id: int) -> bool:
        """Verifica si hay una solicitud pendiente para un archivo"""
        return db.session.query(
            ExtensionRequest.query.filter_by(
                user_id=user_id,
                archive_id=archive_id,
                status='pending'
            ).exists()
        ).scalar()

    @staticmethod
    def get_effective_deadline(user_id: int, archive_id: int) -> datetime | None:
        """
        Obtiene la fecha límite efectiva para un archivo.
        Si hay una prórroga granted, devuelve esa fecha.
        Si no, devuelve None (usar deadline por defecto del programa).
        """
        extension = ExtensionsService.get_active_extension(user_id, archive_id)
        return extension.granted_until if extension else None