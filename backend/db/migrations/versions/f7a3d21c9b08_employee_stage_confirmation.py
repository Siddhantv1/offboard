"""Employee-side confirmation on a clearance stage.

IT Clearance used to be driven by three document uploads -- an asset return
checklist, a device handover acknowledgement and a VPN/MFA deactivation
confirmation. None of them is a document anybody actually has: the employee
hands back a laptop at a desk, and IT sees it in their own asset system. So
the paperwork was theatre, and the stage moved on whether or not the hardware
came back.

It is now a two-sided mark. The employee says "I have done my part" (these
columns), and IT verifies independently by completing the stage. Only IT's
verification makes it Done; the employee's word alone never closes a clearance.

Deliberately NOT added to `CaseStage.to_meta()` -- that shape is pinned by
`tests/golden/store_snapshot.json` and read by nineteen agents.

Revision ID: f7a3d21c9b08
Revises: e5c02fa87b14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f7a3d21c9b08"
down_revision = "e5c02fa87b14"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "case_stages",
        sa.Column("employee_confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "case_stages",
        sa.Column("employee_confirmed_note", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("case_stages", "employee_confirmed_note")
    op.drop_column("case_stages", "employee_confirmed_at")
