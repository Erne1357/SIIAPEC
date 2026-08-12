# app/services/interview_service.py
"""
Interview eligibility rules.

SCOPE CONTRACT — read before adding a caller.
This module answers domain questions ("is this applicant eligible?", "who is
eligible in program P?", "is this profile actually complete?"). It never
answers "may the caller see/do this": that is program scope, and it lives in
`app/services/program_scope_service.py` / `app/utils/permissions.py`.

Every public function here therefore assumes the caller ALREADY passed
`@program_scope_required(...)` (or an equivalent guard) for the program and the
target user it is being asked about. The functions are still written
fail-closed — an applicant who does not belong to the requested program gets no
document detail, and a program id collection is never interpreted as "all
programs" — but that is defence in depth, not the guard.
"""

from sqlalchemy import select, and_
from typing import Dict, Iterable, List, Optional

from app import db
from app.models.user import User
from app.models.program import Program
from app.models.program_step import ProgramStep
from app.models.step import Step
from app.models.phase import Phase
from app.models.submission import Submission
from app.models.archive import Archive
from app.models.user_program import UserProgram
from app.models.extension_request import ExtensionRequest
from app.services.user_history_service import UserHistoryService
from app.services.notification_service import NotificationService


class InterviewEligibilityService:

    #: Roles whose `profile_completed` flag is read by the admission flow.
    #: Any other account (program_admin, postgraduate_admin, social_service)
    #: is refused as a target: the flag means nothing for staff, so writing it
    #: could only ever be a mistake or an attempt to touch a record that is
    #: nobody's student.
    PROFILE_TARGET_ROLES = frozenset({'applicant', 'student'})

    @staticmethod
    def _normalize_ids(values: Optional[Iterable]) -> List[int]:
        """Coerce an iterable of ids to a de-duplicated list of ints, dropping junk."""
        out = []
        seen = set()
        for raw in (values or []):
            try:
                value = int(raw)
            except (TypeError, ValueError):
                continue
            if value not in seen:
                seen.add(value)
                out.append(value)
        return out

    @staticmethod
    def is_enrolled_in_program(student_id: int, program_id: int) -> bool:
        """True if the student has a UserProgram row for that program."""
        row = db.session.execute(
            select(UserProgram.id).where(
                and_(
                    UserProgram.user_id == student_id,
                    UserProgram.program_id == program_id,
                )
            ).limit(1)
        ).first()
        return row is not None

    @staticmethod
    def check_student_eligibility(student_id: int, program_id: int) -> Dict:
        """
        Verifica si un estudiante es elegible para entrevista.

        Criterios:
        1. Perfil completo
        2. Todos los archivos de pasos anteriores en estado 'approved' o 'extended'
        3. Solo considerar pasos de la fase de admisión
        4. No incluir el último paso (entrevista)
        5. Considerar extensiones aprobadas como válidas cuando no hay submission

        The payload carries per-document detail (archive names, submission
        status, missing items), so the caller must already be in scope for BOTH
        `student_id` and `program_id`. The student/program pairing is verified
        here as well: an applicant who is not enrolled in `program_id` yields no
        document detail at all.

        Returns:
            Dict con 'eligible', 'reason', 'missing_items', 'profile_status', 'documents_status'
        """
        user = db.session.get(User, student_id)
        if not user:
            return {"eligible": False, "reason": "Estudiante no encontrado"}

        if not InterviewEligibilityService.is_enrolled_in_program(student_id, program_id):
            return {
                "eligible": False,
                "reason": "El estudiante no está inscrito en este programa",
            }

        # 1. Verificar perfil completo (ahora usa el método del modelo)
        profile_complete = user.profile_completed

        # 2. Obtener todos los pasos del programa que pertenezcan a la fase de admisión
        program_steps = db.session.execute(
            select(ProgramStep, Step).join(
                Step, ProgramStep.step_id == Step.id
            ).join(
                Phase, Step.phase_id == Phase.id
            ).where(
                and_(
                    ProgramStep.program_id == program_id,
                    Phase.name == 'admission'
                )
            ).order_by(ProgramStep.sequence)
        ).all()

        if not program_steps:
            return {"eligible": False, "reason": "Programa sin pasos configurados"}

        # Excluir el último paso (presumiblemente la entrevista)
        steps_to_check = program_steps[1:-1] if len(program_steps) > 1 else []

        # 3. Verificar estado de documentos en cada paso
        missing_items = []
        documents_status = []

        for program_step, step in steps_to_check:
            # Obtener archivos requeridos para este paso
            archives = db.session.execute(
                select(Archive).where(
                    Archive.step_id == step.id,
                    Archive.is_uploadable == True
                )
            ).scalars().all()

            step_status = {
                "step_name": step.name,
                "step_id": step.id,
                "sequence": program_step.sequence,
                "archives": []
            }

            for archive in archives:
                # Buscar submission del estudiante para este archivo
                submission = db.session.execute(
                    select(Submission).where(
                        and_(
                            Submission.user_id == student_id,
                            Submission.archive_id == archive.id
                        )
                    )
                ).scalar_one_or_none()

                archive_status = {
                    # Handle público del Archive; `archive.id` interno se sigue
                    # usando arriba para la consulta de Submission.
                    "archive_name": archive.name,
                    "archive_id": str(archive.uuid) if archive.uuid else None,
                    "has_submission": bool(submission),
                    "status": submission.status if submission else "missing",
                    "is_valid": False,
                    "has_granted_extension": False
                }

                # Determinar si el archivo está en estado válido
                if submission and submission.status in ['approved', 'extended']:
                    archive_status["is_valid"] = True
                else:
                    # Si no hay submission o no está aprobada, verificar si hay extensión aprobada
                    if not submission:
                        extension_request = db.session.execute(
                            select(ExtensionRequest).where(
                                and_(
                                    ExtensionRequest.user_id == student_id,
                                    ExtensionRequest.archive_id == archive.id,
                                    ExtensionRequest.program_step_id == program_step.id,
                                    ExtensionRequest.status == 'granted'
                                )
                            )
                        ).scalar_one_or_none()

                        if extension_request:
                            archive_status["is_valid"] = True
                            archive_status["has_granted_extension"] = True
                            archive_status["status"] = "extension_granted"

                    # Si aún no es válido, agregar a elementos faltantes
                    if not archive_status["is_valid"]:
                        missing_items.append({
                            "type": "document",
                            "step": step.name,
                            "archive": archive.name,
                            "current_status": submission.status if submission else "missing"
                        })

                step_status["archives"].append(archive_status)

            documents_status.append(step_status)

        # 4. Verificar si faltan elementos
        if not profile_complete:
            missing_items.append({
                "type": "profile",
                "description": "Perfil de usuario incompleto"
            })

        # 5. Determinar elegibilidad
        eligible = len(missing_items) == 0

        return {
            "eligible": eligible,
            "reason": "Cumple todos los requisitos" if eligible else "Faltan requisitos",
            "missing_items": missing_items,
            "profile_status": {
                "complete": profile_complete,
                "required": True
            },
            "documents_status": documents_status,
            "total_steps_checked": len(steps_to_check),
            "last_step_excluded": program_steps[-1][1].name if program_steps else None
        }

    @staticmethod
    def get_eligible_students(program_id: int) -> List[Dict]:
        """
        Obtiene todos los estudiantes elegibles para entrevista en un programa.

        Returns name, e-mail and the full eligibility payload (which includes
        per-document status), so the caller must be in scope for `program_id`.
        """
        # Obtener todos los estudiantes del programa
        user_programs = db.session.execute(
            select(UserProgram, User).join(
                User, UserProgram.user_id == User.id
            ).where(
                UserProgram.program_id == program_id,
                User.role.has(name='applicant')
            )
        ).all()

        eligible_students = []

        for user_program, user in user_programs:
            eligibility = InterviewEligibilityService.check_student_eligibility(
                user.id, program_id
            )

            if eligibility["eligible"]:
                eligible_students.append({
                    # Public handle: la consola de entrevistas lo devuelve como
                    # `applicant_id` al asignar una cita, y esa ruta ya resuelve
                    # el UUID público.
                    "id": str(user.uuid) if user.uuid else None,
                    "full_name": f"{user.first_name} {user.last_name}",
                    "email": user.email,
                    "eligibility": eligibility
                })

        return eligible_students

    @staticmethod
    def all_program_ids() -> List[int]:
        """
        Every program id in the system.

        Only for a caller whose scope is GLOBAL (`accessible_program_ids()`
        returned None). Resolving that None into a concrete id list is the
        route's job, on purpose: this module never receives a value that means
        "all programs".
        """
        return [
            pid for (pid,) in
            db.session.execute(select(Program.id).order_by(Program.id)).all()
        ]

    @staticmethod
    def get_eligible_students_by_programs(program_ids: Optional[Iterable[int]]) -> List[Dict]:
        """
        Eligible applicants grouped by program.

        `program_ids` must always be a CONCRETE collection of ids. `None` and
        an empty collection both mean "no programs" and return an empty list —
        never "every program". A caller with global scope must expand its scope
        with `all_program_ids()` first, so the dangerous `None == all` idiom
        stays visible at exactly one call site instead of hiding in here.
        """
        wanted = InterviewEligibilityService._normalize_ids(program_ids)
        if not wanted:
            return []

        programs = db.session.execute(
            select(Program).where(Program.id.in_(wanted)).order_by(Program.name)
        ).scalars().all()

        programs_data = []
        for program in programs:
            eligible_students = InterviewEligibilityService.get_eligible_students(program.id)
            programs_data.append({
                "program_id": program.id,
                "program_name": program.name,
                "program_slug": program.slug,
                "eligible_students": eligible_students,
                "eligible_count": len(eligible_students),
            })
        return programs_data

    @staticmethod
    def mark_profile_complete(user_id: int, admin_id: int) -> Dict:
        """
        Confirma que el perfil de un aspirante está completo.

        Two things this function deliberately does NOT do:

        1. It does not decide who may call it. `profile_completed` is criterion
           #1 of interview eligibility, so this is a program-scoped WRITE: the
           route must have passed `@program_scope_required(user_id_kwarg=...)`
           before getting here.
        2. It does not grant the flag by fiat. `User.is_profile_complete()` is
           the single criterion — the same one the applicant's own profile form
           uses. An administrator may CONFIRM that the data is there; they
           cannot invent eligibility for an applicant who never filled it in.

        The state change, its history entry and the applicant's notification
        share ONE transaction: if the audit trail cannot be written, the flag
        is not written either.

        Args:
            user_id:  target applicant/student.
            admin_id: id of the account performing the action (from the route).

        Returns:
            Dict with 'status' in {'ok', 'already_complete', 'not_found',
            'invalid_target', 'incomplete'} and a Spanish 'message'.
        """
        user = db.session.get(User, user_id)
        if not user:
            return {"status": "not_found", "message": "Usuario no encontrado"}

        role_name = getattr(user.role, 'name', None)
        if role_name not in InterviewEligibilityService.PROFILE_TARGET_ROLES:
            return {
                "status": "invalid_target",
                "message": "Solo se puede completar el perfil de aspirantes o estudiantes",
            }

        if user.profile_completed:
            return {
                "status": "already_complete",
                "message": "El perfil ya estaba marcado como completo",
            }

        if not user.is_profile_complete():
            return {
                "status": "incomplete",
                "message": "No se pudo completar el perfil - faltan campos requeridos",
            }

        user.profile_completed = True

        UserHistoryService.log_action(
            user_id=user.id,
            admin_id=admin_id,
            action='profile_completed',
            details={
                'marked_by_admin_id': admin_id,
                'source': 'interviews.api.manage',
            },
        )
        NotificationService.create_notification(
            user_id=user.id,
            notification_type='profile_completed',
            title='Perfil marcado como completo',
            message=(
                'Un administrador confirmó que tu perfil está completo. '
                'Ya cuenta para tu elegibilidad a entrevista.'
            ),
            priority='low',
        )

        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise

        return {"status": "ok", "message": "Perfil marcado como completo"}
