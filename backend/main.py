"""FastAPI application.

Assembly only. Every route lives in `backend/api/` -- see `api/router.py` for
the map. This file owns the three things the routers cannot: the app object,
the startup hook, and the static frontend mount.

The API surface, by router:

  api/auth_routes.py           /api/auth/*       sign in/out, who am I, password
  api/health_routes.py         /api/health       gateway + config sanity
  api/agents_routes.py         /api/agents/*     catalogue, run, upload, execute
  api/cases_routes.py          /api/cases/*, /api/me/case, /api/queue,
                               /api/employees, documents, transitions, timeline
  api/orchestration_routes.py  /api/orchestrate/*, /api/approvals,
                               /api/deprovisioning/{id}/approve
  api/admin_routes.py          /api/admin/users/*  HR user administration

Ordering constraint: the SPA catch-all at the bottom matches *everything*, so
it must stay the last route registered. The `include_router` call above it is
what keeps the API reachable at all.
"""
from __future__ import annotations

import secrets
import threading

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import auth, ocr
from .api.router import api_router
from .config import FRONTEND_DIST
from .rag import retriever as rag

app = FastAPI(title="Exit Automation Platform", version="2.0")


@app.on_event("startup")
def _build_rag_index() -> None:
    # Build the FAQ vector index on first boot so the first chat is fast.
    try:
        rag.ensure_index()
    except Exception as exc:  # noqa: BLE001 - don't block the app if RAG is degraded
        print(f"[rag] index build skipped: {exc}")
    # Load the local OCR models in the background so the first upload isn't slow.
    threading.Thread(target=ocr.warm_up, name="ocr-warmup", daemon=True).start()


#: Mutating requests to these paths carry no session yet (login) or must work
#: even when the session is already gone (logout), so the double-submit check
#: cannot apply to them.
CSRF_EXEMPT = frozenset({"/api/auth/login", "/api/auth/logout"})
_MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@app.middleware("http")
async def _enforce_csrf(request: Request, call_next):
    """Double-submit CSRF on every mutating API request.

    Middleware rather than a per-route dependency, deliberately. Before this,
    exactly one route (the orchestrator's SSE resume) called `verify_csrf`, and
    every other mutating endpoint -- create employee, run agent, execute a
    deprovisioning plan, change password -- relied on `SameSite=Lax` alone.
    Lax stops a cross-site POST today, but it is one cookie-attribute change
    away from not doing so, and the failure would be silent.

    Putting it here means a new mutating route is protected by *where it lives*
    rather than by its author remembering. GETs are untouched: the SSE stream
    route does its own check, because EventSource cannot send headers and has
    to pass the token as a query parameter instead.
    """
    if (request.method in _MUTATING
            and request.url.path.startswith("/api/")
            and request.url.path not in CSRF_EXEMPT
            # No session means there is nothing for a cross-site page to abuse,
            # so let it through and answer with the 401 the route would give.
            # Without this, an expired session gets "Missing or invalid CSRF
            # token" on its next write -- which tells the user nothing useful
            # and never prompts them to sign in again.
            and request.cookies.get(auth.SESSION_COOKIE)):
        cookie = request.cookies.get(auth.CSRF_COOKIE)
        header = request.headers.get("X-CSRF-Token")
        if not cookie or not header or not secrets.compare_digest(header, cookie):
            return JSONResponse(
                status_code=403,
                content={"detail": "Missing or invalid CSRF token."},
            )
    return await call_next(request)


app.include_router(api_router)


# -- static frontend (declared last so it doesn't shadow /api) --------------
# The React bundle is built by Vite into frontend/dist. Hashed assets are
# served straight from disk; everything else falls through to index.html so a
# client-side route survives a reload or a shared link.
if (FRONTEND_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")


@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str) -> FileResponse:
    # Declared last, so every real API route has already matched. An unknown
    # /api path must still 404 as JSON rather than silently returning the app
    # shell, which would surface as a JSON parse error in the client.
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail=f"No such endpoint: /{full_path}")

    index = FRONTEND_DIST / "index.html"
    if not index.is_file():
        raise HTTPException(
            status_code=503,
            detail="Frontend is not built. Run: cd frontend && npm install && npm run build",
        )
    if full_path:
        candidate = (FRONTEND_DIST / full_path).resolve()
        # Serve a real file only when it is genuinely inside the build
        # directory -- otherwise "../.." would walk out of it.
        root = FRONTEND_DIST.resolve()
        if candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
    return FileResponse(index)
