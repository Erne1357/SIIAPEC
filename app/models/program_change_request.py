from app import db
from datetime import datetime, timezone
from sqlalchemy.orm import validates
from app.utils.datetime_utils import now_local

#: Estados de una solicitud de cambio de programa.
#:
#:   pending   — registrada, esperando a un coordinador.
#:   approved  — un coordinador la aprobó y el servicio aplicó el cambio.
#:   rejected  — un coordinador la negó.
#:   cancelled — se abortó (el aspirante, o un fallo al ejecutar el cambio).
#:   executed  — el propio aspirante ejecutó el cambio por AUTOSERVICIO, bajo
#:               las reglas de `ProgramTransferService.validate_transfer`.
#:               No es 'approved': nadie la aprobó y no hay `decided_by`;
#:               firmarla como aprobada por el solicitante sería una firma
#:               falsa. Cualquier reporte que cuente traslados consumados debe
#:               usar `COMPLETED_STATUSES`, no sólo 'approved'.
PENDING = 'pending'
APPROVED = 'approved'
REJECTED = 'rejected'
CANCELLED = 'cancelled'
EXECUTED = 'executed'

#: Estados que un coordinador puede fijar desde `decide_request`. 'executed'
#: NO está aquí a propósito: no es una decisión, es un hecho del autoservicio.
DECISION_STATUSES = frozenset({APPROVED, REJECTED, CANCELLED})

#: El cambio de programa ocurrió de verdad (por decisión o por autoservicio).
COMPLETED_STATUSES = frozenset({APPROVED, EXECUTED})

#: Toda la enumeración. La columna es String(20) sin CHECK en la base, así que
#: la validación vive en el modelo y cubre cualquier escritura desde Python.
VALID_STATUSES = frozenset({PENDING, APPROVED, REJECTED, CANCELLED, EXECUTED})


class ProgramChangeRequest(db.Model):
    __tablename__ = 'program_change_request'

    id = db.Column(db.Integer, primary_key=True)
    applicant_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE', onupdate='CASCADE'), nullable=False)
    from_program_id = db.Column(db.Integer, db.ForeignKey('program.id', ondelete='CASCADE', onupdate='CASCADE'), nullable=False)
    to_program_id = db.Column(db.Integer, db.ForeignKey('program.id', ondelete='CASCADE', onupdate='CASCADE'), nullable=False)

    reason = db.Column(db.Text)
    # pending|approved|rejected|cancelled|executed — ver VALID_STATUSES arriba.
    status = db.Column(db.String(20), nullable=False, default=PENDING)
    decided_by = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='SET NULL', onupdate='CASCADE'))
    decided_at = db.Column(db.DateTime)

    created_at = db.Column(db.DateTime, nullable=False, default=now_local)
    updated_at = db.Column(db.DateTime, default=now_local, onupdate=now_local, nullable=False)
    
    @validates('status')
    def _validate_status(self, key, value):
        """
        Rechaza cualquier estado fuera de la enumeración.

        La columna no tiene CHECK en la base: sin esto, un typo o un estado
        inventado se persiste en silencio y los reportes dejan de cuadrar sin
        que nadie se entere.
        """
        if value not in VALID_STATUSES:
            raise ValueError(
                f"status inválido para ProgramChangeRequest: {value!r}. "
                f"Valores permitidos: {sorted(VALID_STATUSES)}"
            )
        return value

    def to_dict(self):
        return {
            'id': self.id,
            'applicant_id': self.applicant_id,
            'from_program_id': self.from_program_id,
            'to_program_id': self.to_program_id,
            'reason': self.reason,
            'status': self.status,
            'decided_by': self.decided_by,
            'decided_at': self.decided_at.isoformat() if self.decided_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
