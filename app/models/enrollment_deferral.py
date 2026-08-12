# app/models/enrollment_deferral.py
"""
Modelo para registrar diferimientos de inscripción (Fase 7).

Flujo:
- coordinator_initiated: coordinador difiere directamente → status='active'
- applicant_requested:   aspirante solicita → status='pending' hasta que coordinador aprueba

Estados:
  pending   → solicitud del aspirante esperando aprobación del coordinador
  active    → diferimiento vigente (aprobado o iniciado por coordinador)
  used      → el aspirante completó la inscripción en el periodo diferido
  rejected  → coordinador rechazó la solicitud del aspirante
  expired   → el periodo diferido pasó sin que el aspirante se inscribiera
"""

from app import db
from app.utils.datetime_utils import now_local


class EnrollmentDeferral(db.Model):
    __tablename__ = 'enrollment_deferral'

    id = db.Column(db.Integer, primary_key=True)

    # FK al proceso del aspirante
    user_program_id = db.Column(
        db.Integer, db.ForeignKey('user_program.id'), nullable=False
    )

    # Periodo original en que fue aceptado
    original_period_id = db.Column(
        db.Integer, db.ForeignKey('academic_period.id'), nullable=False
    )

    # Periodo al que se difiere (asignado automáticamente = siguiente periodo)
    deferred_to_period_id = db.Column(
        db.Integer, db.ForeignKey('academic_period.id'), nullable=True
    )

    # Número de diferimiento (1 o 2). Máximo 2 por aspirante/programa.
    deferral_number = db.Column(db.Integer, nullable=False, default=1)

    # Estado del diferimiento
    status = db.Column(db.String(20), nullable=False, default='pending')

    # Quién inició: 'coordinator' | 'applicant'
    requested_by = db.Column(db.String(20), nullable=False, default='coordinator')

    # Razón del diferimiento (libre)
    reason = db.Column(db.Text, nullable=True)

    # Coordinador que aprobó/rechazó (null si fue iniciado directamente por él)
    reviewed_by_id = db.Column(
        db.Integer, db.ForeignKey('user.id'), nullable=True
    )
    reviewed_at = db.Column(db.DateTime, nullable=True)
    review_notes = db.Column(db.Text, nullable=True)

    # Metadatos
    created_at = db.Column(db.DateTime, default=now_local, nullable=False)

    # Notificación de vencimiento próximo
    expiry_notified_at = db.Column(db.DateTime, nullable=True)

    # ── Relaciones ────────────────────────────────────────────────────────────
    user_program = db.relationship('UserProgram', back_populates='enrollment_deferrals')
    original_period = db.relationship(
        'AcademicPeriod', foreign_keys=[original_period_id]
    )
    deferred_to_period = db.relationship(
        'AcademicPeriod', foreign_keys=[deferred_to_period_id]
    )
    reviewed_by = db.relationship('User', foreign_keys=[reviewed_by_id])

    # ── Helpers ───────────────────────────────────────────────────────────────

    def to_dict(self):
        # Public payload: ids that name a User, a Submission or an Archive go
        # out as their UUID handle. Key names are unchanged on purpose.
        from app.models.user import User
        from app.services.public_id_service import uuid_for

        return {
            'id': self.id,
            'user_program_id': self.user_program_id,
            'original_period_id': self.original_period_id,
            'original_period_name': self.original_period.name if self.original_period else None,
            'deferred_to_period_id': self.deferred_to_period_id,
            'deferred_to_period_name': (
                self.deferred_to_period.name if self.deferred_to_period else None
            ),
            'deferral_number': self.deferral_number,
            'status': self.status,
            'requested_by': uuid_for(User, self.requested_by),
            'reason': self.reason,
            'reviewed_by_id': uuid_for(User, self.reviewed_by_id),
            'reviewed_at': self.reviewed_at.isoformat() if self.reviewed_at else None,
            'review_notes': self.review_notes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return (
            f'<EnrollmentDeferral #{self.id} '
            f'up={self.user_program_id} #{self.deferral_number} {self.status}>'
        )
