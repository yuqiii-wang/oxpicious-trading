"""server_cluster.llm.backends.dummy — the offline chat
simulation as a managed model.

Migration of the former ``test.dummy_llm.server`` FastAPI host: the
engine is the in-process
``test.dummy_llm.DummyLlmProvider`` (the gherkin feature dataset —
on matched what request, return what response), and this backend owns
the HTTP skin's behavioral contract verbatim:

* the rate-limit controls (``fail_next``, ``rate_limit_code``,
  ``max_rpm`` sliding window) and their counters — driven by the host's
  POST /control, answered by GET /stats,
* the ZhiPu-style 429 error bodies (``{"error": {"code": ...}}`` with
  the classifier-visible Chinese markers) via :func:`rate_limit_body`,
* the OpenAI-style envelope rendering (:meth:`_render_envelope`,
  non-streaming — exactly the old server's shape; moved here 2026-11-11
  when the test package dropped its serving logic).

Auth is NOT here: the host route performs the Bearer check first (401
code 1002 on a missing/malformed header), then this backend's
:meth:`chat_envelope` applies fail_next → window → engine, the old
order. No weights, no torch — ``load``/``offload`` are no-ops and the
model is permanently ``loaded``.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any, Dict, Optional

from ..config import ModelBackendSpec
from ..data_definitions.admin import ControlResponse
from .base import ModelBackend

logger = logging.getLogger(__name__)

# The rate-limit error codes the simulation may emit — exactly the
# ZhiPu codes the harness's classifiers know (repair._TRANSIENT_PATTERNS
# and the transport's retry ladder).
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


def rate_limit_body(code: str) -> Dict[str, Any]:
    """The 429 body — the shape ZhipuChatProvider's transport parses
    (``data["error"]["code"]``) with the classifier-visible message."""
    return {"error": {
        "code": code,
        "message": _CODE_MESSAGES.get(code, f"dummy simulated error {code}"),
    }}


class DummyChatBackend(ModelBackend):
    """The ``dummy-1`` model over one ``DummyLlmProvider``
    engine — the migrated provider simulation (see module docstring)."""

    def __init__(self, spec: ModelBackendSpec,
                 default_idle_offload_s: float) -> None:
        super().__init__(spec, default_idle_offload_s)
        from test.dummy_llm import DummyLlmProvider

        self.provider = DummyLlmProvider()
        # The mutable knobs + counters (the former _ServerState).
        self.fail_next = 0
        self.rate_limit_code = "1302"
        self.max_rpm = 0
        self.window: deque = deque()
        self.served = 0
        self.rejected_rate_limit = 0
        self.rejected_auth = 0
        # The cumulative served token consumption (the consumption API
        # the /control + /stats surface answers).
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_tokens = 0
        # The engine needs no loading — mark it ready immediately.
        self._state = "loaded"

    # ------------------------------------------------- lifecycle no-ops
    def _load(self) -> None:  # weightless backend
        return None

    def _offload(self) -> None:
        return None

    def offload(self) -> None:
        """Admin offload is a true no-op here: nothing weighty exists,
        and flipping the state to 'offloaded' would be a fib nothing
        could rehydrate — the model stays honestly ``loaded``."""
        return None

    # -------------------------------------------------------- controls
    def controls(self) -> ControlResponse:
        return ControlResponse(
            fail_next=self.fail_next,
            rate_limit_code=self.rate_limit_code,
            max_rpm=self.max_rpm,
            served=self.served,
            rejected_rate_limit=self.rejected_rate_limit,
            rejected_auth=self.rejected_auth,
            total_prompt_tokens=self.total_prompt_tokens,
            total_completion_tokens=self.total_completion_tokens,
            total_tokens=self.total_tokens)

    def apply_control(
        self, *, fail_next: Optional[int], rate_limit_code: Optional[str],
        max_rpm: Optional[int], reset: bool,
    ) -> ControlResponse:
        """POST /control — the migrated knob flips (ValueError on an
        unknown rate_limit_code, like the old server)."""
        if reset:
            self.window.clear()
            self.served = 0
            self.rejected_rate_limit = 0
            self.rejected_auth = 0
            self.total_prompt_tokens = 0
            self.total_completion_tokens = 0
            self.total_tokens = 0
        if fail_next is not None:
            self.fail_next = max(0, int(fail_next))
        if rate_limit_code is not None:
            if rate_limit_code not in RATE_LIMIT_CODES:
                raise ValueError(
                    f"rate_limit_code must be one of {RATE_LIMIT_CODES}")
            self.rate_limit_code = rate_limit_code
        if max_rpm is not None:
            self.max_rpm = max(0, int(max_rpm))
            self.window.clear()
        return self.controls()

    def note_auth_rejected(self) -> None:
        self.rejected_auth += 1

    # -------------------------------------------------------- inference
    async def chat_envelope(self, payload: Dict[str, Any]) -> Any:
        """One chat-completion envelope — or the 429 body dict when a
        rate-limit control fires (the route maps dict-429 bodies to the
        HTTP 429 response, the old server's exact contract)."""
        # Deterministic test injection FIRST (a mid-run /control flip
        # must hit the very next request), then the organic window.
        if self.fail_next > 0:
            self.fail_next -= 1
            self.rejected_rate_limit += 1
            return rate_limit_body(self.rate_limit_code)
        now = time.monotonic()
        while self.window and now - self.window[0] > _WINDOW_SEC:
            self.window.popleft()
        if self.max_rpm and len(self.window) >= self.max_rpm:
            self.rejected_rate_limit += 1
            return rate_limit_body(self.rate_limit_code)
        self.window.append(now)
        message = await self.provider.chat_complete(
            payload.get("messages") or [],
            model=payload.get("model"),
            tools=payload.get("tools"),
            temperature=payload.get("temperature"),
            max_tokens=payload.get("max_tokens"))
        self.served += 1
        envelope = self._render_envelope(payload, message)
        usage = envelope.get("usage") or {}
        self.total_prompt_tokens += int(
            usage.get("prompt_tokens") or 0)
        self.total_completion_tokens += int(
            usage.get("completion_tokens") or 0)
        self.total_tokens += int(usage.get("total_tokens") or 0)
        self.touch()
        return envelope

    def _render_envelope(self, payload: Dict[str, Any],
                         message: Dict[str, Any]) -> Dict[str, Any]:
        """The OpenAI-style response envelope around one engine reply —
        the shape ``ZhipuChatProvider``'s transport parses
        (``choices[0].message``): ``id`` / ``created`` / ``model`` /
        ``choices`` with ``finish_reason`` ``tool_calls`` vs ``stop`` /
        a ROUGH deterministic ``usage`` estimate (chars/4 — the token
        counts are test payload, not an accounting source; the shared
        :func:`test.dummy_llm.estimate_usage` formula so the
        HTTP-sim and the in-process audit rows report identical
        numbers)."""
        from test.dummy_llm import estimate_usage

        return {
            "id": f"chatcmpl-dummy-{self.served:06d}",
            "created": int(time.time()),
            "model": str(payload.get("model") or self.spec.model_id),
            "choices": [{
                "index": 0,
                "message": message,
                "finish_reason": ("tool_calls" if message.get("tool_calls")
                                  else "stop"),
            }],
            "usage": estimate_usage(payload.get("messages") or [], message),
            "request_id": payload.get("request_id"),
        }
