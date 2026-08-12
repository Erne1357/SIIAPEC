# app/models/user_history.py
from app import db
from datetime import datetime, timezone
from app.utils.datetime_utils import now_local

class UserHistory(db.Model):
    """
    Registra todas las acciones administrativas realizadas sobre un usuario.
    """
    __tablename__ = 'user_history'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    admin_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    action = db.Column(db.String(50), nullable=False)
    details = db.Column(db.Text, nullable=True)
    timestamp = db.Column(db.DateTime, default=now_local, nullable=False)
    
    # Relaciones
    user = db.relationship('User', foreign_keys=[user_id], overlaps="histories")
    admin = db.relationship('User', foreign_keys=[admin_id])
    
    def to_dict(self):
        # `id` stays an integer — UserHistory has no public handle and its id
        # never leaves the server as an addressable identifier. `user_id` and
        # `admin_id` DO name User rows, so they go out as UUIDs; the stored
        # columns are untouched, this is a projection only.
        from app.models.user import User
        from app.services.public_id_service import uuid_for

        return {
            'id': self.id,
            'user_id': uuid_for(User, self.user_id),
            'admin_id': uuid_for(User, self.admin_id),
            'admin_name': f"{self.admin.first_name} {self.admin.last_name}" if self.admin else "Sistema",
            'action': self.action,
            'action_label': self.get_action_label(),
            'details': self.details,
            'timestamp': self.timestamp.isoformat()
        }
    
    def get_action_label(self):
        """Retorna etiqueta legible del tipo de acción"""
        # Importar aquí para evitar dependencias circulares
        from app.services.user_history_service import UserHistoryService
        return UserHistoryService.get_action_label(self.action)