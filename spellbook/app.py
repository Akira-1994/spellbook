from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from spellbook.config import EditorSettings, ProjectPaths
from spellbook.repositories.draft_repository import DraftRepository
from spellbook.repositories.event_repository import EventRepository
from spellbook.repositories.spell_repository import SpellRepository
from spellbook.security import LocalOnlyMiddleware, new_csrf_token, require_csrf
from spellbook.repositories.git_repository import GitError
from spellbook.services.commit_service import CommitService
from spellbook.services.review_service import ReviewError, ReviewService, StaleRevisionError
from spellbook.services.replay_service import ReplayService


PACKAGE_ROOT = Path(__file__).parent


def default_project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd().resolve()


class EditorInput(BaseModel):
    editor_name: str = Field(min_length=1, max_length=100)


class DraftInput(BaseModel):
    revision_hash: str = Field(min_length=64, max_length=64)
    fields: dict[str, Any]


class ChangesInput(DraftInput):
    note: str = Field(default="", max_length=2000)


class CheckInput(BaseModel):
    revision_hash: str = Field(min_length=64, max_length=64)
    check_type: str
    checked: bool


class ApprovalInput(BaseModel):
    revision_hash: str = Field(min_length=64, max_length=64)
    note: str = Field(default="", max_length=2000)


class CommitInput(BaseModel):
    confirm_identity_mismatch: bool = False


class DuplicateDecisionInput(BaseModel):
    revision_hash: str = Field(min_length=64, max_length=64)
    decision: str
    related_spell_id: str | None = None
    note: str = Field(default="", max_length=2000)


class ConflictResolutionInput(BaseModel):
    spell_id: str
    revision_hash: str = Field(min_length=64, max_length=64)
    resolution: str
    custom_value: Any = None
    note: str = Field(default="", max_length=2000)


