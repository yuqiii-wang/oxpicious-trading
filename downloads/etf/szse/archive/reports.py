"""Download SZSE ETF/fund quarterly reports and extract holdings to CSV.

Workflow per ETF:
  1. POST to the annList API (JSON body) to list all fund notices.
  2. Filter to quarterly reports only (YYYY年第X季度报告), excluding
     summaries / updates / corrections.
  3. Download each quarterly-report PDF (not already cached) from
     disc.static.szse.cn via attachPath.
  4. Extract target sections to CSV (see extract.py). All six sections
     (stock- and bond-based) are extracted together; sections not present
     in the PDF produce a 0-byte placeholder CSV. An identify CSV records
     basic fund metadata and which sub-sections had content.

Anti-bot: uses LONG_SLEEP_INTERVAL (90s) between HTTP requests as the
SZSE disclosure endpoint blocks on request volume.

ETF universe source: ``stats.etf_identity`` (exchange='SZ'). The DB
stores codes as ``160812.SZ``; the bare 6-digit code is sent to the API.

Output layout::

    temps/szse_etf_reports/
      160812/
        160812_2026Q2.pdf
        160812_2026Q2_top10_holdings.csv      # stock section
        160812_2026Q2_industry_portfolio.csv  # stock section
        160812_2026Q2_asset_portfolio.csv     # shared section
        160812_2026Q2_bond_type_portfolio.csv # bond section (0-byte if N/A)
        160812_2026Q2_remaining_maturity.csv  # bond section (0-byte if N/A)
        160812_2026Q2_top10_bonds.csv         # bond section (0-byte if N/A)
        160812_2026Q2_identify.csv            # key-value metadata + summary
        ...
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from downloads._common import (
    DEFAULT_START_DATE,
    DEFAULT_TIMEOUT,
    LONG_SLEEP_INTERVAL,
    MIN_VALID_BYTES,
    AntiBotConfig,
    AntiBotProxy,
    RunStats,
    build_default_session,
    is_valid_file,
    resolve_out_dir,
    setup_logger,
)
from downloads.etf.szse.archive.extract import (
    CSV_NAMES,
    IDENTIFY_CSV_NAME,
    write_section_csvs,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ANL_LIST_URL = "https://www.szse.cn/api/disc/announcement/annList"
PDF_BASE_URL = "https://disc.static.szse.cn"
REFERER_PAGE = (
    "https://www.szse.cn/disclosure/fund/notice/index.html"
)
PAGE_SIZE = 50
MAX_PAGES = 20  # safety cap: 50 * 20 = 1000 announcements per fund

# Skip re-downloading if the latest PDF or CSV is within this age.
# Quarterly reports are published every ~3 months, so no new report
# can appear within this window. 90 days ≈ 3 months.
SKIP_WITHIN_SEC = 90 * 24 * 3600

# Quarterly-report title filter.
# Matches: "2026年第2季度报告" or "2026年第二季度报告"
# Does NOT match: "2025年年度报告" (no 第X季度), summaries, updates, etc.
RE_QUARTERLY = re.compile(r"(\d{4})\s*年\s*第\s*([一二三四1-4])\s*季度报告")

# Titles containing any of these substrings are excluded even if the
# quarterly regex matches (they are summaries / corrections, not the
# full report).
EXCLUDE_TITLE_TOKENS = ("摘要", "更新", "更正", "取消", "补充", "修订", "提示性")

_CN_QUARTER = {"一": 1, "二": 2, "三": 3, "四": 4}

logger = setup_logger("szse_etf_reports")


def _parse_start_cutoff(start_date: str) -> Tuple[int, int]:
    """Parse a YYYY-MM-DD string into (year, quarter) for report filtering.

    The quarter is derived from the month: Jan-Mar=Q1, Apr-Jun=Q2, etc.
    Reports with (year, quarter) strictly before the cutoff are skipped.
    """
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", start_date)
    if not m:
        return 0, 1  # no cutoff
    year = int(m.group(1))
    month = int(m.group(2))
    quarter = (month - 1) // 3 + 1
    return year, quarter


def _latest_expected_quarter() -> Tuple[int, int]:
    """Return the (year, quarter) of the most recent report likely available.

    Fund quarterly reports are published ~1.5 months after quarter end.
    Annual reports (Q4) are published by end of March.

    Used to decide whether to skip the annList API call: if the latest
    expected report is already cached on disk, there's no need to fetch
    the announcement list (saving a 90s anti-bot sleep per ETF).
    """
    today = date.today()
    y, m = today.year, today.month
    if m <= 3:    # Jan-Mar: Q3 of prev year (Q4 annual due by end of March)
        return y - 1, 3
    elif m <= 6:  # Apr-Jun: Q1 of current year
        return y, 1
    elif m <= 9:  # Jul-Sep: Q2 of current year
        return y, 2
    else:         # Oct-Dec: Q3 of current year
        return y, 3


def _latest_file_mtime(etf_dir: Path, etf_code: str) -> Optional[float]:
    """Return the most recent mtime of any PDF/CSV for *etf_code*, or None.

    Scans *etf_dir* for files prefixed ``{etf_code}_`` with a ``.pdf`` or
    ``.csv`` extension and returns the max st_mtime. Both real files and
    0-byte empty markers (created when annList returned no new reports)
    are counted, so an ETF that was recently checked still triggers the
    3-month skip via the marker's mtime.
    """
    if not etf_dir.is_dir():
        return None
    prefix = f"{etf_code}_"
    best: Optional[float] = None
    for f in etf_dir.iterdir():
        if not f.name.startswith(prefix):
            continue
        if f.suffix not in (".pdf", ".csv"):
            continue
        mtime = f.stat().st_mtime
        if best is None or mtime > best:
            best = mtime
    return best


def _write_empty_markers(etf_dir: Path, etf_code: str, yq: Tuple[int, int]) -> None:
    """Write 0-byte PDF + CSV marker files for the given (year, quarter).

    Created when annList returned no new quarterly reports (all cached or
    no reports at all). The marker files' mtime allows the cache-first
    skip (based on file age via :func:`_latest_file_mtime`) to skip
    annList on the next run, avoiding a 90s anti-bot sleep. After
    ``SKIP_WITHIN_SEC`` (90 days) the markers' mtime expires and annList
    will be re-queried for the next quarter's reports.
    """
    etf_dir.mkdir(parents=True, exist_ok=True)
    year, quarter = yq
    tag = f"{etf_code}_{year}Q{quarter}"
    # 0-byte PDF marker
    (etf_dir / f"{tag}.pdf").write_bytes(b"")
    # 0-byte CSV markers for every target section + the identify CSV.
    # Only file mtimes are consulted by the cache-first skip
    # (_latest_file_mtime); these markers are NOT the same as the 0-byte
    # placeholders written by real extraction (they accompany a 0-byte PDF).
    for csv_name in CSV_NAMES.values():
        (etf_dir / f"{tag}_{csv_name}").write_bytes(b"")
    (etf_dir / f"{tag}_{IDENTIFY_CSV_NAME}").write_bytes(b"")
    logger.info(
        "[%s] wrote empty markers for %sQ%d (no new reports found)",
        etf_code, year, quarter,
    )


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class QuarterlyReport:
    """A single quarterly report announcement from the annList API."""
    etf_code: str        # bare 6-digit code, e.g. "160812"
    etf_name: str        # fund name, e.g. "长盛同益LOF"
    year: int            # report year, e.g. 2026
    quarter: int         # 1-4
    title: str           # full announcement title
    publish_date: str    # YYYY-MM-DD
    attach_path: str     # path under PDF_BASE_URL, e.g. "/download/..."
    attach_id: str       # announcement uuid (unique id)

    @property
    def tag(self) -> str:
        return f"{self.etf_code}_{self.year}Q{self.quarter}"

    @property
    def pdf_filename(self) -> str:
        return f"{self.tag}.pdf"


# ---------------------------------------------------------------------------
# ETF universe (DB)
# ---------------------------------------------------------------------------

def fetch_sz_etf_codes(
    other_only: bool = True,
    include_lof: bool = True,
) -> List[Tuple[str, str]]:
    """Return distinct (code, name) for SZ ETFs.

    *code* is returned as the bare 6-digit string (suffix stripped) so it
    can be passed directly to the annList API. *name* is the most recent
    non-empty name for that code.

    Both branches filter to ETFs whose ``stats.sec_classification.is_active``
    is TRUE — i.e. ETFs with >=1 record in ``stats.etf_identity`` within the
    trailing 365 days. Delisted ETFs (no recent identity records) are
    skipped, avoiding wasteful annList API calls + 90s anti-bot sleeps
    for funds that no longer publish quarterly reports.

    When *other_only* is True (default), the universe is additionally
    restricted to ETFs whose ``sec_classification.sector_id = 'OTHER'``
    — i.e. ETFs not yet assigned to a specific sector/industry. This
    avoids downloading quarterly reports for ETFs that are already
    well-classified (BROAD, TECH, FIN, …) and focuses the download on
    the "unclassified" set whose reports are most useful for
    classification.

    When *include_lof* is True (default), all active SZSE LOFs (codes
    matching ``16____.SZ`` — Listed Open-End Funds) are added to the
    universe regardless of *other_only*. LOF quarterly reports publish
    the same holdings sections as ETF reports, so they are always worth
    downloading even when the LOF has already been sector-classified
    (e.g. BROAD/TECH). Has no effect when *other_only* is False (the
    non-OTHER branch already returns all active SZ ETFs, including LOFs).
    """
    from _common.db_commons import get_db_connection

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if other_only:
                # JOIN sec_classification to restrict to sector_id='OTHER'
                # AND is_active=TRUE (skip delisted ETFs). Use DISTINCT ON
                # (code) because an ETF may have multiple rows in
                # sec_classification (one per parent_index_code); pick the
                # primary parent when available.
                #
                # When include_lof=True, additionally include all active
                # LOFs (16xxxx.SZ codes) regardless of their sector_id —
                # LOF holdings are useful across all sectors, not just
                # OTHER. Implemented as an OR inside the LATERAL WHERE so
                # a single round-trip returns the merged universe.
                if include_lof:
                    cur.execute(
                        "SELECT i.code, i.name FROM stats.etf_identity i "
                        "JOIN LATERAL ("
                        "  SELECT code FROM stats.sec_classification c"
                        "  WHERE c.code = i.code"
                        "    AND c.type = 'etf'"
                        "    AND c.is_active = TRUE"
                        "    AND (c.sector_id = 'OTHER'"
                        "         OR i.code LIKE '16____.SZ')"
                        "  ORDER BY c.parent_index_is_primary DESC"
                        "  LIMIT 1"
                        ") sc ON TRUE "
                        "WHERE i.exchange = 'SZ' "
                        "ORDER BY i.code DESC"
                    )
                else:
                    cur.execute(
                        "SELECT i.code, i.name FROM stats.etf_identity i "
                        "JOIN LATERAL ("
                        "  SELECT code FROM stats.sec_classification c"
                        "  WHERE c.code = i.code"
                        "    AND c.type = 'etf'"
                        "    AND c.sector_id = 'OTHER'"
                        "    AND c.is_active = TRUE"
                        "  ORDER BY c.parent_index_is_primary DESC"
                        "  LIMIT 1"
                        ") sc ON TRUE "
                        "WHERE i.exchange = 'SZ' "
                        "ORDER BY i.code DESC"
                    )
            else:
                # Filter to active ETFs only (skip delisted / dead funds).
                # LOFs are already included here regardless of include_lof
                # since this branch returns ALL active SZ ETFs.
                cur.execute(
                    "SELECT i.code, i.name FROM stats.etf_identity i "
                    "WHERE i.exchange = 'SZ' "
                    "  AND EXISTS ("
                    "    SELECT 1 FROM stats.sec_classification c"
                    "    WHERE c.code = i.code"
                    "      AND c.type = 'etf'"
                    "      AND c.is_active = TRUE"
                    "  ) "
                    "ORDER BY i.code DESC"
                )
            rows = cur.fetchall()
    finally:
        conn.close()

    seen: Dict[str, str] = {}
    for code, name in rows:
        bare = code.split(".")[0] if "." in code else code
        if bare and bare not in seen and name:
            seen[bare] = name
    return sorted(seen.items())


# ---------------------------------------------------------------------------
# annList API — list quarterly reports
# ---------------------------------------------------------------------------

def _build_annlist_headers() -> Dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": REFERER_PAGE,
        "Origin": "https://www.szse.cn",
    }


def _parse_quarterly(
    etf_code: str,
    etf_name: str,
    rows: List[Dict[str, Any]],
) -> List[QuarterlyReport]:
    """Filter annList data rows to quarterly reports."""
    reports: List[QuarterlyReport] = []
    for row in rows:
        title = (row.get("title") or "").strip()
        # Strip nested HTML tags SZSE sometimes embeds in the title.
        title_plain = re.sub(r"<[^>]+>", "", title)
        if any(tok in title_plain for tok in EXCLUDE_TITLE_TOKENS):
            continue
        m = RE_QUARTERLY.search(title_plain)
        if not m:
            continue
        year = int(m.group(1))
        q_raw = m.group(2)
        quarter = _CN_QUARTER.get(q_raw, int(q_raw) if q_raw.isdigit() else 0)
        if quarter < 1 or quarter > 4:
            continue
        attach = row.get("attachPath") or ""
        if not attach:
            continue
        reports.append(QuarterlyReport(
            etf_code=etf_code,
            etf_name=etf_name,
            year=year,
            quarter=quarter,
            title=title_plain,
            publish_date=(row.get("publishTime") or "")[:10],
            attach_path=attach,
            attach_id=row.get("announcementId") or row.get("id") or "",
        ))
    return reports


def fetch_annlist_page(
    session: requests.Session,
    proxy: AntiBotProxy,
    etf_code: str,
    page: int,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Fetch a single annList page. Returns (rows, has_more_pages)."""
    headers = _build_annlist_headers()
    body = {
        "type": 2,
        "pageSize": PAGE_SIZE,
        "pageNum": page,
        "stock": [etf_code],
        "channelCode": ["fundinfoNotice_disc"],
    }
    resp = proxy.post(
        session, ANL_LIST_URL,
        params={"random": str(time.time())},
        data=json.dumps(body),
        headers=headers,
        timeout=DEFAULT_TIMEOUT,
        logger=logger,
        log_tag=f"[{etf_code} annList p{page}]",
    )
    if resp is None:
        logger.warning("[%s] annList page %d returned None", etf_code, page)
        return [], False
    try:
        j = resp.json()
    except ValueError:
        logger.warning("[%s] annList page %d: non-JSON response", etf_code, page)
        return [], False
    head = j[0] if isinstance(j, list) and j else j
    if not isinstance(head, dict):
        return [], False
    md = head.get("metadata") or {}
    rows = head.get("data") or head.get("rows") or []
    pagecount = int(md.get("pagecount") or 0)
    has_more = bool(rows) and (not pagecount or page < pagecount)
    logger.info(
        "[%s] annList p%d: %d rows (pagecount=%s, recordcount=%s)",
        etf_code, page, len(rows), md.get("pagecount"), md.get("recordcount"),
    )
    return rows, has_more


