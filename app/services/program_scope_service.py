"""
Program Scope Service — the single source of truth for "is this object inside
the caller's programs?".

Holding a permission codename answers *what* a user may do; it never answers
*to whom*. Every route that touches a concrete student, program, document,
photo or decision must ALSO ask this module whether the target object lives
inside the caller's program scope.

Two tiers, deliberately named so the call site reads unambiguously:

  1. FULL SCOPE — `user_in_scope()` / `program_in_scope()` / `require_*`.
     Short names, strict semantics. Passing means the caller may read the full
     record (CURP, RFC, NSS, address, birth date, documents, photos, history)
     and may perform writes.

  2. CROSS-PROGRAM SUMMARY — `may_view_cross_program_summary()` plus the
     `CROSS_PROGRAM_SUMMARY_FIELDS` allow-list and the
     `to_cross_program_summary()` projection.
     Long names on purpose. A caller who FAILS the full-scope check may still
     see a student's NAME, E-MAIL and PROGRESS/STATUS — nothing else, and no
     writes. This is the shipped behaviour of
     `GET /api/v1/coordinator/students?show_other=true`.

     There is no function here that returns a full record for an
     out-of-scope caller. If you want personal data, `user_in_scope()` must
     return True; there is no other door.

Global admins (`User.get_accessible_program_ids()` returns None — i.e. the
postgraduate_admin, whose ROLE grants `academic_periods.api.create`) are in
scope for everything and both tiers return True for them. Global scope is a
role-level fact on purpose: `User.has_global_program_scope()` ignores
delegations, so a delegation of that codename scoped to one program can never
turn its holder into a global admin.

Where a non-global scope comes from (`User.get_accessible_program_ids()`):
  - the programs the user coordinates (`Program.coordinator_id`), plus
  - the program of each active delegation THAT ADDS A CAPABILITY — a delegated
    codename the user's role already grants contributes nothing, so nobody can
    widen their reach by handing themselves (or being handed) a permission they
    already had. A delegation with `program_id = NULL` adds no scope either.

Permission ≠ scope, and the permission layer no longer pretends otherwise:
`User.has_permission(codename)` takes NO program_id and answers only "may this
account do this at all". Every "may it do it HERE" question comes through this
module.

Framework-agnostic by contract: no `request`, no `g`, no `current_user` in
this module. Callers pass User objects / ids explicitly. The Flask-facing
decorators and guards that read `current_user` live in
`app/utils/permissions.py` and delegate here.
"""

from typing import Iterable, Mapping, Optional, Union

from app import db
from app.models.user_program import UserProgram


# ─── Constants ───────────────────────────────────────────────────────────────

#: Permission that grants the cross-program summary tier (the "ver otros
#: programas" toggle of the coordinator dashboard). Kept as a named constant
#: so no call site has to hardcode the codename.
CROSS_PROGRAM_SUMMARY_PERMISSION = 'coordinator.api.list_students'

#: The ONLY fields a caller may see about a student who is OUTSIDE their
#: program scope: name, e-mail and progress/status. Everything absent from
#: this set is forbidden cross-program — CURP, RFC, NSS, address, birth date,
#: birth place, cédula, emergency contact, control number, any document or
#: document URL, the avatar/profile photo, and the user history.
#:
#: Read endpoints must project their payload onto this set with
#: `to_cross_program_summary()` whenever `user_in_scope()` returned False.
CROSS_PROGRAM_SUMMARY_FIELDS = frozenset({
    # Identity — name and e-mail only
    'id',
    'user_id',
    'full_name',
    'first_name',
    'last_name',
    'mother_last_name',
    'email',
    # Program the row belongs to (needed to render the "otro programa" chip)
    'program_id',
    'program_name',
    'program_slug',
    # Progress / status
    'current_phase',
    'overall_status',
    'admission_status',
    'progress_percentage',
    'approved_docs',
    'pending_docs',
    'rejected_docs',
    'extended_docs',
    'ready_for_interview',
    'current_semester',
    'completed_semesters',
    'total_semesters',
    'academic_progress',
    'in_progress_segment',
    'academic_status',
    'conclusion_stage',
    'conclusion_progress',
    'conclusion_status',
    # Whether the caller may act on the row (always False cross-program)
    'can_manage',
})

