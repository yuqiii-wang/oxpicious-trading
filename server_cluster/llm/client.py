"""server_cluster.llm.client — the in-repo HTTP client.

The request/response side of every migration onto the host: a small
requests-based class (mirrors ``llm_agents._core.transport``'s plain
POST discipline — no fastapi/torch imports so ``_common.tokenization``
can hold one cheaply). Callers:

* ``_common.tokenization.chinese_word_segmenter`` — the segmenter's
  host-first path (:meth:`segment_batch`),
* the deep-agent decision submodule — typed decision questions to the
  jev models via :meth:`systemone` (calibrated probabilities, the
  validated ``/v1/systemone`` contract),
* OCR consumers — image files to the PaddleOCR-VL transcription model
  via :meth:`ocr` (images → text, the validated ``/v1/ocr`` contract),
* harness HTTP-sim runs — ZhipuChatProvider talks the OpenAI wire
  format itself (the URL is all it needs); this client's
  :meth:`chat_completion` is the direct-call convenience,
* operators/tests — health/stats/control and the load/offload admin.

Failures raise :class:`LocalModelHostError` (status_code carried);
callers decide the fallback — the segmenter falls back to its
in-process torch path, per the repo rule that a helper outage must
never stop a pipeline.
"""
from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

# The default endpoint — the resident host beside the MCP daemon (8765)
# / deep-agent daemon (8766). Overridable for tests/staging.
DEFAULT_BASE_URL = "http://127.0.0.1:8767"

_REPO_ROOT = Path(__file__).resolve().parents[2]


def resolve_base_url(base_url: Optional[str] = None) -> str:
    """Explicit arg → LOCAL_MODEL_HOST_URL env → the 127.0.0.1:8767
    default (a config file here would duplicate the host's own; the env
    var is the deployment knob)."""
    if base_url and base_url.strip():
        return base_url.rstrip("/")
    return os.environ.get(
        "LOCAL_MODEL_HOST_URL", DEFAULT_BASE_URL).rstrip("/")


# The serving paths whose traffic belongs in llm.llm_requests (admin /
# health / stats / control traffic stays unlogged noise).
_LOGGED_PATH_PREFIXES = (
    "/v1/systemone", "/v1/tokenization/", "/v1/ocr",
    "/v1/chat/completions", "/api/paas/v4/chat/completions")

# The provider name these rows carry (transport rows use e.g.
# "zhipu" / "dummy"; this is the resident host's counterpart).
_LOG_PROVIDER = "local-model-host"


