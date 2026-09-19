"""llm_agents.llm_ask — Plain LLM ask agent (no online search).

Sends one question to a registered chat provider as a PLAIN LLM request —
``chat/completions`` with no tools attached, so no web search is ever
involved — and returns the normalized answer. Architectural mirror of
``llm_agents.online_search_summary`` with the search half removed and no
persistence layer (nothing is stored; callers consume the AskResult);
the shared client plumbing lives in ``llm_agents._core``.

Package layout — two layered subpackages plus the CLI, dependencies
pointing one way (providers/cli -> core, never back):

  ``core/``      — the pure domain layer (no network, no DB); the shared
    primitives (the provider error type, SHANGHAI_TZ, the language
    labels) live in ``llm_agents._core`` and are re-exported here:
    * ``core.errors``  — LlmAskError (alias of the shared
      llm_agents._core LlmProviderError).
    * ``core.models``  — normalized, provider-independent result models
      (AskOptions / ChatCompletion / AskResult) + SHANGHAI_TZ; image
      input is the optional ``AskOptions.images`` tuple.
    * ``core.prompts`` — the default ask system prompt, its builders
      (ask_system_prompt, lang_label), and the chart-adviser prompt
      pair (adviser_system_prompt, chart_context_block).
    * ``core.vision``  — the image-capability CONFIG: which models
      accept image input per provider (exact names + the trailing-"v"
      family convention) and each provider's default vision model.
  ``providers/`` — the chat provider integrations (network):
    * ``providers.base`` — BaseLlmAskProvider: the abstract contract
      (``_chat_complete`` — plain completion, no tools) and the shared
      ``ask`` flow (images attach as image_url parts only on
      vision-capable models, else they are dropped and the ask proceeds
      text-only); the client plumbing (retrying JSON transport,
      API-key resolution, POST helpers) is inherited from the shared
      ``llm_agents._core`` layer.
    * ``providers.zhipu``  — ZhiPu BigModel plain chat completions (glm
      models; glm-4*v multimodal ones take image_url parts).
    * ``providers.registry`` — PROVIDERS / get_provider: where providers
      register — subclass the base in ``providers/``, then add here.
  ``payload.py``  — the data_viz AI Ask chart-adviser payload contract
    (read/validate/delete the Express service's payload file, build the
    adviser ask request; screenshots default the model to the
    provider's vision model).
  ``cli.py``     — argparse CLI (ask via --question or --payload-file,
    --json).

CLI::

    python -m llm_agents.llm_ask ask --question "…" [--provider zhipu] \
        [--model glm-5.2] [--lang zh] [--system "…"] [--temperature 0.3] \
        [--json]
    python -m llm_agents.llm_ask ask --payload-file … [--json]

``python -m llm_agents ask …`` dispatches here too (see
llm_agents.__main__).

The package re-exports its public surface (models, prompts, errors,
providers, registry, main) so ``from llm_agents.llm_ask import X`` keeps
working regardless of which file X lives in — depend on the facade, not
on internal paths (cli-dependent names load lazily: importing cli runs
the entry-point resource pre-check, which library importers must not
inherit).
"""
from __future__ import annotations

from llm_agents.llm_ask.core.errors import LlmAskError
from llm_agents.llm_ask.core.models import (
    SHANGHAI_TZ, AskOptions, AskResult, ChatCompletion,
)
from llm_agents.llm_ask.core.prompts import (
    LANG_LABELS, adviser_system_prompt, ask_system_prompt,
    chart_context_block, lang_label,
)
from llm_agents.llm_ask.core.vision import (
    DEFAULT_VISION_MODELS, VISION_MODELS, VISION_MODEL_SUFFIXES,
    default_vision_model, model_accepts_images,
)
from llm_agents.llm_ask.providers.base import BaseLlmAskProvider
from llm_agents.llm_ask.providers.zhipu import ZhipuLlmAskProvider
from llm_agents.llm_ask.providers.registry import PROVIDERS, get_provider

__all__ = [
    "LlmAskError",
    "AskOptions", "AskResult", "ChatCompletion", "SHANGHAI_TZ",
    "LANG_LABELS", "adviser_system_prompt", "ask_system_prompt",
    "chart_context_block", "lang_label",
    "DEFAULT_VISION_MODELS", "VISION_MODELS", "VISION_MODEL_SUFFIXES",
    "default_vision_model", "model_accepts_images",
    "BaseLlmAskProvider", "ZhipuLlmAskProvider",
    "PROVIDERS", "get_provider",
    "RESULT_MARKER", "main",
]

# cli.py carries entry-point side effects (resource pre-check + stdout
# setup), so its names are resolved lazily: library importers of this
# package never trigger them; ``from …llm_ask import main`` (the CLI
# dispatch path) does.
_CLI_ATTRS = frozenset({"main", "RESULT_MARKER"})


def __getattr__(name: str):
    if name in _CLI_ATTRS:
        from llm_agents.llm_ask import cli
        return getattr(cli, name)
    raise AttributeError(
        f"module {__name__!r} has no attribute {name!r}")
