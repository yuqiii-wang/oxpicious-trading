"""llm_agents.test.dummy_llm.server — the dummy LLM as a FastAPI server.

The dummy LLM's HTTP form: a FastAPI app that SIMULATES the real chat
provider's endpoint surface so tests can point the genuine
``ZhipuChatProvider`` at it (``api_url=http://127.0.0.1:8768/v1/chat/
completions, api_key="dummy"``) and exercise the whole transport —
Bearer auth, the request/response envelope, ``RetryingJsonClient``'s
retry ladder, and the error codes the repair lane classifies — with
zero provider spend and full determinism. The reply engine is the
in-process ``DummyLlmProvider`` (sibling module); this file is the HTTP
skin around it.

Routes:

* ``POST /v1/chat/completions`` and the ZhiPu-mirror
  ``POST /api/paas/v4/chat/completions`` — one handler: Bearer auth
  required (401 + code 1002 otherwise), then the rate-limit controls,
  then ``provider.complete_from_payload`` renders the envelope.
* ``GET /health`` — the liveness probe.
* ``GET /stats`` — served / rejected counters (what a test asserts on).
* ``POST /control`` — flip the failure knobs MID-RUN (a test starts a
  run healthy, rate-limits the provider, watches the repair lane
  handle it): ``fail_next`` (the next N requests get the rate-limit
  error — the deterministic injection), ``rate_limit_code``
  (``1302`` account rate limit / ``1701`` concurrency limit /
  ``1113`` balance exhaustion — the transport retries 1701 and fails
  1302/1113 immediately, exactly the real provider's contract),
  ``max_rpm`` (a sliding 60s window — the organic rate limit),
  ``reset`` (clear window + counters).

The rate-limit ERROR is the point: HTTP 429 + ``{"error": {"code":
"<code>", "message": ...}}`` — the message carrying the classifier's
Chinese markers (速率限制 for 1302, 并发 for 1701) so
``supervisor.repair.is_transient`` / ``is_rate_limited`` read the
injected failure exactly like a live one.

Run: ``python -m llm_agents.test.dummy_llm.server`` (args: --host
--port --max-rpm --fail-next --rate-limit-code --no-auth). Default port
8768 — beside the MCP server (8765) and the deep-agent daemon (8766).
ALWAYS-ON: registered in ``_common/service_heartbeat.py::
MONITORED_SERVICES`` (2026-11-07) — the stream watchdog starts/restarts
it like the other daemons, so HTTP-sim runs never meet a cold server;
``main()`` registers the heartbeat (long-running mode only — the
app-factory form stays heartbeat-free).
"""
from __future__ import annotations

import time
from collections import deque
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from llm_agents.test.dummy_llm.provider import DummyLlmProvider

# The simulated endpoint paths — the OpenAI-style one and the ZhiPu
# mirror (ZhipuChatProvider.CHAT_URL's path), so either URL works.
CHAT_PATHS = ("/v1/chat/completions",
              "/api/paas/v4/chat/completions")
DEFAULT_PORT = 8768

# The heartbeat/service name — the module path itself (the watchdog's
# spawn contract; MONITORED_SERVICES lists this exact string).
SERVICE = "llm_agents.test.dummy_llm.server"

# The rate-limit error codes the simulation may emit — exactly the
# ZhiPu codes the harness's classifiers know (repair._TRANSIENT_PATTERNS
# and the transport's retry ladder). Anything else would classify as an
# unknown failure and teach the tests nothing.
RATE_LIMIT_CODES = ("1302", "1701", "1113")

# The per-code message markers: 1302's 速率限制 and 1701's 并发 are what
# the repair lane's regexes match beside the bare codes.
_CODE_MESSAGES = {
    "1302": "速率限制 — dummy simulated account rate limit (1302)",
    "1701": "并发上限 — dummy simulated concurrency limit (1701)",
    "1113": "dummy simulated balance/quota exhaustion (1113) — "
            "the account has insufficient balance",
}

# The sliding window's length (seconds) behind max_rpm.
_WINDOW_SEC = 60.0


