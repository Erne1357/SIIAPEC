"""
Profile photo service.

Workflow:
  - First upload: any user can upload (avatar='default.jpg').
  - After first upload: student needs photo_change_allowed=True to upload again.
  - Student requests change → notifies coordinators of their program.
  - Coordinator enables (or rejects) → photo_change_allowed flag updated.
  - On successful upload, the flag resets to False (one-shot enablement).

All photos are compressed to 512px JPEG q=85 by `image_processing.compress_profile_photo`.

Program scope: every write on somebody ELSE's photo (a coordinator uploading
for a student, or enabling/rejecting a change request) requires the target to
live inside the requester's program scope. The routes declare it with
`@program_scope_required`; these functions re-check it so the rule also holds
for CLI commands, Celery tasks and any future caller.
"""

from pathlib import Path
from flask import current_app

from app import db
from app.services import public_id_service
from app.models.user import User
from app.models.program import Program
from app.models.user_program import UserProgram
from app.services import program_scope_service
from app.services.notification_service import NotificationService
from app.services.user_history_service import UserHistoryService
from app.utils.datetime_utils import now_local
from app.utils.image_processing import compress_profile_photo, ImageProcessingError


AVATAR_FILENAME = 'avatar.jpg'


class ProfilePhotoError(Exception):
    pass


class PhotoChangeNotAllowed(ProfilePhotoError):
    pass


class PhotoRequestNotFound(ProfilePhotoError):
    pass


def _get_user(user_id: int) -> User:
    user = User.query.get(user_id)
    if not user:
        raise ProfilePhotoError(f"Usuario {user_id} no encontrado")
    return user


def _avatar_dir(user_id: int) -> Path:
    folder = current_app.config['AVATAR_FOLDER'] / str(user_id)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _delete_previous_avatars(user_id: int) -> None:
    folder = _avatar_dir(user_id)
    for f in folder.iterdir():
        if f.is_file():
            try:
                f.unlink()
            except OSError:
                pass


def _require_scope_over(requester_id: int, target_user: User) -> None:
    """
    Fail closed unless `requester_id` may act on `target_user`.

    Raises:
        program_scope_service.ProgramScopeDenied — target outside the scope, or
        the requester no longer exists.
    """
    if requester_id is None:
        raise program_scope_service.ProgramScopeDenied(
            'No tienes acceso a la foto de perfil de este usuario.'
        )
    requester = db.session.get(User, requester_id)
    if requester is None:
        raise program_scope_service.ProgramScopeDenied(
            'No tienes acceso a la foto de perfil de este usuario.'
        )
    if not program_scope_service.user_in_scope(requester, target_user):
        raise program_scope_service.ProgramScopeDenied(
            'No tienes acceso a la foto de perfil de este usuario.'
        )


def _coordinator_ids_for_user(user: User) -> list[int]:
    """Returns coordinator_ids of all programs the user is enrolled in."""
    coord_ids = set()
    for up in (user.user_program or []):
        program = up.program
        if program and program.coordinator_id:
            coord_ids.add(program.coordinator_id)
    return list(coord_ids)


def upload_photo(user_id: int, file_storage, requester_id: int = None,
                 is_self: bool = True) -> User:
    """
    Save a compressed profile photo for the user.

    Rules:
      - If is_self and user already has a photo and not allowed → raises PhotoChangeNotAllowed.
      - If a coordinator uploads on behalf of a student, the student must be
        inside the coordinator's program scope (raises ProgramScopeDenied).
      - Resets photo_change_allowed to False and photo_change_requested_at to None.
    """
    user = _get_user(user_id)

    # Escritura sobre la foto de OTRA persona: exige alcance de programa.
    # Se comprueba ANTES de comprimir o de borrar el avatar anterior, para que
    # una denegación no deje al usuario sin foto.
    acting_for_other = requester_id is not None and requester_id != user_id
    if acting_for_other or not is_self:
        _require_scope_over(requester_id, user)

    has_photo = user.avatar and user.avatar != 'default.jpg'

    if is_self and has_photo and not user.photo_change_allowed:
        raise PhotoChangeNotAllowed(
            "Cambio de foto no autorizado. Solicita autorización al coordinador del programa."
        )

    try:
        compressed_bytes = compress_profile_photo(file_storage)
    except ImageProcessingError as exc:
        raise ProfilePhotoError(str(exc)) from exc

    _delete_previous_avatars(user_id)
    folder = _avatar_dir(user_id)
    target = folder / AVATAR_FILENAME
    with open(target, 'wb') as fh:
        fh.write(compressed_bytes)

    user.avatar = AVATAR_FILENAME
    user.photo_change_allowed = False
    user.photo_change_requested_at = None

    UserHistoryService.log_action(
        user_id=user_id,
        admin_id=requester_id or user_id,
        action='profile_photo_uploaded',
        details=(
            'Foto de perfil actualizada'
            + ('' if is_self else ' por el coordinador')
        ),
    )

    if not is_self:
        NotificationService.create_notification(
            user_id=user_id,
            notification_type='profile_photo_uploaded_by_coordinator',
            title='Tu foto de perfil fue actualizada',
            message='Un coordinador actualizó tu foto de perfil.',
            priority='normal',
            action_url='/user/profile',
        )

    db.session.commit()
    return user


