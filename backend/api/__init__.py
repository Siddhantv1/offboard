"""HTTP layer.

`backend/main.py` assembles the app from the routers in this package; the
modules here are leaves. They import from `agents`, `orchestration`,
`data_store`, `policy` and `auth`, and nothing in those packages imports back --
which is what keeps the existing
`agents -> end_to_end_automation -> orchestration -> graph -> agents` cycle from
spreading into the HTTP layer. Do not let a service module import a router.
"""
