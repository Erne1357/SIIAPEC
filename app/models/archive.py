from app import db
from app.models.mixins import PublicUUIDMixin
from app.utils.datetime_utils import now_local

class Archive(PublicUUIDMixin, db.Model):
    __tablename__ = 'archive'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    is_downloadable = db.Column(db.Boolean, default=True)
    is_uploadable = db.Column(db.Boolean, default=True)
    file_path = db.Column(db.String(200))
    step_id = db.Column(db.Integer, db.ForeignKey('step.id'), nullable=False)
    allow_coordinator_upload = db.Column(db.Boolean,default=False,nullable=False)
    allow_extension_request = db.Column(db.Boolean, default=False, nullable=False)
    # Meses de validez del documento; NULL = sin expiración (válido indefinidamente)
    validity_months = db.Column(db.Integer, nullable=True)
    # False = desactivado (no visible para estudiantes/coordinadores, pero conservado por historial)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    # Clave única para identificar archives especiales por código (ej. 'leave_request')
    archive_key = db.Column(db.String(50), unique=True, nullable=True)
    created_at = db.Column(db.DateTime, default=now_local, nullable=False)
    updated_at = db.Column(db.DateTime, default=now_local, onupdate=now_local, nullable=False)

    step = db.relationship("Step", back_populates="archives")
    submissions = db.relationship('Submission', back_populates='archive')

    def __init__(self, name, description, file_path, step_id, is_downloadable=True, is_uploadable=True):
        self.name = name
        self.description = description
        self.file_path = file_path
        self.is_downloadable = is_downloadable
        self.step_id = step_id
        self.is_uploadable = is_uploadable
    
    def to_dict(self):
        # `id` is the public UUID handle; `step_id` stays an integer because
        # Step carries no public handle. Key names unchanged on purpose.
        return {
            'id': str(self.uuid) if self.uuid else None,
            'name': self.name,
            'description': self.description,
            'is_downloadable': self.is_downloadable,
            'is_uploadable': self.is_uploadable,
            'file_path': self.file_path,
            'step_id': self.step_id,
            'allow_coordinator_upload': self.allow_coordinator_upload,
            'allow_extension_request': self.allow_extension_request,
            'validity_months': self.validity_months,
            'is_active': self.is_active,
            'archive_key': self.archive_key,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
