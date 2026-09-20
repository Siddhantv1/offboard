"""case stage events

The audit trail behind stage transitions. Pure DDL.

Revision ID: d4b91c7e5a83
Revises: c3a8d16b42f7
Create Date: 2026-09-20 22:05:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd4b91c7e5a83'
down_revision: Union[str, Sequence[str], None] = 'c3a8d16b42f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'case_stage_events',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('case_id', sa.String(length=32), nullable=False),
        sa.Column('stage_key', sa.String(length=32), nullable=True),
        sa.Column('stage_name', sa.Text(), nullable=False),
        sa.Column('from_status', sa.String(length=16), nullable=True),
        sa.Column('to_status', sa.String(length=16), nullable=False),
        sa.Column('actor_user_id', sa.BigInteger(), nullable=True),
        sa.Column('actor_label', sa.Text(), nullable=False, server_default=''),
        sa.Column('actor_role', sa.String(length=16), nullable=True),
        sa.Column('escalated', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('note', sa.Text(), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        # CASCADE from the case (the events belong to it), but SET NULL from
        # the user: deleting an account must not delete the record of what
        # that account did. `actor_label` keeps the name either way.
        sa.ForeignKeyConstraint(['case_id'], ['exit_cases.case_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_case_stage_events_case_id'), 'case_stage_events', ['case_id'])
    op.create_index(op.f('ix_case_stage_events_stage_key'), 'case_stage_events', ['stage_key'])
    op.create_index(op.f('ix_case_stage_events_actor_user_id'), 'case_stage_events', ['actor_user_id'])
    op.create_index(op.f('ix_case_stage_events_created_at'), 'case_stage_events', ['created_at'])
    op.create_index('ix_case_stage_event_case_time', 'case_stage_events', ['case_id', 'created_at'])


def downgrade() -> None:
    """Downgrade schema.

    Drops the audit trail. On a database that has recorded real transitions
    this destroys compliance evidence, which is not recoverable from anywhere
    else -- `CaseStage.status` holds only the current value, not the history.
    """
    op.drop_index('ix_case_stage_event_case_time', table_name='case_stage_events')
    op.drop_index(op.f('ix_case_stage_events_created_at'), table_name='case_stage_events')
    op.drop_index(op.f('ix_case_stage_events_actor_user_id'), table_name='case_stage_events')
    op.drop_index(op.f('ix_case_stage_events_stage_key'), table_name='case_stage_events')
    op.drop_index(op.f('ix_case_stage_events_case_id'), table_name='case_stage_events')
    op.drop_table('case_stage_events')
