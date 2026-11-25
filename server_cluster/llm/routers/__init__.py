"""server_cluster.llm.routers — the HTTP layer, split by
resource (the official FastAPI ``routers/`` structure): each module owns
one ``APIRouter`` over a thin slice of the surface, HTTP/wire concerns
only — parsing, auth, error mapping, SSE framing — while all lifecycle
and inference logic stays in :mod:`..manager` / :mod:`..backends`.

  chat.py          POST /v1/chat/completions (+ ZhiPu mirror) — auth
                   first, model routing, envelope/SSE assembly
  jev.py           POST /v1/systemone — the standard Jev decision
                   contract (noul / choice / score)
  ocr.py           POST /v1/ocr — image transcription (image files in,
                   the extracted text out)
  models_admin.py  GET /v1/models, retrieve, POST load|offload
  tokenization.py  POST /v1/tokenization/segment
  ops.py           GET /health, GET /stats, POST /control
  deps.py          app.state accessors (get_manager/get_config) + the
                   typed backend lookups routers share
  errors.py        the OpenAI error envelope + the validation handler

``app.create_app`` includes the routers — the app factory is the only
place that knows the whole surface.
"""
from . import chat, jev, models_admin, ocr, ops, tokenization

ROUTER_MODULES = (chat, jev, ocr, models_admin, tokenization, ops)

__all__ = ["ROUTER_MODULES", "chat", "jev", "ocr", "models_admin",
           "ops", "tokenization"]