def fetch_quarterly_reports(
    session: requests.Session,
    proxy: AntiBotProxy,
    etf_code: str,
    etf_name: str = "",
) -> List[QuarterlyReport]:
    """Paginate the annList API and return quarterly reports for *etf_code*.

    Kept for backwards compatibility. The orchestration loop now uses
    lazy per-page download (see download_szse_etf_reports) instead of
    fetching all pages upfront.
    """
    all_rows: List[Dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        if proxy.is_blocked(ANL_LIST_URL):
            logger.warning("[%s] annList host blocked, stopping pagination", etf_code)
            break
        rows, has_more = fetch_annlist_page(session, proxy, etf_code, page)
        if not rows:
            break
        all_rows.extend(rows)
        if not has_more:
            break
    return _parse_quarterly(etf_code, etf_name, all_rows)


# ---------------------------------------------------------------------------
# PDF download
# ---------------------------------------------------------------------------

def _pdf_headers() -> Dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "application/pdf,application/octet-stream,*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.szse.cn/disclosure/listed/bulletinDetail/index.html",
    }


def is_report_cached(report: QuarterlyReport, out_dir: Path) -> bool:
    """True if the report's PDF is already downloaded (valid file on disk)."""
    return is_valid_file(out_dir / report.pdf_filename, min_bytes=MIN_VALID_BYTES)