class ControlRequest(BaseModel):
    """One POST /control body — every field optional, absent = keep."""
    fail_next: Optional[int] = Field(default=None, ge=0,
                                     description="Make the next N "
                                         "chat requests fail with the "
                                         "rate-limit error.")
    rate_limit_code: Optional[str] = Field(
        default=None, description="The zhipu-style error code the "
        "rate-limit failures carry (1302 | 1701 | 1113).")
    max_rpm: Optional[int] = Field(
        default=None, ge=0, description="Sliding-window requests-per-"
        "minute cap; 0 clears the cap.")
    reset: bool = Field(
        default=False, description="Clear the window and counters.")


class ControlResponse(BaseModel):
    """The controls' state as every /control (and GET /stats) answers."""
    fail_next: int
    rate_limit_code: str
    max_rpm: int
    served: int
    rejected_rate_limit: int
    rejected_auth: int


class _ServerState:
    """The mutable server knobs + counters (one instance per app)."""

    def __init__(self, *, max_rpm: Optional[int] = None,
                 fail_next: int = 0, rate_limit_code: str = "1302",
                 require_auth: bool = True) -> None:
        if rate_limit_code not in RATE_LIMIT_CODES:
            raise ValueError(
                f"rate_limit_code must be one of {RATE_LIMIT_CODES}")
        self.max_rpm = max(0, int(max_rpm or 0))
        self.fail_next = max(0, int(fail_next))
        self.rate_limit_code = rate_limit_code
        self.require_auth = require_auth
        self.window: deque = deque()
        self.served = 0
        self.rejected_rate_limit = 0
        self.rejected_auth = 0

    def response(self) -> ControlResponse:
        return ControlResponse(
            fail_next=self.fail_next, rate_limit_code=self.rate_limit_code,
            max_rpm=self.max_rpm, served=self.served,
            rejected_rate_limit=self.rejected_rate_limit,
            rejected_auth=self.rejected_auth)


def rate_limit_body(code: str) -> Dict[str, Any]:
    """The 429 body — the shape ZhipuChatProvider's transport parses
    (``data["error"]["code"]``) with the classifier-visible message."""
    return {"error": {
        "code": code,
        "message": _CODE_MESSAGES.get(code, f"dummy simulated error {code}"),
    }}


