"""
File Access Service — who may READ a file served by `app/routes/api/files_api.py`,
and which bytes an opaque public identifier points at.

Serving a file is an authorisation decision about the OBJECT behind it, never
about the URL that points at it. Both rules in this module start from the
DATABASE ROW that owns the bytes:

  * a personal document → the `Submission` / `AcceptanceDocument` /
    `SemesterEnrollment` row whose stored path is exactly the requested one;
    the owner is read from that row, NOT from any URL segment.
  * an avatar → the `User` the photo belongs to.

Deriving the owner from the row (and not from the URL) is what allowed the URL
to become an opaque UUID without touching a single line of authorisation logic.
That cutover has now happened and the promise held: `user_doc_owner_id`,
`may_view_user_doc` and `may_view_avatar` are byte-for-byte what they were.

What this module gained instead is the two halves of the ROW ↔ URL mapping,
kept side by side so they cannot drift apart:

    user_doc_path(public_id, slot)  URL identifier → stored relative path
    submission_file_url(row) & co.  row            → served URL

DISK LAYOUT IS UNCHANGED, DELIBERATELY (owner's decision). Files keep their
human-readable names and their `<user_id>/<phase>/` folders so that anyone
inspecting the server — or opening a retention ZIP — still reads
`Titulo.pdf`. Only the URL stopped mirroring the disk: it now carries an
opaque row handle, the server looks the row up, and `Content-Disposition`
carries the original basename back so the download is still named correctly.

Scope tier (owner's decision): a profile photo and a personal document are
NEVER part of the cross-program summary tier. A caller who is not the owner
needs BOTH the permission codename AND the target inside their program scope
(`program_scope_service.user_in_scope`). A postgraduate_admin (global scope)
keeps full access.

Framework-agnostic by contract: no `request`, no `g`, no `current_user`, and no
`url_for` either — the URL builders emit plain strings so a Celery task or an
e-mail renderer can call them outside a request context.
"""

from posixpath import basename
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


# ─────────────────────────────────────────────────────────────────────────────
# ROW ↔ URL — the opaque public identifier of a served file
# ─────────────────────────────────────────────────────────────────────────────
#
# `/files/doc/<uuid>` names a ROW, not a path. The row is one of:
#
#     Submission            one file  → `file_path`
#     AcceptanceDocument    one file  → `file_path`
#     SemesterEnrollment    TWO files → `payment_proof_path` + `schedule_path`
#
# The first two need no discriminator: a UUID is unique across tables, so
# `/files/doc/<uuid>` is unambiguous on its own. `SemesterEnrollment` is the
# exception — one row owns two unrelated documents, so a row handle alone
# cannot say which bytes are wanted. It gets an explicit SLOT segment,
# `/files/doc/<uuid>/<slot>`, and only the two slot names below are accepted.
# Guessing the slot buys an attacker nothing: they would still need the row's
# UUID, and the ACL runs afterwards either way.
#
# The slot names are URL identifiers, not UI text, so they are English like
# every other identifier in the codebase.

#: `SemesterEnrollment.payment_proof_path` — comprobante de pago, uploaded by
#: the student.
DOC_SLOT_PAYMENT_PROOF = 'payment-proof'

#: `SemesterEnrollment.schedule_path` — horario del semestre, uploaded by the
#: coordinator. Both belong to the STUDENT, which is what `user_doc_owner_id`
#: resolves them to.
DOC_SLOT_SCHEDULE = 'schedule'

DOC_SLOTS = (DOC_SLOT_PAYMENT_PROOF, DOC_SLOT_SCHEDULE)

#: URL prefixes. They must match the route rules in
#: `app/routes/api/files_api.py`; that module imports these constants for its
#: docstrings and both sides carry a pointer at the other.
DOC_URL_PREFIX = '/files/doc'
AVATAR_URL_PREFIX = '/files/avatar'


def user_doc_path(public_id, slot: Optional[str] = None) -> Optional[str]:
    """
    Resolve the OPAQUE identifier of a document row to the relative path of the
    bytes it owns, canonicalised (no legacy `documents/` prefix).

    This is the lookup that replaced the old URL-mirrors-the-disk scheme. It
    answers WHICH BYTES only; it deliberately says nothing about who may read
    them — that stays with `user_doc_owner_id` + `may_view_user_doc`, which the
    route calls next on the path returned here.

    Args:
        public_id: the row's public UUID handle (a `uuid.UUID` or its string
                   form; anything malformed simply misses).
        slot:      required for `SemesterEnrollment`, forbidden for the other
                   two — see the section banner above.

    Returns:
        str  — path relative to `USER_DOCS_FOLDER`.
        None — no such row, an unknown slot, or a row whose column is NULL
               (`Submission.file_path` is nullable: a coordinator may resolve a
               step without a file). All three collapse into one 404 at the
               route, so an unknown identifier is indistinguishable from a
               known one with nothing attached.
    """
    if public_id is None:
        return None

    if slot is not None:
        if slot not in DOC_SLOTS:
            return None
        enrollment = SemesterEnrollment.by_uuid(public_id)
        if enrollment is None:
            return None
        stored = (
            enrollment.payment_proof_path
            if slot == DOC_SLOT_PAYMENT_PROOF
            else enrollment.schedule_path
        )
        return normalize_relative_path(stored)

    submission = Submission.by_uuid(public_id)
    if submission is not None:
        return normalize_relative_path(submission.file_path)

    acceptance_doc = AcceptanceDocument.by_uuid(public_id)
    if acceptance_doc is not None:
        return normalize_relative_path(acceptance_doc.file_path)

    return None


