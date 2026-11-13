"""server_cluster.llm.routers.errors — the shared error
mapping (the ``exceptions.py`` slot in the standard FastAPI layout).

Every HTTP failure this host emits answers in the OPENAI error envelope
(``{"error": {"message", "type", "code"?}}``) — never FastAPI's bare
``{"detail": ...}`` shape — so the openai SDK and ZhipuChatProvider's
transport surface ``error.message``/``error.code`` the way their
classifiers expect. :func:`install_validation_handler` maps body
validation failures (FastAPI 422s) into the same envelope as 400s for
the routers that use FastAPI's typed parameter parsing (the chat router
parses manually, auth-first, and maps its own)."""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ..data_definitions.chat_api import error_body

logger = logging.getLogger(__name__)


def openai_error(status: int, message: str, *,
                 etype: str = "invalid_request_error",
                 code: Optional[str] = None) -> JSONResponse:
    """One OpenAI-envelope JSON response (see module docstring)."""
    return JSONResponse(status_code=status, content=error_body(
        message, etype=etype, code=code))


def validation_message(exc: RequestValidationError) -> str:
    """A pydantic/FastAPI validation failure as one readable line."""
    return "; ".join(
        "{}: {}".format(".".join(map(str, e.get("loc", []))), e.get("msg"))
        for e in exc.errors())


def install_validation_handler(app: FastAPI) -> None:
    """Map FastAPI's 422 RequestValidationError to the OpenAI 400
    envelope (app-wide)."""

    @app.exception_handler(RequestValidationError)
    async def validation_to_openai_error(
        request: Request, exc: RequestValidationError,
    ) -> JSONResponse:
        return openai_error(
            400, f"invalid request body — {validation_message(exc)}")


def not_found_model(model: str) -> JSONResponse:
    """The standard unknown-model answer (chat + admin routes share)."""
    return openai_error(
        404, f"The model `{model}` does not exist on this local model "
        "host.", etype="invalid_request_error", code="model_not_found")


def model_load_failed(model: str, exc: Any) -> JSONResponse:
    """The 503 for a model whose weights failed to load — the actionable
    retry hint included (the admin API / chat route share it)."""
    return openai_error(
        503, f"model `{model}` failed to serve: {exc!r} (the weights may "
        f"have failed to load — POST /v1/models/{model}/load to retry)",
        etype="service_unavailable", code="model_load_failed")
