"""llm_agents.llm_ask.core.vision_probe — verify a chat model ACTUALLY
accepts image input before an image lands in a real prompt.

The static vision tables (:mod:`llm_agents.llm_ask.core.vision`) and
the secret registry's ``llm_modality`` declaration are HINTS — this
account's history has vision-DECLARED models that fail in practice
(glm-4.5v / glm-4v: error 1113 "no balance / resource pack"; the
2026-11-22 news-image lane needed a candidate chain for exactly this).
The provider's own answer to ONE tiny image+text probe is the truth.

:class:`VisionProbe` runs that probe (a 64×64 PNG + a one-word
instruction, greedy, bounded wait) and caches the verdict per model id
for the owning instance's lifetime — an in-flight run must not re-probe
per image, while a FRESH process re-probes by design (the capability is
the provider's live state — balance, model rollout — not a durable
fact). Every call site is fail-soft: an unverified model means "pick
the next candidate", never an exception.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# The probe's own ceiling — a REASONING model burns tokens before the
# one-word answer (glm-5.3-flash probed EMPTY at 512 and would have
# been falsely rejected), and the wait must stay bounded (the probe
# gates the whole lane; a hung provider falls through to the next
# candidate).
PROBE_MAX_TOKENS = 2048
PROBE_TIMEOUT_S = 60.0


class VisionProbe:
    """Live multimodality verification for one provider endpoint.

    Constructed with the endpoint's identity (the secret registry's
    resolved provider label + key + url — the same values
    ``build_chat_provider`` takes); ``verify(model)`` returns True only
    after the model answered a real image+text request."""

    def __init__(self, *, provider_label: str = "zhipu",
                 api_key: Optional[str] = None,
                 api_url: Optional[str] = None,
                 timeout_s: float = PROBE_TIMEOUT_S) -> None:
        self.provider_label = provider_label
        self.api_key = api_key
        self.api_url = api_url
        self.timeout_s = timeout_s
        self._verdicts: Dict[str, bool] = {}

    def verified(self, model: str) -> bool:
        """Sync entry (callers ride ``asyncio.to_thread`` like every
        provider read): True only after a real image+text probe
        answered. Raises nothing."""
        if model in self._verdicts:
            return self._verdicts[model]
        try:
            verdict = asyncio.run(self._probe_async(model))
        except Exception as exc:  # noqa: BLE001 — a probe failure IS the no verdict
            logger.info(
                "vision probe: %s NOT verified (%s: %s)",
                model, type(exc).__name__, exc)
            self._verdicts[model] = False
            return False
        self._verdicts[model] = verdict
        logger.info("vision probe: %s %s", model,
                    "verified (image+text answered)" if verdict
                    else "NOT verified (empty/error reply)")
        return verdict

    async def _probe_async(self, model: str) -> bool:
        from llm_agents.deep_agents.llm_config import build_chat_provider

        provider = build_chat_provider(
            self.provider_label, api_key=self.api_key,
            api_url=self.api_url)
        messages = [
            {"role": "user", "content": [
                {"type": "text",
                 "text": "连接测试：这张图片是纯色小方块。请只回复一个字：好"},
                {"type": "image_url",
                 "image_url": {"url": self._probe_data_url()}},
            ]},
        ]
        try:
            reply = await asyncio.wait_for(
                provider.chat_complete(
                    messages, model=model, temperature=0.0,
                    max_tokens=PROBE_MAX_TOKENS),
                timeout=self.timeout_s)
        except (asyncio.TimeoutError, TimeoutError):
            return False
        content = str(reply.get("content") or "").strip()
        return bool(content)

    @staticmethod
    def _probe_data_url() -> str:
        """A deterministic 64×64 light-gray PNG — small enough for a
        free probe, big enough that no image-size guard rejects it."""
        from PIL import Image

        image = Image.new("RGB", (64, 64), "#f0f0f0")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return ("data:image/png;base64,"
                + base64.b64encode(buffer.getvalue()).decode("ascii"))
