"""Durability of paused human-approval runs.

With MemorySaver the checkpoint lived in the process and the thread_id lived
only in the browser tab that started the run, so a restart stranded it: resume
returned 409 and every parallel agent's work was lost. There was also no way to
ask what was waiting for a human.

These tests do not run the orchestrator -- that would cost a dozen gateway
calls. They verify the storage substrate and the enumeration contract.
"""
from __future__ import annotations

from langgraph.checkpoint.postgres import PostgresSaver

from backend.orchestration import graph as graph_module


def test_the_checkpointer_is_durable():
    assert isinstance(graph_module.get_checkpointer(), PostgresSaver), (
        "MemorySaver loses every paused approval run on restart"
    )


def test_checkpoint_tables_exist_in_postgres():
    """`setup()` is what creates them; if it were never called, the first
    paused run would fail at the moment it tried to persist."""
    from sqlalchemy import text

    from backend.db import engine

    with engine.connect() as conn:
        names = set(conn.execute(text(
            "select table_name from information_schema.tables "
            "where table_schema = 'public'"
        )).scalars())
    assert "checkpoints" in names, f"no checkpoint tables; found {sorted(names)}"


def test_pending_approvals_returns_a_well_formed_list():
    """An empty list is a valid answer. What matters is that the call works at
    all -- enumerating paused runs was impossible with the in-memory saver."""
    approvals = graph_module.pending_approvals()
    assert isinstance(approvals, list)
    for item in approvals:
        assert "thread_id" in item
        assert isinstance(item["thread_id"], str)


def test_pending_approvals_does_not_deadlock_with_checkpoints_present():
    """Regression: PostgresSaver serialises every operation behind one
    non-reentrant lock, and `list()` holds it while yielding. Inspecting each
    thread inside that loop deadlocks the process against itself -- and the
    empty-table case passes vacuously, so this test writes a checkpoint first.

    Fails by hanging rather than by asserting, so it is wrapped in a watchdog.
    """
    import threading

    from langgraph.checkpoint.base import empty_checkpoint

    thread_id = "deadlock-probe"
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    saver = graph_module.get_checkpointer()
    saver.put(config, empty_checkpoint(), {"source": "test", "step": -1}, {})

    result: list[object] = []
    worker = threading.Thread(
        target=lambda: result.append(graph_module.pending_approvals()),
        daemon=True,
    )
    try:
        worker.start()
        worker.join(timeout=30)
        assert not worker.is_alive(), (
            "pending_approvals() deadlocked while iterating checkpoints"
        )
        assert isinstance(result[0], list)
    finally:
        _purge_thread(thread_id)


def _purge_thread(thread_id: str) -> None:
    from sqlalchemy import text

    from backend.db import engine

    with engine.begin() as conn:
        for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            conn.execute(text(f"delete from {table} where thread_id = :t"),
                         {"t": thread_id})


def test_pending_approval_on_an_unknown_thread_is_none():
    """The resume route calls this with a client-supplied thread_id. An
    unknown one must produce a clean None, not an exception."""
    assert graph_module.pending_approval("no-such-thread-id") is None


def test_checkpoints_survive_a_new_saver_instance():
    """The real failure mode, in miniature: state written by one saver object
    must be visible to a freshly constructed one. Under MemorySaver each
    instance had its own dict."""
    from langgraph.checkpoint.base import empty_checkpoint

    from backend.config import PG_URI

    thread_id = "durability-probe"
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    checkpoint = empty_checkpoint()

    saver = graph_module.get_checkpointer()
    saver.put(config, checkpoint, {"source": "test", "step": -1}, {})

    from psycopg_pool import ConnectionPool
    with ConnectionPool(conninfo=PG_URI, min_size=1, max_size=2, open=True,
                        kwargs={"autocommit": True, "prepare_threshold": 0}) as pool:
        other = PostgresSaver(pool)
        assert other.get_tuple(config) is not None, (
            "a checkpoint written by one saver was invisible to another"
        )

    with ConnectionPool(conninfo=PG_URI, min_size=1, max_size=2, open=True,
                        kwargs={"autocommit": True}) as pool:
        with pool.connection() as conn:
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                conn.execute(
                    f"delete from {table} where thread_id = %s", (thread_id,))
