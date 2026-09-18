"""deprovisioning plans

Revision ID: 7c1a9f2e3b6d
Revises: 489b435ad300
Create Date: 2026-09-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '7c1a9f2e3b6d'
down_revision: Union[str, Sequence[str], None] = '489b435ad300'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('deprovisioning_plans',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('employee_id', sa.String(length=32), nullable=False),
    sa.Column('case_id', sa.String(length=32), nullable=True),
    sa.Column('plan', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False, server_default='pending'),
    sa.Column('approved_by', sa.Text(), nullable=True),
    sa.Column('note', sa.Text(), nullable=False, server_default=''),
    sa.Column('execution', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('executed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status in ('pending', 'executed')", name='ck_deprov_plan_status'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_deprovisioning_plans_employee_id'), 'deprovisioning_plans', ['employee_id'], unique=False)
    op.create_index(op.f('ix_deprovisioning_plans_case_id'), 'deprovisioning_plans', ['case_id'], unique=False)
    op.create_index(op.f('ix_deprovisioning_plans_status'), 'deprovisioning_plans', ['status'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_deprovisioning_plans_status'), table_name='deprovisioning_plans')
    op.drop_index(op.f('ix_deprovisioning_plans_case_id'), table_name='deprovisioning_plans')
    op.drop_index(op.f('ix_deprovisioning_plans_employee_id'), table_name='deprovisioning_plans')
    op.drop_table('deprovisioning_plans')
