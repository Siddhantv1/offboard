"""Alembic environment.

The database URL comes from `backend.config`, never from alembic.ini, so there
is exactly one place it is defined and no credential is ever committed.
"""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from backend.config import DATABASE_URL
from backend.db.models import Base, DateAwareJSONB


def render_item(type_: str, obj: object, autogen_context: object) -> str | bool:
    """Render `DateAwareJSONB` as plain `postgresql.JSONB` in migrations.

    Autogenerate otherwise emits `backend.db.models.DateAwareJSONB(...)`, which
    fails immediately (it never adds the import) and is wrong in principle: a
    migration describes the database, and the DDL for that column is JSONB.
    The TypeDecorator only governs Python-side encoding, so baking the
    application class into a migration would break every historical revision
    the day the class is renamed or moved.
    """
    if type_ == "type" and isinstance(obj, DateAwareJSONB):
        autogen_context.imports.add("from sqlalchemy.dialects import postgresql")
        return "postgresql.JSONB(astext_type=sa.Text())"
    return False  # fall back to Alembic's default rendering


config = context.config
config.set_main_option("sqlalchemy.url", DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting. `alembic upgrade head --sql`."""
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Without these, autogenerate misses column type changes and
            # server-default changes entirely and reports a clean diff.
            compare_type=True,
            compare_server_default=True,
            render_item=render_item,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
