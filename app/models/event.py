from app import db
from datetime import datetime, timezone
from app.utils.datetime_utils import now_local

class Event(db.Model):
    __tablename__ = 'event'

    id = db.Column(db.Integer, primary_key=True)
    program_id = db.Column(db.Integer, db.ForeignKey('program.id', ondelete='CASCADE', onupdate='CASCADE'))
    academic_period_id = db.Column(db.Integer, db.ForeignKey('academic_period.id', ondelete='SET NULL'), nullable=True)
    visibility = db.Column(db.String(20), nullable=False, default='public', server_default='public')
    # Values: 'public' | 'private'
    type = db.Column(db.String(50), nullable=False, default='interview')
    title = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text)
    location = db.Column(db.String(200))
    created_by = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE', onupdate='CASCADE'), nullable=False)
    visible_to_students = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=now_local)
    updated_at = db.Column(db.DateTime, default=now_local, onupdate=now_local, nullable=False)
    
    capacity_type = db.Column(db.String(20), nullable=False, default='single')  # single|multiple|unlimited
    max_capacity = db.Column(db.Integer, nullable=True)  # null para unlimited
    requires_registration = db.Column(db.Boolean, nullable=False, default=True)
    allows_attendance_tracking = db.Column(db.Boolean, nullable=False, default=False)
    reminders_enabled = db.Column(db.Boolean, nullable=False, default=True, server_default=db.text('true'))
    status = db.Column(db.String(20), nullable=False, default='published')  # draft|published|ongoing|completed|cancelled

    event_date = db.Column(db.DateTime, nullable=True)  # Fecha/hora general del evento
    event_end_date = db.Column(db.DateTime, nullable=True)  # Para eventos de varios días

    academic_period = db.relationship('AcademicPeriod')
    windows = db.relationship('EventWindow', back_populates='event', cascade='all, delete-orphan')
    
    def to_dict(self):
        # Ids that name a User row are published as their UUID handle;
        # key names are unchanged on purpose.
        from app.models.user import User
        from app.services.public_id_service import uuid_for

        return {
            'id': self.id,
            'program_id': self.program_id,
            'type': self.type,
            'title': self.title,
            'description': self.description,
            'location': self.location,
            'created_by': uuid_for(User, self.created_by),
            'visible_to_students': self.visible_to_students,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'capacity_type': self.capacity_type,
            'max_capacity': self.max_capacity,
            'requires_registration': self.requires_registration,
            'allows_attendance_tracking': self.allows_attendance_tracking,
            'status': self.status,
            'event_date': self.event_date.isoformat() if self.event_date else None,
            'event_end_date': self.event_end_date.isoformat() if self.event_end_date else None,
            'academic_period_id': self.academic_period_id,
            'visibility': self.visibility,
            'reminders_enabled': self.reminders_enabled,
        }


class EventWindow(db.Model):
    __tablename__ = 'event_window'

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id', ondelete='CASCADE', onupdate='CASCADE'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    slot_minutes = db.Column(db.Integer, nullable=False)
    timezone = db.Column(db.String(50), default='America/Ciudad_Juarez')
    created_at = db.Column(db.DateTime, nullable=False, default=now_local)
    updated_at = db.Column(db.DateTime, default=now_local, onupdate=now_local, nullable=False)
    
    # ========== NUEVOS CAMPOS ==========
    slots_generated = db.Column(db.Boolean, nullable=False, default=False)
    current_capacity = db.Column(db.Integer, nullable=False, default=0)

    event = db.relationship('Event', back_populates='windows')
    slots = db.relationship('EventSlot', back_populates='window', cascade='all, delete-orphan')
    
    def to_dict(self):
        return {
            'id': self.id,
            'event_id': self.event_id,
            'date': self.date.isoformat() if self.date else None,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'end_time': self.end_time.isoformat() if self.end_time else None,
            'slot_minutes': self.slot_minutes,
            'timezone': self.timezone,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'slots_generated': self.slots_generated,
            'current_capacity': self.current_capacity
        }


class EventSlot(db.Model):
    __tablename__ = 'event_slot'

    id = db.Column(db.Integer, primary_key=True)
    event_window_id = db.Column(db.Integer, db.ForeignKey('event_window.id', ondelete='CASCADE', onupdate='CASCADE'), nullable=False)
    starts_at = db.Column(db.DateTime, nullable=False)
    ends_at = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='free')
    held_by = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='SET NULL', onupdate='CASCADE'))
    hold_expires_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=now_local)
    updated_at = db.Column(db.DateTime, default=now_local, onupdate=now_local, nullable=False)

    window = db.relationship('EventWindow', back_populates='slots')
    
    def to_dict(self):
        return {
            'id': self.id,
            'event_window_id': self.event_window_id,
            'starts_at': self.starts_at.isoformat() if self.starts_at else None,
            'ends_at': self.ends_at.isoformat() if self.ends_at else None,
            'status': self.status,
            'held_by': self.held_by,
            'hold_expires_at': self.hold_expires_at.isoformat() if self.hold_expires_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class EventAttendance(db.Model):
    """Nueva tabla para eventos con capacidad múltiple/ilimitada"""
    __tablename__ = 'event_attendance'

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id', ondelete='CASCADE', onupdate='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE', onupdate='CASCADE'), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='registered')  # registered|attended|no_show
    registered_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now())
    attended_at = db.Column(db.DateTime, nullable=True)
    notes = db.Column(db.Text, nullable=True)

    # Relaciones
    event = db.relationship('Event')
    user = db.relationship('User')
    
    def to_dict(self):
        # Ids that name a User row are published as their UUID handle;
        # key names are unchanged on purpose.
        from app.models.user import User
        from app.services.public_id_service import uuid_for

        return {
            'id': self.id,
            'event_id': self.event_id,
            'user_id': uuid_for(User, self.user_id),
            'status': self.status,
            'registered_at': self.registered_at.isoformat() if self.registered_at else None,
            'attended_at': self.attended_at.isoformat() if self.attended_at else None,
            'notes': self.notes
        }


