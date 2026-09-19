"""llm_agents.llm_ask.providers.base — Abstract provider contract.

``BaseLlmAskProvider`` defines the contract every plain-LLM provider
implements on top of the shared client plumbing
(``llm_agents._core.base.BaseLlmProvider``: API-key resolution from the
project-root ``.env``, the retrying JSON transport, and the POST
helpers). The domain half kept here:

  * ``ask`` — assemble the system+user messages (today's date anchored,
    the lang-aware default system prompt unless overridden), call the
    subclass ``_chat_complete``, and normalize the reply into an
    ``AskResult``. Optional images (``AskOptions.images``, PNG data
    URLs) are attached as multimodal ``image_url`` parts ONLY when the
    model accepts image input (``core.vision`` config); with a
    text-only model they are dropped with a warning and the ask
    proceeds text-only — image input is optional, never fatal;
  * the ``chat_timeout`` knob and ``DEFAULT_MODEL`` convention.

Subclasses implement ``_chat_complete`` — a PLAIN chat completion: no
tools, no web search, ever. Register them in
``llm_agents.llm_ask.providers.registry`` to expose them through
``get_provider`` / the CLI.
"""
from __future__ import annotations

import abc
import datetime
import logging
from typing import Any, ClassVar, Dict, Optional, Sequence

from llm_agents._core.base import BaseLlmProvider
from llm_agents.llm_ask.core.errors import LlmAskError
from llm_agents.llm_ask.core.models import (
    SHANGHAI_TZ, AskOptions, AskResult, ChatCompletion,
)
from llm_agents.llm_ask.core.prompts import ask_system_prompt
from llm_agents.llm_ask.core.vision import model_accepts_images

logger = logging.getLogger(__name__)


class BaseLlmAskProvider(BaseLlmProvider):
    """Abstract plain-LLM ask provider (chat completion, no search tools).

    Subclasses implement the transport-specific ``_chat_complete`` method;
    the shared plumbing (API key from the project ``.env``, the retrying
    JSON transport, POST helpers) is inherited from ``BaseLlmProvider``,
    and this base offers ``ask`` — the one-question-one-answer flow every
    caller uses. Register new providers in ``PROVIDERS`` to expose them
    through ``get_provider`` / the CLI.
    """

    DEFAULT_MODEL: ClassVar[Optional[str]] = None

    def __init__(self, *, api_key: Optional[str] = None,
                 chat_timeout: float = 180.0,
                 max_attempts: int = 3) -> None:
        super().__init__(api_key=api_key, max_attempts=max_attempts)
        self.chat_timeout = chat_timeout

    # -- abstract contract -----------------------------------------------
    @abc.abstractmethod
    async def _chat_complete(
        self, messages: Sequence[Dict[str, Any]], *, model: str,
        request_id: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> ChatCompletion:
        """Plain LLM completion -> normalized ChatCompletion.

        NO tools attached — this package never does online search. User
        messages may carry multimodal content parts (text + image_url)
        when ``ask`` attached images.
        """

    # -- shared ask flow ---------------------------------------------------
    async def ask(self, question: str,
                  opts: Optional[AskOptions] = None, *,
                  context: Optional[str] = None) -> AskResult:
        """Answer one question with a plain chat completion.

        Builds the system+user messages (today's Shanghai date anchored in
        the user message, ``ask_system_prompt`` unless ``opts.system``
        overrides it, optional ``context`` text between the date anchor
        and the question — the chart-adviser flow renders the plot info
        there), calls ``_chat_complete``, and returns the normalized
        ``AskResult``. ``opts.images`` (data URLs) become image parts when
        the resolved model accepts them; otherwise they are dropped.
        """
        opts = opts or AskOptions()
        opts.validate()
        model = opts.model or self.DEFAULT_MODEL
        if not model:
            raise LlmAskError(
                f"{self.name}: no chat model configured — pass model or set "
                "DEFAULT_MODEL")
        user_msg = (
            f"今天的日期是{datetime.datetime.now(SHANGHAI_TZ):%Y-%m-%d}。\n\n"
            + (f"{context}\n\n" if context else "")
            + f"用户问题：{question}")
        images = tuple(opts.images)
        if images and not model_accepts_images(self.name, model):
            logger.warning(
                "[%s] dropping %d image(s): model %s has no image input "
                "(see core/vision.py) — proceeding text-only",
                self.name, len(images), model)
            images = ()
        if images:
            user_content: Any = [
                {"type": "text", "text": user_msg},
                *[{"type": "image_url",
                   "image_url": {"url": image}} for image in images],
            ]
        else:
            user_content = user_msg
        completion = await self._chat_complete(
            [{"role": "system", "content": opts.system or ask_system_prompt(opts.lang)},
             {"role": "user", "content": user_content}],
            model=model, request_id=opts.request_id,
            temperature=opts.temperature)
        answer = completion.content.strip()
        if not answer:
            raise LlmAskError(
                f"{self.name}: chat completion returned no content "
                f"(request_id={completion.request_id})")
        logger.info("    [%s] ask model=%s chars=%d images=%d/%d "
                    "request_id=%s",
                    self.name, model, len(answer), len(images),
                    len(opts.images), completion.request_id)
        return AskResult(
            question=question, answer=answer, provider=self.name, model=model,
            request_id=completion.request_id, created=completion.created,
            usage=completion.usage)
