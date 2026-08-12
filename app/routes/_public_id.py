# app/routes/_public_id.py
"""
Route-side helpers for the public UUID identifiers.

Every route rule that used to carry `<int:something_id>` for User, Submission,
AcceptanceDocument, DocumentTemplate or Archive now carries
`<uuid:something_uuid>`. Two things follow, and both live here so no route has
to reinvent them:

1. **Resolution.** Flask's built-in `uuid` converter hands the view a
   `uuid.UUID`; the view turns it into a row with `Model.by_uuid(...)`
   (`app/models/mixins.py`) and, if that returns None, answers 404.

2. **Indistinguishability.** "No such identifier" and "that row is not yours"
   MUST answer the same status AND the same body. A 404 for the first and a
   403 for the second is an enumeration oracle at the new front door, which is
   exactly what the previous hardening passes closed. So a route that denies
   out-of-scope rows with a 404 must answer an unknown identifier with THAT
   SAME 404 body (each blueprint already owns one — `acceptance_api._not_found`,
   `review_api._not_found`, `student_record_api._record_not_found`), and a route
   that denies them with the scope 403 must route the unknown identifier through
   the SAME guard: `guard_user_scope(resolved_id(row))` fails closed on None and
   produces the identical 403 body.

Note on the routing-level 404: Werkzeug's uuid converter refuses a malformed
segment before any decorator runs, so the request never reaches the view and
Flask's own 404 handler answers. `app/__init__.py` renders that handler as the
standard JSON envelope for `/api/` paths, so a malformed identifier and an
unknown one are indistinguishable there as well.
"""

__all__ = ['resolved_id']


def resolved_id(row):
    """
    Internal integer id of a resolved row, or None.

    Handed to `guard_user_scope` / `guard_program_scope` so an unresolved
    identifier reaches the scope guard as None and fails closed there, giving
    byte-identical output to a row that exists but is out of scope.
    """
    return getattr(row, 'id', None) if row is not None else None