#: The ONLY user columns a list endpoint may put inside a SEARCH / FILTER
#: predicate for a row that is OUTSIDE the caller's scope.
#:
#: A projection runs AFTER the query, so it cannot protect a field the WHERE
#: clause already touched: if a forbidden column takes part in the filter, the
#: mere presence or absence of the row answers "does this student's <column>
#: contain X?" — the value leaks one guess at a time even though it never
#: appears in the payload. A sort key leaks the same way (it publishes the
#: column's relative order), and so does any count computed over such a filter.
#:
#: Therefore: a caller may only search, filter, sort or count on fields they are
#: allowed to SEE for that row. Endpoints that want the full field set must
#: split the predicate per row (full set AND in-scope) OR (reduced set AND
#: out-of-scope) — see `list_users` in app/routes/api/admin/users_api.py.
#:
#: Deliberately absent, and why:
#:   control_number — named forbidden by the cross-program tier.
#:   username       — IS the control number once `User.assign_control_number()`
#:                    runs, so searching it is searching the control number.
#:   is_active, role, avatar, any personal column — not on the summary
#:                    allow-list, therefore forbidden.
#:   full_name      — not a column; first_name + last_name already cover it.
CROSS_PROGRAM_SEARCHABLE_FIELDS = frozenset({
    'first_name',
    'last_name',
    'mother_last_name',
    'email',
})

if not CROSS_PROGRAM_SEARCHABLE_FIELDS <= CROSS_PROGRAM_SUMMARY_FIELDS:  # pragma: no cover
    raise RuntimeError(
        'CROSS_PROGRAM_SEARCHABLE_FIELDS must stay a subset of '
        'CROSS_PROGRAM_SUMMARY_FIELDS: nothing may be searched cross-program '
        'that cannot be shown cross-program.'
    )


class ProgramScopeDenied(PermissionError):
    """
    Raised by the `require_*` guards when the target object is outside the
    caller's program scope. Routes translate it to a 403 with the standard
    envelope; services may let it bubble.
    """

    def __init__(self, message: str = 'No tienes acceso a este programa.'):
        super().__init__(message)
        self.message = message


# ─── (a) Which programs may this user act on? ────────────────────────────────

def accessible_program_ids(user) -> Optional[set]:
    """
    Program ids the user may act on.

    Returns:
        set[int] — the concrete scope (possibly empty: an undelegated
                   social_service account legitimately has NO scope).
        None     — global access (postgraduate_admin). "None" means ALL, never
                   "none"; always test with `is None` before truth-testing.
    """
    if user is None:
        return set()
    pids = user.get_accessible_program_ids()
    if pids is None:
        return None
    return set(pids)


def is_global_scope(user) -> bool:
    """True if the user may act on every program (postgraduate_admin)."""
    return accessible_program_ids(user) is None


# ─── (c) Is this PROGRAM inside the caller's scope? ──────────────────────────

def program_in_scope(user, program_id) -> bool:
    """
    True if `program_id` is inside the caller's scope.

    A missing / unparseable program_id fails closed (returns False).
    """
    if program_id is None:
        return False
    try:
        program_id = int(program_id)
    except (TypeError, ValueError):
        return False

    scope = accessible_program_ids(user)
    if scope is None:
        return True
    return program_id in scope


def programs_in_scope(user, program_ids: Iterable) -> set:
    """
    Bulk variant of `program_in_scope`: returns the subset of `program_ids`
    inside the caller's scope. No queries beyond the one scope lookup.
    """
    wanted = set()
    for pid in (program_ids or []):
        try:
            wanted.add(int(pid))
        except (TypeError, ValueError):
            continue

    scope = accessible_program_ids(user)
    if scope is None:
        return wanted
    return wanted & scope


def require_program_in_scope(user, program_id) -> None:
    """Raise ProgramScopeDenied unless `program_id` is inside the scope."""
    if not program_in_scope(user, program_id):
        raise ProgramScopeDenied('No tienes acceso a este programa.')


# ─── (b) Is this TARGET USER inside the caller's programs? ───────────────────

def program_ids_of_user(target: Union[int, object]) -> set:
    """
    Program ids the TARGET user belongs to (their UserProgram rows).

    An empty set means the target has no UserProgram at all — a staff account
    (program_admin, postgraduate_admin, social_service) or an applicant who
    has not picked a program yet. See `user_in_scope` for what that implies.
    """
    if target is None:
        return set()

    # A loaded User instance: reuse the relationship, no extra query.
    rows = getattr(target, 'user_program', None)
    if rows is not None and not isinstance(target, int):
        return {up.program_id for up in rows}

    try:
        target_id = int(target)
    except (TypeError, ValueError):
        return set()

    return {
        pid for (pid,) in db.session.query(UserProgram.program_id)
        .filter(UserProgram.user_id == target_id)
        .distinct()
        .all()
    }


def program_ids_by_user(user_ids: Iterable[int]) -> dict:
    """
    Bulk variant of `program_ids_of_user` — ONE query for many users.

    Returns:
        dict[int, set[int]] mapping user_id → program ids. Ids with no
        UserProgram row are present with an empty set, so the caller can tell
        "no programs" from "not asked about".
    """
    wanted = set()
    for uid in (user_ids or []):
        try:
            wanted.add(int(uid))
        except (TypeError, ValueError):
            continue

    out = {uid: set() for uid in wanted}
    if not wanted:
        return out

    rows = (
        db.session.query(UserProgram.user_id, UserProgram.program_id)
        .filter(UserProgram.user_id.in_(wanted))
        .all()
    )
    for uid, pid in rows:
        out[uid].add(pid)
    return out


