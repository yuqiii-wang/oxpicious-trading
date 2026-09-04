"""download.macro.pboc.stats — Download PBoC statistical xls files (2020 → latest).

Source: https://www.pbc.gov.cn/diaochatongjisi/116219/116319/index.html
        (调查统计司 → 统计数据 → 社会融资规模 / 货币统计概览)

Downloads 5 statistical items across all available years (2020 → latest):

  shrzgm page (社会融资规模):
    1. 社会融资规模增量统计表 — Aggregate Financing to the Real Economy (Flow)
    2. 社会融资规模存量统计表 — Aggregate Financing to the Real Economy (Stock)

  hbtjgl page (货币统计概览):
    3. 官方储备资产 — Official Reserve Assets
    4. 存款性公司概览 — Depository Corporations Survey
    5. 境外机构和个人持有境内人民币金融资产情况 — Domestic RMB Financial Assets
       Held by Overseas Entities

Anti-bot: The PBoC CDN blocks ``requests``/urllib3 with HTTP 403 via TLS/JA3
fingerprinting.  This module uses ``curl_cffi`` with ``impersonate="chrome"``
to bypass the block, plus ``LONG_SLEEP_INTERVAL`` (90 s) + jitter between
requests.

Output:
    temps/pboc_stats/pboc_stats_{slug}_{year}.xlsx
    temps/pboc_stats/pboc_stats_{slug}_{year}.csv            (xls→csv conversion)
    temps/pboc_stats/pboc_stats_manifest.csv

For multi-sheet xls files (e.g. Official Reserve Assets has 3 sheets), one CSV
per sheet is written: pboc_stats_{slug}_{year}__{sheet_name}.csv.

Usage:
    python -m downloads.macro.pboc.stats                    # full backfill 2020→latest
    python -m downloads.macro.pboc.stats --latest-only      # current year only
    python -m downloads.macro.pboc.stats --start-year 2024  # 2024→latest
    python -m downloads.macro.pboc.stats --sleep-sec 30     # faster (less safe)
    python -m downloads.macro.pboc.stats --force            # re-download even if cached
    python -m downloads.macro.pboc.stats --no-convert-csv   # skip xls→csv conversion
"""
from __future__ import annotations


import argparse
import csv as _csv
import json
import logging
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from downloads._common import (
    LONG_SLEEP_INTERVAL,
    setup_logger,
    resolve_out_dir,
)

from ._session import PBOC_BASE, build_session
from ._catalog import TARGET_ITEMS
from ._scraper import (
    DownloadLink,
    YearArchive,
    discover_year_archives,
    discover_sub_pages,
    scrape_sub_page,
)


# ============================================================================
# Constants
# ============================================================================
STATS_FILE_PREFIX = "pboc_stats"
MANIFEST_FILENAME = "pboc_stats_manifest.csv"
MIN_VALID_XLS_BYTES = 1024  # minimum size for a valid xls/xlsx file

MANIFEST_COLUMNS = [
    "year",
    "item_slug",
    "cn_label",
    "en_label",
    "page",
    "source_url",
    "pub_year",
    "pub_month",
    "filename",
    "download_date",
    "file_size",
    "row_text",
]

logger = setup_logger("pboc_stats")


# ============================================================================
# Download logic
# ============================================================================
# Local file scanning — detect already-present (slug, year) targets
# ============================================================================
# Filename pattern: pboc_stats_{slug}_{year}.{ext}
# The slug may contain underscores (e.g. afre_flow, depository_corp_survey).
RE_TARGET_FILENAME = re.compile(
    rf"^{STATS_FILE_PREFIX}_(.+)_(\d{{4}})\.(xlsx|xls|csv)$"
)


def _extract_target_from_filename(name: str) -> Optional[Tuple[str, int]]:
    """Return (slug, year) from a pboc_stats filename, or None if it doesn't match."""
    m = RE_TARGET_FILENAME.match(name)
    if not m:
        return None
    slug = m.group(1)
    try:
        year = int(m.group(2))
    except ValueError:
        return None
    # Exclude multi-sheet CSV suffixed names like pboc_stats_{slug}_{year}__Sheet1.csv
    if "__" in name:
        return None
    return (slug, year)


