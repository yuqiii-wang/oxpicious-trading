"""DataPipeline — ABC base class for every build & analysis entry point.

Template-method base that owns the runtime lifecycle previously
copy-pasted across every ``__main__.py`` in builds/ and analyze/:

    1. ``bootstrap_runtime()``  resource pre-check (RAM/VRAM) → silence
       warnings → cudf.pandas import hook → UTF-8 stdout. MUST run before
       the first pandas import anywhere in the process.
    2. argparse                 parser built from :meth:`add_arguments`
       (+ :meth:`apply_args` post-parse validation/derivation).
    3. ``print_build_header``   header from ``title`` + :meth:`header_fields`.
    4. ``asyncio.run(amain())`` the pipeline (:meth:`run`) + wall time.
    5. ``post_check()``         memory release + report — ALWAYS, even on
       exception / Ctrl-C / SystemExit.

Subclasses declare metadata (``title`` / ``description``) and implement
:meth:`run`. The two concrete flavors used by entry points are
:class:`builds._commons style DataBuild <_common.data_build.DataBuild>`
(source CSV → stats.* builds) and
:class:`DataAnalysis <_common.data_analysis.DataAnalysis>`
(stats.* → analysis.* analyses).

IMPORT-ORDER CONTRACT
---------------------
cudf.pandas is an IMPORT-TIME hook: it must be installed before pandas'
first import. Two equivalent layouts satisfy this; pick per file:

- **Lazy-import layout** (thin delegates): the module imports only this
  package (pandas-free) at module level; every pandas-dependent import
  lives INSIDE :meth:`run` / methods. ``execute()`` bootstraps before
  ``run()`` imports anything heavy.
- **Bootstrap-first layout** (big inline pipelines): the module calls
  ``bootstrap_runtime()`` as its FIRST executable line, then may import
  pandas-dependent modules at module level as before. ``execute()``
  detects the completed bootstrap and skips re-running it (idempotent by
  flag, so parents importing child entry modules don't double-run it).

Programmatic composition (parent pipelines embedding child pipelines,
e.g. builds.index → builds.index.baseline) no longer mutates ``sys.argv``:
construct the child class with a preset ``args`` namespace and
``await child.amain()``.
"""
from __future__ import annotations

import abc
import argparse
import asyncio
import time
import warnings

__all__ = ["bootstrap_runtime", "DataPipeline"]

_BOOTSTRAP_DONE = False


def bootstrap_runtime(gpu_mode: str = "auto") -> None:
    """One-time process runtime setup for entry points.

    Sequence (each step idempotent; the whole function is guarded by a
    module flag so parents that import child entry modules — or an
    ``execute()`` call after a module-level bootstrap — never re-run it):

      1. ``pre_check()``     exit(1) early when system RAM / GPU VRAM is
         below the required floor (before ANY heavy import).
      2. ``warnings.filterwarnings("ignore")``  silence third-party
         warnings (cudf.pandas fallback noise, pandas FutureWarnings).
      3. ``activate(gpu_mode)``  install the cudf.pandas import hook —
         must precede pandas' first import; safe (no-op) when it can't.
         ``gpu_mode`` is "auto" (default) / "on" / "off" — entry points
         with a --gpu flag pre-scan argv and pass it here.
      4. ``setup_utf8_stdout()``  Windows cp936 console fix so Chinese
         security names print correctly.
    """
    global _BOOTSTRAP_DONE
    if _BOOTSTRAP_DONE:
        return
    from _common.pre_check import pre_check

    pre_check()
    warnings.filterwarnings("ignore")
    from _common.df_utils._activate import activate

    activate(gpu_mode)
    from _common.build_commons import setup_utf8_stdout

    setup_utf8_stdout()
    _BOOTSTRAP_DONE = True


