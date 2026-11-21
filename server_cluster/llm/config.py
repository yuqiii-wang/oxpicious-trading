"""server_cluster.llm.config — the host's typed configuration.

Pydantic models (repo convention: typed payloads are BaseModels) with a
three-layer resolution — CODE DEFAULTS ← host_config.json ← CLI args —
so the checked-in JSON stays the operator-facing source of record and
every field still has a working default (a missing/malformed file must
never stop the resident process; same contract as the service_config
tree's tolerance in ``_common.service_heartbeat``).

The model registry is :data:`DEFAULT_MODELS`: the three known local
models (see the package docstring). ``path`` values are relative to
THIS package directory (the gitignored ``local_models/`` weights
subdir) and are resolved to absolutes by :meth:`HostConfig.resolve`.
Per-model ``idle_offload_s=None`` inherits the host default
(:data:`DEFAULT_IDLE_OFFLOAD_S` = 1800 — the GPU→CPU offload tier,
tightened from the one-hour age the word segmenter has always used),
and ``idle_unload_s=None`` inherits :data:`DEFAULT_IDLE_UNLOAD_S`
(= 3600 — the second tier: a further hour of no use on CPU drops the
weights from RAM entirely).

Env knobs (all optional):
  * ``LOCAL_MODEL_HOST_URL``       — the URL in-repo clients default to
    (the segmenter's client path; read by :mod:`.client`, not here).
  * ``LOCAL_MODEL_HOST_TOKENIZE``  — "0" disables the segmenter's
    host-first path (read by ``_common.tokenization``, not here).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PACKAGE_DIR / "host_config.json"

# The weights subdir of this package (gitignored content) — every model
# path below is relative to PACKAGE_DIR.
MODELS_DIR_NAME = "local_models"

# The resident-process offload policy, TWO tiers: after
# DEFAULT_IDLE_OFFLOAD_S idle seconds a loaded model's weights move to
# CPU (RAM keeps them; CUDA cache is emptied), and after a further
# DEFAULT_IDLE_UNLOAD_S idle seconds on CPU the weights drop from RAM
# entirely (the next use cold-loads from disk). Per-model overrides via
# ModelBackendSpec.idle_offload_s / idle_unload_s.
DEFAULT_IDLE_OFFLOAD_S = 1800.0
DEFAULT_IDLE_UNLOAD_S = 3600.0

DEFAULT_PORT = 8767


class ModelBackendSpec(BaseModel):
    """One registered model: identity, backend kind, lifecycle policy."""

    model_id: str = Field(
        description="The OpenAI `model` value routing to this backend "
        "(also the admin-API path segment).")
    kind: Literal["dummy", "tokenization", "jev"] = Field(
        description="The backend class that serves this model: "
        "``tokenization`` = the StructBERT word segmenter, ``jev`` = a "
        "Jev / System One decision model (OneJev fine-tunes), ``dummy`` "
        "= the offline provider simulation.")
    path: Optional[str] = Field(
        default=None,
        description="Model directory, relative to this package (the "
        "local_models/ weights subdir); None for path-less backends "
        "(the dummy simulation).")
    enabled: bool = Field(default=True, description="Serve at all?")
    warmup: bool = Field(
        default=True,
        description="Load (and run a tiny warm-up inference) during the "
        "host's startup warm-up pass.")
    idle_offload_s: Optional[float] = Field(
        default=None, ge=1.0,
        description="Idle seconds before weights offload to CPU; None "
        "inherits the host default (1800).")
    idle_unload_s: Optional[float] = Field(
        default=None, ge=1.0,
        description="Idle seconds after the CPU offload before the RAM "
        "weights drop entirely (next use cold-loads from disk); None "
        "inherits the host default (3600).")
    # --- torch lifecycle (tokenization / jev) --------------------------
    device: str = Field(default="cuda", description="torch device; a "
        "missing CUDA degrades to CPU at load time.")
    dtype: str = Field(default="bfloat16", description="torch dtype the "
        "weights load as (bf16 on CUDA is the repo's studied default).")
    # --- tokenization -----------------------------------------------------
    aggressive: bool = Field(default=False, description="Segmenter "
        "post-process tier: number+unit word synthesis (1830.5元).")
    batch_size: int = Field(default=16, ge=1, description="Segmenter "
        "forward-chunk size.")
    # --- jev ---------------------------------------------------------------
    max_new_tokens: int = Field(default=2048, ge=1, description="Default "
        "completion budget when the request omits max_tokens.")

    def resolved_path(self) -> Optional[Path]:
        """Absolute model dir (None for path-less backends). Relative
        paths anchor at this package dir; absolute paths pass through."""
        if self.path is None:
            return None
        p = Path(self.path)
        return p if p.is_absolute() else PACKAGE_DIR / p


# The three known local models — the checked-in registry. A custom
# host_config.json REPLACES this list wholesale (set `enabled: false` on
# an entry rather than deleting it to keep the id resolvable).
DEFAULT_MODELS: list[ModelBackendSpec] = [
    ModelBackendSpec(
        model_id="dummy-1", kind="dummy", path=None, enabled=True,
        warmup=False),
    ModelBackendSpec(
        model_id="word-segmenter-zh", kind="tokenization",
        path=f"{MODELS_DIR_NAME}/iic/"
             "nlp_structbert_word-segmentation_chinese-base",
        enabled=True, warmup=True),
    ModelBackendSpec(
        model_id="onejev-1.6-4b", kind="jev",
        path=f"{MODELS_DIR_NAME}/OmniJev/OneJev1.6-4B",
        enabled=True, warmup=True, dtype="bfloat16"),
]


class HostConfig(BaseModel):
    """The whole host: bind address, lifecycle policy, model registry."""

    host: str = Field(default="127.0.0.1",
                      description="uvicorn bind host (loopback only — "
                      "the host serves local pipelines).")
    port: int = Field(default=DEFAULT_PORT, ge=1, le=65535)
    warmup_on_start: bool = Field(
        default=True,
        description="Run the warm-up pass (load warmup-enabled models) "
        "in a background thread right after bind.")
    idle_offload_s: float = Field(
        default=DEFAULT_IDLE_OFFLOAD_S, ge=1.0,
        description="Host-default tier-1 idle age (GPU -> CPU offload; "
        "the 30-minute policy).")
    idle_unload_s: float = Field(
        default=DEFAULT_IDLE_UNLOAD_S, ge=1.0,
        description="Host-default tier-2 idle age on top of the "
        "offload (CPU RAM weights drop entirely; the further-hour "
        "policy).")
    auth_token: Optional[str] = Field(
        default=None,
        description="When set, chat requests must present exactly this "
        "Bearer value; when None any non-empty Bearer is accepted (the "
        "former dummy-llm server contract).")
    models: list[ModelBackendSpec] = Field(
        default_factory=lambda: [m.model_copy(deep=True)
                                 for m in DEFAULT_MODELS])

    @classmethod
    def load(cls, path: Optional[str | Path] = None) -> "HostConfig":
        """Defaults ← JSON file (default host_config.json beside this
        module; absent/malformed → pure defaults with a warning) — CLI
        overrides are applied on the returned instance by __main__."""
        cfg_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
        try:
            doc = json.loads(cfg_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            logger.info("local model host: no config file at %s — using "
                        "built-in defaults", cfg_path)
            return cls()
        except (OSError, ValueError) as exc:
            logger.warning("local model host: config file %s unreadable "
                           "(%r) — using built-in defaults", cfg_path, exc)
            return cls()
        try:
            return cls.model_validate(doc)
        except Exception as exc:  # noqa: BLE001 — never block the resident
            logger.warning("local model host: config file %s invalid "
                           "(%r) — using built-in defaults", cfg_path, exc)
            return cls()

    def enabled_models(self) -> list[ModelBackendSpec]:
        return [m for m in self.models if m.enabled]

    def model(self, model_id: str) -> Optional[ModelBackendSpec]:
        for m in self.models:
            if m.model_id == model_id:
                return m
        return None
