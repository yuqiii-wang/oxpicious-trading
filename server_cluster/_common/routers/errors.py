"""server_cluster._common.routers.errors — the error-envelope handlers.

Non-2xx bodies follow the Express API's error envelope convention
(``data_viz/api/CONTRACTS.md``: ``{success: false, error, code?, ...}``
— ``INVALID_PARAM`` for malformed payloads, ``NOT_FOUND`` for unknown
resources), so the Express client sees ONE error shape whether the
rejection happened in its own routes or in the cluster behind them."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def install_validation_handler(app: FastAPI) -> None:
    """Replace FastAPI's default 422/HTTPException bodies with the
    repo's envelope."""

    @app.exception_handler(RequestValidationError)
    async def _validation_envelope(
            request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "error": str(exc),
                "code": "INVALID_PARAM",
                "details": exc.errors(),
            },
        )

    @app.exception_handler(HTTPException)
    async def _http_envelope(
            request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "error": str(exc.detail),
                "code": ("NOT_FOUND" if exc.status_code == 404
                         else "INVALID_PARAM" if exc.status_code in (400, 422)
                         else "FORBIDDEN" if exc.status_code == 403
                         else "INTERNAL"),
            },
        )
