"""server_cluster.llm — the resident local-model host.

The server cluster's FIFTH area server (the ``llm`` area; migrated from
``llm_agents.local_model_host`` 2026-11) and unlike the four jobs areas
it serves its OWN model-host app (``create_app``), not the shared jobs
surface of ``_common.app_factory``. ONE always-on FastAPI process
(127.0.0.1:8767, beside the MCP daemon 8765 / deep-agent daemon 8766 /
former dummy-llm server 8768) that owns every LOCAL AI model of this
repo — three backend kinds:

  * ``dummy-1`` (kind ``dummy``) — the offline chat-completions
    simulation (``test.dummy_llm.DummyLlmProvider`` gherkin feature
    replies + rate-limit error injection; migrated from the former
    standalone ``test.dummy_llm.server``),
  * ``word-segmenter-zh`` (kind ``tokenization``) — the StructBERT
    Chinese word segmenter (``_common.tokenization``), the tokenization
    LLM served as request/response,
  * ``onejev-1.6-4b`` (kind ``jev``) — the OmniJev OneJev1.6-4B Jev
    decision model (a "System One" model: typed questions → calibrated
    probabilities, fine-tuned from Qwen3.5-4B) via transformers.

Request surface — the standard OpenAI chat-completions API, the
standard Jev decision API, and the host's own management endpoints:

  POST /v1/chat/completions            (+ ZhiPu mirror /api/paas/v4/...)
  POST /v1/systemone                   (Jev: noul / choice / score →
                                        calibrated answers)
  GET  /v1/models  |  GET /v1/models/{id}
  POST /v1/models/{id}/load | /offload | /unload
                                        (admin: lifecycle API)
  POST /v1/models/offload-all           (admin: manual save-memory —
                                        every loaded model's weights
                                        GPU -> CPU in one call)
  POST /v1/tokenization/segment        (word segmentation batches)
  GET  /health | /stats  |  POST /control (dummy rate-limit knobs)

Model management lives in :mod:`.manager`: one registry, per-model
lifecycle actions serialized on a lock, ONE idle-policy watchdog driving
the TWO-tier policy (default 1800 s idle → weights move GPU to CPU, RAM
keeps them; a further 3600 s idle → the RAM weights drop entirely and
the next request cold-loads from disk; either tier rehydrates
transparently) and a warm-up pass at startup. The
weights live under this package's gitignored ``local_models/`` subdir.

Layout (the official FastAPI structure, right-sized): ``app.py`` is the
composition root (``create_app``); ``routers/`` splits the HTTP layer by
resource (chat / models_admin / tokenization / ops, thin — wire concerns
only) over the service layer ``manager.py`` + ``backends/``; the typed
payloads live in ``data_definitions/``; in-repo HTTP clients use
``client.py``. In-repo consumers of the segmenter are host-first
automatically (``_common.tokenization.chinese_word_segmenter`` —
in-process torch fallback keeps pipelines alive when the host is down);
``SearchIndexer`` callers need no change at all.

Run: ``python -m server_cluster.llm`` (--host --port --config
--idle-offload-s --idle-unload-s --no-warmup). ALWAYS-ON: registered in
``_common/service_heartbeat.py`` ``MONITORED_SERVICES`` — the stream
watchdog starts/restarts it like the other daemons; ``main()``
registers the heartbeat (the app-factory form stays heartbeat-free).
"""
from __future__ import annotations

# The OpenAI-style base path and the default port — mirrored by
# .client.LocalModelHostClient and the seeded dummy secret_config row.
API_PREFIX = "/v1"
DEFAULT_PORT = 8767

# The cluster area key — also this package's name within server_cluster
# (mirrors the four jobs areas' __init__ shape).
AREA = "llm"

# The heartbeat/service name — the module path itself (the watchdog's
# spawn contract; MONITORED_SERVICES lists this exact string).
SERVICE = "server_cluster.llm"


def create_app(config=None, manager=None):
    """The app-factory form (uvicorn programmatic runs, TestClient) —
    imports :mod:`.app` lazily so ``import server_cluster.llm``
    (e.g. for SERVICE/client constants) stays fastapi-free. See
    ``app.create_app`` for the parameters; the class-first shell
    (config + manager + app) is ``server.LocalModelHost``."""
    from .app import create_app as _create_app

    return _create_app(config=config, manager=manager)


__all__ = ["AREA", "API_PREFIX", "DEFAULT_PORT", "SERVICE", "create_app"]
