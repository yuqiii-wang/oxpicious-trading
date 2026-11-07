"""llm_agents.deep_agents.llm_config — the DB-backed LLM provider config.

The deep agent's provider identity (model, chat endpoint, API key) is
UI-editable in ``static.secret_config`` (DDL database/sql/static/
secrets/, Config → Secrets tab, writes via
database/sql_writes/data_viz/api/secret_config/) — the
``secret_type = llm`` rows of the registry
(web_search credentials live beside them for the search providers).
It loads AT AGENT START — once per ``run_thread`` / CLI invocation,
never at process import and never per LLM request: an agent pins
whatever was current when it started; a config edit applies to the
NEXT run (the same next-run semantics the deep_agent_config.json limits
already have, minus the mtime cache — the DB read replaces it, and one
small SELECT per run is the whole cost).

Fail-soft like ``limits.py``: any problem — table missing, DB down, no
active row, bad blob — logs a warning and returns ``None``, and the
caller falls back to the provider class's built-in constants (model /
CHAT_URL) and the env/registry key chain (``_core.base``). A row whose
KEY fails to decrypt still yields its model/url (only the key falls
back): a rotated LLM_SECRET_KEY must not silently discard an endpoint
edit.

Secret handling: ``api_key_enc`` stores AES-256-GCM ciphertext —
``v1:<b64 iv>.<b64 ciphertext||tag>`` — written by the UI API
(data_viz/api/services/secret-box.service.ts). The shared format reader
(master key + decrypt) lives in ``llm_agents._core.secret_config``
(every provider's key fallback uses the same registry); the plaintext
exists only inside the returned config / the run's provider instance;
it is never logged and never persisted anywhere else.

stdlib-light module (the ``limits.py`` layering rule — imports nothing
from the package besides the secret reader): the DB import is lazy
inside the function, same as every other llm_agents DB consumer.
"""
from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, ConfigDict

from llm_agents._core.secret_config import SecretDecryptError, decrypt_secret_blob

logger = logging.getLogger(__name__)

# The provider label a missing row / failed load degrades to — the
# built-in chat provider (core.provider.ZhipuChatProvider.name).
_DEFAULT_PROVIDER_LABEL = "zhipu"


class LlmRuntimeConfig(BaseModel):
    """One agent start's resolved LLM identity (the active
    ``secret_type = llm`` static.secret_config row; ``provider`` is the
    row's own ``ext_llm_provider`` label — zhipu / dummy; ``api_key``
    is the DECRYPTED key or None when the row stores none — the
    caller's env/registry fallback applies)."""

    model_config = ConfigDict(frozen=True)

    provider: str
    model_name: str
    api_url: Optional[str]
    api_key: Optional[str]
    context_length: Optional[int]




    @classmethod
    async def load(cls) -> Optional["LlmRuntimeConfig"]:
        """The active provider config for THIS agent start, or None.

        One primary-pool SELECT (fresh, not cached — a UI save seconds
        before a run must apply), one row: the active ``llm`` secret
        row, most recently updated first — ANY ext_llm_provider label
        (zhipu, dummy, ...), the row picks the provider via
        :func:`build_chat_provider`. Failures degrade to None with a
        warning; a row whose key blob fails to decrypt keeps its
        model/url and only the key degrades to the caller's fallback
        chain.
        """
        try:
            from _common.db_commons import get_db_connection_async

            conn = await get_db_connection_async()
            try:
                rows = await conn.fetch(_SELECT_SQL)
            finally:
                await conn.close()
        except Exception as exc:  # noqa: BLE001 — fail-soft by contract
            logger.warning("llm_config load failed (falling back to the "
                           "provider's built-ins): %s", exc)
            return None
        if not rows:
            return None
        row = rows[0]
        api_key: Optional[str] = None
        blob = row["api_key_enc"]
        if blob:
            try:
                api_key = decrypt_secret_blob(blob)
            except SecretDecryptError as exc:
                logger.warning(
                    "llm_config api_key undecryptable (%s) — the key "
                    "falls back to env/registry, model/url still apply",
                    exc)
        return cls(
            provider=row["provider"],
            model_name=row["model_name"],
            api_url=row["api_url"],
            api_key=api_key,
            context_length=row["context_length"],
        )

_SELECT_SQL = """
SELECT c.model_name, c.context_length, c.api_url, c.api_key_enc,
       ld.label AS provider
  FROM static.secret_config c
  JOIN static.label_dim st
    ON st.domain = 'secret_type' AND st.code = c.secret_type
  JOIN static.label_dim ld
    ON ld.domain = 'ext_llm_provider' AND ld.code = c.provider
 WHERE c.is_active
   AND st.label = 'llm'
 ORDER BY c.updated_at DESC
 LIMIT 1
"""


def build_chat_provider(
    provider_label: str, *,
    api_key: Optional[str] = None,
    api_url: Optional[str] = None,
    max_output_tokens: Optional[int] = None,
):
    """Construct the chat provider a config label names — the ONE place
    the label → class mapping lives. ``zhipu`` builds the real chat
    provider; ``dummy`` builds the offline test double, in one of TWO
    forms:

    * IN-PROCESS (default — ``api_url`` empty): the canned-reply
      ``llm_agents.test.dummy_llm.DummyLlmProvider`` directly — no
      server, no network, instant;
    * HTTP-SIM (``api_url`` set — the dummy secret_config row's
      endpoint): the REAL ``ZhipuChatProvider`` transport pointed at
      the running dummy llm server
      (``python -m llm_agents.test.dummy_llm.server``, default
      http://127.0.0.1:8768) — retries, error codes, the rate-limit
      ladder and the audit rows all exercise for real. The key rides
      along as ``dummy`` (the server checks the header, not the
      secret).

    An unknown label logs a warning and degrades to zhipu (a bad
    registry row must not take a run down); provider classes import
    lazily so this module stays stdlib-only at import time (the
    limits.py layering rule)."""
    label = (provider_label or "").strip().lower() or \
        _DEFAULT_PROVIDER_LABEL
    if label == "dummy":
        if api_url and api_url.strip():
            from llm_agents.deep_agents.core.provider import (
                ZhipuChatProvider)

            return ZhipuChatProvider(
                api_key=api_key or "dummy", api_url=api_url,
                max_output_tokens=max_output_tokens)
        from llm_agents.test.dummy_llm import DummyLlmProvider

        return DummyLlmProvider()
    if label != _DEFAULT_PROVIDER_LABEL:
        logger.warning("unknown llm provider label %r — falling back to "
                       "%s", provider_label, _DEFAULT_PROVIDER_LABEL)
    from llm_agents.deep_agents.core.provider import ZhipuChatProvider

    return ZhipuChatProvider(
        api_key=api_key, api_url=api_url,
        max_output_tokens=max_output_tokens)


async def load_llm_config() -> Optional[LlmRuntimeConfig]:
    """Module-level delegate over :meth:`LlmRuntimeConfig.load` — the
    export server.py / cli.py / the probes call."""
    return await LlmRuntimeConfig.load()
