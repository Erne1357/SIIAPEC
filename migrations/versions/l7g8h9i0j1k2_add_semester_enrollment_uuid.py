"""add_semester_enrollment_uuid

Revision ID: l7g8h9i0j1k2
Revises: k6f7g8h9i0j1
Create Date: 2026-08-12

Extends the public UUIDv7 handle of revision `k6f7g8h9i0j1` to a sixth table:
`semester_enrollment`.

Why a sixth table, when the owner named five
--------------------------------------------
The five are the entities whose INTEGER ID was exposed in a URL or a payload.
`semester_enrollment` was not one of them and still is not: no route is
addressed by its id and no payload key names it. It needs a handle for one
narrow reason — it is the only row that OWNS SERVED FILE BYTES without already
having one. `/files/doc/<user_id>/<phase>/<filename>` used to reach its
`payment_proof_path` and `schedule_path` through the path; with the path gone
from the URL, the replacement `/files/doc/<handle>/<slot>` needs something
opaque to name the row with. `id` would have reopened exactly the enumeration
hole the cutover closes.

It is a row with TWO files, which is why that route carries a slot segment
(`payment-proof` | `schedule`) while the single-file rows do not.

Everything else is identical to `k6f7g8h9i0j1`, on purpose — same generator,
same three-step order, same index naming, same idempotency guarantees. See that
revision's docstring for why a single `ADD COLUMN … NOT NULL DEFAULT` is not
available here.

`semester_enrollment` is created by the alembic chain (revision
`c4d5e6f7g8h9` added its payment proof column), NOT by
`database/DDL/01_create_tables.sql`, which only owns ten tables. So there is no
DDL file to keep in step and nothing to double-create on a fresh build.
"""
import sqlalchemy as sa
from alembic import op

from app.utils.uuid7 import uuid7


# revision identifiers, used by Alembic.
revision = 'l7g8h9i0j1k2'
down_revision = 'k6f7g8h9i0j1'
branch_labels = None
depends_on = None


TABLE = 'semester_enrollment'
INDEX_NAME = 'ix_semester_enrollment_uuid'

#: Rows per UPDATE round-trip during the backfill.
CHUNK = 500


def _backfill(conn):
    """Give every row with a NULL uuid a distinct, freshly generated UUIDv7."""
    ids = conn.execute(
        sa.text(f'SELECT id FROM {TABLE} WHERE "uuid" IS NULL ORDER BY id')
    ).scalars().all()

    # str + explicit CAST: psycopg2 registers no uuid.UUID adapter by default.
    stmt = sa.text(f'UPDATE {TABLE} SET "uuid" = CAST(:val AS uuid) WHERE id = :id')

    for start in range(0, len(ids), CHUNK):
        chunk = ids[start:start + CHUNK]
        conn.execute(stmt, [{'val': str(uuid7()), 'id': row_id} for row_id in chunk])

    return len(ids)


def upgrade():
    conn = op.get_bind()

    op.execute(f'ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS "uuid" uuid')
    _backfill(conn)
    op.execute(f'ALTER TABLE {TABLE} ALTER COLUMN "uuid" SET NOT NULL')
    op.execute(
        f'CREATE UNIQUE INDEX IF NOT EXISTS {INDEX_NAME} ON {TABLE} ("uuid")'
    )


def downgrade():
    op.execute(f'DROP INDEX IF EXISTS {INDEX_NAME}')
    op.execute(f'ALTER TABLE {TABLE} DROP COLUMN IF EXISTS "uuid"')
