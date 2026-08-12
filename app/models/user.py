from app import db
from flask import url_for, g
from flask_login import UserMixin
from datetime import datetime, timezone
from app.utils.datetime_utils import now_local
from werkzeug.security import generate_password_hash

#: The codename that marks the jefe de posgrado — the only account with global
#: program scope. It is checked at ROLE level only (see
#: `User.has_global_program_scope`): it is a delegatable codename, and a
#: delegation scoped to one program must never grant access to all of them.
GLOBAL_SCOPE_PERMISSION = 'academic_periods.api.create'

#: Permisos que un usuario ejerce SOBRE SU PROPIA CUENTA. Delegarlos con un
#: program_id no significa nada —nadie "sube su propia foto" en el programa 7—
#: así que NO aportan alcance de programa (ver `get_accessible_program_ids`).
#:
#: Sin esta lista bastaba una delegación de 'users.api.me' sobre el programa B
#: para que su titular ganara acceso completo a B: expediente, PII, documentos
#: y escrituras. El alcance debe venir de un permiso que signifique "trabaja en
#: este programa", no de un permiso que todo el mundo ya tiene sobre sí mismo.
#:
#: Corresponde al conjunto base de los roles applicant / student. Al añadir un
#: permiso nuevo de cuenta propia, añádelo también aquí.
SELF_SERVICE_PERMISSIONS = frozenset({
    # Cuenta propia
    'users.api.me',
    'users.api.profile_completion',
    'users.api.history',
    'users.api.change_password',
    'notifications.api.list',
    'notifications.api.unread_count',
    'notifications.api.mark_all_read',
    'notifications.api.delete',
    'files.api.view_doc',
    'files.api.view_template',
    'profile.api.upload_photo',
    'profile.api.request_photo_change',
    # Documentos propios
    'submissions.api.create',
    'submissions.api.delete_own',
    'submissions.api.upload',
    # Consulta pública / inscripción propia
    'programs.page.view',
    'programs.api.list',
    'programs.api.detail',
    'programs.api.enroll',
    'academic_periods.api.view_active',
    'events.api.list',
    # Trámites propios
    'acceptance.api.view_status',
    'acceptance.api.request_deferral',
    'permanence.api.view_status',
    'extensions.api.request',
    'extensions.api.list_own',
    'extensions.api.view_archive_status',
    'program_changes.api.request',
    'appointments.api.book',
    'appointments.api.list_own',
    'appointments.api.cancel',
    'appointments.api.change_request',
    'invitations.api.respond',
    'attendance.api.register',
    'attendance.api.unregister',
})


