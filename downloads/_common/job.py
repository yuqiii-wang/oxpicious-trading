"""DownloadJob — base class for the downloads.* entry points.

The downloads package's counterpart of the global ``DataPipeline`` ABC:
the runtime lifecycle the ``__main__.py`` orchestrators previously
duplicated as module functions (argparse parse → heartbeat registration →
optional single-instance lock → the fetch cycle), now a template method
over subclass hooks. Unlike DataPipeline this base makes NO runtime
posture changes for its callers — no cudf bootstrap, no GPU/RAM
pre-check, no async DB lifecycle: download jobs keep their sync psycopg
stack, their ``single_instance_lock``, and their in-``run()`` header /
wall-time prints exactly as before.

Subclass shape (class attrs + hooks)::

    class MsciA50Download(DownloadJob):
        title = "DOWNLOAD HKEX MSCI A50 FUTURES DAILY ..."   # optional
        component = "hkex_mca"        # setup_logger name
        service = SERVICE             # single_instance_lock name ("" = none)

        def add_arguments(self, parser) -> None: ...
        def apply_args(self) -> None: ...   # post-parse validation/derivation
        def run(self) -> None: ...          # the fetch cycle (sync)

    if __name__ == "__main__":
        MsciA50Download().execute()

``execute()`` = build parser → parse → ``apply_args`` → lazy
``autoregister()`` (heartbeat + run audit, AFTER the parse so --help /
bad-argv exits are not audited as runs — the ordering every downloads
entry already had) → when ``service`` is set, hold
``downloads._common.single_instance_lock(SERVICE)`` around ``run()``
(the overlapping-invocation RuntimeError logs a skip warning, never a
crash) → ``run()``.
"""
from __future__ import annotations

import argparse
import time

__all__ = ["DownloadJob"]


class DownloadJob:
    """Runtime lifecycle + argparse plumbing for one download entry point.

    Class attributes (override in subclasses):
      title       optional header bar title — most jobs print their own
                  header inside run() (dynamic per-mode fields) and keep
                  this empty.
      description argparse description (defaults to title).
      component   setup_logger file name; empty = self.logger stays None
                  (run() uses its own module logger).
      service     single_instance_lock name; empty = no lock.
    """

    title: str = ""
    description: str = ""
    component: str = ""
    service: str = ""

    def __init__(self, args: argparse.Namespace | None = None) -> None:
        self.args: argparse.Namespace | None = args
        self.parser: argparse.ArgumentParser | None = None
        self.t0: float | None = None
        self.logger = self._make_logger() if self.component else None

    def _make_logger(self):
        from downloads._common.net import setup_logger

        return setup_logger(self.component)

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------
    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Declare CLI arguments (default: none)."""

    def apply_args(self) -> None:
        """Post-parse validation / derivation (default: none)."""

    def run(self) -> None:
        """The fetch cycle itself — sync, exactly the former main() /
        run_scheduled(args) body. Argparse parsing, heartbeat
        registration, the single-instance lock and the logger are owned
        by the base template methods."""
        raise NotImplementedError

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
        args = parser.parse_args(argv)
        self.args = args
        self.apply_args()
        return args

    def execute(self, argv: list[str] | None = None) -> None:
        """Synchronous entry point — replaces the old ``main()`` wrapper.

        parse → lazy ``autoregister()`` → optional single-instance lock →
        ``run()``. The heartbeat import stays lazy (the cheap-import
        convention of the downloads entries) and registration happens
        AFTER the parse so ``--help`` / bad-argv exits are not audited as
        runs.
        """
        if self.args is None:
            self.parse_argv(argv)
        else:
            self.apply_args()
        from _common.service_heartbeat import autoregister  # lazy: keeps imports cheap

        autoregister()  # heartbeat + run audit (monitor.*)

        self.t0 = time.time()
        if self.service:
            from downloads._common import single_instance_lock

            try:
                with single_instance_lock(self.service):
                    self.run()
            except RuntimeError as exc:
                # Overlapping invocations (e.g. a slow run under the block
                # versus the next scheduled start): the active run wins,
                # this one no-ops — the locked jobs' existing convention.
                if self.logger:
                    self.logger.warning("%s — skipping this invocation", exc)
                else:
                    print(f"{exc} — skipping this invocation")
        else:
            self.run()
