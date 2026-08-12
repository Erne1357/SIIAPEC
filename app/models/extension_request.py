# app/models/extension_request.py - Modelo actualizado
from app import db
from datetime import datetime, timezone
from app.utils.datetime_utils import now_local

class ExtensionRequest(db.Model):
    __tablename__ = 'extension_request'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    archive_id = db.Column(db.Integer, db.ForeignKey('archive.id', ondelete='CASCADE'), nullable=False)
    program_step_id = db.Column(db.Integer, db.ForeignKey('program_step.id', ondelete='CASCADE'), nullable=False)
    
    # Información de la solicitud
    requested_by = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='student')  # student | coordinator
    reason = db.Column(db.Text, nullable=False)
    requested_until = db.Column(db.DateTime, nullable=False)
    
    # Estado y decisión
    status = db.Column(db.String(20), nullable=False, default='pending')  # pending | granted | rejected | cancelled
    decided_by = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'))
    decided_at = db.Column(db.DateTime)
    granted_until = db.Column(db.DateTime)
    condition_text = db.Column(db.Text)  # Condiciones específicas de la prórroga
    
    # Auditoría
    created_at = db.Column(db.DateTime, nullable=False, default=now_local)
    updated_at = db.Column(db.DateTime, nullable=False, default=now_local, onupdate=now_local)
    
    # Relaciones
    user = db.relationship('User', foreign_keys=[user_id], backref='extension_requests')
    archive = db.relationship('Archive', backref='extension_requests')
    program_step = db.relationship('ProgramStep', backref='extension_requests')
    requester = db.relationship('User', foreign_keys=[requested_by])
    reviewer = db.relationship('User', foreign_keys=[decided_by])
    
    def __init__(self, user_id, archive_id, program_step_id, requested_by, reason, requested_until, role='student'):
        self.user_id = user_id
        self.archive_id = archive_id
        self.program_step_id = program_step_id
        self.requested_by = requested_by
        self.reason = reason
        self.requested_until = requested_until
        self.role = role
        self.status = 'pending'
        self.created_at = now_local()
        self.updated_at = now_local()
    
    def to_dict(self):
        # Public payload: ids that name a User, a Submission or an Archive go
        # out as their UUID handle. Key names are unchanged on purpose.
        from app.models.archive import Archive
        from app.models.user import User
        from app.services.public_id_service import uuid_for

        return {
            'id': self.id,
            'user_id': uuid_for(User, self.user_id),
            'archive_id': uuid_for(Archive, self.archive_id),
            'program_step_id': self.program_step_id,
            'requested_by': uuid_for(User, self.requested_by),
            'role': self.role,
            'reason': self.reason,
            'requested_until': self.requested_until.isoformat() if self.requested_until else None,
            'status': self.status,
            'decided_by': uuid_for(User, self.decided_by),
            'decided_at': self.decided_at.isoformat() if self.decided_at else None,
            'granted_until': self.granted_until.isoformat() if self.granted_until else None,
            'condition_text': self.condition_text,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }