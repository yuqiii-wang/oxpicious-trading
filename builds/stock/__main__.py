"""Thin entry point for ``python -m builds.stock``.

The pipeline lives in builds.stock.pipeline (cli / discovery /
gap_detection / margin_gap / archive / writer / main). This file only
defines the :class:`StockBuild` entry class — the runtime lifecycle
(resource pre-check, cudf.pandas activation, UTF-8 stdout, post-check
memory release) is owned by the :class:`DataBuild` ABC; the header and
wall time are printed by pipeline.main() itself (empty ``title`` tells
the base template to stay out of the way). The pipeline module is
imported lazily inside :meth:`StockBuild.run` so the cudf.pandas import
hook is installed before its pandas import.
"""

from _common.data_build import DataBuild


class StockBuild(DataBuild):
    """``python -m builds.stock`` — delegates to builds.stock.pipeline."""

    allow_unknown_args = True  # pipeline.cli.parse_args() owns sys.argv

    async def run(self) -> None:
        from builds.stock.pipeline.main import main

        await main()


if __name__ == "__main__":
    StockBuild().execute()