class DataPipeline(abc.ABC):
    """Runtime lifecycle for one build/analysis entry point.

    Class attributes (override in subclasses):
      title               header bar title (print_build_header).
      description         argparse description (defaults to title).
      allow_unknown_args  parse_known_args instead of parse_args — for
                          thin delegates whose pipeline module parses
                          sys.argv itself (builds.stock / builds.bond).
      component           logging file name for setup_logging(); empty =
                          don't create a base logger (module already set
                          its own).
    """

    title: str = ""
    description: str = ""
    allow_unknown_args: bool = False
    component: str = ""

    def __init__(self, args: argparse.Namespace | None = None) -> None:
        self.args: argparse.Namespace | None = args
        self.parser: argparse.ArgumentParser | None = None
        self.t0: float | None = None
        self.logger = (
            self._make_logger() if self.component else None
        )

    def _make_logger(self):
        from _common.log_setup import setup_logging

        return setup_logging(self.component)

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------
    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Declare CLI arguments (default: none)."""

    def add_max_concurrent_arg(
        self,
        parser: argparse.ArgumentParser,
        default: int = 20,
    ) -> None:
        """--max-concurrent (parallel upsert chunks; sizes the DB pool)."""
        parser.add_argument(
            "--max-concurrent", type=int, default=default,
            help="Maximum parallel upsert chunks. Each chunk acquires one "
                 "Postgres backend connection from the pool, so this also "
                 "sets the pool's max_size. Reduce if you see 'too many "
                 f"clients' errors. Default: {default}.",
        )

    def apply_args(self) -> None:
        """Post-parse validation / derivation (default: none)."""

    def resolve_sec_types(self) -> tuple:
        """(args.sec_type,) when --sec-type is given, else all sec_types."""
        st = getattr(self.args, "sec_type", None) if self.args else None
        return (st,) if st else tuple(getattr(self, "sec_types", ()) or ())

    def header_fields(self) -> dict:
        """``key: value`` lines under the header bar (default: none)."""
        return {}

    @abc.abstractmethod
    async def run(self) -> None:
        """The pipeline itself.

        Everything around it — argv parsing, runtime bootstrap, header,
        wall time, (for analyses) the DB connection lifecycle, and the
        final post_check — is owned by the base template methods.
        """

    # ------------------------------------------------------------------
    # Template methods
    # ------------------------------------------------------------------
    def build_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            description=self.description or self.title or None
        )
        self.add_arguments(parser)
        return parser

    def parse_argv(self, argv: list[str] | None = None) -> argparse.Namespace:
        """Parse ``argv`` (default: sys.argv) through the hook-built parser."""
        parser = self.build_parser()
        self.parser = parser  # exposed so apply_args() can parser.error(...)
        if self.allow_unknown_args:
            args, _unknown = parser.parse_known_args(argv)
        else:
            args = parser.parse_args(argv)
        self.args = args
        self.apply_args()
        return args

    async def amain(self) -> None:
        """Async template: header → run() → wall time.

        An empty ``title`` suppresses both header and wall time — for
        thin delegates whose pipeline module prints its own (builds.stock
        / builds.bond pipelines). (DataAnalysis overrides this to wrap
        run() in the shared DB connection lifecycle: connect before
        run(), timed-close after.)
        """
        from _common.build_commons import print_build_header, print_wall_time

        self.t0 = time.time()
        if self.title:
            print_build_header(self.title, **self.header_fields())
        await self.run()
        if self.title:
            print_wall_time(self.t0)

    def execute(self, argv: list[str] | None = None) -> None:
        """Synchronous entry point — replaces the old ``__main__`` block.

        bootstrap → (parse argv unless args were preset) →
        ``asyncio.run(amain())`` → ``post_check()`` in ``finally`` so the
        memory release + report also runs on exception / SystemExit /
        Ctrl-C, exactly like the old wrapper.
        """
        bootstrap_runtime()
        if self.args is None:
            self.parse_argv(argv)
        else:
            self.apply_args()
        from _common.post_check import post_check

        try:
            asyncio.run(self.amain())
        finally:
            post_check()

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------
    @staticmethod
    async def close_conn(conn, timeout: float = 10.0) -> None:
        """Timed graceful connection close.

        ``await conn.close()`` can stall for minutes when Postgres is
        saturating disk I/O during a heavy WAL checkpoint; bound it and
        swallow failures — the connection is about to be discarded anyway.
        """
        try:
            await asyncio.wait_for(conn.close(), timeout=timeout)
        except (asyncio.TimeoutError, Exception):
            pass

    @staticmethod
    async def close_pool(pool, timeout: float = 10.0) -> None:
        """Timed pool close with ``terminate()`` fallback (asyncpg)."""
        try:
            await asyncio.wait_for(pool.close(), timeout=timeout)
        except (asyncio.TimeoutError, Exception):
            pool.terminate()
