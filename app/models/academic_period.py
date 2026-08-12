from app import db
from app.utils.datetime_utils import now_local

class AcademicPeriod(db.Model):
    """
    Modelo para gestionar periodos académicos.

    El código sigue el formato YYYYN donde:
    - YYYY: Año (ej: 2025)
    - N: Número de periodo (1=Ene-Jun, 2=Verano, 3=Ago-Dic)

    Ejemplo: 20253 = Agosto-Diciembre 2025
    """
    __tablename__ = 'academic_period'

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(5), unique=True, nullable=False)  # Ej: "20253"
    name = db.Column(db.String(100), nullable=False)  # Ej: "Agosto-Diciembre 2025"

    # Fechas del periodo académico (cuando se cursa)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)

    # Fechas de inscripción/admisión (cuando se hace el proceso)
    admission_start_date = db.Column(db.Date, nullable=False)
    admission_end_date = db.Column(db.Date, nullable=False)

    # Estado
    is_active = db.Column(db.Boolean, default=False, nullable=False)
    status = db.Column(db.String(20), default='upcoming', nullable=False)
    # Estados: upcoming, active, admission_closed, completed

    # Metadatos
    created_at = db.Column(db.DateTime, default=now_local, nullable=False)
    updated_at = db.Column(db.DateTime, default=now_local, onupdate=now_local)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    # Relación con el creador
    creator = db.relationship('User', foreign_keys=[created_by])

    # Relaciones inversas
    submissions = db.relationship('Submission', back_populates='academic_period', lazy='dynamic')
    user_programs = db.relationship('UserProgram', back_populates='admission_period', lazy='dynamic')
    semester_enrollments = db.relationship('SemesterEnrollment', back_populates='academic_period', lazy='dynamic')

    def to_dict(self):
        # `coordinator_id` / `created_by` name a User row and are published as
        # that user's UUID handle; the program's own id stays an integer.
        from app.models.user import User
        from app.services.public_id_service import uuid_for

        return {
            'id': self.id,
            'code': self.code,
            'name': self.name,
            'start_date': self.start_date.isoformat() if self.start_date else None,
            'end_date': self.end_date.isoformat() if self.end_date else None,
            'admission_start_date': self.admission_start_date.isoformat() if self.admission_start_date else None,
            'admission_end_date': self.admission_end_date.isoformat() if self.admission_end_date else None,
            'is_active': self.is_active,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'created_by': uuid_for(User, self.created_by)
        }

    @staticmethod
    def validate_code(code):
        """
        Valida que el código tenga el formato correcto YYYYN.
        Retorna True si es válido, False si no.
        """
        if not code or len(code) != 5:
            return False

        try:
            year = int(code[:4])
            period_num = int(code[4])

            # Año debe ser razonable (2020-2100)
            if year < 2020 or year > 2100:
                return False

            # Número de periodo debe ser 1, 2 o 3
            if period_num not in [1, 2, 3]:
                return False

            return True
        except ValueError:
            return False

    @staticmethod
    def get_active_period():
        """
        Obtiene el periodo activo actual.
        Retorna None si no hay ninguno activo.
        """
        return AcademicPeriod.query.filter_by(is_active=True).first()

    def activate(self):
        """
        Activa este periodo y desactiva cualquier otro que esté activo.
        """
        # Desactivar todos los demás periodos
        AcademicPeriod.query.filter(
            AcademicPeriod.id != self.id,
            AcademicPeriod.is_active == True
        ).update({'is_active': False})

        # Activar este periodo
        self.is_active = True
        self.status = 'active'

    def __repr__(self):
        return f'<AcademicPeriod {self.code}: {self.name}>'
