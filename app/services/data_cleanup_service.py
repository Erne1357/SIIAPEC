# app/services/data_cleanup_service.py
"""
Servicio de preview para limpieza de datos de aspirantes expirados y
estudiantes sin inscripción semestral confirmada.

La ejecución real de limpieza de aspirantes corre en:
  app.tasks.maintenance.cleanup_expired_admission_files
Este servicio sólo sirve para previsualizar candidatos desde la UI.
"""
from typing import List, Dict

from app.services import public_id_service


class DataCleanupService:

    @staticmethod
    def get_expired_candidates() -> List[Dict]:
        """
        Retorna aspirantes que serían marcados como 'expired' en la próxima
        ejecución automática del task cleanup_expired_admission_files.

        Criterio: admission_status en ('in_progress', 'interview_completed',
        'deliberation') y 2+ periodos cerrados desde su admission_period.
        """
        from app.models.user_program import UserProgram
        from app.models.academic_period import AcademicPeriod
        from app.models.submission import Submission
        from app.models.program_step import ProgramStep
        from app.tasks.maintenance import _periods_elapsed_since

        candidates = (
            UserProgram.query
            .join(AcademicPeriod, UserProgram.admission_period_id == AcademicPeriod.id)
            .filter(
                UserProgram.admission_status.in_(
                    ['in_progress', 'interview_completed', 'deliberation']
                ),
                UserProgram.admission_period_id.isnot(None),
            )
            .all()
        )

        result = []
        for up in candidates:
            enrollment_period = AcademicPeriod.query.get(up.admission_period_id)
            if not enrollment_period:
                continue

            elapsed = _periods_elapsed_since(enrollment_period.code)
            if elapsed < 2:
                continue

            files_count = (
                Submission.query
                .join(ProgramStep, Submission.program_step_id == ProgramStep.id)
                .filter(
                    Submission.user_id == up.user_id,
                    ProgramStep.program_id == up.program_id,
                    Submission.file_path.isnot(None),
                )
                .count()
            )

            result.append({
                'user_id': public_id_service.user_uuid(up.user_id),
                'user_program_id': up.id,
                'name': f"{up.user.first_name} {up.user.last_name} {up.user.mother_last_name or ''}".strip(),
                'email': up.user.email,
                'program_name': up.program.name,
                'admission_status': up.admission_status,
                'enrollment_period': enrollment_period.name,
                'periods_elapsed': elapsed,
                'files_to_delete': files_count,
            })

        return result

    @staticmethod
    def get_inactive_students() -> List[Dict]:
        """
        Retorna estudiantes activos (admission_status='enrolled') cuya
        inscripción semestral NO ha sido confirmada en el período activo.

        Si no hay período activo, retorna lista vacía.
        """
        from app.models.user_program import UserProgram
        from app.models.semester_enrollment import SemesterEnrollment
        from app.models.academic_period import AcademicPeriod

        active_period = AcademicPeriod.get_active_period()
        if not active_period:
            return []

        enrolled_ups = UserProgram.query.filter_by(admission_status='enrolled').all()

        result = []
        for up in enrolled_ups:
            enrollment = SemesterEnrollment.query.filter_by(
                user_program_id=up.id,
                academic_period_id=active_period.id,
            ).first()

            if enrollment and enrollment.enrollment_confirmed:
                continue

            result.append({
                'user_id': public_id_service.user_uuid(up.user_id),
                'user_program_id': up.id,
                'name': f"{up.user.first_name} {up.user.last_name} {up.user.mother_last_name or ''}".strip(),
                'email': up.user.email,
                'program_name': up.program.name,
                'current_semester': up.current_semester,
                'active_period': active_period.name,
                'has_enrollment': enrollment is not None,
                'enrollment_status': enrollment.status if enrollment else None,
            })

        return result
