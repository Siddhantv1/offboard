"""Assembles every sub-router into one `api_router`.

Import order here is irrelevant to matching -- all these routes carry explicit,
non-overlapping prefixes. What *does* matter is that `backend/main.py` includes
this before declaring the SPA catch-all, which would otherwise shadow the lot.
"""
from __future__ import annotations

from fastapi import APIRouter

from . import (admin_routes, agents_routes, auth_routes, cases_routes,
               health_routes, orchestration_routes)

api_router = APIRouter()
api_router.include_router(auth_routes.router)
api_router.include_router(health_routes.router)
api_router.include_router(agents_routes.router)
api_router.include_router(cases_routes.router)
api_router.include_router(orchestration_routes.router)
api_router.include_router(admin_routes.router)
