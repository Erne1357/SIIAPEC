# app/services/public_id_service.py
"""
Public identifier projection — the one place that turns an internal integer
primary key into the UUID a payload is allowed to publish.

Why this module exists
----------------------
The integer primary key stays exactly as it is: it remains every foreign key,
every service argument, every history/notification/log value and every Celery
task argument. What changes is the PUBLIC surface — URLs and JSON. So the
codebase needs a narrow, boring seam between the two, and this is it.

Contract:

    public_id(obj)                 -> str | None    row already in hand
    uuid_for(Model, pk)            -> str | None    only the FK integer in hand
    uuids_for(Model, pks)          -> dict[int,str] bulk, ONE query

`uuid_for` is the expensive one — it is a lookup. Prefer `public_id` whenever
the relationship is already loaded (`sub.user` instead of `sub.user_id`), and
prefer `uuids_for` on anything that runs per row of a list endpoint.

Framework-agnostic by contract, like `program_scope_service`: no `request`, no
`g`, no `current_user`. Callers pass models and ids explicitly.
"""

import json
from typing import Iterable, Mapping, Optional

from app import db

__all__ = [
    'public_id',
    'uuid_for',
    'uuids_for',
    'project_uuid_keys',
    'correction_required_to_public',
    'correction_required_to_internal',
    'user_uuid',
    'archive_uuid',
    'submission_uuid',
]


def public_id(obj) -> Optional[str]:
    """
    Canonical hyphenated string form of a row's public handle.

    Returns None for a missing row and for a row whose `uuid` is not populated
    yet (a pending instance before flush), so a payload never publishes a
    half-built identifier.
    """
    if obj is None:
        return None
    value = getattr(obj, 'uuid', None)
    return str(value) if value is not None else None


def uuid_for(model, pk) -> Optional[str]:
    """
    Public handle of `model` row `pk`, given only the integer foreign key.

    Uses `Session.get`, so a row already in the identity map costs no query —
    which is the common case inside a service that just loaded the object.
    """
    if pk is None:
        return None
    try:
        pk = int(pk)
    except (TypeError, ValueError):
        return None
    return public_id(db.session.get(model, pk))


def uuids_for(model, pks: Iterable) -> dict:
    """
    Bulk variant of `uuid_for` — ONE query for a whole page of rows.

    Returns:
        dict[int, str] mapping integer id → uuid string. Ids with no row are
        simply absent, so `out.get(pk)` yields None exactly as `uuid_for` would.
    """
    wanted = set()
    for pk in (pks or []):
        if pk is None:
            continue
        try:
            wanted.add(int(pk))
        except (TypeError, ValueError):
            continue
    if not wanted:
        return {}

    rows = (
        db.session.query(model.id, model.uuid)
        .filter(model.id.in_(wanted))
        .all()
    )
    return {row_id: str(row_uuid) for row_id, row_uuid in rows}


def project_uuid_keys(payload: Mapping, mapping: Mapping) -> dict:
    """
    Rewrite the integer ids of an already-built dict in place-ish.

    `mapping` is {key_name: model}. Every key present in `payload` is replaced
    by the public handle of that model's row. Keys absent from `payload` are
    left absent — this never invents a key.

    Deliberately keeps the SAME key names: `program_scope_service`'s
    `CROSS_PROGRAM_SUMMARY_FIELDS` is an allow-list BY KEY NAME, so renaming
    `user_id` to `user_uuid` would silently drop the field from every
    cross-program projection.
    """
    out = dict(payload or {})
    for key, model in mapping.items():
        if key in out:
            out[key] = uuid_for(model, out[key])
    return out


# ─── UserProgram.correction_required ─────────────────────────────────────────
#
# That column is free text that MAY hold a JSON object
# `{"archive_id": …, "archive_name": …, "notes": …}`. It is persisted, so the
# stored value keeps the INTEGER archive id like every other persisted id; only
# the copy that goes out over the wire is projected, and only the copy that
# comes in over the wire is translated back. Anything that is not that exact
# JSON shape (plain prose, which is the common case) passes through untouched.

def correction_required_to_public(value):
    """Stored `correction_required` → the form published in a payload."""
    return _swap_archive_id(value, to_public=True)


def correction_required_to_internal(value):
    """Client-supplied `correction_required` → the form that gets persisted."""
    return _swap_archive_id(value, to_public=False)


def _swap_archive_id(value, to_public: bool):
    from app.models.archive import Archive

    if not value or not isinstance(value, str):
        return value
    try:
        blob = json.loads(value)
    except (ValueError, TypeError):
        return value
    if not isinstance(blob, dict) or 'archive_id' not in blob:
        return value

    raw = blob['archive_id']
    if raw is None:
        return value

    if to_public:
        blob['archive_id'] = uuid_for(Archive, raw)
    else:
        row = Archive.by_uuid(raw) if not isinstance(raw, int) else None
        blob['archive_id'] = row.id if row is not None else None

    return json.dumps(blob, ensure_ascii=False)


# ─── Shorthands for the three ids that appear in almost every payload ────────
#
# They exist so a service can publish a public handle with one call and one
# import, instead of repeating the model lookup at every emit site. Models are
# imported lazily to keep this module free of import cycles.

def user_uuid(pk):
    """Public handle of a User, from its internal id."""
    from app.models.user import User
    return uuid_for(User, pk)


def archive_uuid(pk):
    """Public handle of an Archive, from its internal id."""
    from app.models.archive import Archive
    return uuid_for(Archive, pk)


def submission_uuid(pk):
    """Public handle of a Submission, from its internal id."""
    from app.models.submission import Submission
    return uuid_for(Submission, pk)
