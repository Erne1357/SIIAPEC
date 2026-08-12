from app import db
from app.utils.datetime_utils import now_local


class UserPermission(db.Model):
    """
    Permisos directos asignados a un usuario específico.
    Se usan para delegación (coordinador → servicio social) y casos especiales.

    - program_id: scope opcional. NULL = permiso aplica a todos los programas.
    - granted_by: quién otorgó el permiso (obligatorio para trazabilidad).
    - expires_at: si es NULL, el permiso no tiene vencimiento.
    - is_active: False cuando el permiso fue revocado (se conserva el registro).
    """
    __tablename__ = 'user_permission'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    permission_id = db.Column(db.Integer, db.ForeignKey('permission.id'), nullable=False)
    program_id = db.Column(db.Integer, db.ForeignKey('program.id'), nullable=True)
    granted_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    granted_at = db.Column(db.DateTime, default=now_local, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    note = db.Column(db.Text, nullable=True)

    # Evitar duplicados activos: mismo usuario + permiso + programa
    __table_args__ = (
        db.UniqueConstraint(
            'user_id', 'permission_id', 'program_id',
            name='uq_user_permission_active'
        ),
    )

    # Relationships
    user = db.relationship(
        'User',
        foreign_keys=[user_id],
        back_populates='direct_permissions'
    )
    permission = db.relationship('Permission', back_populates='user_permissions')
    program = db.relationship('Program', foreign_keys=[program_id])
    granted_by_user = db.relationship('User', foreign_keys=[granted_by])

    def __init__(self, user_id, permission_id, granted_by, program_id=None, expires_at=None, note=None):
        self.user_id = user_id
        self.permission_id = permission_id
        self.granted_by = granted_by
        self.program_id = program_id
        self.expires_at = expires_at
        self.note = note

    @property
    def is_expired(self):
        if self.expires_at is None:
            return False
        return now_local() > self.expires_at

    @property
    def is_valid(self):
        return self.is_active and not self.is_expired

    def revoke(self):
        self.is_active = False

    def to_dict(self):
        # Public payload: ids that name a User, a Submission or an Archive go
        # out as their UUID handle. Key names are unchanged on purpose.
        from app.models.user import User
        from app.services.public_id_service import uuid_for

        return {
            'id': self.id,
            'user_id': uuid_for(User, self.user_id),
            'permission_id': self.permission_id,
            'permission_codename': self.permission.codename if self.permission else None,
            'permission_display_name': self.permission.display_name if self.permission else None,
            'program_id': self.program_id,
            'program_name': self.program.name if self.program else None,
            'granted_by': uuid_for(User, self.granted_by),
            'granted_by_name': (
                f'{self.granted_by_user.first_name} {self.granted_by_user.last_name}'
                if self.granted_by_user else None
            ),
            'granted_at': self.granted_at.isoformat() if self.granted_at else None,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'is_active': self.is_active,
            'is_expired': self.is_expired,
            'note': self.note,
        }

    def __repr__(self):
        return f'<UserPermission user={self.user_id} perm={self.permission_id} program={self.program_id}>'
