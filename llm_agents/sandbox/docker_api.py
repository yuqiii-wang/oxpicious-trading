"""llm_agents.sandbox.docker_api — the middleware's HTTP client of the
dockerized py-sandbox runner (llm_agents/sandbox/runner.py inside the
oxpicious-py-sandbox container).

One thin stdlib-urllib client: no repo imports beyond the error type,
so the middleware daemon stays network-only (light resource gate — no
pandas/cudf in this module graph).

SELF-HEAL: the container is kept live by the compose ``restart:
unless-stopped`` policy + its HEALTHCHECK; this client adds the
last-mile heal — when the runner API is unreachable it best-effort runs
``docker start oxpicious-py-sandbox`` (the docker CLI is available in
the repo's WSL distro), WAITS for /health to answer (the published
port accepts connections before the python server binds), and retries
once, so a hand-stopped container (e.g. `docker stop` for maintenance)
comes back on the next tool call without waiting for anyone.
Everything here fails soft into SandboxError text the MCP tools return
as {"error": …}.
"""
from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

DEFAULT_SANDBOX_URL = "http://127.0.0.1:8788"
CONTAINER_NAME = "oxpicious-py-sandbox"

# The HTTP read timeout must cover the longest allowed script run
# (runner MAX_RUN_TIMEOUT_S = 600) plus scheduling slack.
CONNECT_TIMEOUT_S = 10.0
READ_TIMEOUT_S = 660.0


class SandboxError(RuntimeError):
    """One failed sandbox operation — message text reaches the LLM."""


def _flatten_error(exc: Exception) -> str:
    """An HTTP error response's {"error": …} body when present, else the
    exception text — the runner's messages are the useful ones."""
    if isinstance(exc, urllib.error.HTTPError):
        try:
            doc = json.loads(exc.read().decode("utf-8", errors="replace"))
            if isinstance(doc, dict) and doc.get("error"):
                return str(doc["error"])
        except Exception:  # noqa: BLE001 — fall through to the repr
            pass
    return f"{type(exc).__name__}: {exc}"


class SandboxDockerClient:
    """Call the container's runner API; self-heal the container once."""

    def __init__(self, base_url: str = DEFAULT_SANDBOX_URL,
                 container: str = CONTAINER_NAME) -> None:
        self.base_url = base_url.rstrip("/")
        self._container = container

    # -- one request -----------------------------------------------------

    def _request(self, method: str, path: str,
                 body: Optional[Dict[str, Any]] = None,
                 query: Optional[Dict[str, Any]] = None,
                 _retry: bool = True) -> Any:
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(
                {k: v for k, v in query.items() if v is not None})
        data = (json.dumps(body, ensure_ascii=False).encode("utf-8")
                if body is not None else None)
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={"Content-Type": "application/json; charset=utf-8"})
        try:
            with urllib.request.urlopen(
                    req, timeout=READ_TIMEOUT_S) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise SandboxError(_flatten_error(exc)) from None
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            cause = _flatten_error(exc)
            if _retry and self._ensure_container():
                return self._request(method, path, body, query,
                                     _retry=False)
            raise SandboxError(
                f"py-sandbox container unreachable at {self.base_url} "
                f"({cause}); docker start "
                f"{self._container} did not fix it — see `docker ps` / "
                "`docker logs oxpicious-py-sandbox`") from None

    def _ensure_container(self) -> bool:
        """Last-mile heal: ``docker start`` the container, then WAIT for
        the runner API to answer /health (the published port accepts
        connections the moment the container starts, but the python
        server needs a few seconds to bind — an immediate retry gets a
        connection reset). True when the container answered."""
        try:
            proc = subprocess.run(
                ["docker", "start", self._container],
                capture_output=True, text=True, timeout=60.0)
        except (OSError, subprocess.SubprocessError):
            return False  # no docker CLI — restart policy is the guard
        if proc.returncode != 0:
            return False
        deadline = time.monotonic() + 45.0
        probe = urllib.request.Request(self.base_url + "/health")
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(probe, timeout=3.0):
                    return True
            except urllib.error.HTTPError:
                return True  # serving HTTP — good enough
            except (urllib.error.URLError, OSError, TimeoutError):
                time.sleep(1.0)
        return False

    # -- the runner API --------------------------------------------------

    def health(self) -> Dict[str, Any]:
        return self._request("GET", "/health")

    def new_session(self) -> Dict[str, Any]:
        return self._request("POST", "/new_session", body={})

    def write_file(self, session: str, path: str,
                   content: str) -> Dict[str, Any]:
        return self._request("POST", "/write", body={
            "session": session, "path": path, "content": content})

    def run(self, session: str, path: str,
            timeout_s: Optional[float] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {"session": session, "path": path}
        if timeout_s is not None:
            body["timeout_s"] = timeout_s
        return self._request("POST", "/run", body=body)

    def read_file(self, session: str, path: str,
                  max_bytes: Optional[int] = None) -> Dict[str, Any]:
        return self._request("GET", "/read", query={
            "session": session, "path": path, "max_bytes": max_bytes})

    def list_files(self, session: str) -> Any:
        return self._request("GET", "/list", query={"session": session})
