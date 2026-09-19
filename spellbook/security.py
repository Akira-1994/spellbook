from __future__ import annotations

import secrets

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware


class LocalOnlyMiddleware(BaseHTTPMiddleware):
    """Rejects rebinding and cross-origin write attempts against the local server."""

    async def dispatch(self, request: Request, call_next):
        host = (request.headers.get("host") or "").split(":", 1)[0].lower()
        if host not in {"127.0.0.1", "localhost", "testserver"}:
            return _plain_error(400, "Only local requests are accepted")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin:
                allowed = {f"http://{request.headers.get('host')}", f"https://{request.headers.get('host')}"}
                if origin.rstrip("/") not in allowed:
                    return _plain_error(403, "Cross-origin request rejected")
        return await call_next(request)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def require_csrf(request: Request) -> None:
    header = request.headers.get("x-csrf-token", "")
    cookie = request.cookies.get("spellbook_csrf", "")
    expected = request.app.state.csrf_token
    if not header or not cookie or not secrets.compare_digest(header, expected) or not secrets.compare_digest(cookie, expected):
        raise HTTPException(status_code=403, detail="CSRF 驗證失敗，請重新整理頁面")


def _plain_error(status: int, message: str):
    from starlette.responses import PlainTextResponse

    return PlainTextResponse(message, status_code=status)
