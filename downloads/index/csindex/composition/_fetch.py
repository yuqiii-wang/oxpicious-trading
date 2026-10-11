"""HTTP fetch for csindex.com.cn closeweight xls."""
from __future__ import annotations

from typing import Optional

import requests

from downloads._common import (
    MIN_VALID_BYTES,
    AntiBotProxy,
)

from ._config import CLOSEWEIGHT_URL_TEMPLATE, SERVICE, logger


def fetch_closeweight_xls(
    session: requests.Session,
    index_code: str,
    proxy: Optional[AntiBotProxy] = None,
) -> Optional[bytes]:
    """Download the closeweight xls for the given index code.

    Returns raw bytes on success, None on failure.
    """
    if proxy is None:
        # Pacing (sleep/jitter/timeouts/toggles) from the service's
        # external_request_config JSON row.
        proxy = AntiBotProxy(service=SERVICE)

    url = CLOSEWEIGHT_URL_TEMPLATE.format(index_code=index_code)

    resp = proxy.get(
        session,
        url,
        logger=logger,
        log_tag=f"[dl {index_code}]",
    )
    if resp is None:
        return None
    if len(resp.content) < MIN_VALID_BYTES:
        logger.warning("[dl %s] content too small (%d bytes)", index_code, len(resp.content))
        return None
    return resp.content
