"""BrowserConfig — data definition.

Migrated verbatim from :mod:`_common.playwright.browser` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


DEFAULT_BROWSER_ARGS: List[str] = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
]


@dataclass
class BrowserConfig:
    """Configuration for launching an anti-detection Playwright browser.

    Attributes:
        browser_type: Playwright engine name ("chromium" | "firefox" | "webkit").
        headless: Run without a visible window.
        viewport_width / viewport_height: Page viewport size.
        locale: Browser locale (e.g. "zh-CN").
        timezone_id: Optional browser timezone (e.g. "Asia/Shanghai").
        user_agent: Pin a specific User-Agent. When None and
            rotate_browser_profile is True, a fingerprint is drawn from the
            shared anti-bot profile pool (same pool as AntiBotProxy).
        extra_http_headers: Custom site-specific headers merged over the
            shared defaults (these win).
        browser_args: Chromium launch arguments.
        rotate_browser_profile: Reuse the anti-bot module's fingerprint
            rotation (User-Agent + Sec-Ch-Ua* headers) per browser launch.
        accept_downloads: Allow the context to capture file downloads.
        default_timeout_ms / default_navigation_timeout_ms: Page timeouts.
        remove_webdriver_flag: Inject the navigator.webdriver removal script.
        proxy: Optional proxy dict for the launch call.
    """

    browser_type: str = "chromium"
    headless: bool = True
    viewport_width: int = 1920
    viewport_height: int = 1080
    locale: str = "zh-CN"
    timezone_id: Optional[str] = None
    user_agent: Optional[str] = None
    extra_http_headers: Dict[str, str] = field(default_factory=dict)
    browser_args: List[str] = field(default_factory=lambda: list(DEFAULT_BROWSER_ARGS))
    rotate_browser_profile: bool = True
    accept_downloads: bool = True
    default_timeout_ms: int = 30000
    default_navigation_timeout_ms: int = 30000
    remove_webdriver_flag: bool = True
    proxy: Optional[Dict[str, str]] = None
