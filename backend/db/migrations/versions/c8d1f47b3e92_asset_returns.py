"""A record of which company assets actually came back.

There was none. `systems.asset_status` decided an asset was returned from the
IT Clearance stage's status -- completed meant everything was back, in progress
meant the first item in the list was back and the rest were not. So the list an
engineer read to decide whether to sign off was computed from the sign-off. The
MacBook on case-001 showed as returned because it happened to be first in the
array.

Nothing in the product could record "the YubiKey was handed in at reception on
Tuesday", which is the only fact that matters and the only one IT is actually
in a position to assert.

`state` has three values because two is not enough. An asset that is never
coming back -- lost, kept, broken -- is written off and charged to the final
settlement; calling that "outstanding" forever would block every clearance
behind one missing dongle.

The backfill is honest about itself: where IT Clearance is already completed,
somebody accepted those assets as returned, and the row says it was imported
rather than logged by a person. On a fresh database it backfills nothing, which
is correct -- no assets have been returned yet.

Revision ID: c8d1f47b3e92
Revises: b4c7e9a21f36
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c8d1f47b3e92"
down_revision = "b4c7e9a21f36"
branch_labels = None
depends_on = None

STATES = ("returned", "outstanding", "written_off")


def upgrade() -> None:
    op.create_table(
        "asset_returns",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("employee_id", sa.String(length=32), nullable=False),
        sa.Column("case_id", sa.String(length=32), nullable=True),
        sa.Column("asset", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False,
                  server_default="outstanding"),
        # Denormalised beside the FK, as everywhere else in this schema: the
        # compliance record must still name who took delivery after that
        # account is gone.
        sa.Column("logged_by", sa.Text(), nullable=True),
        sa.Column("logged_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("logged_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["logged_by_user_id"], ["users.id"],
                                ondelete="SET NULL"),
        sa.CheckConstraint(
            "state in ('returned','outstanding','written_off')",
            name="ck_asset_return_state"),
        # One row per asset per person: this is the current position, not a log
        # of handling attempts. Changing your mind updates the row, and the
        # timestamp says when it was last decided.
        sa.UniqueConstraint("employee_id", "asset", name="uq_asset_return_item"),
    )
    op.create_index("ix_asset_returns_employee_id", "asset_returns", ["employee_id"])
    op.create_index("ix_asset_returns_case_id", "asset_returns", ["case_id"])

    op.execute(
        """
        INSERT INTO asset_returns (employee_id, case_id, asset, state, logged_by, note)
        SELECT e.id, c.case_id, a.asset, 'returned',
               'Imported', 'Recorded when IT Clearance was completed, before '
                           'assets were tracked individually.'
        FROM exit_cases c
        JOIN employees e ON e.id = c.employee_id
        JOIN case_stages s ON s.case_id = c.case_id AND s.stage_key = 'it'
        CROSS JOIN LATERAL unnest(coalesce(e.assets_assigned, '{}')) AS a(asset)
        WHERE s.status = 'completed'
        ON CONFLICT (employee_id, asset) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_asset_returns_case_id", table_name="asset_returns")
    op.drop_index("ix_asset_returns_employee_id", table_name="asset_returns")
    op.drop_table("asset_returns")