class User(db.Model, UserMixin):
    __tablename__ = 'user'
    
    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(50), nullable=False)
    last_name = db.Column(db.String(50), nullable=False)
    mother_last_name = db.Column(db.String(50))
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)  # Aseguramos la longitud para el hash
    email = db.Column(db.String(100), unique=True, nullable=False)
    last_login = db.Column(db.DateTime, default=now_local, nullable=False)
    is_internal = db.Column(db.Boolean, default=False)
    scolarship_type = db.Column(db.String(50), nullable=True)
    registration_date = db.Column(db.DateTime, default=now_local, nullable=False)
    role_id = db.Column(db.Integer, db.ForeignKey('role.id'), nullable=False)
    avatar = db.Column(db.String(255), default='default.jpg', nullable=True)
    must_change_password = db.Column(db.Boolean, default=True, nullable=False)

    is_active = db.Column(db.Boolean, default=True, nullable=False)
    control_number = db.Column(db.String(20), unique=True, nullable=True)
    control_number_assigned_at = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, default=now_local, onupdate=now_local, nullable=False)

    role = db.relationship('Role', back_populates='users', uselist=False)
    user_program = db.relationship(
        'UserProgram',
        foreign_keys='UserProgram.user_id',
        back_populates='user'
    )

    coordinated_programs = db.relationship('Program', back_populates='coordinator')

    # Permisos directos asignados al usuario (delegación / casos especiales)
    direct_permissions = db.relationship(
        'UserPermission',
        foreign_keys='UserPermission.user_id',
        back_populates='user',
        cascade='all, delete-orphan'
    )
    
    submissions = db.relationship(
        'Submission',
        foreign_keys='Submission.user_id',
        back_populates='user',
        cascade='all, delete-orphan'
    )
    reviews = db.relationship(
        'Submission',
        foreign_keys='Submission.reviewer_id',
        back_populates='reviewer'
    )
    histories = db.relationship(
        'UserHistory', 
        foreign_keys='UserHistory.user_id',
        back_populates='user', 
        cascade='all, delete-orphan'
    )

    phone = db.Column(db.String(20))
    mobile_phone = db.Column(db.String(20))
    address = db.Column(db.Text)
    curp = db.Column(db.String(18))
    rfc = db.Column(db.String(13))
    birth_date = db.Column(db.Date)
    birth_place = db.Column(db.String(200))
    cedula_profesional = db.Column(db.String(20))
    nss = db.Column(db.String(15))

    # Contacto de emergencia
    emergency_contact_name = db.Column(db.String(200))
    emergency_contact_phone = db.Column(db.String(20))
    emergency_contact_relationship = db.Column(db.String(50))

    # Campo para marcar perfil completo (calculado automáticamente)
    profile_completed = db.Column(db.Boolean, default=False, nullable=False)

    # Tracks when the user last opened the public events list (null = never viewed)
    last_events_seen_at = db.Column(db.DateTime, nullable=True)

    # Photo change workflow:
    #   - First upload always allowed (avatar='default.jpg' permits it)
    #   - After first upload, student must request a change
    #   - Coordinator enables one change by setting photo_change_allowed=True
    #   - Student uploads → flag resets to False
    photo_change_allowed = db.Column(db.Boolean, default=False, nullable=False)
    photo_change_requested_at = db.Column(db.DateTime, nullable=True)


    def __init__(self, first_name, last_name, mother_last_name, username, password, email, is_internal, role_id, avatar='default.jpg', must_change_password=True):
        self.first_name = first_name
        self.last_name = last_name
        self.mother_last_name = mother_last_name
        self.username = username
        self.password = generate_password_hash(password)
        self.email = email
        self.registration_date = now_local()
        self.last_login = now_local()
        self.is_internal = is_internal
        self.role_id = role_id
        self.avatar = avatar
        self.must_change_password = must_change_password
    
    def is_profile_complete(self):
        """
        Verifica si el perfil está completo basándose en los campos requeridos.
        Los campos básicos (nombre, email, etc.) ya están en el registro.
        Los campos adicionales son los que determinan si está "completo".
        """
        required_fields = [
            self.phone or self.mobile_phone,  # Al menos uno de los teléfonos
            self.address,
            self.curp,
            self.birth_date,
            self.emergency_contact_name,
            self.emergency_contact_phone,
            self.emergency_contact_relationship
        ]
        
        # Todos los campos requeridos deben tener valor
        return all(field and str(field).strip() if isinstance(field, str) else field for field in required_fields)

    def update_profile_completion_status(self):
        """
        Actualiza automáticamente el estado de profile_completed
        """
        self.profile_completed = self.is_profile_complete()
        return self.profile_completed

    @property
    def avatar_url(self):
        if self.avatar and self.avatar != 'default.jpg':
            return url_for('api_files.avatar', user_id=self.id, filename=self.avatar)
        return url_for('static', filename='assets/images/default.jpg')
    
    # ─── Permissions ─────────────────────────────────────────────────────────
    #
    # CONTRACT — capability vs. reach, and why they are two different methods:
    #
    #   has_permission(codename)      → WHAT this account may do. Never WHOM to.
    #   get_accessible_program_ids()  → WHICH programs it may do it in.
    #
    # A role grant is institution-wide by nature: `program_admin` holds
    # `deliberation.api.decide` for the institution, not for a program, and
    # nothing about the row in `role_permission` can narrow it. Any attempt to
    # answer "may I decide on program 7?" from the permission tables alone is
    # therefore a lie, which is exactly what the old
    # `has_permission(codename, program_id=...)` was. The parameter is gone;
    # program reach is answered ONLY by `get_accessible_program_ids()` and the
    # guards built on it (`app/services/program_scope_service.py`).

    def _role_permission_codenames(self):
        """
        Codenames granted by the ROLE alone: seed rows (RolePermission) plus
        active role overrides (RolePermissionOverride). No delegations.

        Institution-wide by definition — this is the set a delegation cannot
        narrow and must not be confused with. Cached per request.

        `Permission.is_active` is honoured here, not only on delegations.
        Deactivating a row in the catalogue is the ONLY kill switch this system
        has for a permission that turned out to be wrong, and role grants are
        the majority of all grants: if the flag stopped at `UserPermission`,
        deactivating a permission revoked it from the handful of delegates and
        from nobody else, which made the flag a lie precisely where it mattered.
        Both sources are filtered, so an inactive permission grants nothing
        through any path.
        """
        cache_key = f'_role_perm_cache_{self.id}'
        if not hasattr(g, cache_key):
            from app.models.permission import Permission
            from app.models.role_permission import RolePermission, RolePermissionOverride

            codenames = set()
            if self.role_id:
                base = (
                    db.session.query(RolePermission)
                    .join(RolePermission.permission)
                    .filter(
                        RolePermission.role_id == self.role_id,
                        Permission.is_active == True
                    )
                    .all()
                )
                codenames.update(rp.permission.codename for rp in base)

                overrides = (
                    db.session.query(RolePermissionOverride)
                    .join(RolePermissionOverride.permission)
                    .filter(
                        RolePermissionOverride.role_id == self.role_id,
                        RolePermissionOverride.is_active == True,
                        Permission.is_active == True
                    )
                    .all()
                )
                codenames.update(ov.permission.codename for ov in overrides)

            setattr(g, cache_key, codenames)
        return getattr(g, cache_key)

    def has_role_permission(self, codename):
        """
        True if the ROLE grants `codename` (seed or active override).

        Use this — never `has_permission` — for anything that must not be
        reachable through a delegation, above all the global-scope marker
        (`has_global_program_scope`).
        """
        return codename in self._role_permission_codenames()

    def has_permission(self, codename):
        """
        ¿Puede este usuario ejecutar esta acción? — capacidad, NUNCA alcance.

        Fuentes (unión):
          1. Permisos base del rol (RolePermission, del seed)
          2. Overrides activos del rol (RolePermissionOverride)
          3. Delegaciones activas y no vencidas (UserPermission)

        Devolver True NO autoriza a tocar a un estudiante, programa, documento
        o decisión concretos. Para eso está el alcance de programa:
        `program_scope_required` / `guard_program_scope` / `guard_user_scope`
        en `app/utils/permissions.py`, o `program_scope_service` desde un
        servicio. Este método no acepta `program_id` a propósito: un permiso de
        rol es institucional y filtrarlo por programa daría una respuesta falsa.

        Caché por request (flask.g) para evitar N+1 queries.
        """
        cache_key = f'_perm_cache_{self.id}'
        if not hasattr(g, cache_key):
            from app.models.permission import Permission
            from app.models.user_permission import UserPermission
            from app.utils.datetime_utils import now_local

            codenames = set(self._role_permission_codenames())

            now = now_local()
            # `Permission.is_active` filters here too, and it is NOT the same
            # column as `UserPermission.is_active`: that one says "this
            # delegation was revoked", the catalogue flag says "this permission
            # is withdrawn from the whole system". Only the grant path checked
            # the catalogue flag (`delegate_permission` refuses to create a row
            # for an inactive codename), so delegations signed BEFORE the
            # withdrawal kept working forever. With role grants now filtered,
            # leaving this branch unfiltered would be the worse failure of the
            # two: deactivating a permission would visibly strip it from every
            # role holder — proof to the admin that the switch worked — while
            # every existing delegate silently kept it.
            direct_q = (
                db.session.query(UserPermission)
                .join(UserPermission.permission)
                .filter(
                    UserPermission.user_id == self.id,
                    UserPermission.is_active == True,
                    Permission.is_active == True,
                    db.or_(
                        UserPermission.expires_at == None,
                        UserPermission.expires_at > now
                    ),
                )
            )
            codenames.update(up.permission.codename for up in direct_q.all())

            setattr(g, cache_key, codenames)

        return codename in getattr(g, cache_key)

    def has_global_program_scope(self):
        """
        True only for the jefe de posgrado: the ROLE grants
        `GLOBAL_SCOPE_PERMISSION`.

        Deliberately a role-level check. `academic_periods.api.create` is a
        delegatable codename, so reading it with `has_permission()` let a
        delegation scoped to ONE program turn its holder into a global admin.
        A scoped delegation must never produce global scope.
        """
        return self.has_role_permission(GLOBAL_SCOPE_PERMISSION)

    def get_accessible_program_ids(self):
        """
        Programas sobre los que este usuario puede OPERAR (alcance).

        Reglas:
          - Alcance global (jefe de posgrado, por rol) → None = TODOS.
          - Programas que coordina (`Program.coordinator_id`).
          - Programas de sus delegaciones activas cuyo permiso signifique
            "trabaja en este programa". Los permisos de cuenta propia
            (`SELF_SERVICE_PERMISSIONS`) quedan fuera: delegarlos con un
            program_id no describe ningún trabajo sobre ese programa y su único
            efecto sería ampliar el alcance de tapadillo — bastaba delegar
            'users.api.me' sobre el programa B para ganar acceso completo a B.
          - Una delegación con program_id NULL no aporta alcance: no se
            interpreta como "todos los programas" (fail-closed).

        Returns:
          set[int] | None — None significa "todos los programas". Un set VACÍO
          significa NINGUNO; nunca lo trates como "todos".
        """
        if self.has_global_program_scope():
            return None

        pids = {p.id for p in self.coordinated_programs}

        from app.models.permission import Permission
        from app.models.user_permission import UserPermission
        from app.utils.datetime_utils import now_local

        now = now_local()
        delegated = (
            db.session.query(UserPermission.program_id, Permission.codename)
            .join(Permission, Permission.id == UserPermission.permission_id)
            .filter(
                UserPermission.user_id == self.id,
                UserPermission.is_active == True,
                # Un permiso retirado del catálogo tampoco otorga ALCANCE. Sin
                # este filtro la delegación seguía metiendo su programa en el
                # conjunto aunque ya no confiera capacidad ninguna, y eso no es
                # peso muerto: el alcance no es por permiso, es del usuario, así
                # que ese programa habilitaba TODOS los demás permisos que la
                # cuenta tiene por rol. Retirar un permiso dejaba intacta la
                # puerta que ese permiso había abierto.
                Permission.is_active == True,
                UserPermission.program_id.isnot(None),
                db.or_(
                    UserPermission.expires_at == None,
                    UserPermission.expires_at > now
                ),
            )
            .all()
        )
        for program_id, codename in delegated:
            if codename in SELF_SERVICE_PERMISSIONS:
                # Acting on your own account is not working on a program.
                continue
            pids.add(program_id)

        return pids

    def deactivate(self):
        """Desactiva el usuario"""
        self.is_active = False
        
    def activate(self):
        """Activa el usuario"""
        self.is_active = True

    def assign_control_number(self, control_number):
        """
        Asigna un número de control al usuario y actualiza su username.
        
        Args:
            control_number (str): El número de control (ej: M21111182)
        """
        self.control_number = control_number
        self.username = control_number
        self.control_number_assigned_at = now_local()

    def can_be_deleted(self):
        """
        Verifica si el usuario puede ser eliminado.
        Un usuario puede ser eliminado si no tiene:
        - Documentos subidos (submissions)
        - Citas programadas (appointments)
        """
        has_submissions = len(self.submissions) > 0 if hasattr(self, 'submissions') else False
        # has_appointments = len(self.appointments) > 0 if hasattr(self, 'appointments') else False
        
        return not has_submissions  # and not has_appointments

    def to_dict(self, include_sensitive=False):
        user_data = {
            'id': self.id,
            'first_name': self.first_name,
            'last_name': self.last_name,
            'mother_last_name': self.mother_last_name,
            'username': self.username,
            'email': self.email,
            'last_login': self.last_login.isoformat() if self.last_login else None,
            'is_internal': self.is_internal,
            'scolarship_type': self.scolarship_type,
            'registration_date': self.registration_date.isoformat() if self.registration_date else None,
            'role': self.role.name if self.role else None,
            'avatar_url': self.avatar_url,
            'must_change_password': self.must_change_password,
            'profile_completed': self.profile_completed
        }
        if include_sensitive:
            user_data.update({
                'is_active': self.is_active,
                'control_number': self.control_number,
                'control_number_assigned_at': self.control_number_assigned_at.isoformat() if self.control_number_assigned_at else None,
                'program' : {
                    'id': self.user_program[0].program.id,
                    'name': self.user_program[0].program.name,
                    'slug': self.user_program[0].program.slug
                } if self.user_program else None,
                'histories': [h.to_dict() for h in (self.histories or [])]
            })

        return user_data