class DummyLlmServer:
    """The simulated provider endpoint over one ``DummyLlmProvider``
    engine — build it, then serve ``.app`` with uvicorn (``main()``
    below) or point a test client at it.

    The auth/rate-limit contract mirrors the real endpoint closely
    enough to exercise the transport's every branch: a missing bearer
    header is a 401 (non-retried), a 429 body carrying 1701 burns the
    retry ladder (backoff 1s/3s then raises), a 429 body carrying 1302
    or 1113 raises immediately — the exact distinction the repair
    lane's transient classification is built on.
    """

    def __init__(self, *, provider: Optional[DummyLlmProvider] = None,
                 max_rpm: Optional[int] = None, fail_next: int = 0,
                 rate_limit_code: str = "1302",
                 require_auth: bool = True) -> None:
        self.provider = provider or DummyLlmProvider()
        self.state = _ServerState(
            max_rpm=max_rpm, fail_next=fail_next,
            rate_limit_code=rate_limit_code, require_auth=require_auth)
        self.app = self._build_app()

    # ------------------------------------------------------------------
    # The ASGI app
    # ------------------------------------------------------------------

    def _build_app(self) -> FastAPI:
        app = FastAPI(
            title="dummy-llm",
            description="Offline chat-provider simulation (the deep "
                        "agent harness's test double).",
            version="1.0.0")
        state, provider = self.state, self.provider

        @app.post(CHAT_PATHS[0])
        @app.post(CHAT_PATHS[1])
        async def chat_completions(request: Request) -> JSONResponse:
            # Bearer auth first — the real endpoint rejects an
            # unauthenticated call regardless of any load state.
            if state.require_auth:
                auth = request.headers.get("authorization") or ""
                if not auth.startswith("Bearer ") or len(auth) <= 7:
                    state.rejected_auth += 1
                    return JSONResponse(status_code=401, content={"error": {
                        "code": "1002",
                        "message": "dummy: missing or malformed "
                                   "Authorization bearer header"}})
            payload = await request.json()
            # Deterministic test injection FIRST (a mid-run /control
            # flip must hit the very next request), then the organic
            # sliding window.
            if state.fail_next > 0:
                state.fail_next -= 1
                state.rejected_rate_limit += 1
                return JSONResponse(
                    status_code=429,
                    content=rate_limit_body(state.rate_limit_code))
            now = time.monotonic()
            while state.window and now - state.window[0] > _WINDOW_SEC:
                state.window.popleft()
            if state.max_rpm and len(state.window) >= state.max_rpm:
                state.rejected_rate_limit += 1
                return JSONResponse(
                    status_code=429,
                    content=rate_limit_body(state.rate_limit_code))
            state.window.append(now)
            envelope = await provider.complete_from_payload(payload)
            state.served += 1
            return JSONResponse(status_code=200, content=envelope)

        @app.get("/health")
        async def health() -> Dict[str, str]:
            return {"status": "ok", "service": "llm_agents.test."
                                               "dummy_llm.server"}

        @app.get("/stats", response_model=ControlResponse)
        async def stats() -> ControlResponse:
            return state.response()

        @app.post("/control", response_model=ControlResponse)
        async def control(req: ControlRequest) -> ControlResponse:
            if req.reset:
                state.window.clear()
                state.served = 0
                state.rejected_rate_limit = 0
                state.rejected_auth = 0
            if req.fail_next is not None:
                state.fail_next = req.fail_next
            if req.rate_limit_code is not None:
                if req.rate_limit_code not in RATE_LIMIT_CODES:
                    raise ValueError(
                        f"rate_limit_code must be one of "
                        f"{RATE_LIMIT_CODES}")
                state.rate_limit_code = req.rate_limit_code
            if req.max_rpm is not None:
                state.max_rpm = max(0, req.max_rpm)
                state.window.clear()
            return state.response()

        return app


def create_app(*, provider: Optional[DummyLlmProvider] = None,
               **kwargs: Any) -> FastAPI:
    """The app-factory form (uvicorn programmatic runs, TestClient)."""
    return DummyLlmServer(provider=provider, **kwargs).app


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(
        prog="python -m llm_agents.test.dummy_llm.server",
        description="Serve the dummy LLM as a chat-completions "
                    "endpoint the real ZhipuChatProvider can run "
                    "against (offline provider simulation with "
                    "rate-limit error injection).")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--max-rpm", type=int, default=None,
                    help="Sliding-window requests-per-minute cap "
                         "(0 = unlimited; the organic rate limit).")
    ap.add_argument("--fail-next", type=int, default=0,
                    help="Fail the first N chat requests with the "
                         "rate-limit error (deterministic injection).")
    ap.add_argument("--rate-limit-code", default="1302",
                    choices=list(RATE_LIMIT_CODES),
                    help="The zhipu-style error code rate-limit "
                         "failures carry (default: %(default)s).")
    ap.add_argument("--no-auth", action="store_true",
                    help="Skip the Bearer auth check (testing "
                         "convenience; the real endpoint never does "
                         "this).")
    args = ap.parse_args()

    # Always-on under the stream watchdog (MONITORED_SERVICES — the
    # service name IS this module path, its spawn contract): the
    # heartbeat keeps the simulation live for HTTP-sim runs so a test
    # never waits on a cold server. The LONG-RUNNING mode only — the
    # app-factory path (create_app / TestClient) never registers, a
    # heartbeat from a dying one-shot would make the watchdog think
    # the real service is alive. Import lazily: service_heartbeat
    # pulls db_commons (and transitively pandas) — this module stays
    # light for every test import.
    from _common.service_heartbeat import register_service

    register_service(SERVICE)

    import uvicorn

    server = DummyLlmServer(
        max_rpm=args.max_rpm, fail_next=args.fail_next,
        rate_limit_code=args.rate_limit_code,
        require_auth=not args.no_auth)
    uvicorn.run(server.app, host=args.host, port=args.port,
                log_level="info")


if __name__ == "__main__":
    main()