class LocalModelHostError(RuntimeError):
    """One host request failed (transport error or non-2xx answer);
    ``status_code`` is None on transport-level failures."""

    def __init__(self, message: str,
                 status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LocalModelHostClient:
    """HTTP client for the resident local-model host (see module
    docstring). One instance per process is typical."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        *,
        connect_timeout_s: float = 3.0,
        read_timeout_s: float = 600.0,
        bearer: str = "lmh-dummy",
    ) -> None:
        self.base_url = resolve_base_url(base_url)
        self.connect_timeout_s = float(connect_timeout_s)
        self.read_timeout_s = float(read_timeout_s)
        self.bearer = bearer
        self._session: Optional[requests.Session] = None

    # ------------------------------------------------------------- plumbing
    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
        return self._session

    def _timeout(self, read_s: Optional[float]) -> tuple[float, float]:
        return (self.connect_timeout_s,
                self.read_timeout_s if read_s is None else read_s)

    def _request(
        self, method: str, path: str, *,
        json_body: Optional[Dict[str, Any]] = None,
        read_timeout_s: Optional[float] = None,
        ok_statuses: tuple[int, ...] = (200,),
    ) -> Any:
        """One Bearer-auth JSON request → parsed body. Non-expected
        statuses raise :class:`LocalModelHostError` with the body's
        error message when one is present (OpenAI / zhipu envelopes).

        Serving-path traffic (see ``_LOGGED_PATH_PREFIXES``) is logged
        to ``llm.llm_requests`` via the shared
        ``llm_agents._core.request_log`` — the same pipeline the
        llm_agents transport uses; the usage block the serving routers
        return feeds the token-consumption columns. Logging NEVER
        raises and never alters the return value."""
        url = f"{self.base_url}{path}"
        started = time.monotonic() if self._logs(path) else None
        try:
            resp = self.session.request(
                method, url, json=json_body,
                timeout=self._timeout(read_timeout_s),
                headers={"Authorization": f"Bearer {self.bearer}"})
        except requests.RequestException as exc:
            self._log_request(path, json_body, None, None, True,
                              f"transport: {exc!r}", started)
            raise LocalModelHostError(
                f"local model host unreachable at {self.base_url} "
                f"({exc!r})", status_code=None) from exc
        if resp.status_code not in ok_statuses:
            self._log_request(path, json_body, None, resp, True,
                              f"HTTP {resp.status_code}", started)
            raise LocalModelHostError(
                self._error_message(resp), status_code=resp.status_code)
        data: Any = None
        if resp.content:
            data = resp.json()
        self._log_request(path, json_body, data, resp, False, None,
                          started)
        return data

    # ------------------------------------------------------------ logging
    @staticmethod
    def _logs(path: str) -> bool:
        return path.startswith(_LOGGED_PATH_PREFIXES)

    def _log_request(
        self, path: str, payload: Optional[Dict[str, Any]],
        data: Any, resp: Optional[requests.Response], is_failed: bool,
        failure_reason: Optional[str],
        started: Optional[float],
    ) -> None:
        """Enqueue one ``llm.llm_requests`` row (best-effort — a log
        failure never touches the serving result)."""
        if started is None:
            return
        try:
            from llm_agents._core.request_log import record_request

            model = data.get("model") if isinstance(data, dict) else None
            record_request(
                _LOG_PROVIDER, f"{self.base_url}{path}", payload or {},
                model=model if isinstance(model, str) else None,
                data=data if isinstance(data, dict) else None,
                status_code=(resp.status_code
                             if resp is not None else None),
                is_failed=is_failed, failure_reason=failure_reason,
                duration_ms=(time.monotonic() - started) * 1000.0)
        except Exception:  # noqa: BLE001 — logging never breaks serving
            pass

    @staticmethod
    def _error_message(resp: requests.Response) -> str:
        try:
            data = resp.json()
        except ValueError:
            return (f"local model host returned HTTP {resp.status_code} "
                    f"for {resp.url}")
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            return (f"local model host HTTP {resp.status_code}: "
                    f"{err.get('code', '')} {err.get('message', err)}")
        if isinstance(data, dict) and "detail" in data:
            return (f"local model host HTTP {resp.status_code}: "
                    f"{data['detail']}")
        return f"local model host HTTP {resp.status_code}: {data!r}"

    # -------------------------------------------------------------- surface
    def health(self) -> Dict[str, Any]:
        return self._request("GET", "/health")

    def stats(self) -> Dict[str, Any]:
        return self._request("GET", "/stats")

    def control(self, **knobs: Any) -> Dict[str, Any]:
        """POST /control — the migrated dummy rate-limit knobs
        (fail_next / rate_limit_code / max_rpm / reset)."""
        return self._request("POST", "/control", json_body=knobs)

    def list_models(self) -> List[Dict[str, Any]]:
        data = self._request("GET", "/v1/models")
        return list(data.get("data", []))

    def model_status(self, model_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/v1/models/{model_id}")

    def load(self, model_id: str, read_timeout_s: Optional[float] = None
             ) -> Dict[str, Any]:
        """Admin load (a cold big-model load takes minutes — override
        the read timeout as needed)."""
        return self._request(
            "POST", f"/v1/models/{model_id}/load",
            read_timeout_s=read_timeout_s)

    def offload(self, model_id: str) -> Dict[str, Any]:
        return self._request("POST", f"/v1/models/{model_id}/offload")

    def offload_all(self) -> Dict[str, Any]:
        """Admin offload-all: move every loaded model's weights GPU →
        CPU now — the manual save-memory action (already-offloaded
        models are skipped; the answer lists what got offloaded)."""
        return self._request("POST", "/v1/models/offload-all")

    def unload(self, model_id: str) -> Dict[str, Any]:
        """Admin unload: drop the RAM weights of an offloaded model
        entirely (the next load cold-reads from disk)."""
        return self._request("POST", f"/v1/models/{model_id}/unload")

    def segment_batch(
        self, texts: List[str], *, aggressive: bool = False,
        batch_size: Optional[int] = None,
        read_timeout_s: float = 600.0,
    ) -> List[List[str]]:
        """POST /v1/tokenization/segment — texts -> word lists (order
        preserved; non-Chinese texts come back as ``[text]``). The
        ChineseWordSegmenter.batch contract over HTTP."""
        body: Dict[str, Any] = {"texts": list(texts), "aggressive": aggressive}
        if batch_size is not None:
            body["batch_size"] = batch_size
        data = self._request(
            "POST", "/v1/tokenization/segment", json_body=body,
            read_timeout_s=read_timeout_s)
        return list(data["words"])

    def systemone(
        self, *, questions: Dict[str, Dict[str, Any]],
        state: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
        media: Optional[List[Any]] = None,
        read_timeout_s: float = 600.0,
    ) -> Any:
        """``POST /v1/systemone`` — typed questions in, CALIBRATED
        answers out: ``noul`` → ``{noul: p_yes}``, ``choice`` →
        ``{probabilities: {label: p}}``, ``score`` → ``{score,
        level_probabilities}``. questions follows the request
        vocabulary (``{"key": {"type": ..., "instructions": ...,
        "criteria": ...}}``). ``media`` accepts, per entry, the same
        image inputs as :meth:`ocr` (path / bytes / data URL / bare
        base64) — the read conditions on the images when the served
        backend is vision-capable (a text-only backend answers 400);
        ``state`` text may reference them with ``<image:N>``
        placeholders. The answer is validated against THIS
        request's contract (one answer per key, labels == criteria,
        calibrated distributions, expected score in range) and returned
        as the typed :class:`~.data_definitions.jev_api.SystemOneResponse`;
        a host that breaks the contract raises
        :class:`LocalModelHostError` like any other failure."""
        from .data_definitions.jev_api import SystemOneRequest

        request = SystemOneRequest(
            state=state, questions=questions, model=model,
            media=self._encoded_image_inputs(media) if media else None)
        data = self._request(
            "POST", "/v1/systemone",
            json_body=request.model_dump(exclude_none=True),
            read_timeout_s=read_timeout_s)
        if not isinstance(data, dict) or \
                "model" not in data or "answers" not in data:
            raise LocalModelHostError(
                f"local model host broke the systemone contract — "
                f"expected {{model, answers}}, got: {data!r}")
        try:
            return request.validate_response(
                data["model"], data["answers"])
        except ValueError as exc:  # includes pydantic ValidationError
            raise LocalModelHostError(
                f"local model host broke the systemone contract: "
                f"{exc}") from exc

    def ocr(
        self, *, images: List[Any],
        prompt: str = "OCR:", model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        layout: bool = False,
        read_timeout_s: float = 600.0,
    ) -> Any:
        """``POST /v1/ocr`` — IMAGE FILES in, the extracted TEXT out
        (one transcription per image, submission order preserved).

        ``images`` accepts, per entry: a local file path (``str`` /
        ``Path`` — read and base64-encoded here), raw ``bytes`` of one
        of the supported raster formats, or an already-encoded data
        URL / base64 string (passed through). ``layout`` switches to
        the model's text-spotting read: each result carries
        ``elements`` — ``{bbox: (x1, y1, x2, y2), text}`` per spotted
        line in ORIGINAL pixel space (``text`` stays the clean
        reading-order transcription). Returns the typed
        :class:`~.data_definitions.ocr_api.OcrResponse` after the
        wire-shape check (one text per image, summed usage); a host
        that breaks the contract raises :class:`LocalModelHostError`
        like any other failure. Transcription is greedy by default
        (the host applies the OCR model's deterministic temp-0
        discipline when ``temperature`` is omitted)."""
        from .data_definitions.ocr_api import OcrRequest, OcrResponse

        request = OcrRequest(
            images=self._encoded_image_inputs(images), prompt=prompt, model=model,
            temperature=temperature, max_tokens=max_tokens, layout=layout)
        data = self._request(
            "POST", "/v1/ocr",
            json_body=request.model_dump(exclude_none=True),
            read_timeout_s=read_timeout_s)
        if not isinstance(data, dict) or \
                "model" not in data or "texts" not in data:
            raise LocalModelHostError(
                f"local model host broke the ocr contract — expected "
                f"{{model, texts, usage}}, got: {data!r}")
        try:
            response = OcrResponse.model_validate(data)
            request.validate_response(
                response.model, list(response.texts), response.usage)
        except ValueError as exc:  # includes pydantic ValidationError
            raise LocalModelHostError(
                f"local model host broke the ocr contract: {exc}") from exc
        return response

    @staticmethod
    def _encoded_image_inputs(images: List[Any]) -> List[Dict[str, Any]]:
        """The shared image-input encode: entries of path (``str`` /
        ``Path``, read and base64-encoded here) / raw ``bytes`` /
        already-encoded data URL or bare base64 (passed through) →
        ``OcrImageInput``-shaped dicts — the OCR and systemone ``media``
        payloads share this exact shape."""
        inputs: List[Dict[str, Any]] = []
        for image in images:
            if isinstance(image, (bytes, bytearray)):
                encoded = base64.b64encode(bytes(image)).decode("ascii")
            elif isinstance(image, (str, Path)):
                as_str = str(image)
                if as_str.lower().startswith("data:") or \
                        LocalModelHostClient._is_b64_text(as_str):
                    encoded = as_str
                else:
                    encoded = base64.b64encode(
                        Path(as_str).read_bytes()).decode("ascii")
            else:
                raise TypeError(
                    f"unsupported image input {type(image).__name__} "
                    "(expected a path, bytes, or an encoded string)")
            inputs.append({"data": encoded})
        return inputs

    @staticmethod
    def _is_b64_text(value: str) -> bool:
        """The pre-encoded-image heuristic: no path separator, no
        extension, and a base64 charset (a data URL already matched;
        anything else is treated as a file path)."""
        import re

        if "/" in value or "\\" in value or "." in value:
            return False
        return bool(re.fullmatch(r"[A-Za-z0-9+/=\s]+", value))

    def chat_completion(
        self, *, model: str, messages: List[Dict[str, Any]],
        temperature: Optional[float] = None, top_p: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stop: Optional[List[str]] = None,
        read_timeout_s: float = 600.0,
    ) -> Dict[str, Any]:
        """One non-streaming POST /v1/chat/completions envelope. (The
        openai SDK / ZhipuChatProvider can also be pointed at the host
        directly — this method is the in-repo convenience.)"""
        payload: Dict[str, Any] = {
            "model": model, "messages": messages, "stream": False}
        if temperature is not None:
            payload["temperature"] = temperature
        if top_p is not None:
            payload["top_p"] = top_p
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if stop:
            payload["stop"] = stop
        return self._request(
            "POST", "/v1/chat/completions", json_body=payload,
            read_timeout_s=read_timeout_s,
            ok_statuses=(200, 429))  # 429 bodies ARE the simulation's data
