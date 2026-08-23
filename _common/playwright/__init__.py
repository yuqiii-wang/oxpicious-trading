"""_common.playwright — Shared Playwright browser automation module.

Provides a configurable, anti-bot-aware browser launcher and common
page/DOM helpers so Playwright-based downloaders don't duplicate
browser boilerplate.

Cross-compatibility with the existing anti-bot module
(downloads._common.core.AntiBotProxy): browser fingerprint rotation
and default User-Agent / Accept headers are imported from there, so
HTTP-based and browser-based downloaders share ONE anti-bot policy.

Submodules:
  browser — BrowserConfig, launch_browser, playwright_session, sleep helper
  page    — table extraction, wait-for-data, CSV link download, CSV parse
"""

from _common.playwright.browser import (
    ANTI_DETECTION_SCRIPT,
    DEFAULT_BROWSER_ARGS,
    BrowserConfig,
    launch_browser,
    playwright_session,
    resolve_context_options,
    sleep_between_requests,
)
from _common.playwright.page import (
    NULL_TOKENS,
    clean_numeric_rows,
    clean_numeric_value,
    download_csv_via_link,
    extract_table_rows,
    page_has_table_data,
    parse_csv_rows,
    wait_for_table_data,
)

__all__ = [
    "ANTI_DETECTION_SCRIPT",
    "DEFAULT_BROWSER_ARGS",
    "BrowserConfig",
    "NULL_TOKENS",
    "clean_numeric_rows",
    "clean_numeric_value",
    "download_csv_via_link",
    "extract_table_rows",
    "launch_browser",
    "page_has_table_data",
    "parse_csv_rows",
    "playwright_session",
    "resolve_context_options",
    "sleep_between_requests",
    "wait_for_table_data",
]
