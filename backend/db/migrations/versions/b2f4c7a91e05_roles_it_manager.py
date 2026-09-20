"""widen user roles to hr/it/manager/employee

Pure DDL: drop and recreate `ck_user_role` with the two new values. No data
moves, because no existing row changes meaning -- `hr` and `employee` keep
exactly the scope they had.

Revision ID: b2f4c7a91e05
Revises: 7c1a9f2e3b6d
Create Date: 2026-09-20 21:10:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b2f4c7a91e05'
down_revision: Union[str, Sequence[str], None] = '7c1a9f2e3b6d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint('ck_user_role', 'users', type_='check')
    op.create_check_constraint(
        'ck_user_role', 'users', "role in ('hr','it','manager','employee')"
    )


def downgrade() -> None:
    """Downgrade schema.

    Aborts if any `it` or `manager` account exists, rather than reassigning it.

    The obvious way to make this "just work" is
    `UPDATE users SET role='hr' WHERE role IN ('it','manager')`. That would
    silently promote every IT engineer and every line manager to full HR --
    which is the blanket override over every case, every agent and the
    orchestrator -- and leave them there, because nothing demotes them on the
    way back up. A downgrade that grants privilege while appearing to succeed
    is far worse than one that refuses, so this refuses and names the count.
    """
    bind = op.get_bind()
    stuck = bind.execute(
        sa.text("select role, count(*) from users "
                "where role in ('it','manager') group by role order by role")
    ).all()
    if stuck:
        detail = ", ".join(f"{n} {role}" for role, n in stuck)
        raise RuntimeError(
            f"Cannot downgrade: {detail} account(s) exist, and the narrower "
            "constraint has no value for them.\n"
            "Reassign or deactivate them deliberately first, e.g.\n"
            "  UPDATE users SET is_active = false WHERE role IN ('it','manager');\n"
            "  DELETE FROM users WHERE role IN ('it','manager');\n"
            "This migration will not silently promote them to 'hr'."
        )
    op.drop_constraint('ck_user_role', 'users', type_='check')
    op.create_check_constraint('ck_user_role', 'users', "role in ('hr','employee')")
