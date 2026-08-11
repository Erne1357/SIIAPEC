"""
File Access Service — who may READ a file served by `app/routes/api/files_api.py`.

Serving a file is an authorisation decision about the OBJECT behind it, never
about the URL that points at it. Both rules in this module start from the
DATABASE ROW that owns the bytes:

  * a personal document → the `Submission` / `AcceptanceDocument` /
    `SemesterEnrollment` row whose stored path is exactly the requested one;
    the owner is read from that row, NOT from the `<user_id>` URL segment.
  * an avatar → the `User` the photo belongs to.

Deriving the owner from the row (and not from the URL) is what allows a later
batch to replace `/files/doc/<user_id>/<phase>/<filename>` with an opaque UUID
without touching a single line of authorisation logic.

Scope tier (owner's decision): a profile photo and a personal document are
NEVER part of the cross-program summary tier. A caller who is not the owner
needs BOTH the permission codename AND the target inside their program scope
(`program_scope_service.user_in_scope`). A postgraduate_admin (global scope)
keeps full access.

Framework-agnostic by contract: no `request`, no `g`, no `current_user`.
Callers pass the User object and the stored relative path.
"""

from typing import Optional

from app import db
from app.models.acceptance_document import AcceptanceDocument
from app.models.semester_enrollment import SemesterEnrollment
from app.models.submission import Submission
from app.models.user_program import UserProgram
from app.services import program_scope_service


#: Permission needed to read a document that belongs to somebody else. Holding
#: it is necessary but NOT sufficient — the owner must also be inside the
#: caller's program scope.
VIEW_DOC_OTHERS_PERMISSION = 'files.api.view_doc_others'


# Two storage formats coexist in the database. save_user_doc() returns
# '<user_id>/<phase>/<filename>', relative to USER_DOCS_FOLDER, and that is what
# new rows carry. Older rows were written relative to UPLOAD_FOLDER instead and
# carry a leading 'documents/'. In the production dump the split is 121 of 227
# Submission rows and 21 of 21 AcceptanceDocument rows on the prefixed form.
_LEGACY_PREFIX = 'documents/'


def normalize_relative_path(relative_path: str) -> Optional[str]:
    """
    Normalise a stored/served relative path to the canonical, unprefixed form
    ('42/admission/acta.pdf').

    Returns None when the value is empty or unusable.
    """
    if not relative_path or not isinstance(relative_path, str):
        return None
    cleaned = relative_path.replace('\\', '/').strip().lstrip('/')
    while cleaned.startswith(_LEGACY_PREFIX):
        cleaned = cleaned[len(_LEGACY_PREFIX):]
    return cleaned or None


def path_variants(relative_path: str) -> list:
    """
    Every stored spelling a given document could have, canonical form first.

    Ownership is resolved by matching the served path against the persisted
    column, so the query has to accept both formats or every legacy row becomes
    unreachable — a 404 for its own owner, not just for staff. Comparing on the
    normalised form alone was exactly that bug.

    Returns [] when the path is unusable.
    """
    rel = normalize_relative_path(relative_path)
    if rel is None:
        return []
    return [rel, f'{_LEGACY_PREFIX}{rel}']


def user_doc_owner_id(relative_path: str) -> Optional[int]:
    """
    Resolve the OWNER of a personal document from the database row that
    references its stored path.

    Args:
        relative_path: path as persisted ('<user_id>/<phase>/<filename>').

    Returns:
        int  — id of the user the document belongs to.
        None — no row references this path. The route must answer 404: a file
               that no record points at is, for the application, non-existent
               (orphan bytes left behind by a re-upload must not be servable).
    """
    variants = path_variants(relative_path)
    if not variants:
        return None

    # 1. Submissions (admission / permanence / conclusion documents)
    owner = (
        db.session.query(Submission.user_id)
        .filter(Submission.file_path.in_(variants))
        .first()
    )
    if owner:
        return owner[0]

    # 2. Acceptance documents (carta, tira de materias, dictamen, boleta)
    owner = (
        db.session.query(UserProgram.user_id)
        .join(AcceptanceDocument,
              AcceptanceDocument.user_program_id == UserProgram.id)
        .filter(AcceptanceDocument.file_path.in_(variants))
        .first()
    )
    if owner:
        return owner[0]

    # 3. Semester enrollment attachments (comprobante de pago / horario)
    owner = (
        db.session.query(UserProgram.user_id)
        .join(SemesterEnrollment,
              SemesterEnrollment.user_program_id == UserProgram.id)
        .filter(db.or_(
            SemesterEnrollment.payment_proof_path.in_(variants),
            SemesterEnrollment.schedule_path.in_(variants),
        ))
        .first()
    )
    if owner:
        return owner[0]

    return None


def may_view_user_doc(viewer, owner_id) -> bool:
    """
    True if `viewer` may read a personal document owned by `owner_id`.

        owner                                  → True
        holds VIEW_DOC_OTHERS_PERMISSION and
        the owner is inside the viewer's scope → True
        everything else                        → False

    A document is forbidden cross-program: holding the codename alone (which
    every program_admin and every social_service account does, by role) is not
    enough.
    """
    if viewer is None or owner_id is None:
        return False

    try:
        owner_id = int(owner_id)
    except (TypeError, ValueError):
        return False

    if getattr(viewer, 'id', None) == owner_id:
        return True

    if not viewer.has_permission(VIEW_DOC_OTHERS_PERMISSION):
        return False

    return program_scope_service.user_in_scope(viewer, owner_id, allow_self=False)


def may_view_avatar(viewer, target_user_id) -> bool:
    """
    True if `viewer` may read the profile photo of `target_user_id`.

    A profile photo is a facial photograph: it is NOT part of the cross-program
    summary tier (name / e-mail / progress), so a shared program is required.

        self                                        → True
        target inside the viewer's program scope    → True
        target is a host (ponente) of an event the
        viewer may see                              → True
        everything else                             → False

    The event-host exception keeps the shipped public events UI working: the
    ponente chips of `/events` and the event detail page render
    `User.avatar_url` of internal hosts to every authenticated attendee, and a
    staff host has no UserProgram row, so no program scope would ever cover it.
    """
    if viewer is None or target_user_id is None:
        return False

    try:
        target_user_id = int(target_user_id)
    except (TypeError, ValueError):
        return False

    if getattr(viewer, 'id', None) == target_user_id:
        return True

    if program_scope_service.user_in_scope(viewer, target_user_id, allow_self=False):
        return True

    return _hosts_event_visible_to(viewer, target_user_id)


def _hosts_event_visible_to(viewer, target_user_id: int) -> bool:
    """
    True if `target_user_id` is registered as the host of an event the viewer
    may see: either an event published to students, or an event of a program
    inside the viewer's scope.
    """
    from app.models.event import Event, EventHost

    scope = program_scope_service.accessible_program_ids(viewer)
    if scope is None:
        # Global scope already returned True in `may_view_avatar`; kept for
        # direct callers.
        return True

    conditions = [
        db.and_(
            Event.visible_to_students.is_(True),
            Event.status != 'draft',
        )
    ]
    if scope:
        conditions.append(Event.program_id.in_(scope))

    row = (
        db.session.query(EventHost.id)
        .join(Event, Event.id == EventHost.event_id)
        .filter(
            EventHost.user_id == target_user_id,
            db.or_(*conditions),
        )
        .first()
    )
    return row is not None