def _scan_present_targets(out_dir: Path) -> Dict[Tuple[str, int], Path]:
    """Scan out_dir and return {(slug, year): path} for all valid local files.

    Checks xlsx/xls (primary, original downloads) first, then csv (converted).
    A valid file must be >= MIN_VALID_XLS_BYTES.
    """
    result: Dict[Tuple[str, int], Path] = {}
    if not out_dir.exists():
        return result

    for p in out_dir.iterdir():
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext not in {".xlsx", ".xls", ".csv"}:
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if size < MIN_VALID_XLS_BYTES:
            continue

        target = _extract_target_from_filename(p.name)
        if target is None:
            continue

        # Prefer xlsx/xls over csv as the "source of truth" key
        slug, year = target
        if target not in result or ext in (".xlsx", ".xls"):
            result[target] = p

    return result


def _purge_targets(
    out_dir: Path,
    targets: Set[Tuple[str, int]],
    logger: Optional[logging.Logger] = None,
) -> int:
    """Delete all local files matching the given (slug, year) targets.

    Removes the xls/xlsx original, all converted CSVs, and .meta.json sidecars.
    Returns the number of files deleted.
    """
    deleted = 0
    for slug, year in targets:
        # Match base filename + any derived files (csv, meta.json, multi-sheet)
        base_stem = f"{STATS_FILE_PREFIX}_{slug}_{year}"
        for p in out_dir.glob(f"{base_stem}*"):
            try:
                p.unlink()
                deleted += 1
                if logger:
                    logger.info("  [purge] deleted %s", p.name)
            except OSError as e:
                if logger:
                    logger.warning("  [purge] failed to delete %s: %s", p.name, e)
    return deleted


def _existing_downloaded_files(out_dir: Path) -> Dict[str, Path]:
    """Map filename -> path for all existing pboc_stats_*.xls(x) files.

    Kept for backward compat — newer code should use _scan_present_targets.
    """
    result: Dict[str, Path] = {}
    for p in out_dir.glob(f"{STATS_FILE_PREFIX}_*.xls"):
        result[p.name] = p
    for p in out_dir.glob(f"{STATS_FILE_PREFIX}_*.xlsx"):
        result[p.name] = p
    return result


def download_xls(
    session,
    dl: DownloadLink,
    out_dir: Path,
    existing: Dict[str, Path],
    *,
    force: bool = False,
) -> Optional[Path]:
    """Download a single xls file. Returns the saved path or None on failure."""
    fname = dl.filename()
    fpath = out_dir / fname

    # Caching: skip if file already exists and is valid
    if not force and fname in existing:
        existing_path = existing[fname]
        try:
            size = existing_path.stat().st_size
        except OSError:
            size = 0
        if size >= MIN_VALID_XLS_BYTES:
            logger.info("  [cache] skip %s (exists, %d bytes)", fname, size)
            return existing_path
        else:
            logger.warning("  [cache] %s exists but too small (%d bytes), re-downloading",
                           fname, size)

    # Download
    referer = f"{PBOC_BASE}/diaochatongjisi/116219/116319/"
    logger.info("  [download] %s <- %s", fname, dl.href)
    content = session.get_bytes(dl.href, referer=referer)
    if content is None:
        logger.error("  [download] failed: %s", dl.href)
        return None

    if len(content) < MIN_VALID_XLS_BYTES:
        logger.error("  [download] too small (%d bytes): %s", len(content), dl.href)
        return None

    fpath.write_bytes(content)
    logger.info("  [saved] %s (%d bytes)", fname, len(content))
    return fpath


