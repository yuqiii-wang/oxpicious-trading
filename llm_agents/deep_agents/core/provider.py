"""llm_agents.deep_agents.core.provider — ZhiPu chat completions with tools.

Same endpoint as ``llm_agents.llm_ask.providers.zhipu`` (``POST
https://open.bigmodel.cn/api/paas/v4/chat/completions``, Bearer auth)
minus that package's no-tools contract: here the payload CARRIES the
``tools`` declarations, so the model can answer with ``tool_calls``
(OpenAI-style function calling: each call is
``{"id", "type": "function", "function": {"name", "arguments": "<json>"}}``).
The raw assistant message dict — ``content`` and/or ``tool_calls`` —
is returned as-is; the agent loop interprets it.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, Optional, Sequence

from llm_agents._core.base import BaseLlmProvider
from llm_agents.deep_agents.limits import run_limits


class ZhipuChatProvider(BaseLlmProvider):
    """ZhiPu BigModel chat provider whose replies may carry tool_calls.

    Every request carries a per-request OUTPUT ceiling (``max_tokens``):
    no single reply — runaway synthesis, degenerate repetition, a model
    stuck re-emitting its context — can grow unbounded. It is a LOOSE
    guard, not a length prescription (a full research finding is
    ~2-4k tokens; the default leaves multiples of headroom). It rides
    the provider instance, so every caller through it (the agent loop,
    each spawned child, the planner session) is capped identically.

    The ceiling resolves PER REQUEST: an explicit ctor
    ``max_output_tokens`` pins it; otherwise the static deep_agent_config's
    ``max_output_tokens`` applies (limits.run_limits() — config edits
    take effect without restarting the daemon; the class constant is
    the fail-soft default).
    """

    name = "zhipu"
    API_KEY_ENV_VARS = ("GLM_ONLINE_SEARCH_KEY", "ZHIPU_API_KEY",
                        "BIGMODEL_API_KEY")

    CHAT_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    # glm-5.3-flash is the project default (same family the other
    # llm_agents providers use); override per call/CLI when needed.
    DEFAULT_MODEL = "glm-5.3-flash"
    chat_timeout = 120.0
    # The default per-request output ceiling (see class docstring).
    DEFAULT_MAX_TOKENS = 8192

    def __init__(self, *, api_key: Optional[str] = None,
                 max_attempts: int = 3,
                 max_output_tokens: Optional[int] = None,
                 api_url: Optional[str] = None) -> None:
        super().__init__(api_key=api_key, max_attempts=max_attempts)
        # None = resolve per request from the static deep_agent_config
        # (run_limits().max_output_tokens); an explicit value pins it.
        self.max_output_tokens = (
            None if max_output_tokens is None
            else max(1, int(max_output_tokens)))
        # The chat endpoint: an explicit api_url (the static.llm_config
        # row's endpoint, injected at agent start) overrides the class
        # default; blank/None keeps CHAT_URL.
        self.chat_url = (api_url if api_url and api_url.strip()
                         else self.CHAT_URL)

    async def chat_complete(
        self, messages: Sequence[Dict[str, Any]], *,
        model: Optional[str] = None,
        tools: Optional[Sequence[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """One chat completion; returns the assistant ``message`` dict.

        Args:
            messages: the running conversation (system / user /
                assistant / tool messages, OpenAI roles).
            model: model id (DEFAULT_MODEL when None).
            tools: chat-completion tool declarations (the McpToolClient
                schemas); None sends no ``tools`` key.
            temperature: sampling temperature within [0, 1].
            max_tokens: per-request output ceiling — the provider's
                ``max_output_tokens`` when None.

        Returns:
            ``choices[0].message`` — ``{"role": "assistant",
            ``content": str | None, "tool_calls": [...] | None}``.
        """
        payload: Dict[str, Any] = {
            "model": model or self.DEFAULT_MODEL,
            "messages": list(messages),
            "stream": False,
            "request_id": str(uuid.uuid4()),
            "max_tokens": (max(1, int(max_tokens)) if max_tokens is not None
                           else (self.max_output_tokens
                                 if self.max_output_tokens is not None
                                 else run_limits().max_output_tokens)),
        }
        if tools:
            payload["tools"] = list(tools)
        if temperature is not None:
            payload["temperature"] = temperature
        # The per-request WALL-CLOCK budget (the run limits'
        # llm_request_timeout_sec — default 300s): the transport's
        # retry ladder terminates at the budget, each attempt's read
        # timeout shrinking to the remainder. Resolved per request like
        # the output ceiling — config edits apply without a restart.
        # The caller-side contract of an overrun: the input is too
        # heavy for one call; refactor it into smaller tasks (never
        # resend the same payload hoping for a faster round).
        data = await self._post_json_async(
            self.chat_url, payload,
            timeout=self.chat_timeout,
            budget=run_limits().llm_request_timeout_sec)
        choices = data.get("choices") or []
        message = (choices[0].get("message") or {}
                   if isinstance(choices[0], dict) else {})
        return message if isinstance(message, dict) else {}
