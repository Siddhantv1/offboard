"""Look at the database without leaving the repo.

    python -m scripts.db                       # every table, with row counts
    python -m scripts.db users                 # one table, most recent first
    python -m scripts.db --schema              # every table's columns
    python -m scripts.db --schema case_stages  # one table's columns, keys, indexes
    python -m scripts.db "select ..."          # any read-only query
    python -m scripts.db --dsn                 # the psql connection string

Exists because `psql` is not installed on every machine that runs this project,
and the connection string lives in `.env` where it is easy to mistype. The
driver is already a dependency, so this needs nothing new.

**Reads only.** A statement that is not a SELECT (or WITH ... SELECT) is
refused. Nothing here should be the way anyone changes data: migrations own the
schema and the application owns the rows, and a convenience tool that can also
UPDATE is a convenience tool somebody eventually updates with.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

from sqlalchemy import inspect, text

from backend.config import DATABASE_URL
from backend.db import engine, session_scope
from backend.db.models import Base

#: Tables worth knowing about first, and what each one is for. The rest are
#: listed too; these are the ones that answer most questions.
GUIDE = {
    "employees": "everyone on the payroll, exiting or not",
    "exit_cases": "one per exit; `current_stage` is authored, not derived",
    "case_stages": "the five clearances per case; owner_user_id is what queues route on",
    "case_stage_events": "the audit trail -- transitions, assignments, chases, confirmations",
    "asset_returns": "which company assets actually came back, and who logged it",
    "deprovisioning_plans": "drafted / authorised / executed access revocation",
    "deprovisioning_actions": "what was actually revoked, when, on whose authority",
    "exit_interview_responses": "interviews leavers filled in here (interviews = old corpus)",
    "case_documents": "uploaded paperwork, with the review verdict",
    "agent_runs": "every agent invocation and its result -- useful when a view looks wrong",
    "users": "logins and roles; employees is the HR record, this is the account",
}

_READ_ONLY = re.compile(r"^\s*(select|with|explain|show|table)\b", re.IGNORECASE)


def _render(rows, keys) -> None:
    if not rows:
        print("  (no rows)")
        return
    widths = [max(len(str(k)), *(len(str(r[i])) for r in rows)) for i, k in enumerate(keys)]
    widths = [min(w, 48) for w in widths]
    print("  " + " | ".join(str(k).ljust(w)[:w] for k, w in zip(keys, widths)))
    print("  " + "-+-".join("-" * w for w in widths))
    for r in rows:
        print("  " + " | ".join(str(v).replace("\n", " ").ljust(w)[:w]
                                for v, w in zip(r, widths)))


def overview() -> None:
    print(f"\n  {DATABASE_URL}\n")
    with session_scope() as s:
        rows = []
        for name in sorted(Base.metadata.tables):
            n = s.execute(text(f'select count(*) from "{name}"')).scalar()
            rows.append((name, n, GUIDE.get(name, "")))
        _render(rows, ("table", "rows", "what it holds"))
    print("\n  python -m scripts.db <table>        one table")
    print('  python -m scripts.db "select ..."   any read-only query\n')


def dump(table: str, limit: int) -> None:
    if table not in Base.metadata.tables:
        print(f"  no such table: {table}")
        print(f"  try: {', '.join(sorted(Base.metadata.tables))}")
        raise SystemExit(1)
    # Newest first where the table says what "new" means.
    cols = Base.metadata.tables[table].columns
    order = next((c for c in ("created_at", "logged_at", "started_at", "executed_at",
                              "uploaded_at", "id") if c in cols), None)
    sql = f'select * from "{table}"'
    if order:
        sql += f' order by "{order}" desc'
    sql += f" limit {int(limit)}"
    query(sql)


def schema(table: str | None) -> None:
    """Columns, keys and indexes, read from the live database.

    Reflected rather than printed out of `Base.metadata`, and the difference
    matters: the models are what the code believes, the database is what is
    actually there. A migration that was written but never run, or run against
    one database and not another, shows up here as a disagreement instead of
    being invisible -- which is the main reason anyone opens a schema viewer.

    The checkpoint tables are LangGraph's own and are not in `Base.metadata`,
    so they only appear here.
    """
    inspector = inspect(engine)
    names = sorted(inspector.get_table_names())
    if table:
        if table not in names:
            print(f"  no such table: {table}")
            print(f"  try: {', '.join(names)}")
            raise SystemExit(1)
        names = [table]

    print(f"\n  {DATABASE_URL}\n")
    for name in names:
        unmodelled = "" if name in Base.metadata.tables else "   (not a model)"
        print(f"  {name}{unmodelled}")
        if GUIDE.get(name):
            print(f"    {GUIDE[name]}")

        pk = set(inspector.get_pk_constraint(name).get("constrained_columns") or [])
        fks = {c: f'-> {fk["referred_table"]}.{(fk["referred_columns"] or [""])[0]}'
               for fk in inspector.get_foreign_keys(name)
               for c in fk.get("constrained_columns") or []}
        rows = []
        for col in inspector.get_columns(name):
            default = col.get("default")
            rows.append((
                ("* " if col["name"] in pk else "  ") + col["name"],
                str(col["type"]),
                "" if col["nullable"] else "not null",
                fks.get(col["name"], ""),
                "" if default is None else str(default),
            ))
        _render(rows, ("column (* = pk)", "type", "", "references", "default"))

        # Only for a single table: across nineteen of them this is a wall.
        if table:
            for index in inspector.get_indexes(name):
                unique = "unique " if index.get("unique") else ""
                print(f"    {unique}index {index['name']}: "
                      f"{', '.join(index.get('column_names') or [])}")
        print()


def query(sql: str) -> None:
    if not _READ_ONLY.match(sql):
        print("  Reads only. Use a migration to change the schema, and the app "
              "to change rows.")
        raise SystemExit(2)
    with session_scope() as s:
        result = s.execute(text(sql))
        _render(list(result.fetchall()), result.keys())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("what", nargs="?", help="a table name, or a SELECT statement")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--dsn", action="store_true",
                    help="print a psql-style URL and exit")
    ap.add_argument("--schema", action="store_true",
                    help="show columns and keys instead of rows")
    args = ap.parse_args()

    if args.schema:
        schema(args.what)
        return 0
    if args.dsn:
        # SQLAlchemy's driver prefix is not something psql understands.
        print(DATABASE_URL.replace("postgresql+psycopg://", "postgresql://"))
        return 0
    if not args.what:
        overview()
    elif args.what in Base.metadata.tables:
        dump(args.what, args.limit)
    else:
        query(args.what)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
