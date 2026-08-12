# app/models/mixins.py
"""
Shared declarative mixins.

`PublicUUIDMixin` gives a model a public identifier that is safe to put in a
URL or a JSON payload. The integer primary key stays exactly as it is and
remains the target of every foreign key — the UUID is purely the public handle,
so nothing about the relational shape of the schema changes.

Applied to: User, Submission, AcceptanceDocument, DocumentTemplate, Archive.

The column type is `sa.Uuid`, not `postgresql.UUID`: it renders as the native
`uuid` type on PostgreSQL and as `CHAR(32)` on SQLite, so the in-memory test
database gets the same column semantics as production without a dialect branch.
"""
import sqlalchemy as sa
from sqlalchemy.orm import declared_attr

from app import db
from app.utils.uuid7 import parse_uuid, uuid7


class PublicUUIDMixin:
    """Adds a NOT NULL, uniquely indexed, app-generated UUIDv7 public handle."""

    @declared_attr
    def uuid(cls):
        # `unique=True` together with `index=True` produces a single UNIQUE
        # INDEX named `ix_<table>_uuid` — the same object the migration creates
        # by hand, so `db.create_all()` and `flask db upgrade` agree.
        #
        # `default=uuid7` is a Python-side column default: it fires on flush
        # for ORM and Core inserts alike. There is deliberately no
        # `server_default` — see the module docstring of app/utils/uuid7.py.
        return db.Column(
            sa.Uuid(as_uuid=True),
            nullable=False,
            unique=True,
            index=True,
            default=uuid7,
        )

    @classmethod
    def by_uuid(cls, value):
        """
        Look a row up by its public identifier.

        Returns None both when `value` is not a well-formed UUID and when no
        row carries it, so a caller can collapse the two into one 404 without a
        try/except — and without letting a malformed identifier be
        distinguishable from an unknown one.
        """
        parsed = parse_uuid(value)
        if parsed is None:
            return None
        return cls.query.filter_by(uuid=parsed).first()
