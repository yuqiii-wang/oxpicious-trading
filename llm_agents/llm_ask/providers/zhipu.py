"""llm_agents.llm_ask.providers.zhipu — ZhiPu BigModel plain chat provider.

Same endpoint family as the search provider in
``llm_agents.online_search_summary.providers.zhipu``, restricted to the
plain chat completion: ``POST
https://open.bigmodel.cn/api/paas/v4/chat/completions`` (Bearer auth)
with ``model`` / ``messages`` / ``stream: false`` — the payload NEVER
carries a ``tools`` key, so no web search (or any other tool) is ever
attached. The reply's ``choices[0].message.content`` is the answer;
``request_id`` / ``created`` / ``usage`` are normalized into the
``ChatCompletion``.
"""
from __future__ import annotations

import datetime
import uuid
from typing import Any, Dict, Optional, Sequence

from llm_agents.llm_ask.core.models import SHANGHAI_TZ, ChatCompletion
from llm_agents.llm_ask.providers.base import BaseLlmAskProvider


class ZhipuLlmAskProvider(BaseLlmAskProvider):
    """ZhiPu BigModel implementation (open.bigmodel.cn, glm chat models)."""

    name = "zhipu"
    API_KEY_ENV_VARS = ("GLM_ONLINE_SEARCH_KEY", "ZHIPU_API_KEY",
                        "BIGMODEL_API_KEY")

    CHAT_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    # glm-5.2 is the project default (same model the online-search agent
    # summarizes with). Override per call/CLI when needed.
    DEFAULT_MODEL = "glm-5.2"

    async def _chat_complete(
        self, messages: Sequence[Dict[str, Any]], *, model: str,
        request_id: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> ChatCompletion:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": list(messages),
            "stream": False,
            "request_id": request_id or str(uuid.uuid4()),
        }
        if temperature is not None:
            payload["temperature"] = temperature
        data = await self._post_json_async(self.CHAT_URL, payload,
                                           timeout=self.chat_timeout)
        choices = data.get("choices") or []
        message = (choices[0].get("message") or {}
                   if isinstance(choices[0], dict) else {})
        created: Optional[datetime.datetime] = None
        if isinstance(data.get("created"), int):
            created = datetime.datetime.fromtimestamp(
                data["created"], tz=SHANGHAI_TZ)
        return ChatCompletion(
            content=(message.get("content") or ""),
            request_id=data.get("request_id"),
            created=created,
            usage=data.get("usage") or {})