class EventInvitation(db.Model):
    """Invitaciones a eventos para estudiantes específicos"""
    __tablename__ = 'event_invitation'
    
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    invited_by = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'))
    status = db.Column(db.String(20), nullable=False, default='pending')  # pending|accepted|rejected
    invited_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now())
    responded_at = db.Column(db.DateTime)
    notes = db.Column(db.Text)
    
    # Relaciones
    event = db.relationship('Event', foreign_keys=[event_id])
    user = db.relationship('User', foreign_keys=[user_id])
    inviter = db.relationship('User', foreign_keys=[invited_by])
    
    def to_dict(self):
        # Ids that name a User row are published as their UUID handle;
        # key names are unchanged on purpose.
        from app.models.user import User
        from app.services.public_id_service import uuid_for

        return {
            'id': self.id,
            'event_id': self.event_id,
            'user_id': uuid_for(User, self.user_id),
            'invited_by': uuid_for(User, self.invited_by),
            'status': self.status,
            'invited_at': self.invited_at.isoformat() if self.invited_at else None,
            'responded_at': self.responded_at.isoformat() if self.responded_at else None,
            'notes': self.notes
        }


class EventHost(db.Model):
    """Ponentes/presentadores de un evento. Puede ser usuario interno (user_id) o externo (external_name)."""
    __tablename__ = 'event_host'

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    external_name = db.Column(db.String(150), nullable=True)
    external_bio = db.Column(db.Text, nullable=True)
    external_photo_path = db.Column(db.String(255), nullable=True)
    role_label = db.Column(db.String(100), nullable=False)
    display_order = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=now_local)

    event = db.relationship(
        'Event',
        backref=db.backref(
            'hosts',
            cascade='all, delete-orphan',
            order_by='EventHost.display_order'
        )
    )
    user = db.relationship('User')

    __table_args__ = (
        db.CheckConstraint(
            '(user_id IS NOT NULL) OR (external_name IS NOT NULL)',
            name='ck_event_host_identity'
        ),
    )

    def to_dict(self):
        # Ids that name a User row are published as their UUID handle;
        # key names are unchanged on purpose.
        from app.models.user import User
        from app.services.public_id_service import uuid_for

        return {
            'id': self.id,
            'event_id': self.event_id,
            'user_id': uuid_for(User, self.user_id),
            'external_name': self.external_name,
            'external_bio': self.external_bio,
            'external_photo_path': self.external_photo_path,
            'role_label': self.role_label,
            'display_order': self.display_order,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class EventImage(db.Model):
    """Imágenes de un evento. Solo una con is_cover=True por evento (validado en servicio)."""
    __tablename__ = 'event_image'

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id', ondelete='CASCADE'), nullable=False)
    path = db.Column(db.String(500), nullable=False)
    caption = db.Column(db.String(200), nullable=True)
    is_cover = db.Column(db.Boolean, nullable=False, default=False)
    display_order = db.Column(db.Integer, nullable=False, default=0)
    uploaded_at = db.Column(db.DateTime, nullable=False, default=now_local)

    event = db.relationship(
        'Event',
        backref=db.backref(
            'images',
            cascade='all, delete-orphan',
            order_by='EventImage.display_order'
        )
    )

    def to_dict(self):
        return {
            'id': self.id,
            'event_id': self.event_id,
            'path': self.path,
            'caption': self.caption,
            'is_cover': self.is_cover,
            'display_order': self.display_order,
            'uploaded_at': self.uploaded_at.isoformat() if self.uploaded_at else None,
        }


class EventReminderLog(db.Model):
    """Registro de recordatorios enviados. Idempotente vía UniqueConstraint."""
    __tablename__ = 'event_reminder_log'

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointment.id', ondelete='SET NULL'), nullable=True)
    reminder_type = db.Column(db.String(10), nullable=False)  # '24h' | '2h'
    sent_at = db.Column(db.DateTime, nullable=False, default=now_local)

    event = db.relationship('Event')
    user = db.relationship('User')
    appointment = db.relationship('Appointment')

    __table_args__ = (
        db.UniqueConstraint(
            'event_id', 'user_id', 'reminder_type', 'appointment_id',
            name='uq_event_reminder_once'
        ),
        db.Index('ix_event_reminder_lookup', 'event_id', 'user_id', 'reminder_type'),
    )

    def to_dict(self):
        # Ids that name a User row are published as their UUID handle;
        # key names are unchanged on purpose.
        from app.models.user import User
        from app.services.public_id_service import uuid_for

        return {
            'id': self.id,
            'event_id': self.event_id,
            'user_id': uuid_for(User, self.user_id),
            'appointment_id': self.appointment_id,
            'reminder_type': self.reminder_type,
            'sent_at': self.sent_at.isoformat() if self.sent_at else None,
        }