def document_download_name(relative_path: str) -> Optional[str]:
    """
    The human filename a download must be saved as: the basename exactly as it
    sits on disk ('Constancia_de_estudios.pdf').

    The URL no longer carries it, so the route has to put it back in
    `Content-Disposition`. Werkzeug emits the RFC 5987 `filename*=UTF-8''…`
    form automatically as soon as the value is not pure ASCII, which is what
    keeps a Spanish name like 'Título.pdf' intact.
    """
    rel = normalize_relative_path(relative_path)
    if rel is None:
        return None
    return basename(rel) or None


def _doc_url(row, slot: Optional[str] = None) -> Optional[str]:
    """Served URL for a row that carries a public handle, or None."""
    handle = getattr(row, 'uuid', None) if row is not None else None
    if handle is None:
        return None
    if slot is None:
        return f'{DOC_URL_PREFIX}/{handle}'
    return f'{DOC_URL_PREFIX}/{handle}/{slot}'


def submission_file_url(submission) -> Optional[str]:
    """Served URL of a `Submission`'s attachment, or None when it has none."""
    if submission is None or not getattr(submission, 'file_path', None):
        return None
    return _doc_url(submission)


def acceptance_document_url(document) -> Optional[str]:
    """Served URL of an `AcceptanceDocument`, or None when nothing is uploaded."""
    if document is None or not getattr(document, 'file_path', None):
        return None
    return _doc_url(document)


def enrollment_payment_proof_url(enrollment) -> Optional[str]:
    """Served URL of a semester's comprobante de pago, or None."""
    if enrollment is None or not getattr(enrollment, 'payment_proof_path', None):
        return None
    return _doc_url(enrollment, DOC_SLOT_PAYMENT_PROOF)


def enrollment_schedule_url(enrollment) -> Optional[str]:
    """Served URL of a semester's horario, or None."""
    if enrollment is None or not getattr(enrollment, 'schedule_path', None):
        return None
    return _doc_url(enrollment, DOC_SLOT_SCHEDULE)


def avatar_url(user) -> Optional[str]:
    """
    Served URL of a user's profile photo, or None when they have none.

    'default.jpg' is a sentinel meaning "no photo": it is a static asset, not a
    stored file, and the route would 404 on it.
    """
    if user is None:
        return None
    stored = getattr(user, 'avatar', None)
    handle = getattr(user, 'uuid', None)
    if not stored or stored == 'default.jpg' or handle is None:
        return None
    return f'{AVATAR_URL_PREFIX}/{handle}'


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
        viewer ATTENDS but does not manage          → True
        everything else                             → False

    The event-host exception keeps the shipped public events UI working: the
    ponente chips of `/events` and the event detail page render
    `User.avatar_url` of internal hosts to every authenticated attendee, and a
    staff host has no UserProgram row, so no program scope would ever cover it.
    What it must never do is answer to the person who WROTE the host row — see
    `_hosts_event_visible_to`.
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
    True if `target_user_id` is registered as the host (ponente) of an event
    the viewer may PARTICIPATE in and may NOT MANAGE.

    The rule is not restated here: the events module owns it
    (`EventsService.user_may_manage_event` / `user_may_participate_in_event`),
    and this is the door that legitimises a staff member's face photo, so it
    must not be one millimetre wider than the page that shows the chip. The
    hand-written predicate it replaces (`visible_to_students AND status !=
    'draft'`, OR the program in scope) ignored `visibility` and the program of
    a published event, so the ponente photos of a PRIVATE event of any
    postgraduate programme were served to every authenticated account.

    WHY MANAGING THE EVENT IS NOT ENOUGH — the third self-granted
    authorisation of this codebase. An `EventHost` row can only be written by
    somebody who MANAGES the event (`PUT /api/v1/events/<id>/hosts` →
    `_check_event_access`). So for a manager the row is not evidence of
    anything: they create an event on their own programme, name any account in
    the institution as "ponente", and this function used to answer that they
    manage an event naming that person — serving the face photograph of
    students of other programmes and of staff accounts that no `user_in_scope`
    call would ever cover. The ACL was right; its input was attacker-written.

    Re-derived like the Appointment fix: the exception is honoured only through
    an event the viewer ATTENDS but CANNOT manage. Whoever cannot manage the
    event could not have written the row, so the row is a third-party fact
    about the host — the organiser's public claim "this person is presenting" —
    which is precisely what the chip renders. The `and not manage` half is not
    redundant with the participation rule: a manager can invite themselves
    (`EventInvitation`), and that invitation would otherwise reopen the same
    self-grant one hop later.

    `set_event_hosts` closes the same hole from the writing side
    (`EventsService.user_may_be_named_host`). Both are needed: this one so a
    legacy row cannot be cashed in, that one so no new row can be minted.

    Candidate events are loaded and filtered in Python on purpose: a person
    hosts a handful of events, and expressing the rule twice — once in SQL,
    once in Python — is exactly how the four answers of this module came to
    disagree.
    """
    from app.models.event import Event, EventHost
    from app.services.events_service import EventsService

    if program_scope_service.accessible_program_ids(viewer) is None:
        # Global scope already returned True in `may_view_avatar`; kept for
        # direct callers.
        return True

    events = (
        db.session.query(Event)
        .join(EventHost, EventHost.event_id == Event.id)
        .filter(EventHost.user_id == target_user_id)
        .all()
    )
    return any(
        EventsService.user_may_participate_in_event(viewer, event)
        and not EventsService.user_may_manage_event(viewer, event)
        for event in events
    )