def create_app(project_root: Path | str | None = None) -> FastAPI:
    root = Path(project_root or default_project_root()).resolve()
    paths = ProjectPaths.from_root(root)
    if not paths.database.exists():
        raise RuntimeError(f"找不到資料庫：{paths.database}")
    spells = SpellRepository(paths.database, root / "migrations" / "002_review_workflow.sql")
    spells.migrate()
    events = EventRepository(paths.events)
    replay_result = ReplayService(spells, events).replay()
    drafts = DraftRepository(paths.local_state / "reviewer.local.sqlite")
    settings = EditorSettings(paths)
    reviews = ReviewService(spells, events)
    commits = CommitService(paths)

    app = FastAPI(title="Spellbook 校對工作台", docs_url=None, redoc_url=None)
    app.add_middleware(LocalOnlyMiddleware)
    app.mount("/static", StaticFiles(directory=PACKAGE_ROOT / "web" / "static"), name="static")
    templates = Jinja2Templates(directory=PACKAGE_ROOT / "web" / "templates")
    app.state.csrf_token = new_csrf_token()
    app.state.paths = paths
    app.state.replay_result = replay_result

    def editor_name() -> str:
        value = settings.load()
        if not value["confirmed"] or not value["editor_name"]:
            raise HTTPException(status_code=428, detail="請先確認校對者名稱")
        return str(value["editor_name"])

    @app.exception_handler(ReviewError)
    async def review_error_handler(_request: Request, exc: ReviewError):
        from fastapi.responses import JSONResponse

        status = 409 if isinstance(exc, StaleRevisionError) else 422
        return JSONResponse(status_code=status, content={"detail": str(exc)})

    @app.exception_handler(GitError)
    async def git_error_handler(_request: Request, exc: GitError):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.get("/health")
    def health():
        return {"status": "ok", "database": paths.database.exists(), "pdf": paths.source_pdf.exists(), "replay": replay_result}

    @app.get("/")
    def index(request: Request):
        response = templates.TemplateResponse(
            request,
            "index.html",
            {
                "csrf_token": app.state.csrf_token,
                "settings": settings.load(),
                "project_name": root.name,
            },
        )
        response.set_cookie(
            "spellbook_csrf",
            app.state.csrf_token,
            samesite="strict",
            httponly=False,
            secure=False,
        )
        return response

    @app.get("/api/summary")
    def summary():
        return spells.summary()

    @app.get("/api/spells")
    def list_spells(
        q: str = "",
        status: str = "",
        letter: str = "",
        issue: str = "",
        limit: int = Query(default=80, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ):
        return {"items": spells.list_spells(query=q, status=status, letter=letter, issue=issue, limit=limit, offset=offset)}

    @app.get("/api/spells/{spell_id}")
    def get_spell(spell_id: str):
        try:
            result = spells.get_spell(spell_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="找不到法術") from None
        result["draft"] = drafts.load(spell_id)
        return result

    @app.post("/api/settings/editor", dependencies=[Depends(require_csrf)])
    def set_editor(payload: EditorInput):
        settings.save(payload.editor_name)
        return settings.load()

    @app.put("/api/spells/{spell_id}/draft", dependencies=[Depends(require_csrf)])
    def save_draft(spell_id: str, payload: DraftInput, _editor: str = Depends(editor_name)):
        drafts.save(spell_id, payload.revision_hash, payload.fields)
        return {"saved": True}

    @app.delete("/api/spells/{spell_id}/draft", dependencies=[Depends(require_csrf)])
    def delete_draft(spell_id: str, _editor: str = Depends(editor_name)):
        drafts.delete(spell_id)
        return {"deleted": True}

    @app.post("/api/spells/{spell_id}/changes", dependencies=[Depends(require_csrf)])
    def save_changes(spell_id: str, payload: ChangesInput, editor: str = Depends(editor_name)):
        event = reviews.update_fields(
            spell_id=spell_id,
            changes=payload.fields,
            editor_name=editor,
            base_revision_hash=payload.revision_hash,
            note=payload.note,
        )
        drafts.delete(spell_id)
        return {"event_id": event.event_id, "spell": spells.get_spell(spell_id)}

    @app.post("/api/spells/{spell_id}/checks", dependencies=[Depends(require_csrf)])
    def set_check(spell_id: str, payload: CheckInput, editor: str = Depends(editor_name)):
        event = reviews.set_check(
            spell_id=spell_id,
            check_type=payload.check_type,
            checked=payload.checked,
            editor_name=editor,
            base_revision_hash=payload.revision_hash,
        )
        return {"event_id": event.event_id, "spell": spells.get_spell(spell_id)}

    @app.post("/api/spells/{spell_id}/approve", dependencies=[Depends(require_csrf)])
    def approve(spell_id: str, payload: ApprovalInput, editor: str = Depends(editor_name)):
        event = reviews.approve(
            spell_id=spell_id,
            editor_name=editor,
            base_revision_hash=payload.revision_hash,
            note=payload.note,
        )
        drafts.delete(spell_id)
        return {"event_id": event.event_id, "spell": spells.get_spell(spell_id)}

    @app.get("/api/spells/{spell_id}/duplicates")
    def duplicate_group(spell_id: str):
        try:
            items = reviews.duplicate_group(spell_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="找不到法術") from None
        return {"items": items}

    @app.post("/api/spells/{spell_id}/duplicate-decision", dependencies=[Depends(require_csrf)])
    def duplicate_decision(spell_id: str, payload: DuplicateDecisionInput, editor: str = Depends(editor_name)):
        event = reviews.decide_duplicate(
            spell_id=spell_id,
            decision=payload.decision,
            related_spell_id=payload.related_spell_id,
            editor_name=editor,
            base_revision_hash=payload.revision_hash,
            note=payload.note,
        )
        return {"event_id": event.event_id, "decision": payload.decision}

    @app.post("/api/conflicts/{conflict_id}/resolve", dependencies=[Depends(require_csrf)])
    def resolve_conflict(conflict_id: str, payload: ConflictResolutionInput, editor: str = Depends(editor_name)):
        event = reviews.resolve_conflict(
            spell_id=payload.spell_id,
            conflict_id=conflict_id,
            resolution=payload.resolution,
            custom_value=payload.custom_value,
            editor_name=editor,
            base_revision_hash=payload.revision_hash,
            note=payload.note,
        )
        return {"event_id": event.event_id, "spell": spells.get_spell(payload.spell_id)}

    @app.get("/api/git/preview")
    def git_preview(_editor: str = Depends(editor_name)):
        result = commits.preview()
        result["editor_name"] = _editor
        result["identity_matches"] = result["identity"]["name"] == _editor
        return result

    @app.post("/api/git/commit", dependencies=[Depends(require_csrf)])
    def git_commit(payload: CommitInput, editor: str = Depends(editor_name)):
        identity = commits.git.identity()
        if identity["name"] != editor and not payload.confirm_identity_mismatch:
            raise GitError("Git 作者與校對者名稱不同，請明確確認後再提交")
        return commits.commit()

    @app.get("/source/pdf")
    def source_pdf():
        if not paths.source_pdf.is_file() or paths.source_pdf.parent.resolve() != root:
            raise HTTPException(status_code=404, detail="找不到來源 PDF")
        return FileResponse(
            paths.source_pdf,
            media_type="application/pdf",
            filename=paths.source_pdf.name,
            content_disposition_type="inline",
        )

    return app


app = create_app()
