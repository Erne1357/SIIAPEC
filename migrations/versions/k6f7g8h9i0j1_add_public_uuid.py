"""add_public_uuid

Revision ID: k6f7g8h9i0j1
Revises: j5e6f7g8h9i0
Create Date: 2026-08-12

Adds the public UUIDv7 handle to the five entities whose integer id is
currently exposed in URLs and JSON payloads: `user`, `submission`,
`acceptance_document`, `document_template`, `archive`.

The integer primary key is NOT touched, and neither is any foreign key. The
relational shape of the schema is unchanged; this only adds a second, opaque,
non-enumerable way to name a row from outside the application.

Written by hand. `flask db migrate` on this project reports unrelated drift
(see revision ab113aa21d11), so autogenerate output is not reviewable here.

Three steps per table, in this order, and the order matters
-----------------------------------------------------------
1. ADD COLUMN nullable. A single-statement `ADD COLUMN … NOT NULL DEFAULT <x>`
   is not an option: a literal default would give every pre-existing row the
   SAME uuid and then fail the unique index, and a volatile server-side default
   (`uuidv7()`) does not exist before PostgreSQL 18 — the owner's dev cluster is
   16.9, so the migration would not run at all.
2. BACKFILL every NULL row with a distinct value from the one application-side
   generator, `app.utils.uuid7.uuid7`. This is mandatory, not cosmetic:
   `database/DML/03_insert_user.sql` and `08_insert_archive.sql` INSERT rows at
   initdb time, before alembic ever runs, so on a fresh build there are already
   rows waiting here with a NULL uuid.
3. SET NOT NULL, then CREATE UNIQUE INDEX.

Row counts read from the owner's dev database with SELECT on 2026-08-12
(alembic_version `ab113aa21d11`, PostgreSQL 16.9):

    "user"               81
    submission          227
    acceptance_document  24
    document_template     0
    archive              51
    ------------------------
    total               383 rows backfilled

A production cluster will differ in magnitude but not in kind; the backfill is
chunked and is a plain indexed UPDATE by primary key.

Coupling note
-------------
This revision imports the generator from `app.utils.uuid7` rather than inlining
a copy, so seeded rows and runtime rows carry values of the same shape from one
implementation. That means this historical migration depends on that module
continuing to exist; if it is ever moved, re-point this import rather than
duplicating the algorithm.

Idempotency
-----------
Every statement is re-runnable: `ADD COLUMN IF NOT EXISTS`, a backfill scoped
to `WHERE uuid IS NULL`, `SET NOT NULL` (a no-op when already set) and
`CREATE UNIQUE INDEX IF NOT EXISTS`. This is what lets
`database/DDL/01_create_tables.sql` declare the column for the three tables it
owns without this migration colliding with it on a fresh build.
"""
import sqlalchemy as sa
from alembic import op

from app.utils.uuid7 import uuid7


# revision identifiers, used by Alembic.
revision = 'k6f7g8h9i0j1'
down_revision = 'j5e6f7g8h9i0'
branch_labels = None
depends_on = None


#: Tables receiving the public handle. Quoted because `user` is reserved.
TABLES = (
    '"user"',
    'submission',
    'acceptance_document',
    'document_template',
    'archive',
)

#: Index names match SQLAlchemy's default convention (`ix_%(column_0_label)s`),
#: so a database built by `db.create_all()` — which is how the test suite builds
#: it — carries an index of the same name and shape as one built by alembic.
INDEX_NAMES = {
    '"user"': 'ix_user_uuid',
    'submission': 'ix_submission_uuid',
    'acceptance_document': 'ix_acceptance_document_uuid',
    'document_template': 'ix_document_template_uuid',
    'archive': 'ix_archive_uuid',
}

#: Rows per UPDATE round-trip during the backfill.
CHUNK = 500


def _backfill(conn, table):
    """Give every row with a NULL uuid a distinct, freshly generated UUIDv7."""
    ids = conn.execute(
        sa.text(f'SELECT id FROM {table} WHERE "uuid" IS NULL ORDER BY id')
    ).scalars().all()

    # The parameter is bound as text and cast explicitly: psycopg2 does not
    # register a uuid.UUID adapter by default, so a str + CAST is the form that
    # is guaranteed to reach the server as a uuid.
    stmt = sa.text(f'UPDATE {table} SET "uuid" = CAST(:val AS uuid) WHERE id = :id')

    for start in range(0, len(ids), CHUNK):
        chunk = ids[start:start + CHUNK]
        conn.execute(stmt, [{'val': str(uuid7()), 'id': row_id} for row_id in chunk])

    return len(ids)


def upgrade():
    conn = op.get_bind()

    for table in TABLES:
        # 1. nullable column
        op.execute(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "uuid" uuid')

        # 2. backfill
        _backfill(conn, table)

        # 3. constrain
        op.execute(f'ALTER TABLE {table} ALTER COLUMN "uuid" SET NOT NULL')
        op.execute(
            f'CREATE UNIQUE INDEX IF NOT EXISTS {INDEX_NAMES[table]} '
            f'ON {table} ("uuid")'
        )


def downgrade():
    for table in reversed(TABLES):
        op.execute(f'DROP INDEX IF EXISTS {INDEX_NAMES[table]}')
        op.execute(f'ALTER TABLE {table} DROP COLUMN IF EXISTS "uuid"')
