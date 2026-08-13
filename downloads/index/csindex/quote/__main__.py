"""Entry point: ``python -m downloads.index.csindex.quote``."""
from __future__ import annotations

from .runner import download_index

if __name__ == "__main__":
    print(download_index())
