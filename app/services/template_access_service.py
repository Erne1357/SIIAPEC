"""
Template Access Service — who may READ a blank template file.

Two different catalogs store "templates" and both were reachable through
`/files/template/<filename>`, which until now served every byte in
`TEMPLATE_STORE` to any authenticated account with no check at all:

  * `Archive.file_path` — the official blank form of a step of the admission /
    permanence / conclusion process. `archives_api.download_template` already
    had the right rule for these (published formats are open, internal ones
    need the step); that rule lived as a private helper inside the route
    module, so the flat-file route could not reuse it.
  * `DocumentTemplate.file_path` — the institutional letter templates
    (acceptance letter, tira de materias, referencia de pago). These are
    administrative material, never applicant-facing.

The rule therefore had to leave the route module. It lives here so that both
`archives_api` and `files_api` ask the SAME question instead of growing a
second, divergent answer.

Authorisation starts from the DATABASE ROW that owns the bytes, never from the
URL: the row says which step (and therefore which programs) or which program
the template belongs to. A file that no row references does not exist for the
application and must be answered with 404 — serving orphan bytes left behind by
a re-upload is exactly the hole this module closes.

Framework-agnostic by contract: no `request`, no `g`, no `current_user`.
Callers pass the User object and the requested filename.
"""

from __future__ import annotations

from typing import Optional, Set

from sqlalchemy import select

from app import db
from app.models.archive import Archive
from app.models.document_template import DocumentTemplate
from app.models.program_step import ProgramStep
from app.services import program_scope_service


#: Capability needed to pull an institutional document template
#: (`DocumentTemplate`). It is the same codename that lists them in the admin
#: UI: whoever may not see the catalog may not download its files either.
#: Holding it is necessary but NOT sufficient — the owning program must also be
#: inside the caller's scope.
DOCUMENT_TEMPLATE_PERMISSION = 'admin_templates.api.list'


def _basename(stored_path: Optional[str]) -> Optional[str]:
    """Last segment of a stored path, tolerating both path separators.

    `Archive.file_path` is written with `os.path.relpath`, so it carries
    backslashes when the row was created on Windows and forward slashes when it
    was created inside the container. Both spellings name the same file.
    """
    if not stored_path or not isinstance(stored_path, str):
        return None
    cleaned = stored_path.replace('\\', '/').rstrip('/')
    if not cleaned:
        return None
    return cleaned.rsplit('/', 1)[-1] or None


def visible_step_ids(viewer) -> Optional[Set[int]]:
    """Steps whose material `viewer` may CONSULT (download templates).

    Wider than the administration filter: it also covers the steps of the
    programs the viewer is ENROLLED in, because an applicant must be able to
    download the blank forms of their own process.

    Returns:
        None if the viewer has global scope (every step);
        otherwise the set of step ids.
    """
    scope = program_scope_service.accessible_program_ids(viewer)
    if scope is None:
        return None

    pids = set(scope)
    viewer_id = getattr(viewer, 'id', None)
    if viewer_id is not None:
        pids |= program_scope_service.program_ids_of_user(viewer_id)
    if not pids:
        return set()

    step_ids = db.session.execute(
        select(ProgramStep.step_id).where(ProgramStep.program_id.in_(pids))
    ).scalars().all()
    return set(step_ids)


def may_download_archive_template(viewer, archive: Archive) -> bool:
    """True if `viewer` may download the blank form attached to `archive`.

    `is_downloadable` marks the published formats: blank forms the program page
    offers to anyone interested, so they stay open to any authenticated user.
    The rest is internal material — only whoever administers that step or
    studies a program that includes it.
    """
    if viewer is None or archive is None:
        return False
    if archive.is_downloadable:
        return True
    visible = visible_step_ids(viewer)
    if visible is None:            # global scope
        return True
    return archive.step_id in visible


def may_download_document_template(viewer, template: DocumentTemplate) -> bool:
    """True if `viewer` may download an institutional `DocumentTemplate`.

    These are administrative artefacts, not applicant material, so the
    capability is required first. Scope follows the same shape the events
    module uses for objects that may or may not belong to a program: a template
    tied to a program needs that program in scope; a GLOBAL template
    (`program_id IS NULL`) is institutional and needs global scope.
    """
    if viewer is None or template is None:
        return False
    if not viewer.has_permission(DOCUMENT_TEMPLATE_PERMISSION):
        return False
    if template.program_id is None:
        return program_scope_service.accessible_program_ids(viewer) is None
    return program_scope_service.program_in_scope(viewer, template.program_id)


def _rows_matching_filename(model, column, filename: str) -> list:
    """Rows of `model` whose stored `column` ends in exactly `filename`.

    The LIKE is only a prefilter (the stored value may carry a directory prefix
    and either separator); the exact decision is the basename comparison in
    Python, so LIKE metacharacters inside the filename can widen the candidate
    set but never widen the match.
    """
    candidates = db.session.execute(
        select(model).where(column.like(f'%{filename}'))
    ).scalars().all()
    return [row for row in candidates if _basename(getattr(row, 'file_path', None)) == filename]


def may_download_flat_template(viewer, filename: str) -> bool:
    """True if `viewer` may download the flat template file `filename`.

    Resolves the owning row first — `DocumentTemplate` (relative to
    `TEMPLATE_STORE`) and then `Archive` (legacy rows that stored a bare
    filename) — and applies that catalog's rule. No owning row → False, and the
    route must answer 404: for the application, that file does not exist.
    """
    if viewer is None or not filename:
        return False

    for template in _rows_matching_filename(
            DocumentTemplate, DocumentTemplate.file_path, filename):
        if may_download_document_template(viewer, template):
            return True

    for archive in _rows_matching_filename(Archive, Archive.file_path, filename):
        if may_download_archive_template(viewer, archive):
            return True

    return False


__all__ = [
    'DOCUMENT_TEMPLATE_PERMISSION',
    'visible_step_ids',
    'may_download_archive_template',
    'may_download_document_template',
    'may_download_flat_template',
]
