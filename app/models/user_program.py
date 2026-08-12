from app import db
from datetime import datetime
from app.utils.datetime_utils import now_local

class UserProgram(db.Model):
    """
    Modelo que representa la relacion entre un usuario y un programa.

    Estados de admision (admission_status):
    - in_progress: Proceso de admision en curso
    - interview_completed: Entrevista realizada, esperando deliberacion
    - deliberation: En proceso de deliberacion por el comite
    - accepted: Aceptado al programa
    - rejected: Rechazado del programa
    - deferred: Inscripcion diferida para otro periodo
    - enrolled: Inscrito oficialmente (ya es estudiante)
    - expired: Proceso expirado por antiguedad (>=2 periodos sin completar)

    Alta masiva de estudiantes ya registrados:
    Cuando se da de alta a un estudiante existente que NO completó admision en
    SIIAP (por ej. import de generaciones previas), se crea su UserProgram con
    admission_status='enrolled' directamente. No se generan submissions sinteticas
    ni se requieren documentos historicos. La capa de dashboard (DashboardService.
    get_student_pending_docs) detecta admission_status='enrolled' y devuelve lista
    vacia para 'admission' — los archives historicos no aparecen como pendientes.
    """
    __tablename__ = 'user_program'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    program_id = db.Column(db.Integer, db.ForeignKey('program.id'), nullable=False)
    enrollment_date = db.Column(db.DateTime, default=now_local)
    current_semester = db.Column(db.Integer)
    updated_at = db.Column(db.DateTime, default=now_local, onupdate=now_local, nullable=False)

    # Relacion con periodo de admision
    admission_period_id = db.Column(db.Integer, db.ForeignKey('academic_period.id'), nullable=True)

    # Campos de deliberacion (Fase 3)
    admission_status = db.Column(db.String(30), default='in_progress', nullable=False)
    deliberation_started_at = db.Column(db.DateTime, nullable=True)
    decision_at = db.Column(db.DateTime, nullable=True)
    decision_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    decision_notes = db.Column(db.Text, nullable=True)

    # Para rechazos parciales (corregir algo especifico)
    rejection_type = db.Column(db.String(30), nullable=True)  # full, partial
    correction_required = db.Column(db.Text, nullable=True)

    # Beca CONACyT — True si el estudiante es becario activo (Módulo C, Permanencia)
    has_conacyt_scholarship = db.Column(db.Boolean, default=False, nullable=False)

    # Relaciones
    user = db.relationship('User', foreign_keys=[user_id], back_populates='user_program')
    program = db.relationship('Program', back_populates='user_program')
    admission_period = db.relationship('AcademicPeriod', back_populates='user_programs')
    decision_maker = db.relationship('User', foreign_keys=[decision_by])
    acceptance_documents = db.relationship('AcceptanceDocument', back_populates='user_program', lazy='dynamic')
    semester_enrollments = db.relationship('SemesterEnrollment', back_populates='user_program', lazy='dynamic', order_by='SemesterEnrollment.semester_number')
    enrollment_deferrals = db.relationship('EnrollmentDeferral', back_populates='user_program', lazy='dynamic', order_by='EnrollmentDeferral.deferral_number')
    
    def to_dict(self, include_deliberation=False):
        # Derivar current_semester del ultimo SemesterEnrollment confirmado.
        # Si no existen registros aun (pre-Fase 6), se usa el valor en columna
        # (= 1, asignado al momento de la transicion a estudiante).
        from app.models.semester_enrollment import SemesterEnrollment
        # `user_id` / `decision_by` name User rows and are published as UUIDs;
        # `id`, `program_id` and `admission_period_id` stay integers (no public
        # handle on those models). Key names unchanged on purpose.
        from app.models.user import User
        from app.services.public_id_service import (
            correction_required_to_public,
            uuid_for,
        )

        last_se = (SemesterEnrollment.query
                   .filter_by(user_program_id=self.id)
                   .order_by(SemesterEnrollment.semester_number.desc())
                   .first())
        current_sem = last_se.semester_number if last_se else self.current_semester

        data = {
            'id': self.id,
            'user_id': uuid_for(User, self.user_id),
            'program_id': self.program_id,
            'enrollment_date': self.enrollment_date.isoformat() if self.enrollment_date else None,
            'current_semester': current_sem,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'admission_period_id': self.admission_period_id,
            'admission_status': self.admission_status,
            'has_conacyt_scholarship': self.has_conacyt_scholarship,
        }

        if include_deliberation:
            data.update({
                'deliberation_started_at': self.deliberation_started_at.isoformat() if self.deliberation_started_at else None,
                'decision_at': self.decision_at.isoformat() if self.decision_at else None,
                'decision_by': uuid_for(User, self.decision_by),
                'decision_notes': self.decision_notes,
                'rejection_type': self.rejection_type,
                'correction_required': correction_required_to_public(self.correction_required)
            })

        return data

    def start_deliberation(self):
        """Marca el inicio del proceso de deliberacion despues de la entrevista."""
        self.admission_status = 'deliberation'
        self.deliberation_started_at = now_local()

    def accept(self, decision_by, notes=None):
        """Acepta al aspirante en el programa."""
        self.admission_status = 'accepted'
        self.decision_at = now_local()
        self.decision_by = decision_by
        self.decision_notes = notes
        self.rejection_type = None
        self.correction_required = None

    def reject(self, decision_by, rejection_type='full', notes=None, correction_required=None):
        """
        Rechaza al aspirante.

        Args:
            decision_by: ID del usuario que toma la decision
            rejection_type: 'full' para rechazo total, 'partial' para solicitar correcciones
            notes: Notas sobre la decision
            correction_required: Descripcion de lo que debe corregir (si es parcial)
        """
        self.admission_status = 'rejected'
        self.decision_at = now_local()
        self.decision_by = decision_by
        self.decision_notes = notes
        self.rejection_type = rejection_type
        self.correction_required = correction_required

    def defer(self, decision_by, notes=None):
        """Difiere la inscripcion del aspirante para otro periodo."""
        self.admission_status = 'deferred'
        self.decision_at = now_local()
        self.decision_by = decision_by
        self.decision_notes = notes
