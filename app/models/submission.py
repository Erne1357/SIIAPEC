from app import db
from datetime import datetime,timezone
from app.models.mixins import PublicUUIDMixin
from app.utils.datetime_utils import now_local

class Submission(PublicUUIDMixin, db.Model):
    __tablename__ = 'submission'

    id = db.Column(db.Integer, primary_key=True)
    # Nullable: permite al coordinador aprobar/rechazar sin archivo
    # (ej: validación de examen presencial). Aspirantes/estudiantes siempre
    # deben subir archivo — la validación se hace en el endpoint.
    file_path = db.Column(db.String(200), nullable=True)
    status = db.Column(db.String(50), nullable=False)
    upload_date = db.Column(db.DateTime, default=now_local)
    review_date = db.Column(db.DateTime)
    reviewer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    updated_at = db.Column(db.DateTime, default=now_local, onupdate=now_local, nullable=False)
    reviewer_comment = db.Column(db.Text, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    archive_id = db.Column(db.Integer,db.ForeignKey('archive.id') , nullable = False)
    program_step_id = db.Column(db.Integer, db.ForeignKey('program_step.id') , nullable = False)
    semester = db.Column(db.Integer, nullable=True)
    academic_period_id = db.Column(db.Integer, db.ForeignKey('academic_period.id'), nullable=True)
    uploaded_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    uploaded_by_role = db.Column(db.String(20))  # 'student' | 'coordinator'
    deadline_at = db.Column(db.DateTime)         # fecha límite efectiva (si hay prórroga)
    is_in_extension = db.Column(db.Boolean, default=False, nullable=False)

    # FK nullable: solo submissions de permanencia con ventana configurada
    document_deadline_id = db.Column(
        db.Integer, db.ForeignKey('document_deadline.id'), nullable=True
    )

    user          = db.relationship('User',foreign_keys=[user_id],back_populates='submissions')
    reviewer      = db.relationship('User',foreign_keys=[reviewer_id],back_populates='reviews')
    archive       = db.relationship('Archive',back_populates='submissions')
    program_step  = db.relationship('ProgramStep',back_populates='submissions')
    uploader = db.relationship('User', foreign_keys=[uploaded_by], viewonly=True)
    academic_period = db.relationship('AcademicPeriod', back_populates='submissions')
    document_deadline = db.relationship(
        'DocumentDeadline',
        backref=db.backref('submissions', lazy='dynamic')
    )

    def __init__(self, file_path, status, user_id, archive_id, program_step_id, semester, review_date=None, reviewer_id=None, reviewer_comment=None, uploaded_by=None, uploaded_by_role=None, deadline_at=None, is_in_extension=False):
        self.file_path = file_path
        self.status = status
        self.user_id = user_id
        self.archive_id = archive_id
        self.program_step_id = program_step_id
        self.review_date = review_date
        self.semester = semester
        self.reviewer_id = reviewer_id
        self.reviewer_comment = reviewer_comment
        self.uploaded_by = uploaded_by
        self.uploaded_by_role = uploaded_by_role
        self.deadline_at = deadline_at
        self.is_in_extension = is_in_extension
    
    def to_dict(self):
        # Public payload: every id that names a User, a Submission or an
        # Archive is published as its UUID, never as the integer primary key.
        # The key NAMES are unchanged on purpose — `CROSS_PROGRAM_SUMMARY_FIELDS`
        # in program_scope_service is an allow-list by key name, and renaming
        # would silently drop fields from every cross-program projection.
        # `program_step_id`, `academic_period_id` and `document_deadline_id`
        # stay integers: those models carry no public handle.
        # `file_path` publica SÓLO el basename. El valor almacenado empieza por
        # `<user_id>/`, así que publicarlo entero entregaba el id entero del
        # alumno —y una ruta con la que se reconstruía el URL viejo— en cada
        # payload. El nombre humano es lo que la UI muestra; `file_url` es la
        # única forma de llegar a los bytes y nombra la FILA, no la ruta.
        from app.models.archive import Archive
        from app.models.user import User
        from app.services import file_access_service
        from app.services.public_id_service import uuid_for

        return {
            'id': str(self.uuid) if self.uuid else None,
            'file_path': file_access_service.document_download_name(self.file_path),
            'file_url': file_access_service.submission_file_url(self),
            'status': self.status,
            'upload_date': self.upload_date.isoformat() if self.upload_date else None,
            'review_date': self.review_date.isoformat() if self.review_date else None,
            'reviewer_id': uuid_for(User, self.reviewer_id),
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'reviewer_comment': self.reviewer_comment,
            'user_id': uuid_for(User, self.user_id),
            'archive_id': uuid_for(Archive, self.archive_id),
            'program_step_id': self.program_step_id,
            'semester': self.semester,
            'academic_period_id': self.academic_period_id,
            'uploaded_by': uuid_for(User, self.uploaded_by),
            'uploaded_by_role': self.uploaded_by_role,
            'deadline_at': self.deadline_at.isoformat() if self.deadline_at else None,
            'is_in_extension': self.is_in_extension,
            'document_deadline_id': self.document_deadline_id,
        }
