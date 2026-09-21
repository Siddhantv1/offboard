"""What kind of thing a case_stage_event records.

The table was built for status changes and has since been asked to carry three
other things -- an SLA chase, an employee confirming their side, and now an
assignment. None of those moves a stage, so each was written with
`from_status == to_status`, and the timeline rendered them as "pending →
pending". Worse, the only way to tell them apart was to sniff: `escalated` for
a chase, `actor_role == 'employee'` plus a note prefix for a confirmation.
Adding assignment as a fifth would have meant a fourth heuristic.

`kind` makes it explicit. The backfill is not a guess -- it inverts exactly how
each kind is constructed today:

    from <> to                      -> transition   (the only thing that moves a stage)
    from == to and escalated        -> escalation
    from == to and not escalated    -> confirmation

Revision ID: b4c7e9a21f36
Revises: a9e6b3c04d71
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b4c7e9a21f36"
down_revision = "a9e6b3c04d71"
branch_labels = None
depends_on = None

KINDS = ("transition", "assignment", "escalation", "confirmation")


def upgrade() -> None:
    # Nullable first, backfill, then NOT NULL: a single NOT NULL add would
    # fail on every existing row.
    op.add_column(
        "case_stage_events",
        sa.Column("kind", sa.String(length=16), nullable=True),
    )
    op.execute(
        """
        UPDATE case_stage_events SET kind = CASE
            WHEN from_status IS DISTINCT FROM to_status THEN 'transition'
            WHEN escalated THEN 'escalation'
            ELSE 'confirmation'
        END
        """
    )
    op.alter_column("case_stage_events", "kind", nullable=False,
                    server_default="transition")
    op.create_check_constraint(
        "ck_case_stage_event_kind",
        "case_stage_events",
        "kind in ('transition','assignment','escalation','confirmation')",
    )
    # The case page reads one case's timeline in order; the existing index is
    # on (case_id, created_at) and still serves that. This one serves "show me
    # every assignment" without scanning, which is the question an auditor
    # asks and the reason this column exists.
    op.create_index("ix_case_stage_events_kind", "case_stage_events", ["kind"])


def downgrade() -> None:
    op.drop_index("ix_case_stage_events_kind", table_name="case_stage_events")
    op.drop_constraint("ck_case_stage_event_kind", "case_stage_events",
                       type_="check")
    op.drop_column("case_stage_events", "kind")
