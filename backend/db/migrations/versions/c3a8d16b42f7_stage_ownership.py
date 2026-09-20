"""stage ownership, ageing and blocking

Adds the columns that let a clearance stage be routed to a role and a person,
aged for real, and marked stalled.

Two deliberate restraints:

*`stage_key` is backfilled but left NULLABLE.* The backfill is a pure function
of `name`, a column in the same table, so it is legitimate in-migration data
movement -- deterministic, idempotent, and reversed by dropping the column.
But nothing constrains `CaseStage.name` (the seeder writes whatever the corpus
says), so an unexpected name maps to NULL. `SET NOT NULL` would abort the whole
upgrade on one such row; a NULL `stage_key` merely means "only HR can act on
this stage", which is a safe default.

*`owner_user_id` is NOT backfilled here.* Linking a stage's free-text `owner`
to an account requires accounts that `scripts/create_users.py` creates *after*
seeding. A backfill depending on that would make `alembic upgrade head` a lie:
on a fresh clone or in CI it runs first against empty tables, links nothing,
and never runs again -- leaving every install except the original developer's
with permanently unassigned queues and no error. Alembic's contract is that
revision N is a pure function of revision N-1, and a step whose output depends
on whether someone ran a demo-data script breaks it invisibly. Linking lives in
`scripts/link_stage_owners.py`, which is re-runnable.

Revision ID: c3a8d16b42f7
Revises: b2f4c7a91e05
Create Date: 2026-09-20 21:40:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c3a8d16b42f7'
down_revision: Union[str, Sequence[str], None] = 'b2f4c7a91e05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: `CaseStage.name` as authored in the corpus -> stage_key. Mirrors
#: `backend.db.models.STAGE_KEY_BY_NAME`, spelled out rather than imported:
#: a migration describes the database at a point in time, and must not change
#: meaning when the application constant is later edited.
_STAGE_KEYS = {
    'IT Clearance': 'it',
    'Finance Clearance': 'finance',
    'HR Clearance': 'hr',
    'Knowledge Transfer': 'kt',
    'Manager Sign-off': 'manager',
}


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('case_stages', sa.Column('stage_key', sa.String(length=32), nullable=True))
    op.add_column('case_stages', sa.Column('owner_user_id', sa.BigInteger(), nullable=True))
    op.add_column('case_stages', sa.Column('status_changed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('case_stages', sa.Column('blocked_reason', sa.Text(), nullable=True))
    op.add_column('case_stages', sa.Column('blocked_at', sa.DateTime(timezone=True), nullable=True))

    bind = op.get_bind()
    for name, key in _STAGE_KEYS.items():
        bind.execute(
            sa.text("update case_stages set stage_key = :key where name = :name"),
            {"key": key, "name": name},
        )

    unmapped = bind.execute(sa.text(
        "select count(*) from case_stages where stage_key is null"
    )).scalar_one()
    if unmapped:
        # Not fatal -- NULL is a valid state -- but silence here would be the
        # kind of thing someone discovers months later via an empty queue.
        print(f"  [stage_ownership] {unmapped} stage row(s) have a name outside the "
              f"canonical five; stage_key left NULL (HR-only until renamed).")

    op.create_foreign_key(
        'fk_case_stage_owner_user', 'case_stages', 'users',
        ['owner_user_id'], ['id'], ondelete='SET NULL',
    )
    op.create_index(op.f('ix_case_stages_stage_key'), 'case_stages', ['stage_key'], unique=False)
    op.create_index(op.f('ix_case_stages_owner_user_id'), 'case_stages', ['owner_user_id'], unique=False)
    op.create_index('ix_case_stage_key_status', 'case_stages', ['stage_key', 'status'], unique=False)

    op.create_check_constraint(
        'ck_case_stage_key', 'case_stages',
        "stage_key is null or stage_key in ('it','finance','hr','kt','manager')",
    )
    # There was no constraint on `status` at all. Adding the transition API
    # without one would let a typo'd `to` value write a status that every
    # agent silently reads as "not completed" forever.
    op.create_check_constraint(
        'ck_case_stage_status', 'case_stages',
        "status in ('pending','in_progress','completed')",
    )


def downgrade() -> None:
    """Downgrade schema.

    Drops the columns outright. Note this discards any owner assignment made
    through the UI -- re-upgrading restores the schema but not the data, and
    `link_stage_owners` can only re-derive what the free-text `owner` column
    still says. That is inherent to dropping a column, not a defect here, but
    it is worth knowing before running this against a live database.
    """
    op.drop_constraint('ck_case_stage_status', 'case_stages', type_='check')
    op.drop_constraint('ck_case_stage_key', 'case_stages', type_='check')
    op.drop_index('ix_case_stage_key_status', table_name='case_stages')
    op.drop_index(op.f('ix_case_stages_owner_user_id'), table_name='case_stages')
    op.drop_index(op.f('ix_case_stages_stage_key'), table_name='case_stages')
    op.drop_constraint('fk_case_stage_owner_user', 'case_stages', type_='foreignkey')
    op.drop_column('case_stages', 'blocked_at')
    op.drop_column('case_stages', 'blocked_reason')
    op.drop_column('case_stages', 'status_changed_at')
    op.drop_column('case_stages', 'owner_user_id')
    op.drop_column('case_stages', 'stage_key')