def request_photo_change(user_id: int, reason: str = None) -> User:
    """
    Student requests permission to change their profile photo. Notifies coordinators.
    Idempotent: if a request is already pending, raises ProfilePhotoError.
    """
    user = _get_user(user_id)

    if user.photo_change_allowed:
        raise ProfilePhotoError(
            "Ya tienes habilitado el cambio de foto. Sube tu nueva foto directamente."
        )
    if user.photo_change_requested_at is not None:
        raise ProfilePhotoError(
            "Ya tienes una solicitud de cambio de foto pendiente de revisión."
        )

    user.photo_change_requested_at = now_local()

    UserHistoryService.log_action(
        user_id=user_id,
        admin_id=user_id,
        action='profile_photo_change_requested',
        details=f'Solicitud de cambio de foto. Motivo: {reason or "Sin especificar"}',
    )

    coord_ids = _coordinator_ids_for_user(user)
    full_name = f"{user.first_name} {user.last_name}".strip()
    for coord_id in coord_ids:
        NotificationService.create_notification(
            user_id=coord_id,
            notification_type='profile_photo_change_requested',
            title='Solicitud de cambio de foto de perfil',
            message=(
                f'{full_name} ha solicitado cambiar su foto de perfil.'
                + (f' Motivo: {reason}' if reason else '')
            ),
            priority='normal',
            # La página del expediente se direcciona por el identificador
            # PÚBLICO (`/students/<uuid>/record`). Con el entero el enlace de
            # la notificación devolvía 404 — y las notificaciones se guardan,
            # así que un enlace roto sobrevive al despliegue que lo rompió.
            action_url=f'/students/{public_id_service.public_id(user)}/record',
        )

    db.session.commit()
    return user


def enable_photo_change(target_user_id: int, coordinator_id: int,
                         approve: bool = True, reason: str = None) -> User:
    """
    Coordinator approves or rejects a photo change request.

    Raises:
        ProgramScopeDenied — the student is outside the coordinator's programs.
    """
    user = _get_user(target_user_id)

    if coordinator_id != target_user_id:
        _require_scope_over(coordinator_id, user)

    if not approve:
        user.photo_change_requested_at = None
        user.photo_change_allowed = False

        NotificationService.create_notification(
            user_id=target_user_id,
            notification_type='profile_photo_change_rejected',
            title='Solicitud de cambio de foto rechazada',
            message=(
                f'Tu solicitud de cambio de foto fue rechazada por el coordinador.'
                + (f' Motivo: {reason}' if reason else '')
            ),
            priority='normal',
            action_url='/user/profile',
        )

        UserHistoryService.log_action(
            user_id=target_user_id,
            admin_id=coordinator_id,
            action='profile_photo_change_rejected',
            details=reason or 'Sin motivo especificado',
        )

        db.session.commit()
        return user

    user.photo_change_allowed = True
    user.photo_change_requested_at = None

    NotificationService.create_notification(
        user_id=target_user_id,
        notification_type='profile_photo_change_enabled',
        title='Cambio de foto autorizado',
        message='Ya puedes subir una nueva foto de perfil. Ingresa a tu perfil.',
        priority='high',
        action_url='/user/profile',
    )

    UserHistoryService.log_action(
        user_id=target_user_id,
        admin_id=coordinator_id,
        action='profile_photo_change_enabled',
        details='Coordinador habilitó cambio de foto',
    )

    db.session.commit()
    return user


def list_pending_photo_requests(coordinator_id: int) -> list:
    """
    Returns pending photo-change requests visible to ``coordinator_id``.

    Scope:
      * Users with ``photo_change_requested_at IS NOT NULL`` and
        ``photo_change_allowed = False`` (no longer pending after coordinator
        approves and the flag is set, or after the user uploads).
      * Restricted to users enrolled in any program inside the coordinator's
        scope, via ``program_scope_service.accessible_program_ids``
        (None == all programs, empty set == no programs).
    """
    requester = db.session.get(User, coordinator_id)
    if not requester:
        return []

    accessible = program_scope_service.accessible_program_ids(requester)

    base_q = (
        User.query
        .filter(
            User.photo_change_requested_at.isnot(None),
            User.photo_change_allowed == False,  # noqa: E712
        )
    )

    if accessible is None:
        # postgraduate_admin / global access — all pending requests
        users = base_q.order_by(User.photo_change_requested_at.asc()).all()
    else:
        if not accessible:
            return []
        program_ids = list(accessible)
        user_ids = {
            up.user_id for up in
            UserProgram.query.filter(UserProgram.program_id.in_(program_ids)).all()
        }
        if not user_ids:
            return []
        users = (
            base_q
            .filter(User.id.in_(user_ids))
            .order_by(User.photo_change_requested_at.asc())
            .all()
        )

    return [
        {
            # Public handle — the console puts it straight back in the URL of
            # `POST /api/v1/users/<uuid>/photo/enable-change`.
            'user_id': str(u.uuid) if u.uuid else None,
            'full_name': f"{u.first_name} {u.last_name} {u.mother_last_name or ''}".strip(),
            'email': u.email,
            'avatar_url': u.avatar_url,
            'requested_at': u.photo_change_requested_at.isoformat() if u.photo_change_requested_at else None,
        }
        for u in users
    ]