def download_report_pdf(
    session: requests.Session,
    proxy: AntiBotProxy,
    report: QuarterlyReport,
    out_dir: Path,
    *,
    sleep_sec: Optional[float] = None,
) -> Optional[Path]:
    """Download a single quarterly-report PDF. Returns the saved path or None.

    Anti-bot: sleeps *sleep_sec* (default: proxy's configured base_sleep_sec,
    which is LONG_SLEEP_INTERVAL=90s in production) after each HTTP request
    via the AntiBotProxy. Cached PDFs skip the request and the sleep.
    """
    out_file = out_dir / report.pdf_filename
    if is_valid_file(out_file, min_bytes=MIN_VALID_BYTES):
        logger.info("[%s] PDF cached, skipping", report.tag)
        return out_file

    pdf_url = PDF_BASE_URL + report.attach_path
    # proxy.get() applies anti-bot: browser fingerprint rotation, random
    # param, and sleep with jitter after the request. sleep_sec overrides
    # the proxy's base_sleep_sec for this call.
    resp = proxy.get(
        session, pdf_url,
        headers=_pdf_headers(),
        timeout=(30, 120),
        sleep_sec=sleep_sec,
        logger=logger,
        log_tag=f"[{report.tag} pdf]",
    )
    if resp is None:
        logger.error("[%s] PDF download failed (None response)", report.tag)
        return None
    ctype = resp.headers.get("Content-Type", "")
    if "pdf" not in ctype.lower() and len(resp.content) < MIN_VALID_BYTES:
        logger.warning(
            "[%s] PDF response not pdf (ctype=%s, %d bytes)",
            report.tag, ctype, len(resp.content),
        )
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file.write_bytes(resp.content)
    if not is_valid_file(out_file, min_bytes=MIN_VALID_BYTES):
        logger.warning("[%s] PDF saved but too small (%d bytes)", report.tag, len(resp.content))
        out_file.unlink(missing_ok=True)
        return None
    logger.info("[%s] PDF downloaded (%d bytes)", report.tag, len(resp.content))
    return out_file


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _existing_csvs(pdf_path: Path) -> Dict[str, Path]:
    """Return {section_label: csv_path} for CSVs next to *pdf_path*.

    All six section CSVs are returned (including 0-byte placeholders for
    sections not present in the PDF), keyed by section label.
    """
    prefix = pdf_path.stem + "_"
    out_dir = pdf_path.parent
    existing: Dict[str, Path] = {}
    for section, csv_name in CSV_NAMES.items():
        p = out_dir / f"{prefix}{csv_name}"
        if p.exists():
            existing[section] = p
    return existing