def save_metadata(
    dl: DownloadLink,
    fpath: Path,
    out_dir: Path,
) -> None:
    """Save a JSON metadata sidecar for the downloaded file."""
    meta_path = fpath.with_suffix(fpath.suffix + ".meta.json")
    meta = {
        "filename": fpath.name,
        "item_slug": dl.item.slug,
        "cn_label": dl.item.cn_label,
        "en_label": dl.item.en_label,
        "page": dl.item.page,
        "description": dl.item.description,
        "year": dl.year,
        "source_url": dl.href,
        "pub_year": dl.pub_year,
        "pub_month": dl.pub_month,
        "row_text": dl.row_text,
        "link_text": dl.link_text,
        "download_date": datetime.now().isoformat(),
        "file_size": fpath.stat().st_size,
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


# ============================================================================
# XLS → CSV conversion
# ============================================================================
def convert_xls_to_csv(xls_path: Path) -> List[Path]:
    """Convert an xls/xlsx file to CSV (one CSV per sheet).

    For single-sheet files:  pboc_stats_{slug}_{year}.csv
    For multi-sheet files:   pboc_stats_{slug}_{year}__{sheet_name}.csv

    Returns the list of CSV paths written.
    """
    import pandas as pd

    if not xls_path.exists():
        logger.warning("[conv] xls not found: %s", xls_path.name)
        return []

    try:
        xls = pd.ExcelFile(xls_path)
    except Exception as e:
        logger.warning("[conv] cannot read %s: %s", xls_path.name, e)
        return []

    # Base stem: e.g. "pboc_stats_afre_flow_2026"
    base_stem = xls_path.stem  # strips .xlsx / .xls
    csv_paths: List[Path] = []
    multi_sheet = len(xls.sheet_names) > 1

    for sheet_name in xls.sheet_names:
        try:
            df = pd.read_excel(xls_path, sheet_name=sheet_name, header=None)
        except Exception as e:
            logger.warning("[conv] cannot read sheet '%s' of %s: %s",
                           sheet_name, xls_path.name, e)
            continue

        if multi_sheet:
            # Sanitize sheet name for filename (replace spaces/slashes)
            safe_sheet = str(sheet_name).replace(" ", "_").replace("/", "_")
            csv_name = f"{base_stem}__{safe_sheet}.csv"
        else:
            csv_name = f"{base_stem}.csv"
        csv_path = xls_path.parent / csv_name

        df.to_csv(csv_path, index=False, encoding="utf-8-sig")
        csv_paths.append(csv_path)

    if csv_paths:
        logger.info("[conv] %s → %d CSV(s): %s",
                    xls_path.name, len(csv_paths),
                    ", ".join(p.name for p in csv_paths))
    return csv_paths


# ============================================================================
# Manifest CSV
# ============================================================================
def build_manifest(
    out_dir: Path,
    downloads: List[Dict],
) -> Path:
    """Write (or update) the manifest CSV listing all downloaded files."""
    manifest_path = out_dir / MANIFEST_FILENAME
    with open(manifest_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = _csv.DictWriter(f, fieldnames=MANIFEST_COLUMNS)
        writer.writeheader()
        for row in downloads:
            writer.writerow({col: row.get(col, "") for col in MANIFEST_COLUMNS})
    logger.info("[manifest] saved %s (%d rows)", manifest_path, len(downloads))
    return manifest_path


# ============================================================================
# Main orchestration
# ============================================================================
def _build_missing_targets(
    *,
    start_year: int,
    end_year: int,
    present: Dict[Tuple[str, int], Path],
    force: bool,
) -> Set[Tuple[str, int]]:
    """Build the set of (slug, year) pairs that need downloading.

    When ``force`` is False, excludes targets already present locally
    (valid xls/xlsx or csv).  When ``force`` is True, returns ALL targets
    in the range.
    """
    all_slugs = [item.slug for item in TARGET_ITEMS]
    years = list(range(start_year, end_year + 1))

    all_targets: Set[Tuple[str, int]] = {
        (slug, year) for slug in all_slugs for year in years
    }
    if force:
        return all_targets

    present_set: Set[Tuple[str, int]] = set(present.keys())
    present_in_range: Set[Tuple[str, int]] = present_set & all_targets
    missing = all_targets - present_in_range
    logger.info(
        "[plan] %d total targets in range %d→%d, %d present locally, %d to download",
        len(all_targets), start_year, end_year,
        len(present_in_range), len(missing),
    )
    if present_set:
        by_year: Dict[int, List[str]] = {}
        for slug, year in sorted(present_in_range):
            by_year.setdefault(year, []).append(slug)
        for year, slugs in sorted(by_year.items()):
            year_total = len(all_slugs)
            year_present = len(slugs)
            year_missing = year_total - year_present
            if year_missing == 0:
                logger.info("  [plan] year %d: fully cached (%d/%d)", year, year_present, year_total)
            else:
                logger.info(
                    "  [plan] year %d: %d/%d cached, missing %d",
                    year, year_present, year_total, year_missing,
                )
    return missing


def download_pboc_stats(
    *,
    out_root: Optional[str] = None,
    start_year: int = 2020,
    end_year: Optional[int] = None,
    latest_only: bool = False,
    sleep_sec: float = LONG_SLEEP_INTERVAL,
    force: bool = False,
    convert_csv: bool = True,
) -> dict:
    """Download PBoC statistical xls files.

    Args:
        out_root: Override output directory root.
        start_year: First year to download (inclusive). Default 2020.
        end_year: Last year to download (inclusive). Default: current year.
        latest_only: If True, download only the latest year (overrides start_year).
        sleep_sec: Sleep between requests (anti-bot). Default LONG_SLEEP_INTERVAL.
        force: Purge existing local files in the target year range and re-download.
        convert_csv: After downloading (or caching hit), convert xls → csv.

    Skip logic (when ``force=False``):
        1. Scan out_dir for valid xls/xlsx/csv files matching pboc_stats_{slug}_{year}.*
        2. Build set of missing (slug, year) targets by comparing against TARGET_ITEMS × years
        3. Only scrape year archives that have at least one missing item
        4. At download time, per-file cache check acts as safety net
    """
    out_dir = resolve_out_dir(str(Path(__file__).resolve()), "pboc_stats", out_root)

    if end_year is None:
        end_year = date.today().year

    if latest_only:
        start_year = end_year

    logger.info(
        "Starting PBoC stats download: years %d→%d, sleep=%.0fs, force=%s, out=%s",
        start_year, end_year, sleep_sec, force, out_dir,
    )

    # Build anti-bot session
    session = build_session(logger=logger, sleep_sec=sleep_sec)

    # ------------------------------------------------------------------
    # PHASE 0: Force purge — remove all local files in the target year range
    # ------------------------------------------------------------------
    if force:
        all_slugs = [item.slug for item in TARGET_ITEMS]
        all_years = list(range(start_year, end_year + 1))
        force_targets: Set[Tuple[str, int]] = {
            (slug, year) for slug in all_slugs for year in all_years
        }
        n_deleted = _purge_targets(out_dir, force_targets, logger=logger)
        logger.info("[force] purged %d files for %d targets", n_deleted, len(force_targets))

    # ------------------------------------------------------------------
    # PHASE 1: Scan local files and build download plan
    # ------------------------------------------------------------------
    present = _scan_present_targets(out_dir)
    if present:
        logger.info(
            "[cache] %d local targets found in %s",
            len(present), out_dir,
        )

    missing_targets = _build_missing_targets(
        start_year=start_year, end_year=end_year,
        present=present, force=force,
    )

    if not missing_targets:
        logger.info("[plan] All targets present locally — nothing to download")
        # Still do CSV conversion on cached files if requested
        if convert_csv:
            existing_files = _existing_downloaded_files(out_dir)
            for fname, fpath in existing_files.items():
                if fpath.stat().st_size >= MIN_VALID_XLS_BYTES:
                    try:
                        convert_xls_to_csv(fpath)
                    except Exception as e:
                        logger.warning("[conv] csv failed for %s: %s", fname, e)
        return {
            "years_scanned": 0,
            "download_links_found": 0,
            "downloaded": 0,
            "cached": len(present),
            "failed": 0,
            "out_dir": str(out_dir),
            "start_year": start_year,
            "end_year": end_year,
        }

    # Determine which years have missing targets
    missing_years: Set[int] = {year for (_, year) in missing_targets}
    logger.info(
        "[plan] need to scrape %d year(s): %s",
        len(missing_years), sorted(missing_years),
    )

    # Also keep the existing filename→path map for per-file cache check
    existing_files_map = _existing_downloaded_files(out_dir)

    # ------------------------------------------------------------------
    # PHASE 2: Discover year archives — ONLY for years with missing items
    # ------------------------------------------------------------------
    archives = discover_year_archives(session, start_year, end_year, logger)
    if not archives:
        logger.error("No year archives found — aborting")
        return {"error": "no year archives found"}

    # Filter to only years that have missing items
    archives_to_scrape = [a for a in archives if a.year in missing_years]
    if not archives_to_scrape:
        # This shouldn't happen since we checked missing_targets above,
        # but guard anyway in case discover_year_archives returned a subset
        logger.info("[plan] All missing years are beyond PBoC archive range")
        return {"error": "no scannable archives"}

    logger.info(
        "[scrape] will fetch %d/%d year archives (%s)",
        len(archives_to_scrape), len(archives),
        [a.year for a in archives_to_scrape],
    )

    # ------------------------------------------------------------------
    # PHASE 3: Scrape sub-pages and collect download links, filtered to
    #          only missing targets
    # ------------------------------------------------------------------
    all_download_links: List[DownloadLink] = []
    for archive in archives_to_scrape:
        if session.is_blocked(PBOC_BASE):
            logger.warning("[blocked] PBoC host is blocked — stopping")
            break

        # Discover shrzgm + hbtjgl sub-page URLs
        sub_pages = discover_sub_pages(session, archive, logger)

        # Scrape shrzgm sub-page (AFRE Flow + Stock)
        if sub_pages.shrzgm_url:
            links = scrape_sub_page(
                session, "shrzgm", archive.year,
                sub_pages.shrzgm_url,
                referer=archive.index_url,
                logger=logger,
            )
            # Filter: keep only links for (slug, year) that are missing
            links = [
                dl for dl in links
                if (dl.item.slug, dl.year) in missing_targets
            ]
            all_download_links.extend(links)
        else:
            logger.warning("[scrape] year %d: no shrzgm sub-page found", archive.year)

        # Scrape hbtjgl sub-page (Official Reserve, Depository Corp, Overseas RMB)
        if sub_pages.hbtjgl_url:
            links = scrape_sub_page(
                session, "hbtjgl", archive.year,
                sub_pages.hbtjgl_url,
                referer=archive.index_url,
                logger=logger,
            )
            links = [
                dl for dl in links
                if (dl.item.slug, dl.year) in missing_targets
            ]
            all_download_links.extend(links)
        else:
            logger.warning("[scrape] year %d: no hbtjgl sub-page found", archive.year)

    logger.info("[scrape] collected %d download links (filtered to missing targets)",
                len(all_download_links))

    if not all_download_links:
        logger.warning(
            "[scrape] No download links found for missing targets — "
            "PBoC may not publish all items for early years"
        )
        # Try a full scrape (without year filter) — the PBoC site may not have
        # certain items for certain years
        logger.info("[scrape] falling back: scraping all requested years for all items")
        all_download_links = []
        for archive in archives:
            if session.is_blocked(PBOC_BASE):
                break
            sub_pages = discover_sub_pages(session, archive, logger)
            if sub_pages.shrzgm_url:
                all_download_links.extend(scrape_sub_page(
                    session, "shrzgm", archive.year,
                    sub_pages.shrzgm_url, referer=archive.index_url, logger=logger,
                ))
            if sub_pages.hbtjgl_url:
                all_download_links.extend(scrape_sub_page(
                    session, "hbtjgl", archive.year,
                    sub_pages.hbtjgl_url, referer=archive.index_url, logger=logger,
                ))
        # Re-filter by year range only (not missing-target filter)
        all_download_links = [
            dl for dl in all_download_links
            if start_year <= dl.year <= end_year
        ]

        if not all_download_links:
            logger.error("No download links found — aborting")
            return {"error": "no download links found"}

    # ------------------------------------------------------------------
    # PHASE 4: Download xls files (per-file cache check as safety net)
    # ------------------------------------------------------------------
    downloaded: List[Dict] = []
    n_downloaded = n_cached = n_failed = 0

    for dl in all_download_links:
        if session.is_blocked(PBOC_BASE):
            logger.warning("[blocked] PBoC host is blocked — stopping downloads")
            break

        fpath = download_xls(session, dl, out_dir, existing_files_map, force=force)
        if fpath is None:
            n_failed += 1
            continue

        # Save metadata sidecar
        save_metadata(dl, fpath, out_dir)

        # Track if it was a cache hit or new download
        was_cached = (not force and dl.filename() in existing_files_map
                      and fpath.stat().st_size >= MIN_VALID_XLS_BYTES)
        if was_cached:
            n_cached += 1
        else:
            n_downloaded += 1

        # Convert xls → csv (after every download, and for cached files
        # that don't yet have a CSV sidecar)
        if convert_csv:
            try:
                convert_xls_to_csv(fpath)
            except Exception as e:
                logger.warning("  [conv] csv conversion failed for %s: %s",
                               fpath.name, e)

        # Build manifest row
        row = dl.manifest_row()
        row["download_date"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row["file_size"] = str(fpath.stat().st_size)
        downloaded.append(row)

    # 4. Build manifest CSV
    if downloaded:
        build_manifest(out_dir, downloaded)

    # 5. Summary
    summary = {
        "years_scanned": len(archives_to_scrape),
        "archives_total": len(archives),
        "download_links_found": len(all_download_links),
        "downloaded": n_downloaded,
        "cached": n_cached,
        "failed": n_failed,
        "out_dir": str(out_dir),
        "start_year": start_year,
        "end_year": end_year,
    }
    logger.info(
        "Done PBoC stats. downloaded=%d cached=%d failed=%d links_found=%d years=%d out=%s",
        n_downloaded, n_cached, n_failed, len(all_download_links),
        len(archives_to_scrape), out_dir,
    )
    return summary


# ============================================================================
# CLI
# ============================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Download PBoC statistical xls files (2020 → latest)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Items downloaded:
  shrzgm (社会融资规模):
    1. afre_flow      — Aggregate Financing to the Real Economy (Flow)
    2. afre_stock     — Aggregate Financing to the Real Economy (Stock)
  hbtjgl (货币统计概览):
    3. official_reserve          — Official Reserve Assets
    4. depository_corp_survey    — Depository Corporations Survey
    5. overseas_rmb_assets       — Domestic RMB Financial Assets Held by Overseas Entities

Anti-bot: Uses curl_cffi with impersonate="chrome" to bypass TLS/JA3
fingerprinting, plus LONG_SLEEP_INTERVAL (90s) between requests.
""",
    )
    parser.add_argument(
        "--start-year", type=int, default=2020,
        help="First year to download (inclusive). Default: 2020",
    )
    parser.add_argument(
        "--end-year", type=int, default=None,
        help="Last year to download (inclusive). Default: current year",
    )
    parser.add_argument(
        "--latest-only", action="store_true",
        help="Download only the latest year (overrides --start-year)",
    )
    parser.add_argument(
        "--sleep-sec", type=float, default=LONG_SLEEP_INTERVAL,
        help=f"Sleep between requests in seconds (anti-bot). Default: {LONG_SLEEP_INTERVAL}",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-download even if files are already cached",
    )
    parser.add_argument(
        "--out-root", type=str, default=None,
        help="Override output directory root (default: temps/pboc_stats/)",
    )
    parser.add_argument(
        "--no-convert-csv", action="store_true", default=False,
        help="Skip xls→csv conversion after download",
    )
    args = parser.parse_args()

    result = download_pboc_stats(
        out_root=args.out_root,
        start_year=args.start_year,
        end_year=args.end_year,
        latest_only=args.latest_only,
        sleep_sec=args.sleep_sec,
        force=args.force,
        convert_csv=not args.no_convert_csv,
    )
    print(result)


if __name__ == "__main__":
    main()
