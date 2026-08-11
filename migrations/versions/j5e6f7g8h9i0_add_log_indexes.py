"""add_log_indexes

Revision ID: j5e6f7g8h9i0
Revises: ab113aa21d11
Create Date: 2026-08-11

The `log` table already exists — it comes from database/DDL/01_create_tables.sql
and revision 220e09fdfab8 already made `log.user_id` nullable, so the mapped
model matches the deployed schema. No table or column is created here.

What was missing is any index at all. Until now nothing wrote to `log`; it is
now the destination of the authentication audit trail (AuthAuditService), which
adds one row per successful login. The two access paths an audit uses are
"everything that happened to this account" and "every failed login in this
period", and both were sequential scans.

Written by hand rather than by `flask db migrate`: autogenerate on this project
reports unrelated drift (see revision ab113aa21d11), and an index-only change is
not worth reviewing 200 lines of it.

Raw SQL with IF NOT EXISTS so the migration is idempotent on a database where
these indexes were already created out of band.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'j5e6f7g8h9i0'
down_revision = 'ab113aa21d11'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        'CREATE INDEX IF NOT EXISTS ix_log_user_id_created_at '
        'ON log (user_id, created_at)'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS ix_log_action_created_at '
        'ON log (action, created_at)'
    )


def downgrade():
    op.execute('DROP INDEX IF EXISTS ix_log_action_created_at')
    op.execute('DROP INDEX IF EXISTS ix_log_user_id_created_at')
