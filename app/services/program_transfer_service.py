# app/services/program_transfer_service.py
"""
Cambio de programa del aspirante (flujo de autoservicio) y utilidades de
lectura de las solicitudes de cambio.

Reglas de negocio del autoservicio — se validan AQUÍ, no en la ruta, y por
tanto valen para cualquier llamador:

  1. El aspirante debe tener una inscripción (`UserProgram`) en el programa de
     ORIGEN. Sin ella no hay nada que mover y la barrida de documentos borraría
     archivos sin contrapartida.
  2. Su `admission_status` debe estar en `TRANSFERABLE_ADMISSION_STATUSES`.
     Un aspirante ya aceptado, en deliberación, rechazado, diferido o un alumno
     inscrito NO puede cambiarse solo: arrastraría su estatus, su número de
     control y su historial académico a un programa donde nadie lo evaluó.
  3. La convocatoria de admisión debe estar abierta hoy.
  4. El programa destino debe existir, estar activo y ser distinto al de origen,
     y el aspirante no debe tener ya un proceso allí.

La decisión formal de una solicitud (`ProgramChangeRequest.status`) es de un
coordinador: este flujo NUNCA la aprueba ni se auto-firma como decisor.
"""

from datetime import datetime, timezone
from app.utils.datetime_utils import now_local
from typing import Dict, Iterable, List, Optional, Tuple
from app import db
from app.services import public_id_service
from app.models.user_program import UserProgram
from app.models.program import Program
from app.models.program_change_request import ProgramChangeRequest
from app.models.submission import Submission
from app.models.archive import Archive
from app.models.program_step import ProgramStep
from app.models.step import Step
from app.models.appointment import Appointment
from app.models.event import Event, EventSlot, EventWindow
from sqlalchemy import select, or_
import os
from flask import current_app


#: Fase de admisión. El mapeo de archivos (`_get_program_archives`) sólo cubre
#: esta fase, así que toda barrida de submissions debe filtrar por ella.
ADMISSION_PHASE_ID = 1

#: Únicos estados desde los que el propio aspirante puede cambiarse de programa.
#: Es una lista blanca explícita: cualquier estado nuevo queda fuera por defecto.
#: - 'in_progress'          → sigue armando su expediente, aún no lo evalúa nadie.
#: - interview_completed / deliberation / accepted / rejected / deferred →
#:                           ya hay un dictamen o uno en curso; el cambio lo
#:                           mueve un coordinador con una solicitud decidida.
#: - 'enrolled' / 'expired' → ya no es un proceso de admisión.
TRANSFERABLE_ADMISSION_STATUSES = frozenset({'in_progress'})


class ProgramTransferError(Exception):
    """
    Regla de negocio del cambio de programa incumplida.

    `message` siempre viene en español porque llega tal cual al aspirante.
    """

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ProgramTransferNotFound(ProgramTransferError):
    """El objeto (inscripción, programa o solicitud) no existe para este usuario."""


class ProgramTransferNotAllowed(ProgramTransferError):
    """El objeto existe pero su estado no admite la operación."""