def all_csvs_exist(pdf_path: Path) -> bool:
    """True if extraction was already completed for *pdf_path*.

    The identify CSV is written as the last step of extraction, so its
    presence (and non-zero size) is the "extraction done" marker. When it
    exists, all six section CSVs were also written (some may be 0-byte
    placeholders for sections not present in the PDF).
    """
    prefix = pdf_path.stem + "_"
    out_dir = pdf_path.parent
    identify = out_dir / f"{prefix}{IDENTIFY_CSV_NAME}"
    return identify.exists() and identify.stat().st_size > 0


def extract_report_csvs(pdf_path: Path, *, force: bool = False) -> Dict[str, Path]:
    """Extract the target sections from *pdf_path* to CSVs.

    All six section CSVs are written next to the PDF with a ``{tag}_`` prefix
    (0-byte placeholders for sections not present in the PDF), plus a
    key-value identify CSV. Returns {section_label: csv_path} for ALL sections.

    Skips re-extraction when the identify CSV already exists (see
    :func:`all_csvs_exist`), unless *force* is True.

    Also skips 0-byte PDFs (empty markers created when annList returned no
    new reports) — these are not real PDFs and would fail to parse.
    """
    if pdf_path.stat().st_size < MIN_VALID_BYTES:
        logger.info("[%s] PDF is empty marker, skipping extraction", pdf_path.stem)
        return {}
    if not force and all_csvs_exist(pdf_path):
        logger.info("[%s] CSVs already exist, skipping extraction", pdf_path.stem)
        return _existing_csvs(pdf_path)
    prefix = pdf_path.stem + "_"  # e.g. "160812_2026Q2_"
    return write_section_csvs(pdf_path, out_dir=pdf_path.parent, prefix=prefix)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def download_szse_etf_reports(
    *,
    out_root: Optional[str] = None,
    etf_codes: Optional[List[str]] = None,
    sleep_sec: float = LONG_SLEEP_INTERVAL,
    extract: bool = True,
    max_etfs: Optional[int] = None,
    other_only: bool = True,
    include_lof: bool = True,
    start_date: str = DEFAULT_START_DATE,
) -> dict:
    """Download SZSE ETF quarterly reports and extract holdings CSVs.

    Args:
        out_root: Override output root (default: temps/szse_etf_reports/).
        etf_codes: Optional list of bare 6-digit codes to process. If None,
            the SZ ETF universe is built from the DB. Explicit codes bypass
            the *other_only* and *include_lof* filters.
        sleep_sec: Sleep between HTTP requests (default: LONG_SLEEP_INTERVAL=90s).
        extract: If True, extract the 3 target sections to CSV after each PDF
            download.
        max_etfs: Limit to N ETFs (dev/testing).
        other_only: When True (default) and *etf_codes* is None, restrict the
            ETF universe to those with ``sec_classification.sector_id='OTHER'``
            — i.e. unclassified ETFs whose quarterly reports are most useful
            for classification. Ignored when *etf_codes* is given.
        include_lof: When True (default) and *etf_codes* is None, also include
            all active SZSE LOFs (codes matching ``16____.SZ``) in the
            universe regardless of *other_only*. LOF holdings are useful
            across all sectors, not just OTHER. Ignored when *etf_codes* is
            given or when *other_only* is False (the all-active branch
            already contains LOFs).
        start_date: Only download reports at or after this date
            (default: DEFAULT_START_DATE = 2020-01-01). Reports with
            (year, quarter) before the cutoff are skipped, and pagination
            stops once a page contains only reports older than the cutoff.
    """
    out_dir = resolve_out_dir(__file__, "szse_etf_reports", out_root)
    cutoff_year, cutoff_quarter = _parse_start_cutoff(start_date)

    # Build the ETF universe. Explicit --etf-code always bypasses other_only
    # and include_lof.
    if etf_codes is not None:
        universe: List[Tuple[str, str]] = [(c, "") for c in etf_codes]
    else:
        universe = fetch_sz_etf_codes(other_only=other_only, include_lof=include_lof)
    if max_etfs is not None and max_etfs > 0:
        universe = universe[:max_etfs]

    logger.info(
        "Starting SZSE ETF quarterly-report download: %d ETFs "
        "(other_only=%s, include_lof=%s), sleep=%.1fs, out=%s, start_date=%s",
        len(universe),
        other_only if etf_codes is None else "bypassed",
        include_lof if etf_codes is None else "bypassed",
        sleep_sec, out_dir, start_date,
    )

    session = build_default_session()
    proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=sleep_sec))
    stats = RunStats()
    reports_found = 0
    csvs_written = 0

    try:
        for idx, (code, name) in enumerate(universe, 1):
            if proxy.is_blocked(ANL_LIST_URL):
                logger.warning("[host-blocked] szse.cn blocked, stopping at ETF %s", code)
                break
            logger.info("== ETF %s (%s) %d/%d ==", code, name or "?", idx, len(universe))

            etf_dir = out_dir / code

            # Cache-first skip: if the latest PDF or CSV was written within
            # the last 3 months, skip the annList API call entirely.
            # Quarterly reports are published every ~3 months, so no new
            # report can be available within that window. This saves a 90s
            # anti-bot sleep per ETF. Empty marker files (created when
            # annList returned no new reports) also trigger this skip via
            # their mtime.
            latest_mtime = _latest_file_mtime(etf_dir, code)
            if latest_mtime is not None and (time.time() - latest_mtime) < SKIP_WITHIN_SEC:
                age_days = int((time.time() - latest_mtime) / 86400)
                logger.info(
                    "[%s] latest file %d days old (< %d days), skipping annList",
                    code, age_days, SKIP_WITHIN_SEC // 86400,
                )
                stats.skipped_cached += 1
                continue

            # Lazy pagination: fetch page 1, download its quarterly reports,
            # and only fetch page 2+ if ALL reports on the current page were
            # already cached (i.e. nothing new was downloaded). This avoids
            # paginating through all 6+ pages when the newest reports are
            # the only ones missing — a common case for incremental runs.
            page = 1
            n_new_total = 0
            pagination_blocked = False
            while page <= MAX_PAGES:
                if proxy.is_blocked(ANL_LIST_URL):
                    logger.warning("[%s] annList host blocked, stopping", code)
                    pagination_blocked = True
                    break

                rows, has_more = fetch_annlist_page(session, proxy, code, page)
                if not rows:
                    break

                reports = _parse_quarterly(code, name, rows)
                # Filter out reports older than the start_date cutoff.
                # annList returns newest-first, so once we see reports before
                # the cutoff, all subsequent pages will also be too old.
                in_range: List[QuarterlyReport] = []
                too_old = False
                for rpt in reports:
                    if (rpt.year, rpt.quarter) < (cutoff_year, cutoff_quarter):
                        too_old = True
                        continue
                    in_range.append(rpt)
                reports_found += len(in_range)

                if not in_range:
                    # All reports on this page are before the cutoff (or no
                    # quarterly reports at all). Stop paginating since older
                    # pages will only have older reports.
                    if too_old:
                        logger.info(
                            "[%s] page %d: all reports before cutoff %s, stopping",
                            code, page, start_date,
                        )
                    break

                # Download each in-range quarterly report on this page.
                # PDF downloads use the same anti-bot sleep (sleep_sec,
                # default LONG_SLEEP_INTERVAL=90s) as the annList API.
                n_new = 0
                for rpt in in_range:
                    if proxy.is_blocked(PDF_BASE_URL):
                        logger.warning("[host-blocked] disc.static.szse.cn blocked, stopping")
                        pagination_blocked = True
                        break
                    was_cached = is_report_cached(rpt, etf_dir)
                    pdf_path = download_report_pdf(
                        session, proxy, rpt, etf_dir, sleep_sec=sleep_sec,
                    )
                    if pdf_path is None:
                        stats.failed += 1
                        continue
                    if was_cached:
                        # PDF was already on disk — count as cached, not new.
                        stats.skipped_cached += 1
                    elif pdf_path.stat().st_size >= MIN_VALID_BYTES:
                        n_new += 1
                        stats.downloaded += 1
                        stats.files.append(str(pdf_path))
                    else:
                        stats.failed += 1
                        continue

                    if extract:
                        try:
                            written = extract_report_csvs(pdf_path)
                            # Count only CSVs with actual content
                            # (non-zero size). Empty 0-byte placeholders
                            # are expected for sections not in the PDF.
                            n_with_content = sum(
                                1 for p in written.values()
                                if p.stat().st_size > 0
                            )
                            csvs_written += n_with_content
                            logger.info(
                                "[%s] %d/%d CSV(s) with content",
                                rpt.tag, n_with_content, len(written),
                            )
                        except Exception as e:
                            logger.warning("[%s] CSV extraction failed: %s", rpt.tag, e)

                n_new_total += n_new
                logger.info(
                    "[%s] page %d: %d quarterly reports (in range), "
                    "%d newly downloaded, %d cached",
                    code, page, len(in_range), n_new,
                    len(in_range) - n_new,
                )

                # If at least one report was newly downloaded on this page,
                # stop paginating — we've caught up. Only fetch the next page
                # when everything was already cached (looking for older
                # missing reports further back in the announcement history).
                if n_new > 0:
                    break
                if not has_more:
                    break
                page += 1

            # If pagination completed normally (not blocked) and no new
            # reports were downloaded, write empty marker files for the
            # latest expected quarter. The markers' mtime allows the
            # cache-first skip to avoid annList on the next run (saving a
            # 90s anti-bot sleep). After 90 days the mtime expires and
            # annList will be re-queried for the next quarter's reports.
            if not pagination_blocked and n_new_total == 0:
                _write_empty_markers(etf_dir, code, _latest_expected_quarter())
                stats.empty += 1
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")

    summary = stats.to_dict(
        out_dir=str(out_dir),
        etfs_total=len(universe),
        reports_found=reports_found,
        csvs_written=csvs_written,
        sleep_sec=sleep_sec,
    )
    logger.info(
        "Done SZSE ETF quarterly reports. downloaded=%d skipped_cached=%d "
        "failed=%d reports_found=%d csvs_written=%d etfs=%d",
        stats.downloaded, stats.skipped_cached, stats.failed,
        reports_found, csvs_written, len(universe),
    )
    return summary
