"""separate deprovisioning approval from execution

Adds `approved`, and records approver and executor separately.

Why: opening the execute route to IT (revoking access is IT's job) is only safe
if executing is not also approving. Before this, `execute_plan` flipped
pending->executed in one step and stamped `approved_by` with whoever called it.
An IT account could therefore draft a revocation plan against any employee --
including an active non-leaver, since drafting only checks the employee exists
-- execute it, and have the compliance record name themselves as the approver.
The LangGraph human-approval gate would still exist, but the direct route
carried its whole payload without it.

Revision ID: e5c02fa87b14
Revises: d4b91c7e5a83
Create Date: 2026-09-20 22:35:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e5c02fa87b14'
down_revision: Union[str, Sequence[str], None] = 'd4b91c7e5a83'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('deprovisioning_plans',
                  sa.Column('approved_by_user_id', sa.BigInteger(), nullable=True))
    op.add_column('deprovisioning_plans',
                  sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('deprovisioning_plans',
                  sa.Column('executed_by', sa.Text(), nullable=True))
    op.add_column('deprovisioning_plans',
                  sa.Column('executed_by_user_id', sa.BigInteger(), nullable=True))

    op.create_foreign_key('fk_deprov_plan_approver', 'deprovisioning_plans', 'users',
                          ['approved_by_user_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('fk_deprov_plan_executor', 'deprovisioning_plans', 'users',
                          ['executed_by_user_id'], ['id'], ondelete='SET NULL')

    # Already-executed rows keep their history: whoever was recorded as
    # approving also executed, because the old code could not tell them apart.
    # Copying the name across makes that explicit rather than leaving a NULL
    # that reads as "nobody ran it".
    op.execute(sa.text(
        "update deprovisioning_plans set executed_by = approved_by "
        "where status = 'executed' and executed_by is null"
    ))

    op.drop_constraint('ck_deprov_plan_status', 'deprovisioning_plans', type_='check')
    op.create_check_constraint(
        'ck_deprov_plan_status', 'deprovisioning_plans',
        "status in ('pending', 'approved', 'executed')",
    )
    # The separation of duties, enforced in the database as well as in Python.
    # NULLs pass: a historical row has no approver id to compare.
    op.create_check_constraint(
        'ck_deprov_plan_separate_actors', 'deprovisioning_plans',
        "approved_by_user_id is null or executed_by_user_id is null "
        "or approved_by_user_id <> executed_by_user_id",
    )


def downgrade() -> None:
    """Downgrade schema.

    Aborts if any plan sits in the new `approved` state, which the narrower
    constraint cannot represent. Collapsing those to `executed` would claim
    revocations happened that did not; collapsing them to `pending` would
    discard a recorded human approval. Both are lies, so the operator decides.
    """
    bind = op.get_bind()
    stuck = bind.execute(sa.text(
        "select count(*) from deprovisioning_plans where status = 'approved'"
    )).scalar_one()
    if stuck:
        raise RuntimeError(
            f"Cannot downgrade: {stuck} deprovisioning plan(s) are approved but not "
            "yet executed, and the older schema has no state for that.\n"
            "Execute or delete them first -- this migration will not guess which."
        )

    op.drop_constraint('ck_deprov_plan_separate_actors', 'deprovisioning_plans', type_='check')
    op.drop_constraint('ck_deprov_plan_status', 'deprovisioning_plans', type_='check')
    op.create_check_constraint(
        'ck_deprov_plan_status', 'deprovisioning_plans',
        "status in ('pending', 'executed')",
    )
    op.drop_constraint('fk_deprov_plan_executor', 'deprovisioning_plans', type_='foreignkey')
    op.drop_constraint('fk_deprov_plan_approver', 'deprovisioning_plans', type_='foreignkey')
    op.drop_column('deprovisioning_plans', 'executed_by_user_id')
    op.drop_column('deprovisioning_plans', 'executed_by')
    op.drop_column('deprovisioning_plans', 'approved_at')
    op.drop_column('deprovisioning_plans', 'approved_by_user_id')
