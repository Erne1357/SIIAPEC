# app/models/semester_enrollment.py
from app import db
from app.models.mixins import PublicUUIDMixin
from app.utils.datetime_utils import now_local


class SemesterEnrollment(PublicUUIDMixin, db.Model):
    """
    Registro de inscripcion semestral del estudiante.

    Representa la participacion de un estudiante en un semestre especifico
    dentro de un periodo academico determinado.

    Estados (status):
    - pending:   Inicio de periodo, pendiente de confirmar pago/inscripcion
    - active:    Coordinador confirmo la inscripcion del semestre
    - completed: Semestre terminado exitosamente
    - on_leave:  Baja temporal (permiso)
    - dropped:   Baja definitiva del semestre

    Why this row carries a public UUID handle
    -----------------------------------------
    It is the SIXTH model with `PublicUUIDMixin`, and the reason is narrow: it
    owns two served files (`payment_proof_path`, `schedule_path`) and
    `/files/doc/<handle>/<slot>` needs an opaque way to name the row. Nothing
    else about this model became public — `id` stays an integer in `to_dict()`
    because no route is addressed by it and no payload key names it.
    """
    __tablename__ = 'semester_enrollment'

    id = db.Column(db.Integer, primary_key=True)

    user_program_id = db.Column(
        db.Integer, db.ForeignKey('user_program.id'), nullable=False
    )
    academic_period_id = db.Column(
        db.Integer, db.ForeignKey('academic_period.id'), nullable=False
    )

    # Numero de semestre que corresponde a esta inscripcion (1, 2, 3, ...)
    semester_number = db.Column(db.Integer, nullable=False)

    # Estado de la inscripcion semestral
    status = db.Column(db.String(30), default='pending', nullable=False)

    # Confirmacion por coordinador
    enrollment_confirmed = db.Column(db.Boolean, default=False, nullable=False)
    confirmed_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    confirmed_at = db.Column(db.DateTime, nullable=True)

    # Notas del coordinador
    notes = db.Column(db.Text, nullable=True)

    # Comprobante de pago (PDF opcional). Path relativo dentro de UPLOAD_FOLDER,
    # estructura: <user_id>/permanence/<filename>.pdf — servible vía /files/doc/<path>.
    payment_proof_path = db.Column(db.String(255), nullable=True)

    # Horario del semestre subido por el coordinador al confirmar la inscripción.
    # Mismo formato que payment_proof_path. NULL = aún no subido.
    schedule_path = db.Column(db.String(255), nullable=True)

    created_at = db.Column(db.DateTime, default=now_local, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=now_local, onupdate=now_local, nullable=False
    )

    # Relaciones
    user_program = db.relationship(
        'UserProgram', back_populates='semester_enrollments'
    )
    academic_period = db.relationship(
        'AcademicPeriod', back_populates='semester_enrollments'
    )
    confirmed_by_user = db.relationship('User', foreign_keys=[confirmed_by])

    def to_dict(self):
        # `payment_proof_path` / `schedule_path` are published as the BASENAME
        # only. The stored value starts with `<user_id>/`, so publishing it raw
        # handed every consumer the integer id of the student — the exact leak
        # the opaque URL exists to close. The basename is what the UI actually
        # shows ("Comprobante_marzo.pdf"); the `_url` keys are the only way to
        # reach the bytes and they name the ROW, never the path.
        from app.services import file_access_service

        return {
            'id': self.id,
            'user_program_id': self.user_program_id,
            'academic_period_id': self.academic_period_id,
            'semester_number': self.semester_number,
            'status': self.status,
            'enrollment_confirmed': self.enrollment_confirmed,
            'confirmed_by': self.confirmed_by,
            'confirmed_at': self.confirmed_at.isoformat() if self.confirmed_at else None,
            'notes': self.notes,
            'payment_proof_path': file_access_service.document_download_name(
                self.payment_proof_path),
            'payment_proof_url': file_access_service.enrollment_payment_proof_url(self),
            'schedule_path': file_access_service.document_download_name(
                self.schedule_path),
            'schedule_url': file_access_service.enrollment_schedule_url(self),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
