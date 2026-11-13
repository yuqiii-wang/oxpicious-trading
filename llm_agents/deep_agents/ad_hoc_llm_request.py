"""llm_agents.deep_agents.ad_hoc_llm_request — the INDEPENDENT LLM
request: ONE one-shot chat completion over a FRESH minimal context, no
conversation carried, for when a sub-question does not deserve the
token cost of the context around it.

The harness's asking lanes all carry something: a step hop re-sends its
accumulated history every tool turn; a spawn carries the run goal + the
business pass-down + a whole plan kit; the plan session carries the
data block. Between them sits a whole class of small asks — classify
this list, summarize this one document, judge this one wording, answer
this self-contained side question — where the answer costs a fraction
of what re-reading the carrying context costs. This module is that
middle: every :meth:`AdHocLlmRequest.ask` builds a TWO-message context
(``[system, user]``) from its arguments alone, calls ``chat_complete``
once with ``tools=None``, and returns the bounded text. The class holds
NO message list — nothing persists between asks; each request stands
alone by construction, not by discipline.

THE CHAIN ISOLATION (the logging half of "independent"): the transport
logs every provider POST into ``llm.llm_requests`` under the CONTEXT'S
bound chain, and from a chain's second request on stores INCREMENTAL
payloads (messages already present in the chain deduped away) —
semantics built for a conversation's re-sent history. An ad-hoc request
fired inside a running conversation would inherit its chain through the
ContextVar: its row would dedupe against an unrelated transcript and
pollute the per-conversation token/cache rollup
(``llm_usage.sql``'s ``cache_tokens`` chains consecutive rows by
``prev_request_id`` — an unrelated tiny request between conversation
rows overstates the served cache). So ``ask`` runs inside its OWN fresh
``bind_request_chain(uuid)`` — explicit id, never the ambient one —
making every ad-hoc request a clean ONE-ROW chain: full payload stored,
own ``chain_id``, no predecessor, honest rollups.

Shape per the house one-shot helpers (``compaction.compact_findings``,
``supervisor.engine.intervene_on_failure``): a duck-typed
``chat_complete`` callable is injected (unit tests pass a stub or the
dummy LLM — production wires ``provider.chat_complete``); the helper
raises a specific error and the CALLER stays fail-soft. The
:class:`~llm_agents.deep_agents.tools.ad_hoc_tools.AdHocRequestTool` is
the model-facing face of this module (mounted beside ``load_prompt``
everywhere); this module is also the direct lane for harness code.

stdlib-light module (the ``limits.py`` layering rule): pydantic +
``llm_agents._core.request_log`` at import; ``llm_config`` imports
lazily inside :meth:`AdHocLlmRequest.default`.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

from pydantic import BaseModel, ConfigDict

from llm_agents._core.request_log import bind_request_chain

logger = logging.getLogger(__name__)

# The reply's hard char bound — the prompt asks for a compact answer;
# this is the belt under it (the same idiom as COMPACT_DIGEST_CHARS).
AD_HOC_MAX_REPLY_CHARS = 4000

# The temperature a mechanical side question wants.
AD_HOC_TEMPERATURE = 0.1

# The built-in system prompt when the caller passes none: one breath —
# who the request is, that nothing else exists, that the answer must be
# self-contained and compact.
DEFAULT_SYSTEM_PROMPT = (
    "You are answering ONE standalone request in a single reply. No "
    "conversation exists around it and none will follow — everything "
    "you need is in the request text; do not refer to any prior "
    "context. Answer exactly what is asked, self-contained, compact "
    "(a few sentences unless the ask demands more), no preamble and no "
    "restatement of the question."
)


class AdHocReply(BaseModel):
    """One ad-hoc request's outcome: the whitespace-collapsed reply
    text and whether the char cap truncated it."""

    model_config = ConfigDict(frozen=True)

    text: str
    truncated: bool = False


class AdHocRequestError(RuntimeError):
    """The request produced no usable reply (empty content, or the
    provider returned a non-message shape). Caller-side fail-soft is
    the contract — this error is the thing they catch."""


class AdHocLlmRequest:
    """ONE independent LLM request lane over one injected
    ``chat_complete`` callable.

    The instance is the pinned configuration (model, default system
    prompt, temperature, reply cap); :meth:`ask` is the per-request
    entry point and builds its context fresh from its arguments every
    time. The object is stateless between asks by construction — there
    is no transcript to carry, which is the point.
    """

    def __init__(
        self,
        chat_complete: Callable[..., Awaitable[Dict[str, Any]]],
        *,
        model: Optional[str] = None,
        temperature: float = AD_HOC_TEMPERATURE,
        system_prompt: Optional[str] = None,
        max_reply_chars: int = AD_HOC_MAX_REPLY_CHARS,
    ) -> None:
        self._chat_complete = chat_complete
        self._model = model
        self._temperature = temperature
        self._system_prompt = (system_prompt or "").strip() \
            or DEFAULT_SYSTEM_PROMPT
        self._max_reply_chars = max(1, int(max_reply_chars))

    @classmethod
    async def default(
        cls,
        *,
        model: Optional[str] = None,
        temperature: float = AD_HOC_TEMPERATURE,
        system_prompt: Optional[str] = None,
        max_reply_chars: int = AD_HOC_MAX_REPLY_CHARS,
        max_output_tokens: Optional[int] = None,
    ) -> "AdHocLlmRequest":
        """The lane over the ACTIVE provider config — the composition
        shortcut for harness code calling outside any run's wiring (the
        same ``load_llm_config`` + ``build_chat_provider`` pair the
        server's ``_run_one`` uses; lazy imports keep this module
        stdlib-light at import). ``model`` wins over the config row's
        ``model_name`` the same way a RunRequest override does; with no
        row (fail-soft None) the provider's own fallback chain applies.
        """
        from llm_agents.deep_agents.llm_config import (
            LlmRuntimeConfig, build_chat_provider,
        )

        cfg = await LlmRuntimeConfig.load()
        provider = build_chat_provider(
            cfg.provider if cfg else "zhipu",
            api_key=cfg.api_key if cfg else None,
            api_url=cfg.api_url if cfg else None,
            max_output_tokens=max_output_tokens)
        return cls(
            provider.chat_complete,
            model=model or (cfg.model_name if cfg else None),
            temperature=temperature,
            system_prompt=system_prompt,
            max_reply_chars=max_reply_chars,
        )

    async def ask(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> AdHocReply:
        """ONE independent request: the *prompt* (plus *system* when
        given, else the instance's default system prompt) as a fresh
        two-message context, one ``chat_complete`` call with
        ``tools=None``, the collapsed + capped reply text back.

        Raises :class:`AdHocRequestError` on an empty/unusable reply;
        provider failures propagate (the caller fails soft). The call
        runs under its OWN fresh request-log chain — never the
        ambient one (the module docstring's chain isolation).
        """
        text = str(prompt or "").strip()
        if not text:
            raise AdHocRequestError("ad-hoc request prompt was empty")
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": (system or "").strip()
             or self._system_prompt},
            {"role": "user", "content": text},
        ]
        # Own one-row chain: an explicit fresh id (``bind_request_chain``
        # with None would REUSE an ambient chain — the inheritance this
        # module exists to avoid). The sync manager frames the await —
        # the logged row lands inside THIS chain's scope; nothing else
        # to end (the chain has exactly the one row).
        with bind_request_chain(uuid.uuid4().hex):
            message = await self._chat_complete(
                messages, model=self._model, tools=None,
                temperature=self._temperature if temperature is None
                else temperature)
        content = message.get("content") if isinstance(message, dict) \
            else None
        reply = " ".join(str(content or "").split())
        if not reply:
            raise AdHocRequestError("ad-hoc request reply was empty")
        truncated = len(reply) > self._max_reply_chars
        if truncated:
            reply = reply[:self._max_reply_chars - 1] + "…"
        return AdHocReply(text=reply, truncated=truncated)