def user_in_scope(user, target: Union[int, object], allow_self: bool = True) -> bool:
    """
    THE check. True if the caller may see the TARGET's full record and act on
    them: the target belongs to at least one program inside the caller's scope.

    Args:
        user:       the caller (User).
        target:     the target User instance or its id (int).
        allow_self: when True (default) a user is always in scope for
                    themselves — this preserves "the student sees their own
                    expediente" without a program lookup.

    Truth table:
        postgraduate_admin (global)            → True for anyone
        program_admin, target in their program → True
        program_admin, target elsewhere        → False
        social_service with a delegation on P, target in P    → True
        social_service with a delegation on P, target outside → False
        social_service with no delegation      → False for anyone but self
        applicant / student                    → only themselves
        target with NO UserProgram (staff)     → False for every non-global
                                                 caller, including a caller
                                                 who coordinates every program.
                                                 Fail-closed and deliberate:
                                                 a staff account is nobody's
                                                 student, so no program_admin
                                                 inherits their PII. Only a
                                                 postgraduate_admin (or the
                                                 account itself) may read it.

    A caller who fails this check is NOT simply denied everything: see
    `may_view_cross_program_summary()` for the name / e-mail / progress tier.
    """
    if user is None or target is None:
        return False

    target_id = getattr(target, 'id', None)
    if target_id is None:
        try:
            target_id = int(target)
        except (TypeError, ValueError):
            return False

    if allow_self and getattr(user, 'id', None) == target_id:
        return True

    scope = accessible_program_ids(user)
    if scope is None:
        return True
    if not scope:
        return False

    target_pids = program_ids_of_user(target)
    if not target_pids:
        # Staff account / applicant with no program: nobody but a global admin
        # (handled above) or the account itself inherits it.
        return False

    return bool(target_pids & scope)


def users_in_scope(user, user_ids: Iterable[int], allow_self: bool = True) -> set:
    """
    Bulk variant of `user_in_scope` — use this on list endpoints, ONE query
    for the whole page instead of one per row.

    Returns:
        set[int] — the subset of `user_ids` inside the caller's scope. Ids not
        in the returned set are out of scope: render them with
        `to_cross_program_summary()` or drop them.

    A global caller gets every id back without a lookup; existence of the ids
    is not validated in that case.
    """
    wanted = set()
    for uid in (user_ids or []):
        try:
            wanted.add(int(uid))
        except (TypeError, ValueError):
            continue
    if not wanted:
        return set()

    caller_id = getattr(user, 'id', None)

    scope = accessible_program_ids(user)
    if scope is None:
        return wanted

    allowed = set()
    if allow_self and caller_id in wanted:
        allowed.add(caller_id)
    if not scope:
        return allowed

    rows = (
        db.session.query(UserProgram.user_id)
        .filter(
            UserProgram.user_id.in_(wanted),
            UserProgram.program_id.in_(scope),
        )
        .distinct()
        .all()
    )
    allowed.update(uid for (uid,) in rows)
    return allowed


def require_user_in_scope(user, target, allow_self: bool = True) -> None:
    """Raise ProgramScopeDenied unless the target is inside the caller's scope."""
    if not user_in_scope(user, target, allow_self=allow_self):
        raise ProgramScopeDenied(
            'No tienes acceso al expediente de este usuario.'
        )


# ─── The owner's allowed cross-program tier ──────────────────────────────────

def may_view_cross_program_summary(
    user,
    codename: str = CROSS_PROGRAM_SUMMARY_PERMISSION,
) -> bool:
    """
    True if this caller may see the REDUCED tier — name, e-mail and
    progress/status ONLY — of students who are OUTSIDE their program scope.

    Deliberately long name: it must never be mistaken for `user_in_scope()`.
    Passing this check authorises exactly the fields in
    `CROSS_PROGRAM_SUMMARY_FIELDS` and NOTHING else — no personal data, no
    documents, no photos, no history, and no write of any kind.

    Concretely: this is what powers the "ver otros programas" toggle of
    `GET /api/v1/coordinator/students?show_other=true`. Callers must still run
    the payload through `to_cross_program_summary()`.
    """
    if user is None:
        return False
    return bool(user.has_permission(codename))


def to_cross_program_summary(payload: Mapping) -> dict:
    """
    Project a student payload onto `CROSS_PROGRAM_SUMMARY_FIELDS`.

    Everything not on the allow-list is dropped — including keys added later,
    so a new PII field cannot leak by being forgotten here. `can_manage` is
    forced to False: by definition the caller cannot act on this row.
    """
    out = {k: v for k, v in (payload or {}).items()
           if k in CROSS_PROGRAM_SUMMARY_FIELDS}
    if 'can_manage' in out:
        out['can_manage'] = False
    return out
