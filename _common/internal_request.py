"""_common.internal_request — the ONE internal HTTP request module.

Every process-to-process JSON call inside the repo's own stack — the
server_cluster areas talking to each other (the fleet snapshot's
per-area GETs, a detector's resident restart to the maintainer, the
nightly chain's orchestration runs, the non-daily backfill drive, the
daily 07:00 bring-up fire) — rides THIS module instead of re-rolling
per-client urllib boilerplate. Until 2026-11 the same request shape
(build a stdlib-urllib Request with JSON headers, urlopen with a
timeout, decode the JSON body, run a per-site except ladder that
turns failures into a fallback / an (ok, error) row / a typed domain
error) was duplicated across server_cluster with drifting timeouts
and error texts; this module is that ONE implementation.

stdlib urllib only — the fleet's no-new-dependency rule (loopback
calls need no third-party client), and no pandas import anywhere so
any pipeline can hold this module regardless of its cudf.pandas
activation order.

The contract:

* ONE failure type: :class:`InternalRequestError` — ``status_code``
  carries the HTTP status on a non-2xx answer (an ``HTTPError``) and
  is None on transport-level trouble (unreachable host, timeout,
  non-JSON body). The message names method, url and status (plus the
  error body when readable) so one log line places the failure.
* No retries, no pacing: internal loopback calls fail fast. The
  callers own their fallback contracts (the maintainer restart falls
  back to the in-process spawn, the fleet snapshot marks an area
  unreachable, the nightly chain aborts a tier) — a helper outage
  must never stop a pipeline, and a retry loop here could silently
  double-drive control POSTs.
* Callers resolve their own base urls (ClusterConfig for the cluster
  areas) — this module stays server-agnostic so ``_common`` never
  imports ``server_cluster`` (the dependency arrow points one way).

Class-first per the repo rule: :class:`InternalHttpClient` configures
and issues the requests (one per process is typical; a per-area
timeout policy pins its own instance); the module-level
``request_json`` / ``get_json`` / ``post_json`` helpers ride a shared
default instance for the call sites that need no policy of their own.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

__all__ = [
    "InternalRequestError",
    "InternalHttpClient",
    "request_json",
    "get_json",
    "post_json",
]

# The loopback default — the timeout every migrated call site used
# (15s bounds a control POST comfortably; long drives override).
_DEFAULT_TIMEOUT_S = 15.0


class InternalRequestError(RuntimeError):
    """One internal HTTP request failed (the ONE failure type this
    module raises — never a bare urllib / socket / JSON error).
    ``status_code`` carries the HTTP status of a non-2xx answer and is
    None on transport-level trouble (unreachable host, timeout,
    non-JSON body)."""

    def __init__(self, message: str,
                 status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _error_body_suffix(exc: urllib.error.HTTPError) -> str:
    """The error response's body as a short ' — body' message suffix
    (best-effort: an unreadable body never masks the status code)."""
    try:
        body = exc.read().decode("utf-8", errors="replace").strip()
    except Exception:  # noqa: BLE001 — a body read is best-effort
        return ""
    return f" — {body[:200]}" if body else ""


class InternalHttpClient:
    """The internal JSON request executor (see the module docstring).
    The instance holds only the default timeout — every call may
    override it (a minutes-long backfill drive vs a 5s fleet poll)."""

    def __init__(self, timeout_s: float = _DEFAULT_TIMEOUT_S) -> None:
        self.timeout_s = float(timeout_s)

    def get_json(self, url: str, *,
                 timeout_s: float | None = None) -> Any:
        """GET *url* → the decoded JSON body."""
        return self.request_json("GET", url, timeout_s=timeout_s)

    def post_json(self, url: str,
                  json_body: dict[str, Any] | None = None, *,
                  timeout_s: float | None = None) -> Any:
        """POST *url* (the optional *json_body* serialized as the
        request body) → the decoded JSON response body."""
        return self.request_json("POST", url, json_body=json_body,
                                 timeout_s=timeout_s)

    def request_json(self, method: str, url: str,
                     json_body: dict[str, Any] | None = None, *,
                     timeout_s: float | None = None) -> Any:
        """One JSON request → the decoded JSON body. ``json_body``
        None sends no request body (a bodyless control POST); every
        failure — HTTP status, transport, non-JSON answer — raises
        :class:`InternalRequestError` so callers catch exactly one
        exception type."""
        method = method.upper()
        effective = self.timeout_s if timeout_s is None else float(timeout_s)
        data = (json.dumps(json_body).encode("utf-8")
                if json_body is not None else None)
        req = urllib.request.Request(
            url, method=method, data=data,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=effective) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise InternalRequestError(
                f"{method} {url}: HTTP {exc.code}"
                f"{_error_body_suffix(exc)}",
                status_code=exc.code) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise InternalRequestError(
                f"{method} {url}: {type(exc).__name__}: {exc}") from exc
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise InternalRequestError(
                f"{method} {url}: non-JSON response: {exc}") from exc


# The per-process default executor the module-level helpers ride (the
# caution_log singleton shape — stateless beyond the timeout default).
_DEFAULT_CLIENT = InternalHttpClient()


def request_json(method: str, url: str,
                 json_body: dict[str, Any] | None = None, *,
                 timeout_s: float | None = None) -> Any:
    """One JSON request on the default executor (see
    :meth:`InternalHttpClient.request_json` for the contract)."""
    return _DEFAULT_CLIENT.request_json(method, url, json_body=json_body,
                                        timeout_s=timeout_s)


def get_json(url: str, *, timeout_s: float | None = None) -> Any:
    """GET *url* → the decoded JSON body (default executor)."""
    return _DEFAULT_CLIENT.get_json(url, timeout_s=timeout_s)


def post_json(url: str, json_body: dict[str, Any] | None = None, *,
              timeout_s: float | None = None) -> Any:
    """POST *url* → the decoded JSON body (default executor)."""
    return _DEFAULT_CLIENT.post_json(url, json_body=json_body,
                                     timeout_s=timeout_s)
