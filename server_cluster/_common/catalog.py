"""server_cluster._common.catalog — area/module ownership + light
validation.

The pure, import-light rules that decide whether a module belongs to an
area server (the dotted-prefix table mirrors
``server_cluster/_common/config.py`` DEFAULT_AREAS and the Express-side
``CLUSTER_BASE_URLS`` map in ``data_viz/api/services/py-runner.service.ts``
— all three must stay in sync). Modules are NEVER imported here or
anywhere in the cluster: ownership is a string prefix check, and a job's
runnable-ness is the child process's own problem (same as the watchdog's
spawn contract).

Validation is deliberately light — the same charset the UI's run-guard
(``data_viz/api/routes/monitor/run-guard.ts``) and
``_common/service_heartbeat.service_from_module_path`` enforce — so a
malformed request is a 400 at the API boundary instead of a surprise
inside a spawned process. No shell is involved (``Popen`` list argv),
so metacharacters cannot inject anything; the charset check merely
keeps argv sane and log filenames filesystem-safe.
"""
from __future__ import annotations

import re

# Segment charset shared with service_from_module_path / the service_config
# tree — applies to module path segments AND argv tokens alike.
SEGMENT_RE = re.compile(r"^[\w.,:=@+\-]+$")

# argv bounds: the UI's run-guard allows <=8 tokens; the cluster API is
# the general entry so the cap is looser, but unbounded argv stays a
# mistake (a fat-fingered list loops into one giant command line).
MAX_ARGS = 64


def owns(prefixes: list[str], module: str) -> bool:
    """True when ``module`` falls under one of the area's dotted prefixes."""
    return any(module.startswith(p) for p in prefixes)


def validate_module(module: str) -> str:
    """Raise ``ValueError`` unless the module is a plausible dotted path
    (every segment fits the service charset — the same contract
    ``_common.service_heartbeat.service_from_module_path`` enforces)."""
    if not module or not all(
            SEGMENT_RE.fullmatch(p) for p in module.split(".")):
        raise ValueError(f"module {module!r} is not a valid dotted module "
                         "path")
    return module


def validate_args(args: list[str]) -> list[str]:
    """Raise ``ValueError`` unless every argv token fits the charset and
    the list is within bounds (see module docstring)."""
    if len(args) > MAX_ARGS:
        raise ValueError(f"too many args ({len(args)} > {MAX_ARGS})")
    for arg in args:
        if not arg or not SEGMENT_RE.fullmatch(arg):
            raise ValueError(f"arg {arg!r} contains characters outside "
                             "the allowed set")
    return args