class ProgramTransferService:

    # ─── Validación del autoservicio ─────────────────────────────────────────

    @staticmethod
    def _get_user_program(user_id: int, program_id: int) -> Optional[UserProgram]:
        """Inscripción del usuario en el programa, o None."""
        return db.session.execute(
            select(UserProgram).where(
                UserProgram.user_id == user_id,
                UserProgram.program_id == program_id,
            )
        ).scalars().first()

    @staticmethod
    def get_program(program_id: int) -> Optional[Program]:
        """Programa por id, o None. Para que la ruta no consulte modelos."""
        return db.session.get(Program, program_id)

    @staticmethod
    def validate_change_request(applicant_id: int, from_program_id: int,
                                to_program_id: int) -> UserProgram:
        """
        Validación mínima común a "solicitar" y "ejecutar" un cambio:
        el solicitante es dueño del origen y el destino es utilizable.

        Returns:
            UserProgram del programa de ORIGEN.

        Raises:
            ProgramTransferNotFound: no existe la inscripción de origen o el
                programa destino no existe / está inactivo.
            ProgramTransferNotAllowed: origen y destino son el mismo.
        """
        if from_program_id == to_program_id:
            raise ProgramTransferNotAllowed(
                'El programa de destino debe ser distinto al programa actual.'
            )

        # El aspirante debe ser dueño del origen. Sin esta comprobación
        # cualquiera podía pasar el program_id de otro programa y disparar la
        # barrida de documentos con un mapeo que no le corresponde.
        user_program = ProgramTransferService._get_user_program(
            applicant_id, from_program_id
        )
        if user_program is None:
            raise ProgramTransferNotFound(
                'No encontramos tu proceso de admisión en el programa de origen.'
            )

        to_program = db.session.get(Program, to_program_id)
        if to_program is None or not to_program.is_active:
            raise ProgramTransferNotFound(
                'El programa de destino no existe o no está disponible.'
            )

        return user_program

    @staticmethod
    def validate_transfer(applicant_id: int, from_program_id: int,
                          to_program_id: int) -> UserProgram:
        """
        Validación completa del cambio de programa por autoservicio.

        Añade a `validate_change_request` las tres reglas que faltaban y que
        permitían que un aspirante aceptado o un alumno inscrito se mudara
        solo, fuera de convocatoria, arrastrando su dictamen:

          - `admission_status` dentro de `TRANSFERABLE_ADMISSION_STATUSES`;
          - convocatoria de admisión abierta hoy;
          - sin proceso previo en el programa de destino.

        Returns:
            UserProgram del programa de ORIGEN.

        Raises:
            ProgramTransferNotFound / ProgramTransferNotAllowed (mensaje en
            español, apto para mostrarse al aspirante).
        """
        from app.services import programs_service

        user_program = ProgramTransferService.validate_change_request(
            applicant_id, from_program_id, to_program_id
        )

        if user_program.admission_status not in TRANSFERABLE_ADMISSION_STATUSES:
            raise ProgramTransferNotAllowed(
                'Tu proceso ya avanzó más allá de la etapa de documentos, '
                'así que el cambio de programa debe autorizarlo un coordinador. '
                'Escríbele a la coordinación de tu programa.'
            )

        existing_target = ProgramTransferService._get_user_program(
            applicant_id, to_program_id
        )
        if existing_target is not None:
            raise ProgramTransferNotAllowed(
                'Ya tienes un proceso registrado en el programa de destino.'
            )

        # Misma puerta que usa `programs_service.enroll_user_once` para entrar
        # al proceso: si hoy no se puede ingresar a un programa, tampoco se
        # puede mudar de uno a otro.
        if programs_service.get_open_admission_period() is None:
            raise ProgramTransferNotAllowed(
                'La convocatoria de admisión está cerrada, por lo que no es '
                'posible cambiar de programa en este momento.'
            )

        return user_program

    # ─── Solicitudes de cambio (lectura) ─────────────────────────────────────

    @staticmethod
    def get_change_request(request_id: int) -> ProgramChangeRequest:
        """
        Devuelve la solicitud de cambio.

        Raises:
            ProgramTransferNotFound: si no existe.
        """
        req = db.session.get(ProgramChangeRequest, request_id)
        if req is None:
            raise ProgramTransferNotFound('La solicitud de cambio no existe.')
        return req

    @staticmethod
    def list_change_requests(applicant_id: Optional[int] = None,
                             program_ids: Optional[Iterable[int]] = None
                             ) -> List[ProgramChangeRequest]:
        """
        Lista solicitudes de cambio, más recientes primero.

        Args:
            applicant_id: si se indica, sólo las del solicitante.
            program_ids:  alcance de programas del consultante.
                          **None significa TODOS** (jefe de posgrado), igual que
                          `program_scope_service.accessible_program_ids`; una
                          colección vacía significa NINGUNO y devuelve lista
                          vacía. Nunca confundas ambos casos.
        """
        query = db.session.query(ProgramChangeRequest)

        if applicant_id is not None:
            query = query.filter(ProgramChangeRequest.applicant_id == applicant_id)

        if program_ids is not None:
            pids = {int(pid) for pid in program_ids}
            if not pids:
                return []
            query = query.filter(or_(
                ProgramChangeRequest.from_program_id.in_(pids),
                ProgramChangeRequest.to_program_id.in_(pids),
            ))

        return query.order_by(ProgramChangeRequest.created_at.desc()).all()

    # ─── Análisis y ejecución ────────────────────────────────────────────────

    @staticmethod
    def _admission_submissions(user_id: int, program_id: int) -> List[Submission]:
        """
        Submissions del usuario en la FASE DE ADMISIÓN del programa.

        El filtro por `Step.phase_id == ADMISSION_PHASE_ID` es obligatorio: el
        mapeo de archivos sólo cubre esa fase, así que sin el filtro toda
        submission de permanencia o conclusión quedaba "sin equivalente" y
        `execute_transfer` la borraba del disco de forma irrecuperable.
        """
        return db.session.execute(
            select(Submission)
            .join(ProgramStep, Submission.program_step_id == ProgramStep.id)
            .join(Step, ProgramStep.step_id == Step.id)
            .where(
                Submission.user_id == user_id,
                ProgramStep.program_id == program_id,
                Step.phase_id == ADMISSION_PHASE_ID,
            )
        ).scalars().all()

    @staticmethod
    def analyze_transfer(user_id: int, from_program_id: int, to_program_id: int) -> Dict:
        """
        Analiza qué documentos se pueden reutilizar, cuáles se perderán,
        y si hay entrevista que cancelar.
        
        Returns:
            {
                'can_transfer': bool,
                'reusable_docs': [{'archive_id', 'name', 'from_step', 'to_step'}],
                'incompatible_docs': [{'archive_id', 'name', 'file_path'}],
                'missing_docs': [{'archive_id', 'name', 'step_name'}],
                'interview_status': {'has_interview': bool, 'will_cancel': bool, 'reason': str}
            }
        """
        # 1. Obtener submissions de ADMISIÓN del usuario en programa origen
        current_submissions = ProgramTransferService._admission_submissions(
            user_id, from_program_id
        )

        # 2. Indexar por archive_id
        current_subs_by_archive = {sub.archive_id: sub for sub in current_submissions}
        
        # 3. Mapear archivos entre programas
        mapping = ProgramTransferService._create_archive_mapping(
            from_program_id, 
            to_program_id
        )
        
        # 4. Clasificar documentos
        reusable = []
        incompatible = []
        
        for from_archive_id, to_archive_id in mapping['equivalent'].items():
            if from_archive_id in current_subs_by_archive:
                sub = current_subs_by_archive[from_archive_id]
                from_arch = db.session.get(Archive, from_archive_id)
                to_arch = db.session.get(Archive, to_archive_id)
                
                reusable.append({
                    # Public handles — the transfer console sends them back.
                    'archive_id': public_id_service.archive_uuid(from_archive_id),
                    'target_archive_id': public_id_service.archive_uuid(to_archive_id),
                    'name': from_arch.name,
                    'from_step': from_arch.step.name,
                    'to_step': to_arch.step.name,
                    'status': sub.status,
                    'is_same_file': from_archive_id == to_archive_id
                })
        
        # Documentos que se perderán
        for archive_id, sub in current_subs_by_archive.items():
            if archive_id not in mapping['equivalent']:
                arch = db.session.get(Archive, archive_id)
                incompatible.append({
                    'archive_id': public_id_service.archive_uuid(archive_id),
                    'name': arch.name,
                    'file_path': sub.file_path,
                    'step_name': arch.step.name
                })
        
        # 5. Documentos faltantes en nuevo programa
        to_program_archives = ProgramTransferService._get_program_archives(to_program_id)
        missing = []
        
        for arch in to_program_archives:
            if arch.id not in mapping['equivalent'].values():
                # Este archivo no tiene equivalente en origen
                missing.append({
                    'archive_id': public_id_service.archive_uuid(arch.id),
                    'name': arch.name,
                    'step_name': arch.step.name
                })
        
        # 6. Verificar estado de entrevista
        interview_status = ProgramTransferService._check_interview_status(
            user_id, 
            from_program_id,
            to_program_id,
            reusable
        )
        
        return {
            'can_transfer': True,  # Siempre permitir, pero con advertencias
            'reusable_docs': reusable,
            'incompatible_docs': incompatible,
            'missing_docs': missing,
            'interview_status': interview_status
        }
    
    @staticmethod
    def execute_transfer(user_id: int, from_program_id: int, to_program_id: int, 
                        change_request_id: int = None) -> Dict:
        """
        Ejecuta el cambio de programa:
        1. Valida las reglas del autoservicio (dueño, estado, convocatoria)
        2. Copia submissions compatibles de la fase de admisión
        3. Elimina submissions de admisión incompatibles (archivos físicos)
        4. Cancela entrevista si no cumple requisitos
        5. Mueve user_program y reinicia el proceso de admisión

        Raises:
            ProgramTransferError: si alguna regla del autoservicio no se cumple.
                Se lanza ANTES de tocar nada, fuera del try, para que la ruta
                pueda distinguirla de un fallo inesperado (que sí devuelve
                {'success': False}).
        """
        # 1. Reglas del autoservicio. Van fuera del try: no son "errores", son
        #    denegaciones, y la ruta las traduce a 404 / 409 con su mensaje.
        user_program = ProgramTransferService.validate_transfer(
            user_id, from_program_id, to_program_id
        )

        try:
            # 2. Analizar transferencia
            analysis = ProgramTransferService.analyze_transfer(
                user_id, from_program_id, to_program_id
            )

            # 3. Obtener mapping
            mapping = ProgramTransferService._create_archive_mapping(
                from_program_id, to_program_id
            )

            # 4. Obtener submissions de ADMISIÓN (misma fase que el mapeo)
            current_submissions = ProgramTransferService._admission_submissions(
                user_id, from_program_id
            )

            # 5. Actualizar submissions reutilizables (modificar existentes, no crear nuevas)
            updated_count = 0
            for sub in current_submissions:
                if sub.archive_id in mapping['equivalent']:
                    to_archive_id = mapping['equivalent'][sub.archive_id]
                    to_archive = db.session.get(Archive, to_archive_id)
                    
                    # Encontrar program_step del destino
                    to_program_step = db.session.execute(
                        select(ProgramStep).where(
                            ProgramStep.program_id == to_program_id,
                            ProgramStep.step_id == to_archive.step_id
                        )
                    ).scalar_one_or_none()
                    
                    if to_program_step:
                        # Determinar el status a conservar
                        # Si es el mismo archivo (mismo ID), conservar status original
                        # Si es archivo equivalente, solo conservar 'approved' y 'rejected', resto va a 'pending'
                        is_same_file = (sub.archive_id == to_archive_id)
                        if is_same_file:
                            # Archivo idéntico: conservar status original completo
                            new_status = sub.status
                            keep_review_data = True
                        else:
                            # Archivo equivalente: solo conservar approved/rejected
                            new_status = sub.status if sub.status in ['approved', 'rejected'] else 'pending'
                            keep_review_data = (new_status == sub.status)
                        
                        # Actualizar la submission existente en lugar de crear nueva
                        sub.archive_id = to_archive_id
                        sub.program_step_id = to_program_step.id
                        sub.status = new_status
                        
                        # Solo conservar datos de revisión si el status no cambia
                        if not keep_review_data:
                            sub.review_date = None
                            sub.reviewer_comment = None
                        
                        updated_count += 1
            
            # 6. Eliminar submissions de admisión incompatibles (físico + DB).
            #    `current_submissions` ya viene acotado a la fase de admisión,
            #    así que los documentos de permanencia y conclusión del
            #    aspirante NO entran en esta barrida.
            deleted_count = 0
            for sub in current_submissions:
                if sub.archive_id not in mapping['equivalent']:
                    # Eliminar archivo físico
                    ProgramTransferService._delete_physical_file(sub.file_path)
                    db.session.delete(sub)
                    deleted_count += 1

            # 7. Cancelar entrevista si es necesario
            interview_cancelled = False
            if analysis['interview_status']['will_cancel']:
                interview_cancelled = ProgramTransferService._cancel_interview(
                    user_id, from_program_id,
                    reason="Cambio de programa - Requisitos no cumplidos en nuevo programa"
                )

            # 8. Mover la inscripción y REINICIAR el proceso de admisión.
            #    `user_program` viene de validate_transfer, que ya garantizó que
            #    existe y que su estado es transferible. Limpiar el dictamen es
            #    una red de seguridad: nadie debe llegar al programa destino con
            #    una decisión tomada en otro comité.
            user_program.program_id = to_program_id
            user_program.enrollment_date = now_local()
            user_program.admission_status = 'in_progress'
            user_program.deliberation_started_at = None
            user_program.decision_at = None
            user_program.decision_by = None
            user_program.decision_notes = None
            user_program.rejection_type = None
            user_program.correction_required = None

            db.session.commit()
            
            return {
                'success': True,
                'updated_documents': updated_count,
                'deleted_documents': deleted_count,
                'interview_cancelled': interview_cancelled,
                'new_program_id': to_program_id
            }
            
        except Exception as e:
            db.session.rollback()
            return {
                'success': False,
                'error': str(e)
            }
    
    @staticmethod
    def _create_archive_mapping(from_program_id: int, to_program_id: int) -> Dict:
        """
        Crea mapeo automático de archivos entre programas.
        
        Estrategia:
        1. Archivos con mismo ID → equivalent (archivo idéntico)
        2. Archivos en steps comunes con mismo nombre → equivalent
        3. Resto → incompatible
        """
        # Obtener archivos de ambos programas
        from_archives = ProgramTransferService._get_program_archives(from_program_id)
        to_archives = ProgramTransferService._get_program_archives(to_program_id)
        
        equivalent = {}  # from_archive_id → to_archive_id
        
        # Indexar archivos destino por ID, step_id y nombre
        to_by_id = {arch.id: arch for arch in to_archives}
        to_by_step = {}
        to_by_name = {}
        for arch in to_archives:
            to_by_step.setdefault(arch.step_id, []).append(arch)
            to_by_name.setdefault(arch.name.lower().strip(), []).append(arch)
        
        for from_arch in from_archives:
            matched = False
            
            # Estrategia 1: Mismo ID de archivo (archivo idéntico entre programas)
            if from_arch.id in to_by_id:
                equivalent[from_arch.id] = from_arch.id
                matched = True
            
            # Estrategia 2: Mismo step_id y mismo nombre (para steps comunes)
            elif not matched and from_arch.step_id in to_by_step:
                for to_arch in to_by_step[from_arch.step_id]:
                    if from_arch.name.lower().strip() == to_arch.name.lower().strip():
                        equivalent[from_arch.id] = to_arch.id
                        matched = True
                        break
        
        return {'equivalent': equivalent}
    
    @staticmethod
    def _get_program_archives(program_id: int) -> List[Archive]:
        """Obtiene todos los archivos de admisión de un programa"""
        return db.session.execute(
            select(Archive)
            .join(Step, Archive.step_id == Step.id)
            .join(ProgramStep, Step.id == ProgramStep.step_id)
            .where(
                ProgramStep.program_id == program_id,
                Step.phase_id == ADMISSION_PHASE_ID  # Solo fase de admisión
            )
        ).scalars().all()
    
    @staticmethod
    def _check_interview_status(user_id: int, from_program_id: int, 
                                to_program_id: int, reusable_docs: List) -> Dict:
        """
        Verifica si hay entrevista asignada y si debe cancelarse.
        
        Criterio: Cancelar si no cumple requisitos de documentos aprobados
        en el nuevo programa.
        """
        # Buscar entrevista activa
        appointment = db.session.execute(
            select(Appointment)
            .join(EventSlot, Appointment.slot_id == EventSlot.id)
            .join(EventWindow, EventSlot.event_window_id == EventWindow.id)
            .join(Event, EventWindow.event_id == Event.id)
            .where(
                Appointment.applicant_id == user_id,
                Appointment.status == 'scheduled',
                Event.program_id == from_program_id,
                Event.type == 'interview'
            )
        ).scalar_one_or_none()
        
        if not appointment:
            return {
                'has_interview': False,
                'will_cancel': False,
                'reason': None
            }
        
        # Verificar si cumple requisitos en nuevo programa
        # Contar documentos aprobados reutilizables
        approved_count = sum(1 for doc in reusable_docs if doc['status'] == 'approved')
        
        # Obtener total de documentos requeridos en nuevo programa (excluyendo entrevista)
        required_archives = db.session.execute(
            select(Archive)
            .join(Step, Archive.step_id == Step.id)
            .join(ProgramStep, Step.id == ProgramStep.step_id)
            .where(
                ProgramStep.program_id == to_program_id,
                Step.phase_id == ADMISSION_PHASE_ID,
                ProgramStep.sequence < 4,  # Excluir paso de entrevista
                Archive.is_uploadable == True
            )
        ).scalars().all()
        
        required_count = len(required_archives)
        
        # Cancelar si no tiene suficientes documentos aprobados
        will_cancel = approved_count < required_count
        
        return {
            'has_interview': True,
            'will_cancel': will_cancel,
            'reason': f'Documentos aprobados insuficientes ({approved_count}/{required_count})' if will_cancel else None,
            'appointment_id': appointment.id
        }
    
    @staticmethod
    def _cancel_interview(user_id: int, program_id: int, reason: str) -> bool:
        """Cancela la entrevista del usuario en el programa"""
        try:
            appointment = db.session.execute(
                select(Appointment)
                .join(EventSlot, Appointment.slot_id == EventSlot.id)
                .join(EventWindow, EventSlot.event_window_id == EventWindow.id)
                .join(Event, EventWindow.event_id == Event.id)
                .where(
                    Appointment.applicant_id == user_id,
                    Appointment.status == 'scheduled',
                    Event.program_id == program_id,
                    Event.type == 'interview'
                )
            ).scalar_one_or_none()
            
            if appointment:
                slot = db.session.get(EventSlot, appointment.slot_id)
                appointment.status = 'cancelled'
                appointment.notes = f"{appointment.notes or ''}\n[Auto-cancelada]: {reason}".strip()
                
                if slot:
                    slot.status = 'free'
                    slot.held_by = None
                    slot.hold_expires_at = None
                
                return True
            return False
        except Exception:
            return False
    
    @staticmethod
    def _delete_physical_file(file_path: str):
        """Elimina el archivo físico del sistema"""
        if not file_path:
            return
        
        try:
            full_path = os.path.join(
                current_app.config['USER_DOCS_FOLDER'],
                file_path
            )
            if os.path.exists(full_path):
                os.remove(full_path)
        except Exception as e:
            current_app.logger.error(f"Error eliminando archivo {file_path}: {e}")