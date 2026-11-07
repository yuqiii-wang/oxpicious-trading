"""llm_agents._core.secret_config — the DB secret registry's Python reader.

``static.secret_config`` (DDL database/sql/static/secrets/, Config →
Secrets tab) is the successor of the project-root ``.env`` keys: the
.env was migrated INTO this table (AES-256-GCM ``api_key_enc``) and
deleted 2026-11-06. Every ``BaseLlmProvider`` resolves its key through
``get_secret`` (``base.py::_resolve_api_key`` — after the env chain,
which stays first for externally-set environments), keyed by the
provider's ``name`` + its ``SECRET_TYPE`` class var (the audit's
selected-domain contract: llm rows decode provider via
'ext_llm_provider', web_search rows via 'ext_http_source').

Caching: a small TTL (default 30s) per (secret_type, provider) — the
registry is STARTUP config (a provider resolves its key once at
construction), so the cache only collapses bursts of constructions,
never pins a stale key for long. Fail-soft: any problem — table
missing, DB down, undecryptable blob — logs a warning and returns
None (the caller raises its own no-key error).

Ciphertext format (shared with data_viz/api/services/secret-box.service,
which encrypts on UI save): ``v1:<b64 iv>.<b64 ciphertext||tag>``,
AES-256-GCM under the 32-byte ``LLM_SECRET_KEY`` from database/.env.
Plaintext exists only inside the returned value; never logged.

stdlib-only module: the DB and crypto imports are lazy inside the
functions, same as every other llm_agents DB consumer.
"""
from __future__ import annotations

import base64
import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_AUTH_TAG_BYTES = 16
_SECRET_ENV_VAR = "LLM_SECRET_KEY"
DEFAULT_TTL_SEC = 30.0


class SecretDecryptError(ValueError):
    """A stored API-key blob could not be decrypted (unrecognized
    format, or the LLM_SECRET_KEY master key is absent/wrong)."""


def _master_key() -> Optional[bytes]:
    """The 32-byte AES key from LLM_SECRET_KEY — explicit env wins,
    then database/.env (the file the DB connections already read; parsed
    directly so resolution has no import-order coupling). None (with a
    warning) when unset or not 64 hex chars."""
    hex_val = os.environ.get(_SECRET_ENV_VAR, "").strip()
    if not hex_val:
        env_path = Path(__file__).resolve().parents[2] / "database" / ".env"
        try:
            for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, _, value = line.partition("=")
                if name.strip() == _SECRET_ENV_VAR:
                    hex_val = value.strip()
                    break
        except OSError:
            return None
    if len(hex_val) == 64:
        try:
            return bytes.fromhex(hex_val)
        except ValueError:
            pass
    logger.warning(
        "LLM_SECRET_KEY missing or not 64 hex chars (env / database/.env) "
        "— stored secret_config API keys cannot be decrypted")
    return None


def decrypt_secret_blob(blob: str) -> str:
    """Decrypt one ``v1:<b64 iv>.<b64 ct||tag>`` api_key_enc blob
    (the exact bytes data_viz's secret-box.service produced). Raises
    SecretDecryptError on an unrecognized format, a missing master key,
    or an auth-tag mismatch — a wrong key can never yield bytes."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _master_key()
    if key is None:
        raise SecretDecryptError(
            f"{_SECRET_ENV_VAR} not configured — cannot decrypt the stored API key")
    version, _, payload = blob.strip().partition(":")
    if version != "v1" or not payload:
        raise SecretDecryptError("unrecognized secret blob format")
    iv_b64, _, data_b64 = payload.partition(".")
    if not iv_b64 or not data_b64:
        raise SecretDecryptError("unrecognized secret blob format")
    iv = base64.b64decode(iv_b64)
    data = base64.b64decode(data_b64)
    if len(data) <= _AUTH_TAG_BYTES:
        raise SecretDecryptError("secret blob too short")
    try:
        plain = AESGCM(key).decrypt(iv, data, None)
    except Exception as exc:  # cryptography InvalidTag — never leak bytes
        raise SecretDecryptError(f"API key decrypt failed: {type(exc).__name__}") from exc
    return plain.decode("utf-8")


_SELECT_SQL = """
SELECT c.api_key_enc
  FROM static.secret_config c
  JOIN static.label_dim st ON st.domain = 'secret_type' AND st.code = c.secret_type
  JOIN static.label_dim ld ON ld.domain = (
          CASE WHEN st.label = 'llm' THEN 'ext_llm_provider'
               ELSE 'ext_http_source' END)
       AND ld.code = c.provider
 WHERE c.is_active
   AND st.label = %s
   AND ld.label = %s
 ORDER BY c.updated_at DESC
 LIMIT 1
"""

class SecretRegistry:
    """The credential registry reader (2026-11-07 — the former module
    ``_CACHE`` global): one TTL cache per process, keyed
    (secret_type_label, provider_label). Sync (provider construction is
    sync context), fail-soft (returns None with a warning), TTL-cached
    per pair so construction bursts cost one DB read per ttl window; a
    decryption miss is cached too, so a broken key doesn't re-query per
    construction."""

    def __init__(self) -> None:
        # (secret_type_label, provider_label) -> (monotonic expiry, key
        # or None)
        self._cache: dict[tuple[str, str], tuple[float, Optional[str]]] = {}

    def get(self, provider_label: str, secret_type_label: str,
            *, ttl_sec: float = DEFAULT_TTL_SEC) -> Optional[str]:
        """The active credential's DECRYPTED key for one
        (secret_type, provider) — the newest updated_at row wins — or
        None."""
        cache_key = (secret_type_label, provider_label)
        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached is not None and cached[0] > now:
            return cached[1]
        key = self._load(provider_label, secret_type_label)
        self._cache[cache_key] = (now + ttl_sec, key)
        return key

    def _load(self, provider_label: str,
              secret_type_label: str) -> Optional[str]:
        try:
            from _common.db_commons import get_db_connection

            conn = get_db_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(_SELECT_SQL,
                                (secret_type_label, provider_label))
                    row = cur.fetchone()
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 — fail-soft by contract
            logger.warning("secret_config load failed for %s/%s: %s",
                           secret_type_label, provider_label, exc)
            return None
        if row is None or not row[0]:
            return None
        try:
            return decrypt_secret_blob(row[0])
        except SecretDecryptError as exc:
            logger.warning("secret_config api_key undecryptable for "
                           "%s/%s: %s", secret_type_label, provider_label,
                           exc)
            return None


# The process-wide registry.
SECRET_REGISTRY = SecretRegistry()


def get_secret(provider_label: str, secret_type_label: str,
               *, ttl_sec: float = DEFAULT_TTL_SEC) -> Optional[str]:
    """Module-level delegate over :meth:`SECRET_REGISTRY.get` — the
    export every provider's key fallback uses."""
    return SECRET_REGISTRY.get(provider_label, secret_type_label,
                               ttl_sec=ttl_sec)
