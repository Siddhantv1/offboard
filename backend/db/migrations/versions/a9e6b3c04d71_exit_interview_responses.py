"""The employee's own exit interview answers.

`interviews` is reference data -- transcripts of interviews that already
happened, which the summariser and trends agents read. Nothing let the person
leaving answer anything. This adds the table they write into.

One row per case holding a JSON map of question id to answer, rather than one
row per question. The reasoning is in the `backend/exit_interview` module
docstring; the short version is that submission has to be atomic, no reader
ever wants a single answer on its own, and the question set is versioned in
code so a retired question must not orphan rows.

`case_id` is UNIQUE, not merely indexed: one interview per exit. Without it a
double submit from two tabs writes two rows and the second is invisible behind
the first for ever.

Revision ID: a9e6b3c04d71
Revises: f7a3d21c9b08
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a9e6b3c04d71"
down_revision = "f7a3d21c9b08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "exit_interview_responses",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("case_id", sa.String(length=32), nullable=False),
        sa.Column("employee_id", sa.String(length=32), nullable=True),
        sa.Column("answers", postgresql.JSONB(astext_type=sa.Text()),
                  server_default=sa.text("'{}'::jsonb"), nullable=False),
        # NULL is a draft the employee may still edit; a timestamp closes it.
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["exit_cases.case_id"],
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", name="uq_exit_interview_case"),
    )
    op.create_index("ix_exit_interview_responses_employee_id",
                    "exit_interview_responses", ["employee_id"])


def downgrade() -> None:
    op.drop_index("ix_exit_interview_responses_employee_id",
                  table_name="exit_interview_responses")
    op.drop_table("exit_interview_responses")
