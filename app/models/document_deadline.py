# app/models/document_deadline.py
"""
Ventana de entrega de un documento de permanencia.

El coordinador crea estas ventanas por cada documento que requiere
entrega en un periodo académico. Puede haber múltiples ventanas
para el mismo archivo en el mismo periodo (ej: 2 reportes de avance
para doctorado, ventanas mensuales para CONACyT).
"""

from app import db
from app.utils.datetime_utils import now_local


class DocumentDeadline(db.Model):
    __tablename__ = 'document_deadline'

    id = db.Column(db.Integer, primary_key=True)

    # Qué documento y para qué programa
    archive_id = db.Column(db.Integer, db.ForeignKey('archive.id'), nullable=False)
    program_id = db.Column(db.Integer, db.ForeignKey('program.id'), nullable=False)
    academic_period_id = db.Column(db.Integer, db.ForeignKey('academic_period.id'), nullable=False)

    # Para distinguir 1er y 2do reporte (Doctorado) o mes de entrega (CONACyT)
    # Ejemplos: 1, 2 (reportes) | 1-12 (meses para CONACyT)
    sequence = db.Column(db.Integer, default=1, nullable=False)

    # Etiqueta descriptiva mostrada al estudiante
    # Ejemplos: "1er Reporte Semestral", "2do Reporte Semestral",
    #            "Formato CONACyT — Enero", "Formato CONACyT — Febrero"
    label = db.Column(db.String(100), nullable=False)

    # Ventana de entrega
    opens_at = db.Column(db.DateTime, nullable=True)   # NULL = ya abierta
    closes_at = db.Column(db.DateTime, nullable=True)  # NULL = sin fecha límite

    # Control manual del coordinador (puede anular las fechas)
    is_open = db.Column(db.Boolean, default=True, nullable=False)

    # Soft-archive: en lugar de borrar la ventana (lo cual perdería trazabilidad
    # de las submissions ligadas), se archiva. Las archivadas no aparecen en el
    # panel del coordinador ni del estudiante, pero se preservan en BD.
    is_archived = db.Column(db.Boolean, default=False, nullable=False)
    archived_at = db.Column(db.DateTime, nullable=True)
    archived_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    # Quién lo creó
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=now_local, nullable=False)
    updated_at = db.Column(db.DateTime, default=now_local, onupdate=now_local, nullable=False)

    # Relaciones
    archive = db.relationship('Archive', backref=db.backref('deadlines', lazy='dynamic'))
    program = db.relationship('Program', backref=db.backref('document_deadlines', lazy='dynamic'))
    academic_period = db.relationship(
        'AcademicPeriod',
        backref=db.backref('document_deadlines', lazy='dynamic')
    )
    creator = db.relationship('User', foreign_keys=[created_by])

    @property
    def is_currently_open(self) -> bool:
        """
        La ventana está abierta si:
        1. is_open=True (control manual)
        2. Y si hay fechas, la fecha actual está dentro del rango.
        """
        if not self.is_open:
            return False
        now = now_local().replace(tzinfo=None)
        if self.opens_at and now < self.opens_at:
            return False
        if self.closes_at and now > self.closes_at:
            return False
        return True

    def to_dict(self):
        # Public payload: ids that name a User, a Submission or an Archive go
        # out as their UUID handle. Key names are unchanged on purpose.
        from app.models.archive import Archive
        from app.models.user import User
        from app.services.public_id_service import uuid_for

        return {
            'id': self.id,
            'archive_id': uuid_for(Archive, self.archive_id),
            'archive_name': self.archive.name if self.archive else None,
            'program_id': self.program_id,
            'academic_period_id': self.academic_period_id,
            'sequence': self.sequence,
            'label': self.label,
            'opens_at': self.opens_at.isoformat() if self.opens_at else None,
            'closes_at': self.closes_at.isoformat() if self.closes_at else None,
            'is_open': self.is_open,
            'is_currently_open': self.is_currently_open,
            'is_archived': self.is_archived,
            'archived_at': self.archived_at.isoformat() if self.archived_at else None,
            'archived_by': uuid_for(User, self.archived_by),
            'created_by': uuid_for(User, self.created_by),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
