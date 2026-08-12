# app/models/document_template.py
"""
Plantillas de documentos configurables por programa.
Soporta plantillas HTML (→ PDF via weasyprint) y DOCX (→ descarga directa).

Variables disponibles en plantillas:
  {{student_name}}, {{student_curp}}, {{student_email}},
  {{program_name}}, {{program_level}},
  {{period_code}}, {{period_name}},
  {{acceptance_date}}, {{coordinator_name}}, {{current_date}},
  {{control_number}}

Variables adicionales para payment_reference:
  {{semester_number}}, {{payment_amount}}, {{payment_reference}}, {{due_date}}
"""
from app import db
from app.models.mixins import PublicUUIDMixin
from app.utils.datetime_utils import now_local

# Tipos de documento soportados
DOCUMENT_TYPES = {
    'acceptance_letter':        'Carta de Aceptación',
    'enrollment_confirmation':  'Confirmación de Inscripción',
    'course_schedule':          'Tira de Materias',
    'payment_reference':        'Referencia Bancaria de Pago',
}

# Formatos de plantilla soportados
TEMPLATE_FILE_TYPES = ('html', 'docx')


class DocumentTemplate(PublicUUIDMixin, db.Model):
    """
    Plantilla de documento asociada a un programa (o global si program_id es NULL).
    La plantilla con program_id específico tiene prioridad sobre la global.
    """
    __tablename__ = 'document_template'

    id = db.Column(db.Integer, primary_key=True)

    # NULL = plantilla global (aplica a todos los programas sin plantilla específica)
    program_id = db.Column(db.Integer, db.ForeignKey('program.id'), nullable=True)

    document_type = db.Column(db.String(50), nullable=False)
    # Tipos: acceptance_letter, enrollment_confirmation, course_schedule, payment_reference

    name = db.Column(db.String(100), nullable=False)
    # Nombre descriptivo, ej: "Carta de Aceptación — ITJ 2026"

    file_path = db.Column(db.String(500), nullable=False)
    # Ruta relativa desde instance/templates_sys/

    file_type = db.Column(db.String(10), nullable=False)
    # 'html' o 'docx'

    description = db.Column(db.Text, nullable=True)
    # Descripción opcional de cuándo usar esta plantilla

    is_active = db.Column(db.Boolean, default=True, nullable=False)

    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=now_local, nullable=False)
    updated_at = db.Column(db.DateTime, default=now_local, onupdate=now_local, nullable=False)

    # Relaciones
    program = db.relationship('Program', backref=db.backref('document_templates', lazy='dynamic'))
    creator = db.relationship('User', foreign_keys=[created_by])

    def to_dict(self):
        # `id` and `created_by` go out as UUIDs; `program_id` stays an integer.
        from app.models.user import User
        from app.services.public_id_service import uuid_for

        return {
            'id': str(self.uuid) if self.uuid else None,
            'program_id': self.program_id,
            'program_name': self.program.name if self.program else None,
            'document_type': self.document_type,
            'document_type_label': DOCUMENT_TYPES.get(self.document_type, self.document_type),
            'name': self.name,
            'file_path': self.file_path,
            'file_type': self.file_type,
            'description': self.description,
            'is_active': self.is_active,
            'is_global': self.program_id is None,
            'created_by': uuid_for(User, self.created_by),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    @staticmethod
    def get_for_program(program_id: int, document_type: str):
        """
        Obtiene la plantilla activa para un programa y tipo de documento.
        Prioriza plantilla específica del programa sobre la global.
        """
        specific = DocumentTemplate.query.filter_by(
            program_id=program_id,
            document_type=document_type,
            is_active=True,
        ).first()
        if specific:
            return specific
        return DocumentTemplate.query.filter_by(
            program_id=None,
            document_type=document_type,
            is_active=True,
        ).first()
