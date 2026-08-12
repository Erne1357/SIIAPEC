# app/services/admission.py

from flask import current_app
from sqlalchemy.orm import selectinload
from sqlalchemy import and_, or_, select
from app import db
from app.services import public_id_service
from app.models import Step, ProgramStep, Phase, Submission, ExtensionRequest
from app.models.event import Event, EventSlot, EventAttendance, EventWindow
from datetime import datetime, timezone
import calendar
import logging,json
from app.utils.datetime_utils import now_local, to_local_timezone


def _add_months(dt, months):
    """Suma `months` meses a un datetime, ajustando el día al último del mes si es necesario."""
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)

def get_admission_state(user_id: int, program_id: int, up) -> dict:
    """
    Devuelve todo lo necesario para la vista de Admisión:
      - steps: lista de objetos Step con sus archives cargados
      - subs: dict archive_id → Submission
      - extensions: dict archive_id → ExtensionRequest (solo las activas)
      - lock_info: dict step_id → bool (bloqueado)
      - step_states: dict step_id → 'approved'|'rejected'|'review'|'pending'|'extended'
      - progress_segments, status_count, progress_pct, pending_items, timeline
    """
    # 1) Traer pasos + archivos
    steps = (
        Step.query
        .join(ProgramStep)
        .join(Phase)
        .filter(
            ProgramStep.program_id == program_id,
            Phase.name == 'admission'
        )
        .options(selectinload(Step.archives))
        .order_by(ProgramStep.sequence)
        .all()
    )

    # 2) Map submissions del usuario
    archive_ids = [a.id for step in steps for a in step.archives]
    
    # Separar archivos de secuencia 0 (informativos) de los que cuentan para progreso
    informative_archive_ids = []
    progress_archive_ids = []
    
    for step in steps:
        seq = step.program_steps[0].sequence if step.program_steps else None
        for archive in step.archives:
            if seq == 0:
                informative_archive_ids.append(archive.id)
            else:
                progress_archive_ids.append(archive.id)
    
    subs = {
        s.archive_id: s
        for s in Submission.query
                          .filter_by(user_id=user_id)
                          .filter(Submission.archive_id.in_(archive_ids))
                          .all()
    }

    # 2.5) Calcular documentos con validez vencida (basado en archive.validity_months)
    # Solo aplica a submissions aprobadas cuya vigencia configurada ya expiró.
    expired_archive_ids = set()
    for aid, sub in subs.items():
        arch = sub.archive
        if arch and arch.validity_months and sub.upload_date:
            expiry_dt = _add_months(sub.upload_date, arch.validity_months)
            expiry_dt = to_local_timezone(expiry_dt)
            if expiry_dt < now_local():
                expired_archive_ids.add(aid)

    # 3) Map extensiones activas del usuario (solo para archivos que cuentan para progreso)
    # Usar hora local de Ciudad Juárez
    now = now_local()
    all_extensions = {
        e.archive_id: e
        for e in ExtensionRequest.query
                                .filter_by(user_id=user_id)
                                .filter(ExtensionRequest.archive_id.in_(archive_ids))
                                .order_by(ExtensionRequest.created_at.desc())
                                .all()
    }
    
    # Extensiones activas (solo granted y no expiradas)
    active_extensions = {}
    for aid, ext in all_extensions.items():
        if ext.status == 'granted' and ext.granted_until:
            # Convertir a zona horaria local usando la utilidad
            granted_until = to_local_timezone(ext.granted_until)
            
            if granted_until > now:
                active_extensions[aid] = ext
    
    # 4) Lock info por paso (solo bloquear el último paso)
    def _is_locked(step):
        # Obtener la secuencia de este paso
        seq = step.program_steps[0].sequence
        
        # Nunca bloquear los primeros pasos (secuencia 0 y 1)
        if seq == 0 or seq == 1:
            return False
            
        # Encontrar el paso con la secuencia más alta (último paso)
        max_seq = max(st.program_steps[0].sequence for st in steps)
        
        # Solo bloquear el último paso (entrevista/defensa)
        if seq != max_seq:
            return False
            
        # Para el último paso, verificar que TODOS los pasos anteriores estén completos
        for prev_step in steps:
            prev_seq = prev_step.program_steps[0].sequence
            
            # Saltar el paso actual y el paso 0 (registro)
            if prev_seq >= seq or prev_seq == 0:
                continue
                
            # Revisar todos los archivos del paso anterior
            for arch in prev_step.archives:
                if not arch.is_uploadable:
                    continue
                    
                sub = subs.get(arch.id)
                ext = active_extensions.get(arch.id)
                
                # Si hay extensión activa, está válido
                if ext:
                    continue
                    
                # Si no hay submission o no está aprobada/extendida, bloquea
                if not sub or sub.status not in ['approved', 'extended']:
                    return True
                    
        return False

    lock_info = { step.id: _is_locked(step) for step in steps }

    # 4.5) Verificar si el usuario tiene entrevista asignada o realizada
    def _has_interview_assigned():
        from app.models.appointment import Appointment
        # Cualquier cita no cancelada cuenta: scheduled (pendiente), done (realizada), no_show
        appt = db.session.execute(
            select(Appointment)
            .join(EventSlot, Appointment.slot_id == EventSlot.id)
            .join(EventWindow, EventSlot.event_window_id == EventWindow.id)
            .join(Event, EventWindow.event_id == Event.id)
            .where(
                and_(
                    Appointment.applicant_id == user_id,
                    Appointment.status.in_(['scheduled', 'done', 'no_show']),
                    or_(Event.program_id == program_id, Event.program_id.is_(None)),
                    Event.type == 'interview'
                )
            )
        ).scalar_one_or_none()
        return bool(appt)
    
    has_interview = _has_interview_assigned()

    # 5) Estado resumido por paso (incluyendo extensiones y entrevistas)
    def _step_state(step):
        # Determinar si este es el último paso (entrevista/defensa)
        seq = step.program_steps[0].sequence
        max_seq = max(st.program_steps[0].sequence for st in steps)
        is_last_step = (seq == max_seq)
        
        statuses = []
        has_extension = False
        
        for arch in step.archives:
            if arch.id in active_extensions:  # Usar active_extensions
                has_extension = True
                # Si hay extensión activa, consideramos como "extended"
                statuses.append('extended')
            elif arch.id in subs:
                statuses.append(subs[arch.id].status)
            else:
                statuses.append('pending')
        
        # Para el último paso, considerar también si tiene entrevista asignada
        if is_last_step:
            if has_interview:
                return 'approved'  # Verde con palomita si tiene entrevista
            else:
                # Si no tiene entrevista, seguir la lógica normal pero no aprobar automáticamente
                if 'rejected' in statuses:
                    return 'rejected'
                if 'extended' in statuses:
                    return 'extended'
                if any(s == 'review' for s in statuses):
                    return 'review'
                return 'pending'
        else:
            # Para otros pasos, lógica normal
            if 'rejected' in statuses:
                return 'rejected'
            if all(s == 'approved' for s in statuses):
                return 'approved'
            if 'extended' in statuses:
                return 'extended'
            if any(s == 'review' for s in statuses):
                return 'review'
            return 'pending'

    step_states = { step.id: _step_state(step) for step in steps }

    # 6) Conteos y segmentos de progreso (solo archivos que cuentan, excluyendo secuencia 0)
    total = len(progress_archive_ids)
    status_count = {k:0 for k in ('approved','rejected','review','pending','extended')}
    
    for aid in progress_archive_ids:  # Solo contar archivos que no son informativos
        if aid in active_extensions:  # Usar active_extensions
            status_count['extended'] += 1
        elif aid in subs:
            status_count[subs[aid].status] += 1
        else:
            status_count['pending'] += 1
    
    segments = []
    if total:
        for key, css in [
            ('rejected','danger'),
            ('approved','success'),
            ('extended','info'),
            ('review','warning'),
            ('pending','secondary')
        ]:
            pct = round(status_count[key]/total*100,1)
            if pct:
                segments.append({'pct':pct,'class':css})
    
    progress_pct = round(status_count['approved']/total*100) if total else 0

    # 7) Lista de pendientes/rechazados (solo archivos que cuentan para progreso)
    pending_items = []
    for step in steps:
        seq = step.program_steps[0].sequence if step.program_steps else None
        
        # Saltar archivos informativos (secuencia 0)
        if seq == 0:
            continue
            
        for arch in step.archives:
            if arch.id in active_extensions:
                # Archivo con extensión activa
                ext = active_extensions[arch.id]
                pending_items.append({
                    'name': arch.name,
                    'status': 'extended',
                    'extension_until': ext.granted_until.strftime('%d/%m/%Y %H:%M'),
                    'extension_reason': ext.reason
                })
            elif arch.id not in subs or subs[arch.id].status in ('pending','rejected'):
                # Archivo pendiente o rechazado sin extensión activa
                pending_items.append({
                    'name': arch.name,
                    'status': (subs[arch.id].status if arch.id in subs else 'pending')
                })

    # 8) Timeline mejorado con detección correcta de estados
    # Calcular estados del timeline basado en submissions reales
    has_any_submission = len(subs) > 0
    all_docs_reviewed = (status_count['approved'] + status_count['rejected']) >= total if total > 0 else False
    all_docs_approved = status_count['approved'] >= total if total > 0 else False
    has_docs_in_review = status_count['review'] > 0

    # Estado de "Documentos enviados"
    if has_any_submission:
        docs_sent_state = 'done'
        docs_sent_date = min(sub.upload_date for sub in subs.values()).strftime('%d/%m/%Y')
    else:
        docs_sent_state = 'pending'
        docs_sent_date = None

    # Estado de "Revisión de documentos"
    if not has_any_submission:
        review_state = 'pending'
    elif all_docs_reviewed:
        review_state = 'done'
    elif has_docs_in_review or has_any_submission:
        review_state = 'inprogress'
    else:
        review_state = 'pending'

    # Estado de "Entrevista" - verificar si tiene entrevista asignada
    if has_interview:
        interview_state = 'done'
    elif all_docs_approved:
        interview_state = 'inprogress'
    else:
        interview_state = 'pending'

    # Estado de "Decisión final" - usar admission_status del UserProgram
    admission_status = getattr(up, 'admission_status', 'in_progress')
    if admission_status == 'accepted':
        decision_state = 'done'
    elif admission_status == 'enrolled':
        decision_state = 'done'
    elif admission_status == 'rejected':
        decision_state = 'rejected'
    elif admission_status == 'deliberation':
        decision_state = 'inprogress'
    elif admission_status == 'interview_completed':
        decision_state = 'inprogress'
    elif has_interview:
        decision_state = 'inprogress'
    else:
        decision_state = 'pending'

    timeline = [
        {
            'label': 'Registro completado',
            'date': up.enrollment_date.strftime('%d/%m/%Y') if up.enrollment_date else None,
            'state': 'done',
            'icon': 'bi-check-circle-fill'
        },
        {
            'label': 'Documentos enviados',
            'date': docs_sent_date,
            'state': docs_sent_state,
            'icon': 'bi-file-earmark-arrow-up-fill'
        },
        {
            'label': 'Revisión de documentos',
            'date': None,
            'state': review_state,
            'icon': 'bi-search'
        },
        {
            'label': 'Entrevista',
            'date': getattr(up, 'interview_date', None),
            'state': interview_state,
            'icon': 'bi-people-fill'
        },
        {
            'label': 'Decisión final',
            'date': getattr(up, 'decision_date', None),
            'state': decision_state,
            'icon': 'bi-trophy-fill'
        }
    ]

    processed_steps = []
    skip_next = False
    def _combined_state(step1_id, step2_id):
        """Calcula el estado combinado de dos steps"""
        s1 = step_states.get(step1_id, 'pending')
        s2 = step_states.get(step2_id, 'pending')
        
        if 'rejected' in (s1, s2):
            return 'rejected'
        if s1 == 'approved' and s2 == 'approved':
            return 'approved'
        if 'extended' in (s1, s2):
            return 'extended'
        if 'review' in (s1, s2):
            return 'review'
        return 'pending'
    
    for i, step in enumerate(steps):
        if skip_next:
            skip_next = False
            continue
            
        seq = step.program_steps[0].sequence if step.program_steps else None
        
        
        # Si es sequence 2, intentar combinar con 3
        if seq == 2 and i + 1 < len(steps):
            next_step = steps[i + 1]
            next_seq = next_step.program_steps[0].sequence if next_step.program_steps else None
            
            if next_seq == 3:
                # Crear step combinado
                combined_step = {
                    'is_combined': True,
                    'id': f"combined-{step.id}-{next_step.id}",
                    'name': 'Requisitos Específicos',
                    'sequence': 2,
                    'step1': step,
                    'step2': next_step,
                    'locked': lock_info.get(step.id, False) or lock_info.get(next_step.id, False),
                    'state': _combined_state(step.id, next_step.id)
                }
                processed_steps.append(combined_step)
                skip_next = True
                continue
        
        # Step normal
        processed_steps.append({
            'is_combined': False,
            'step': step,
            'sequence': seq,
            'locked': lock_info.get(step.id, False),
            'state': step_states.get(step.id, 'pending')
        })
    

    
    # ========== FIN NUEVO ==========

    # Documentos de aceptación (solo si está aceptado)
    acceptance_docs = {}
    if up.admission_status == 'accepted':
        try:
            from app.models.acceptance_document import AcceptanceDocument
            for doc_type in ['acceptance_letter', 'course_schedule', 'enrollment_receipt', 'acceptance_opinion']:
                doc = AcceptanceDocument.query.filter_by(
                    user_program_id=up.id,
                    document_type=doc_type
                ).first()
                acceptance_docs[doc_type] = doc.to_dict() if doc else None
        except Exception:
            pass

    return {
        'steps': steps,  # mantener original para compatibilidad
        'processed_steps': processed_steps,  # NUEVO: lista procesada
        'subs': subs,
        'extensions': active_extensions,
        'all_extensions': all_extensions,
        'lock_info': lock_info,
        'step_states': step_states,
        'progress_segments': segments,
        'status_count': status_count,
        'progress_pct': progress_pct,
        'pending_items': pending_items,
        'timeline': timeline,
        'extended_docs': status_count.get('extended', 0),  # NUEVO: conteo de archivos con extensión
        'total_docs': total,  # Total de documentos para el progreso
        'expired_archive_ids': expired_archive_ids,  # IDs de archivos con validez vencida
        # Campos de deliberación para mostrar estado al aspirante
        'admission_status': up.admission_status,
        'decision_notes': up.decision_notes,
        'correction_required': public_id_service.correction_required_to_public(up.correction_required),
        'rejection_type': up.rejection_type,
        'deliberation_started_at': up.deliberation_started_at,
        'decision_at': up.decision_at,
        # Documentos de aceptación (solo cuando admission_status == 'accepted')
        'acceptance_docs': acceptance_docs,
        'user_program_id': up.id,
    }