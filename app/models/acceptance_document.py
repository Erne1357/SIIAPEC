# app/models/acceptance_document.py
from app import db
from app.models.mixins import PublicUUIDMixin
from app.utils.datetime_utils import now_local


class AcceptanceDocument(PublicUUIDMixin, db.Model):
    """
    Documentos de aceptacion e inscripcion.

    Tipos de documentos (document_type):
    - acceptance_letter: Carta de aceptacion (coordinador sube, aspirante descarga)
    - course_schedule:   Tira de materias   (coordinador sube, aspirante descarga)
    - enrollment_receipt: Boleta de servicios escolares (aspirante sube, coordinador revisa)

    Estados (status):
    - pending:  No subido aun
    - uploaded: Subido, pendiente de revision (solo enrollment_receipt)
    - approved: Aprobado
    - rejected: Rechazado
    """
    __tablename__ = 'acceptance_document'

    id = db.Column(db.Integer, primary_key=True)
    user_program_id = db.Column(db.Integer, db.ForeignKey('user_program.id'), nullable=False)
    document_type = db.Column(db.String(30), nullable=False)
    file_path = db.Column(db.String(500), nullable=True)

    uploaded_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    uploaded_at = db.Column(db.DateTime, nullable=True)

    status = db.Column(db.String(20), nullable=False, default='pending')

    reviewed_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    review_notes = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=now_local, nullable=False)
    updated_at = db.Column(db.DateTime, default=now_local, onupdate=now_local, nullable=False)

    # Relaciones
    user_program = db.relationship('UserProgram', back_populates='acceptance_documents')
    uploaded_by = db.relationship('User', foreign_keys=[uploaded_by_id])
    reviewed_by = db.relationship('User', foreign_keys=[reviewed_by_id])

    def to_dict(self):
        # `id`, `uploaded_by_id` and `reviewed_by_id` are published as UUIDs;
        # `user_program_id` stays an integer (UserProgram carries no public
        # handle). Key names are deliberately unchanged — see Submission.to_dict.
        # `file_path` publica sólo el basename y `file_url` nombra la fila —
        # mismo razonamiento que en Submission.to_dict: el valor almacenado
        # empieza por `<user_id>/`.
        from app.models.user import User
        from app.services import file_access_service
        from app.services.public_id_service import uuid_for

        return {
            'id': str(self.uuid) if self.uuid else None,
            'user_program_id': self.user_program_id,
            'document_type': self.document_type,
            'file_path': file_access_service.document_download_name(self.file_path),
            'file_url': file_access_service.acceptance_document_url(self),
            'uploaded_by_id': uuid_for(User, self.uploaded_by_id),
            'uploaded_at': self.uploaded_at.isoformat() if self.uploaded_at else None,
            'status': self.status,
            'reviewed_by_id': uuid_for(User, self.reviewed_by_id),
            'reviewed_at': self.reviewed_at.isoformat() if self.reviewed_at else None,
            'review_notes': self.review_notes,
        }
