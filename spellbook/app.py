from __future__ import annotations

import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from spellbook import __version__
from spellbook.config import PACKAGE_ROOT, AppPaths
from spellbook.database import prepare_database
from spellbook.repositories.spell_repository import PAGE_LIMIT, SpellRepository
from spellbook.security import LocalOnlyMiddleware, new_csrf_token, require_csrf
from spellbook.services.edit_service import MAX_VERSIONS, EditError, EditService, StaleRevisionError


class RevisionInput(BaseModel):
    revision_hash: str = Field(min_length=64, max_length=64)


class UpdateInput(RevisionInput):
    fields: dict[str, Any]


class RollbackInput(RevisionInput):
    version_id: int


def create_app(paths: AppPaths | None = None) -> FastAPI:
    paths = paths or AppPaths.default()
    prepare_database(paths)
    spells = SpellRepository(paths.database)
    editor = EditService(spells, paths.seed_database)

    app = FastAPI(title="法術書", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(LocalOnlyMiddleware)
    app.mount("/static", StaticFiles(directory=PACKAGE_ROOT / "web" / "static"), name="static")
    templates = Jinja2Templates(directory=PACKAGE_ROOT / "web" / "templates")
    app.state.csrf_token = new_csrf_token()
    app.state.paths = paths
    app.state.last_activity = time.monotonic()

    @app.middleware("http")
    async def track_activity(request: Request, call_next):
        # The desktop launcher exits once no open page has been seen for a while.
        app.state.last_activity = time.monotonic()
        return await call_next(request)

    @app.exception_handler(EditError)
    async def edit_error_handler(_request: Request, exc: EditError):
        status = 409 if isinstance(exc, StaleRevisionError) else 422
        return JSONResponse(status_code=status, content={"detail": str(exc)})

    def load(spell_id: str) -> dict[str, Any]:
        try:
            return spells.get_spell(spell_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="找不到法術") from None

    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/api/ping")
    def ping():
        return {"status": "ok"}

    @app.get("/")
    def index(request: Request):
        response = templates.TemplateResponse(
            request,
            "index.html",
            {"csrf_token": app.state.csrf_token, "max_versions": MAX_VERSIONS, "version": __version__},
        )
        response.set_cookie("spellbook_csrf", app.state.csrf_token, samesite="strict", httponly=False, secure=False)
        return response

    @app.get("/api/summary")
    def summary():
        return spells.summary()

    @app.get("/api/spells")
    def list_spells(
        q: str = "",
        letter: str = Query(default="", max_length=1),
        edited: bool = False,
        limit: int = Query(default=PAGE_LIMIT, ge=1, le=PAGE_LIMIT),
        offset: int = Query(default=0, ge=0),
    ):
        return {"items": spells.list_spells(query=q.strip(), letter=letter, edited_only=edited, limit=limit, offset=offset)}

    @app.get("/api/spells/{spell_id}")
    def get_spell(spell_id: str):
        return load(spell_id)

    @app.put("/api/spells/{spell_id}", dependencies=[Depends(require_csrf)])
    def update_spell(spell_id: str, payload: UpdateInput):
        return editor.update(spell_id, payload.fields, payload.revision_hash)

    @app.get("/api/spells/{spell_id}/versions")
    def versions(spell_id: str):
        load(spell_id)
        return {"max_versions": MAX_VERSIONS, "items": editor.versions(spell_id)}

    @app.post("/api/spells/{spell_id}/rollback", dependencies=[Depends(require_csrf)])
    def rollback(spell_id: str, payload: RollbackInput):
        return editor.rollback(spell_id, payload.version_id, payload.revision_hash)

    @app.post("/api/spells/{spell_id}/restore-original", dependencies=[Depends(require_csrf)])
    def restore_original(spell_id: str, payload: RevisionInput):
        return editor.restore_original(spell_id, payload.revision_hash)

    return app
