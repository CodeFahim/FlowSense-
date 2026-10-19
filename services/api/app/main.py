from __future__ import annotations

import logging
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.accounts import router as accounts_router
from app.api.documents import router as documents_router
from app.api.insights import router as insights_router
from app.api.personal import router as personal_router
from app.api.records import router as records_router
from app.api.routes import router
from app.audit.chain import correlation_id
from app.config import get_settings
from app.documents import intake as doc_intake
from app.documents import pdf as pdf_intake
from app.errors import ApiError

log = logging.getLogger("flowsense")
_PDF_UPLOAD_PATH = re.compile(r"/api/invoices/[^/]+/evidence/pdf")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Restart recovery: settle or fail transfers left pending by a crash or timeout.
    # Safe with several workers: settlement events are de-duplicated by event id.
    try:
        from app.db import get_sessionmaker
        from app.reconciliation.recovery import reconcile_all

        with get_sessionmaker()() as db:
            n = reconcile_all(db)
        if n:
            log.warning("Startup recovery reconciled pending transfers for %d business(es)", n)
    except Exception:  # never block startup; /api/reconcile can be run manually
        log.exception("Startup recovery failed")
    yield


def create_app() -> FastAPI:
    settings = get_settings()  # validates configuration at startup (fail safely)
    prod = settings.env in ("production", "hosted_demo")  # no public API docs outside local development
    app = FastAPI(
        title="FlowSense API",
        version="0.1.0",
        description="Sandbox-only cash-flow rescue copilot. No real banking.",
        docs_url=None if prod else "/api/docs",
        openapi_url=None if prod else "/api/openapi.json",
        redoc_url=None,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        cl = request.headers.get("content-length")
        limit = settings.max_body_bytes
        if _PDF_UPLOAD_PATH.fullmatch(request.url.path) or (request.url.path == "/api/documents" and request.method == "POST"):
            limit = max(pdf_intake.MAX_PDF_BYTES, doc_intake.MAX_FILE_BYTES) + 64 * 1024  # one file plus multipart framing
            cl = cl if cl is not None else "x"  # uploads must declare their size up front
        if cl is not None and (not cl.isdigit() or int(cl) > limit):
            return JSONResponse(
                status_code=413, content={"error": {"code": "BODY_TOO_LARGE", "message": "Request body too large.", "reasons": []}}
            )
        rid = request.headers.get("x-request-id", "")
        cid = rid if rid.isalnum() and len(rid) <= 64 else uuid.uuid4().hex
        token = correlation_id.set(cid)
        try:
            response = await call_next(request)
        finally:
            correlation_id.reset(token)
        response.headers["X-Request-ID"] = cid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        if not request.url.path.startswith("/api/docs"):
            response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        return response

    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content=exc.body())

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [{"code": "INVALID_FIELD", "message": f"{'.'.join(str(p) for p in e['loc'][1:])}: {e['msg']}"} for e in exc.errors()]
        return JSONResponse(
            status_code=422, content={"error": {"code": "VALIDATION_FAILED", "message": "Request is invalid.", "reasons": fields}}
        )

    @app.exception_handler(Exception)
    async def unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled error", exc_info=exc)
        return JSONResponse(
            status_code=500, content={"error": {"code": "INTERNAL_ERROR", "message": "Unexpected server error.", "reasons": []}}
        )

    app.include_router(accounts_router)
    app.include_router(router)
    app.include_router(personal_router)
    app.include_router(records_router)
    app.include_router(documents_router)
    app.include_router(insights_router)
    return app


app = create_app()
