"""add_program_admission_interest

Revision ID: ab113aa21d11
Revises: 0d517ddc1e45
Create Date: 2026-08-03 14:01:16.738298

Autogeneration also reported drift unrelated to this change (an
`ix_event_host_event_id` / `ix_event_image_event_id` index drop, a
`document_deadline.archived_by` foreign-key rewrite and a `purge_run.run_id`
unique-constraint drop). Those were removed by hand: they are destructive and
belong to whatever change introduced the drift, not to this table.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ab113aa21d11'
down_revision = '0d517ddc1e45'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'program_admission_interest',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('program_id', sa.Integer(), nullable=False),
        sa.Column('period_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('notified_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['period_id'], ['academic_period.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['program_id'], ['program.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'user_id', 'program_id', 'period_id',
            name='uq_program_admission_interest',
        ),
    )
    with op.batch_alter_table('program_admission_interest', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_program_admission_interest_program_id'),
            ['program_id'], unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_program_admission_interest_user_id'),
            ['user_id'], unique=False,
        )


def downgrade():
    with op.batch_alter_table('program_admission_interest', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_program_admission_interest_user_id'))
        batch_op.drop_index(batch_op.f('ix_program_admission_interest_program_id'))

    op.drop_table('program_admission_interest')
