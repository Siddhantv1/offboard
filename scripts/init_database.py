"""Create the application role and databases on a fresh PostgreSQL install.

Run once after installing the server, before `alembic upgrade head`:

    python -m scripts.init_database

Idempotent -- safe to re-run. It reads the target role, password and database
names out of `DATABASE_URL` / `TEST_DATABASE_URL` so there is exactly one place
those are defined, and connects as a superuser only for the few statements that
genuinely require it.

The application role is deliberately NOT a superuser. It owns its two databases
and nothing else, so a SQL injection or a stray migration cannot reach the rest
of the cluster.

Superuser credentials come from the environment and are never written to disk:

    PGSUPERUSER      (default: postgres)
    PGSUPERPASSWORD  (prompted if unset)
"""
from __future__ import annotations

import getpass
import os
import sys

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

# Importing backend.config loads .env, so DATABASE_URL is picked up the same
# way the application picks it up -- no second source of truth.
from backend.config import DATABASE_URL, TEST_DATABASE_URL


def _superuser_conninfo(host: str, port: int) -> str:
    user = os.getenv("PGSUPERUSER", "postgres")
    password = os.getenv("PGSUPERPASSWORD")
    if password is None:
        password = getpass.getpass(f"PostgreSQL password for superuser {user!r}: ")
    # Connect to the maintenance database: you cannot create a database while
    # connected to the one you are creating.
    return f"host={host} port={port} dbname=postgres user={user} password={password}"


def _ensure_role(cur, role: str, password: str) -> str:
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
    if cur.fetchone():
        # Re-assert the password so a rotated DATABASE_URL takes effect without
        # anyone having to remember this step.
        cur.execute(
            sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )
        return f"role {role!r} already existed (password re-asserted)"
    cur.execute(
        sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD {}").format(
            sql.Identifier(role), sql.Literal(password)
        )
    )
    return f"created role {role!r}"


def _ensure_database(cur, name: str, owner: str) -> str:
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
    if cur.fetchone():
        return f"database {name!r} already existed"
    cur.execute(
        sql.SQL("CREATE DATABASE {} OWNER {} ENCODING 'UTF8'").format(
            sql.Identifier(name), sql.Identifier(owner)
        )
    )
    return f"created database {name!r} owned by {owner!r}"


def main() -> int:
    url = make_url(DATABASE_URL)
    test_url = make_url(TEST_DATABASE_URL)

    if not url.username or not url.password:
        print(
            "DATABASE_URL must include a username and password.\n"
            f"  got: {url.render_as_string(hide_password=True)}",
            file=sys.stderr,
        )
        return 1
    if url.database == test_url.database:
        print(
            "DATABASE_URL and TEST_DATABASE_URL point at the same database. "
            "The test suite truncates tables; it must not share with development data.",
            file=sys.stderr,
        )
        return 1

    host, port = url.host or "localhost", url.port or 5432
    try:
        conn = psycopg.connect(_superuser_conninfo(host, port), autocommit=True)
    except psycopg.OperationalError as exc:
        print(
            f"Could not connect to PostgreSQL at {host}:{port} as superuser.\n"
            f"  {exc}\n"
            "Is the service running?  Get-Service postgresql*",
            file=sys.stderr,
        )
        return 1

    # autocommit: CREATE DATABASE cannot run inside a transaction block.
    with conn, conn.cursor() as cur:
        print(" ", _ensure_role(cur, url.username, url.password))
        for db in (url.database, test_url.database):
            print(" ", _ensure_database(cur, db, url.username))

    # Verify the application role can actually log in. Creating the role and
    # then failing to connect as it is a common and confusing half-success.
    for target in (url, test_url):
        try:
            psycopg.connect(
                f"host={host} port={port} dbname={target.database} "
                f"user={target.username} password={target.password}"
            ).close()
        except psycopg.OperationalError as exc:
            print(f"\nRole cannot connect to {target.database!r}:\n  {exc}", file=sys.stderr)
            return 1
        print(f"  verified login to {target.database!r}")

    print("\nReady. Next:  alembic upgrade head")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
