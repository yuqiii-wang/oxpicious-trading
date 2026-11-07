"""llm_agents.sandbox.runner — the IN-CONTAINER execution API of the
dockerized Python sandbox.

Self-contained stdlib-only HTTP server (no repo imports, no third-party
packages — it must run inside the stock ``sagacient/cutepandas`` image
the Dockerfile copies it into). The llm_agents.sandbox MCP middleware
(``llm_agents/sandbox/server.py``, WSL) is the ONLY intended client: it
delegates LLM-generated files here — write a file, execute one .py
script, read results back, list the session directory. Bind to a
loopback-published port only (compose maps 127.0.0.1:8788); there is no
auth, same trust model as the repo's other localhost daemons.

Layout mirrors the old local sandbox: one directory per session under
``SANDBOX_ROOT`` (default /home/pandas/sandbox, a bind mount so session
files survive container rebuilds). Session ids are minted here as
``YYYYmmdd-HHMMSS-<hex6>``; every file path is session-relative and
contained by the same guard as before — no absolute paths, no ``..``,
resolved target must stay under the resolved session directory.

PERMISSIONS — the container IS the isolation boundary the old local
sandbox disclaimed:

  * filesystem — a script sees the container's filesystem, the sandbox
    bind mount and NOTHING of the host repo; the only host-reachable
    surface is what compose publishes (this API, loopback-only).
  * DB — READ-ONLY, enforced by the DATABASE: compose presets the
    libpq PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD env to the
    ``readonly_analytics`` role (SELECT-only, role-level
    ``default_transaction_read_only = on``), which a plain
    ``import psycopg; psycopg.connect()`` picks up (psycopg is pip
    installed into the image by the Dockerfile; PGHOST is the compose
    network's ``db`` service).
  * time & size — a run is killed at its timeout (default 120s, hard
    cap 600s), stdout/stderr capped head+tail, one write capped at
    512 KB, one read at 1 MB.

The compute env is the cutepandas stack (py3.12, pandas/numpy/scipy/
scikit-learn/statsmodels/matplotlib/polars/pyarrow/duckdb/… — see the
py-sandbox-env skill); /health reports the interpreter version so the
middleware can surface drift.

Local testing without docker: ``python runner.py --host 127.0.0.1
--port 8788 --root temp_scripts/sandbox-root`` (any python >= 3.9).

No pandas/numpy in THIS file: it moves file text, JSON and subprocess
handles only — the dataframe work happens inside the sandboxed scripts.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

SERVICE = "llm_agents.sandbox.runner"

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8788

DEFAULT_RUN_TIMEOUT_S = 120
MAX_RUN_TIMEOUT_S = 600
MAX_WRITE_BYTES = 512 * 1024
DEFAULT_READ_BYTES = 64 * 1024
MAX_READ_BYTES = 1024 * 1024
MAX_OUTPUT_CHARS = 16_000
# One request body overhead beyond the content payload (JSON escaping
# aside — the write cap is enforced on the DECODED content bytes too).
MAX_BODY_BYTES = MAX_WRITE_BYTES + 256 * 1024

# Sessions idle longer than this are reaped when a new session mints
# (the bind mount must not grow forever).
SESSION_GC_DAYS = 14.0

# Minted session ids and caller-supplied ones both match this.
_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")

# The ONLY things a sandbox script subprocess inherits (the container
# env is already minimal — PATH/LANG/PYTHON_* from the image — plus the
# compose-set PG* readonly vars; no secrets ride along). PYTHONPATH
# carries the image-baked study_kit package (/opt/study_kit — the
# Dockerfile sets it); without the passthrough a script subprocess
# would lose the entry and `import study_kit` would fail.
_ENV_PASSTHROUGH: Tuple[str, ...] = (
    "PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR", "TEMP", "TMP",
    "PYTHONPATH",
    "PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD",
)

# The standard session layout, minted with EVERY new session — the
# fixed workspace contract the tool descriptions and the backend_tech
# skills teach: scripts/ for the agent's .py files, data/ for fetched
# or intermediate files, outputs/ for final results, figures/ for
# plots. Pre-created so runtime writes never need their own
# os.makedirs (the runner does NOT auto-create parent dirs at run
# time, only at write-file time).
SESSION_LAYOUT: Tuple[str, ...] = (
    "scripts", "data", "outputs", "figures",
)


def _cap_text(text: str, cap: int = MAX_OUTPUT_CHARS) -> str:
    """Cap one output stream to head+tail with a dropped-chars marker."""
    if len(text) <= cap:
        return text
    head, tail = text[: cap * 2 // 3], text[-cap // 3:]
    dropped = len(text) - len(head) - len(tail)
    return (f"{head}\n...[output truncated — {dropped} chars "
            f"dropped]...\n{tail}")


def _utc_now_iso() -> str:
    return datetime.datetime.now(
        datetime.timezone.utc).isoformat(timespec="seconds")


class SandboxRunner:
    """Session directories plus the five operations the API exposes."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # -- session lifecycle ---------------------------------------------

    def db_note(self) -> str:
        """What ``/health`` and run results report about DB access."""
        if os.environ.get("PGUSER") == "readonly_analytics" \
                and os.environ.get("PGHOST"):
            return ("readonly_analytics role (SELECT-only), "
                    f"PGHOST={os.environ['PGHOST']}")
        return "none (no PG* preset)"

    def new_session(self) -> Dict[str, Any]:
        """Mint one session directory (with the standard layout sub
        dirs — scripts/data/outputs/figures) and reap sessions idle
        past the GC age first (best effort — a busy mount must not
        block minting)."""
        self.gc_sessions()
        session = datetime.datetime.now(
            datetime.timezone.utc).strftime("%Y%m%d-%H%M%S") \
            + "-" + uuid.uuid4().hex[:6]
        base = self.root / session
        base.mkdir(parents=True, exist_ok=True)
        for sub in SESSION_LAYOUT:
            (base / sub).mkdir(exist_ok=True)
        return {"session": session, "root": str(self.root),
                "layout": list(SESSION_LAYOUT),
                "db": self.db_note()}

    def gc_sessions(self) -> int:
        """Delete session directories whose newest mtime is older than
        SESSION_GC_DAYS; returns how many were reaped."""
        cutoff = time.time() - SESSION_GC_DAYS * 86400.0
        reaped = 0
        try:
            entries = list(self.root.iterdir())
        except OSError:
            return 0
        for entry in entries:
            try:
                if not entry.is_dir():
                    continue
                newest = max(
                    p.stat().st_mtime
                    for p in entry.rglob("*") if p.is_file())
                if newest < cutoff:
                    shutil.rmtree(entry, ignore_errors=True)
                    reaped += 1
            except OSError:
                continue  # unreadable/empty — leave it alone
        return reaped

    def _session_dir(self, session: str) -> Path:
        """Validate one caller-supplied session id; return its dir."""
        if not _SESSION_RE.match(str(session or "")):
            raise ValueError(f"{session!r}: not a valid session id")
        target = (self.root / session).resolve()
        if target.parent != self.root:
            raise ValueError(f"{session!r}: not a session directory")
        if not target.is_dir():
            raise ValueError(f"{session!r}: no such session")
        return target

    def _resolve(self, session: str, rel: str) -> Tuple[str, Path]:
        """Validate a session-relative path; return (normalized rel,
        absolute target) — the single containment guard every file
        operation goes through (absolute paths, '..' and anything
        resolving outside the session directory are rejected)."""
        base = self._session_dir(session)
        text = str(rel or "").strip().replace("\\", "/")
        if not text:
            raise ValueError("a sandbox-relative path is required")
        pure = PurePosixPath(text)
        if pure.is_absolute():
            raise ValueError(f"{rel!r}: the path must be RELATIVE to "
                             "the session directory")
        if ".." in pure.parts:
            raise ValueError(f"{rel!r}: '..' segments are not allowed")
        target = (base / Path(*pure.parts)).resolve()
        if target != base and base not in target.parents:
            raise ValueError(f"{rel!r}: the path escapes the session "
                             "directory")
        return pure.as_posix(), target

    # -- the four operations -------------------------------------------

    def write_file(self, session: str, rel: str,
                   content: str) -> Dict[str, Any]:
        if not isinstance(content, str):
            raise ValueError("'content' must be a string")
        rel_clean, target = self._resolve(session, rel)
        data = content.encode("utf-8")
        if len(data) > MAX_WRITE_BYTES:
            raise ValueError(f"content is {len(data)} bytes; the cap is "
                             f"{MAX_WRITE_BYTES}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return {"session": session, "path": rel_clean, "bytes": len(data)}

    def read_file(self, session: str, rel: str,
                  max_bytes: Optional[int] = None) -> Dict[str, Any]:
        rel_clean, target = self._resolve(session, rel)
        if not target.is_file():
            raise ValueError(f"{rel_clean!r}: no such file in the "
                             "session directory")
        data = target.read_bytes()
        cap = (DEFAULT_READ_BYTES if max_bytes is None
               else max(1, min(int(max_bytes), MAX_READ_BYTES)))
        if len(data) <= cap:
            content = data.decode("utf-8", errors="replace")
        else:
            head_n, tail_n = cap * 2 // 3, cap // 3
            content = (
                data[:head_n].decode("utf-8", errors="replace")
                + f"\n...[truncated: showing first {head_n} and last "
                  f"{tail_n} of {len(data)} bytes]...\n"
                + data[-tail_n:].decode("utf-8", errors="replace"))
        return {"session": session, "path": rel_clean, "bytes": len(data),
                "content": content}

    def list_files(self, session: str) -> List[Dict[str, Any]]:
        base = self._session_dir(session)
        rows: List[Dict[str, Any]] = []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames
                                 if d != "__pycache__")
            for name in filenames:
                p = Path(dirpath) / name
                try:
                    stat = p.stat()
                    rel = p.resolve().relative_to(base).as_posix()
                except (OSError, ValueError):
                    continue
                rows.append({
                    "path": rel,
                    "bytes": stat.st_size,
                    "modified": datetime.datetime.fromtimestamp(
                        stat.st_mtime,
                        tz=datetime.timezone.utc,
                    ).isoformat(timespec="seconds"),
                })
        rows.sort(key=lambda row: str(row["path"]))
        return rows

    def _child_env(self) -> Dict[str, str]:
        return {k: os.environ[k] for k in _ENV_PASSTHROUGH
                if k in os.environ}

    def run(self, session: str, rel: str,
            timeout_s: Optional[float] = None) -> Dict[str, Any]:
        """Execute one session .py in a subprocess: cwd = the session
        directory, passthrough env + the compose-preset read-only PG*
        vars, killed (with whatever output it produced) at the timeout.
        ``-B`` keeps __pycache__ out of the session, ``-u`` keeps stdout
        unbuffered so a killed script still reports."""
        rel_clean, target = self._resolve(session, rel)
        if target.suffix != ".py":
            raise ValueError(f"{rel_clean!r}: only .py scripts can "
                             "be executed")
        if not target.is_file():
            raise ValueError(f"{rel_clean!r}: no such file in the "
                             "session directory")
        timeout = (
            float(DEFAULT_RUN_TIMEOUT_S) if timeout_s is None
            else max(1.0, min(float(timeout_s), float(MAX_RUN_TIMEOUT_S))))
        started = time.monotonic()
        proc = subprocess.Popen(
            [sys.executable, "-B", "-u", str(target)],
            cwd=str(self._session_dir(session)),
            env=self._child_env(),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            out, err = proc.communicate(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            timed_out = True
        duration = time.monotonic() - started
        return {
            "session": session,
            "script": rel_clean,
            "exit_code": proc.returncode,
            "timed_out": timed_out,
            "duration_s": round(duration, 2),
            "db": self.db_note(),
            "stdout": _cap_text(
                (out or b"").decode("utf-8", errors="replace")),
            "stderr": _cap_text(
                (err or b"").decode("utf-8", errors="replace")),
        }

    def health(self) -> Dict[str, Any]:
        try:
            sessions = sum(
                1 for e in self.root.iterdir() if e.is_dir())
        except OSError:
            sessions = -1
        return {
            "status": "ok",
            "service": SERVICE,
            "python": sys.version.split()[0],
            "root": str(self.root),
            "sessions": sessions,
            "db": self.db_note(),
            "time_utc": _utc_now_iso(),
        }


class _Handler(BaseHTTPRequestHandler):
    """JSON-over-HTTP front for one SandboxRunner. Errors return
    {"error": …} with a 4xx/5xx status — never a traceback."""

    runner: SandboxRunner  # set by serve()
    server_version = "py-sandbox-runner/1.0"

    # -- helpers ---------------------------------------------------------

    def _send(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type",
                         "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, exc: Exception) -> None:
        self._send(status, {"error": f"{type(exc).__name__}: {exc}"})

    def _query(self, key: str) -> str:
        parsed = parse_qs(urlparse(self.path).query)
        values = parsed.get(key)
        return values[0] if values else ""

    def _body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise ValueError(
                f"request body is {length} bytes; the cap is "
                f"{MAX_BODY_BYTES}")
        raw = self.rfile.read(length)
        doc = json.loads(raw.decode("utf-8"))
        if not isinstance(doc, dict):
            raise ValueError("the request body must be a JSON object")
        return doc

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        # One terse access line per request on the container log.
        sys.stderr.write(f"[{SERVICE}] {self.address_string()} "
                         f"{fmt % args}\n")

    # -- routes ----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 — http.server API
        try:
            route = urlparse(self.path).path
            if route == "/health":
                self._send(200, self.runner.health())
            elif route == "/list":
                session = self._query("session")
                self._send(200, self.runner.list_files(session))
            elif route == "/read":
                max_bytes = self._query("max_bytes")
                result = self.runner.read_file(
                    self._query("session"), self._query("path"),
                    int(max_bytes) if max_bytes.isdigit() else None)
                self._send(200, result)
            else:
                self._send(404, {"error": f"no route {route!r}"})
        except ValueError as exc:
            self._error(400, exc)
        except Exception as exc:  # noqa: BLE001 — JSON error, no traceback
            self._error(500, exc)

    def do_POST(self) -> None:  # noqa: N802 — http.server API
        try:
            route = urlparse(self.path).path
            body = self._body()
            if route == "/new_session":
                self._send(200, self.runner.new_session())
            elif route == "/write":
                self._send(200, self.runner.write_file(
                    str(body.get("session") or ""),
                    str(body.get("path") or ""),
                    body.get("content")))
            elif route == "/run":
                timeout_s = body.get("timeout_s")
                self._send(200, self.runner.run(
                    str(body.get("session") or ""),
                    str(body.get("path") or ""),
                    float(timeout_s) if isinstance(
                        timeout_s, (int, float)) else None))
            else:
                self._send(404, {"error": f"no route {route!r}"})
        except ValueError as exc:
            self._error(400, exc)
        except Exception as exc:  # noqa: BLE001 — JSON error, no traceback
            self._error(500, exc)


def serve(host: str, port: int, root: Path) -> None:
    """Run until SIGTERM/CTRL-C — the container's PID 1."""
    runner = SandboxRunner(root)

    class _BoundHandler(_Handler):
        pass

    _BoundHandler.runner = runner
    httpd = ThreadingHTTPServer((host, port), _BoundHandler)
    print(f"[{SERVICE}] listening on {host}:{port}, root={runner.root}, "
          f"python={sys.version.split()[0]}, db={runner.db_note()}",
          file=sys.stderr, flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main() -> None:
    ap = argparse.ArgumentParser(description=SERVICE)
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--root", default=os.environ.get(
        "SANDBOX_ROOT", "/home/pandas/sandbox"),
        help="session directories' root (default $SANDBOX_ROOT)")
    args = ap.parse_args()
    serve(args.host, args.port, Path(args.root))


if __name__ == "__main__":
    main()
