"""_common.playwright.browser — Configurable Playwright browser launcher.

Shares the anti-bot policy with the existing anti-bot module
(downloads._common.AntiBotProxy): the default User-Agent / Accept
headers and the browser fingerprint profile rotation are imported from
there, so HTTP-based and browser-based downloaders rotate through the
SAME fingerprint pool and the anti-bot policy stays defined in one place.
"""

from __future__ import annotations

import random
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Tuple

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    sync_playwright,
)

from downloads._common import (
    DEFAULT_ACCEPT,
    DEFAULT_ACCEPT_LANG,
    DEFAULT_USER_AGENT,
    random_browser_profile,
    setup_logger,
)

logger = setup_logger("playwright_common")


DEFAULT_BROWSER_ARGS: List[str] = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
]

ANTI_DETECTION_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined
    });
"""


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


def resolve_context_options(config: BrowserConfig) -> Dict[str, Any]:
    """Build playwright new_context() options from a BrowserConfig.

    Header resolution order (later wins):
      1. Shared anti-bot defaults (Accept / Accept-Language)
      2. Rotated browser fingerprint from the anti-bot module pool
         (skipped when user_agent is pinned or rotation disabled)
      3. config.extra_http_headers (site-specific custom headers)
    """
    headers: Dict[str, str] = {
        "Accept": DEFAULT_ACCEPT,
        "Accept-Language": DEFAULT_ACCEPT_LANG,
    }
    user_agent: Optional[str] = config.user_agent
    if user_agent is None and config.rotate_browser_profile:
        profile = random_browser_profile()
        user_agent = profile.get("User-Agent")
        headers.update({
            key: value for key, value in profile.items()
            if key != "User-Agent"
        })
    if user_agent is None:
        user_agent = DEFAULT_USER_AGENT
    headers.update(config.extra_http_headers)

    options: Dict[str, Any] = {
        "viewport": {
            "width": config.viewport_width,
            "height": config.viewport_height,
        },
        "user_agent": user_agent,
        "locale": config.locale,
        "extra_http_headers": headers,
        "accept_downloads": config.accept_downloads,
    }
    if config.timezone_id is not None:
        options["timezone_id"] = config.timezone_id
    return options


def launch_browser(
    playwright: Playwright,
    config: Optional[BrowserConfig] = None,
) -> Tuple[Browser, BrowserContext, Page]:
    """Launch an anti-detection Playwright browser.

    Returns (browser, context, page) with the page pre-configured:
    anti-detection init script and default timeouts applied.
    """
    cfg = config or BrowserConfig()
    engine = getattr(playwright, cfg.browser_type)
    launch_kwargs: Dict[str, Any] = {
        "headless": cfg.headless,
        "args": cfg.browser_args,
    }
    if cfg.proxy is not None:
        launch_kwargs["proxy"] = cfg.proxy
    browser = engine.launch(**launch_kwargs)

    context = browser.new_context(**resolve_context_options(cfg))
    page = context.new_page()
    page.set_default_timeout(cfg.default_timeout_ms)
    page.set_default_navigation_timeout(cfg.default_navigation_timeout_ms)
    if cfg.remove_webdriver_flag:
        page.add_init_script(ANTI_DETECTION_SCRIPT)
    return browser, context, page


@contextmanager
def playwright_session(
    config: Optional[BrowserConfig] = None,
) -> Iterator[Tuple[Browser, BrowserContext, Page]]:
    """Context manager: start sync Playwright, launch browser, yield page.

    Usage:
        with playwright_session(BrowserConfig(headless=True)) as (b, ctx, page):
            page.goto(url)

    The browser is closed and Playwright stopped on exit.
    """
    with sync_playwright() as pw:
        browser, context, page = launch_browser(pw, config)
        try:
            yield browser, context, page
        finally:
            browser.close()


def sleep_between_requests(
    base_sec: float,
    jitter_sec: float = 5.0,
    min_sec: float = 2.0,
    log: Optional[Any] = None,
) -> float:
    """Sleep base_sec ± jitter_sec (floored at min_sec) between page actions.

    Shared anti-bot pacing for browser batch loops so downloaders don't
    re-implement their own jittered sleep. Returns actual sleep seconds.
    """
    actual = max(min_sec, base_sec + random.uniform(-jitter_sec, jitter_sec))
    if log is not None:
        log.info("  Sleeping %.1fs ...", actual)
    time.sleep(actual)
    return actual
