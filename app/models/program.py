from app import db
from sqlalchemy import asc
from app.models.step import Step
from app.models.program_step import ProgramStep
from app.utils.datetime_utils import now_local

class Program(db.Model):
    __tablename__ = 'program'

    # === CAMPOS EXISTENTES ===
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    coordinator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    slug = db.Column(db.String(100), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=now_local, nullable=False)
    updated_at = db.Column(db.DateTime, default=now_local, onupdate=now_local, nullable=False)

    # === 1. INFORMACIÓN GENERAL EXTENDIDA ===
    program_level = db.Column(db.String(50))  # Maestría, Doctorado, etc.
    academic_area = db.Column(db.String(100))  # Ingeniería, Administración, etc.
    image_filename = db.Column(db.String(255))  # nombre del archivo de imagen
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    # === 2. DURACIÓN Y MODALIDAD ===
    duration_semesters = db.Column(db.Integer)
    duration_years = db.Column(db.Numeric(3, 1))
    modality = db.Column(db.String(50))
    schedule_info = db.Column(db.Text)

    # === 3. INFORMACIÓN ACADÉMICA ===
    introduction_text = db.Column(db.Text)
    recognition_text = db.Column(db.Text)
    scholarship_info = db.Column(db.Text)
    admission_requirements = db.Column(db.Text)

    # === 4. OBJETIVOS (JSON) ===
    objectives = db.Column(db.JSON)

    # === 5. PERFIL DEL EGRESADO (JSON) ===
    graduate_profile_intro = db.Column(db.Text)
    graduate_competencies = db.Column(db.JSON)

    # === 6. LÍNEAS DE INVESTIGACIÓN (JSON) ===
    research_lines = db.Column(db.JSON)

    # === 7. MAPA CURRICULAR (JSON) ===
    curriculum_structure = db.Column(db.JSON)
    show_curriculum = db.Column(db.Boolean, default=True)

    # === 8. INFORMACIÓN DE CONTACTO ===
    contact_email = db.Column(db.String(255))
    contact_email_secondary = db.Column(db.String(255))
    contact_phone = db.Column(db.String(50))
    contact_phone_secondary = db.Column(db.String(50))
    contact_address = db.Column(db.Text)
    contact_office = db.Column(db.String(100))
    contact_hours = db.Column(db.String(255))

    # === 9. CONFIGURACIÓN DE VISUALIZACIÓN ===
    show_hero_cards = db.Column(db.Boolean, default=True)
    show_objectives = db.Column(db.Boolean, default=True)
    show_graduate_profile = db.Column(db.Boolean, default=True)
    show_research_lines = db.Column(db.Boolean, default=True)
    show_contact_section = db.Column(db.Boolean, default=True)
    show_contact_form = db.Column(db.Boolean, default=True)

    # === 10. SEO Y METADATOS ===
    meta_title = db.Column(db.String(255))
    meta_description = db.Column(db.Text)
    meta_keywords = db.Column(db.String(255))

    # === RELACIONES ===
    coordinator = db.relationship("User", back_populates="coordinated_programs")

    user_program = db.relationship(
        "UserProgram",
        back_populates="program",
        order_by=asc("user_program.enrollment_date"),
        cascade="all, delete-orphan",
    )

    # 1) asociación con la tabla puente (incluye 'sequence')
    program_steps = db.relationship(
        "ProgramStep",
        back_populates="program",
        order_by="ProgramStep.sequence",
        cascade="all, delete-orphan",
    )

    # 2) vista directa a los pasos, sólo lectura
    steps = db.relationship(
        "Step",
        secondary="program_step",
        order_by="Step.phase_id",
        viewonly=True,
    )

    def __init__(self, name, description, coordinator_id, **kwargs):
        self.name = name
        self.description = description
        self.coordinator_id = coordinator_id
        # Asignar otros campos opcionales
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'slug': self.slug,

            # Información general
            'program_level': self.program_level,
            'academic_area': self.academic_area,
            'image_filename': self.image_filename,
            'is_active': self.is_active,

            # Duración y modalidad
            'duration_semesters': self.duration_semesters,
            'duration_years': float(self.duration_years) if self.duration_years else None,
            'modality': self.modality,
            'schedule_info': self.schedule_info,

            # Información académica
            'introduction_text': self.introduction_text,
            'recognition_text': self.recognition_text,
            'scholarship_info': self.scholarship_info,
            'admission_requirements': self.admission_requirements,

            # Objetivos y perfil
            'objectives': self.objectives,
            'graduate_profile_intro': self.graduate_profile_intro,
            'graduate_competencies': self.graduate_competencies,

            # Investigación y currículo
            'research_lines': self.research_lines,
            'curriculum_structure': self.curriculum_structure,
            'show_curriculum': self.show_curriculum,

            # Contacto
            'contact_email': self.contact_email,
            'contact_email_secondary': self.contact_email_secondary,
            'contact_phone': self.contact_phone,
            'contact_phone_secondary': self.contact_phone_secondary,
            'contact_address': self.contact_address,
            'contact_office': self.contact_office,
            'contact_hours': self.contact_hours,

            # Configuración
            'show_hero_cards': self.show_hero_cards,
            'show_objectives': self.show_objectives,
            'show_graduate_profile': self.show_graduate_profile,
            'show_research_lines': self.show_research_lines,
            'show_contact_section': self.show_contact_section,
            'show_contact_form': self.show_contact_form,

            # SEO
            'meta_title': self.meta_title,
            'meta_description': self.meta_description,
            'meta_keywords': self.meta_keywords,

            # Coordinador
            'coordinator_id': self.coordinator_id,
            'coordinator': {
                'id': self.coordinator.id,
                'name': f"{self.coordinator.first_name} {self.coordinator.last_name}",
                'email': self.coordinator.email
            } if self.coordinator else None,

            # Timestamps
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

    @property
    def image_url(self):
        """Genera la URL de la imagen del programa"""
        if self.image_filename:
            return f"/static/assets/images/programs/{self.image_filename}"
        return "/static/assets/images/programs/default.webp"

    @property
    def duration_display(self):
        """Retorna texto formateado de duración"""
        if self.duration_years and self.duration_semesters:
            return f"{int(self.duration_years)} años ({self.duration_semesters} semestres)"
        elif self.duration_years:
            return f"{int(self.duration_years)} años"
        elif self.duration_semesters:
            return f"{self.duration_semesters} semestres"
        return "Duración variable"

    @property
    def curriculum_semesters(self):
        """
        Normalized curriculum: a list of {semester, name, courses}.

        `curriculum_structure` has been written in two shapes over time. The
        editor's canonical one is {"semester": 1, "courses": [{...}]}; earlier
        seeds used {"number": 1, "name": "...", "subjects": ["Materia", ...]}.
        The public page only ever read the canonical one, so a program with a
        legacy plan loaded rendered "Plan de estudios no disponible" while
        holding four full semesters.

        Reading both here keeps the template unaware that two shapes exist.
        Semesters with no courses and no usable number are dropped.
        """
        structure = self.curriculum_structure
        if not isinstance(structure, dict):
            return []

        raw_semesters = structure.get('semesters')
        if not isinstance(raw_semesters, list):
            return []

        normalized = []
        for index, raw in enumerate(raw_semesters):
            if not isinstance(raw, dict):
                continue

            number = raw.get('semester')
            if not isinstance(number, int) or number <= 0:
                number = raw.get('number')
            if not isinstance(number, int) or number <= 0:
                number = index + 1

            courses = []
            for course in (raw.get('courses') or []):
                if isinstance(course, dict) and (course.get('name') or course.get('code')):
                    courses.append({
                        'code': course.get('code'),
                        'name': course.get('name') or course.get('code'),
                        'credits': course.get('credits'),
                        'type': course.get('type'),
                    })

            # Legacy: lista plana de nombres de materia, sin código ni créditos.
            if not courses:
                for subject in (raw.get('subjects') or []):
                    if isinstance(subject, str) and subject.strip():
                        courses.append({
                            'code': None,
                            'name': subject.strip(),
                            'credits': None,
                            'type': None,
                        })
                    elif isinstance(subject, dict) and subject.get('name'):
                        courses.append({
                            'code': subject.get('code'),
                            'name': subject.get('name'),
                            'credits': subject.get('credits'),
                            'type': subject.get('type'),
                        })

            if not courses:
                continue

            normalized.append({
                'semester': number,
                'name': raw.get('name'),
                'courses': courses,
            })

        return normalized

    @property
    def curriculum_total_credits(self):
        """Suma de créditos del plan normalizado. 0 si el plan no los declara."""
        total = 0
        for semester in self.curriculum_semesters:
            for course in semester['courses']:
                try:
                    total += int(course.get('credits') or 0)
                except (TypeError, ValueError):
                    continue
        return total
