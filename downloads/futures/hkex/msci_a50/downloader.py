"""downloads.futures.hkex.msci_a50.downloader — HKEX daily report fetches.

Also owns the family's DB-row shaping for the shared futures tables: every
parsed row carries its semantic report fields (day_open/settlement/oi_...)
and maps onto ``stats.futures_basic_stats`` via :func:`db_rows_for` plus an
identity twin for ``stats.futures_identity`` via :func:`identity_rows_for`
(product MCA, MSCI China A 50 Connect underlying, 3rd-Friday expiry per the
contract spec).

The report zips are static files on www.hkex.com.hk (Akamai-fronted but
bot-lenient for plain GETs — verified 2026-10-26; the product-page widget
API on www1.hkex.com.hk is the gated one). Every request goes through the
shared :class:`AntiBotProxy` built on this service's config row (sleep with
jitter, browser-profile rotation, 403/429 host-block tracking with a 900s
cooldown). A 404 is a legitimate per-date miss — the exchange only
publishes mcaf zips for realized trading days (the report is final in the
evening) and keeps ~1 year of archives — so the proxy's None-on-4xx is
treated as "no report" and retried per the service policy before giving up.

The fetch layer is the :class:`MsciA50Downloader` class — the former
module-level proxy/session singletons are per-instance attributes
(``self.proxy`` / ``self._session``) and the functions that used them are
its methods, while the parsers and DB-row shapers stay module-level
functions.

Report layout (both kinds, RFC-quoted CSV, ASCII):

  Day report (mcaf): the "Contract Month" block has 17 cells per row —
    [0] contract ("NOV-26"), [1..4] after-hours O/H/L/C of the PREVIOUS
    trading day (ignored — mcafa of that date carries it), [5] its volume,
    [6..8] day-session O/H/L, [9] day volume, [10] settlement, [11]
    settlement change, [12] contract high, [13] contract low, [14] combined
    volume (prev-day AH + this day session — the exchange's legacy view,
    NOT this module's volume), [15] open interest, [16] OI change.

  After-hours report (mcafa): 6 cells per row — [0] contract, [1..4]
    O/H/L/C of the T+1 session that ran the evening of the report date,
    [5] volume.

  0.00 prices are "no trades that session" placeholders → NULL. Calendar-
  spread strategy rows ("NOV-26/DEC-26", 8 cells) and Total rows are
  skipped — only single-contract rows parse.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import zipfile
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

import requests

from downloads._common import (
    AntiBotProxy,
    build_headers_with_referer,
    retry_policy_for,
    setup_logger,
)

from downloads.futures.hkex.msci_a50.config import (
    AH_REPORT_ZIP,
    DAY_REPORT_ZIP,
    MONTH_LETTERS,
    REFERER_URL,
    REPORT_BASE,
    SERVICE,
)

logger: logging.Logger = setup_logger("hkex_mca")

# Retry policy — resolved once at import from the service's
# external_request_config JSON row (max_retries, retry_backoff_sec); the
# code fallback (2 retries) only applies if the row omits them.
_MAX_RETRIES_JSON, _RETRY_BACKOFF_JSON = retry_policy_for(SERVICE)
MAX_RETRIES: int = _MAX_RETRIES_JSON if _MAX_RETRIES_JSON is not None else 2


# "NOV-26" single-contract rows — excludes calendar spreads ("NOV-26/DEC-26").
RE_CONTRACT = re.compile(r"^([A-Z]{3})-(\d{2})$")

# Report dates render as "23 OCT 2026, FRIDAY   ".
RE_REPORT_DATE = re.compile(r"(\d{2} [A-Z]{3} \d{4})")

MONTH_NUMBERS: Dict[str, int] = {
    name: number for number, name in (
        (1, "JAN"), (2, "FEB"), (3, "MAR"), (4, "APR"), (5, "MAY"), (6, "JUN"),
        (7, "JUL"), (8, "AUG"), (9, "SEP"), (10, "OCT"), (11, "NOV"), (12, "DEC"),
    )
}

# "X" → 11 — month-letter → month-number, from the config's mapping.
MONTH_LETTERS_INDEX: Dict[str, int] = {
    letter: number for number, letter in MONTH_LETTERS.items()
}

# Complete-key placeholder so every parsed row carries the same column set
# for the bulk upsert (after-hours unknown → NULL, volume unknown → NULL).
_AH_NULLS: Dict[str, object] = {
    "ah_open": None,
    "ah_high": None,
    "ah_low": None,
    "ah_close": None,
    "ah_volume": None,
    "volume": None,
}


def contract_code(month_name: str, year2: str) -> str:
    """``("NOV", "26")`` → ``MCA26X`` (HKATS product + 2-digit year + month letter)."""
    return f"MCA{year2}{MONTH_LETTERS[MONTH_NUMBERS[month_name]]}"


def _decode(content: bytes) -> str:
    for encoding in ("utf-8", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _unzip_csv(content: bytes) -> str:
    """First member of the report zip, decoded (one CSV per zip)."""
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        name = zf.namelist()[0]
        return _decode(zf.read(name))


def _num(raw: str) -> Optional[float]:
    text = (raw or "").strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _int(raw: str) -> Optional[int]:
    value = _num(raw)
    return int(value) if value is not None else None


def _price(raw: str, has_trades: bool) -> Optional[float]:
    """Session price: NULL when the session had no trades (0.00 placeholder)."""
    if not has_trades:
        return None
    return _num(raw)


def _nonzero(raw: str) -> Optional[float]:
    """Contract lifetime high/low: the exchange reports 0.00 for none yet."""
    value = _num(raw)
    return value if value not in (None, 0.0) else None


def _trading_day(rows: List[List[str]]) -> Optional[date]:
    """The report's own "Trading Day of the Exchange" date.

    The header line pairs the prev-trading-day date (cell 1) with the
    report date (cell 6); the later single-date line of the spread block
    has only one match and is skipped by the >=2 guard.
    """
    for row in rows:
        found = [RE_REPORT_DATE.search(cell).group(1) for cell in row
                 if cell and RE_REPORT_DATE.search(cell)]
        if len(found) >= 2:
            return datetime.strptime(found[1], "%d %b %Y").date()
    return None


class MsciA50Downloader:
    """Report-zip fetch layer of the family (proxy/session-scoped class).

    Every request goes through the shared :class:`AntiBotProxy` and a lazy
    ``requests`` session — the former module-level singletons are now
    instance attributes (``self.proxy`` / ``self._session``) built in
    ``__init__``, and the functions that used them are methods with the
    same names and sequencing; the pure parsers and DB-row shapers below
    stay module-level functions."""

    def __init__(self) -> None:
        # Built on the service row: base sleep + jitter, timeouts, host tracking
        # (900s cooldown on 403/429; 404 misses never block).
        self.proxy = AntiBotProxy(service=SERVICE)
        self._session: Optional[requests.Session] = None

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
        return self._session

    def _fetch_zip(self, url: str, sleep_sec: Optional[float]) -> Optional[bytes]:
        """One report zip via the anti-bot proxy, with the service retry policy.

        Returns the zip bytes, or None when the report does not exist (404 —
        non-trading day, not yet final, or beyond archive retention) or every
        attempt failed; callers log the distinction at the call site.
        """
        headers = build_headers_with_referer(REFERER_URL)
        session = self._get_session()
        for attempt in range(1, MAX_RETRIES + 1):
            resp = self.proxy.get(session, url, headers=headers, sleep_sec=sleep_sec,
                                  logger=logger)
            if resp is not None and resp.status_code == 200:
                return resp.content
            logger.warning("  %s <- HTTP %s (attempt %d/%d)",
                           url.rsplit("/", 1)[-1],
                           resp.status_code if resp is not None else "suppressed/failed",
                           attempt, MAX_RETRIES)
            if attempt < MAX_RETRIES:
                self.proxy.sleep()
        return None

    def fetch_daily_rows(self, day: date,
                         sleep_sec: Optional[float] = None) -> Optional[List[Dict[str, object]]]:
        """Fetch one trading date's full bars (day session + after-hours).

        Args:
            day: HKEX trading date to fetch.
            sleep_sec: Explicit pacing override; None = the service row's base sleep.

        Returns:
            Raw report rows (mapped to the shared futures tables by
            :func:`db_rows_for` / :func:`identity_rows_for`) — day session + settlement +
            OI always; after-hours fields and the combined volume only when the
            mcafa report is already published. None when the day report does not
            exist (404 — non-trading day, day session not yet final, or beyond
            the ~1y archive retention).
        """
        yymmdd = day.strftime("%y%m%d")
        content = self._fetch_zip(f"{REPORT_BASE}/{DAY_REPORT_ZIP.format(yymmdd=yymmdd)}",
                                  sleep_sec)
        if content is None:
            return None

        rows = parse_day_report(content, day)
        if not rows:
            return rows

        ah_content = self._fetch_zip(f"{REPORT_BASE}/{AH_REPORT_ZIP.format(yymmdd=yymmdd)}",
                                     sleep_sec)
        if ah_content is None:
            logger.info("  %s: no after-hours report yet — volume stays NULL "
                        "(next run completes it)", day)
            for row in rows:
                row.update(_AH_NULLS)
            return rows

        ah_by_code = parse_ah_report(ah_content, day)
        for row in rows:
            ah = ah_by_code.pop(row["code"], None)  # type: ignore[arg-type]
            if ah is None:
                row.update(_AH_NULLS)
                continue
            row.update(ah)
            row["volume"] = (row["day_volume"] or 0) + (ah["ah_volume"] or 0)
        return rows


def parse_day_report(content: bytes, expected_date: date) -> List[Dict[str, object]]:
    """Parse an mcaf zip into per-contract rows shaped for the DB upsert.

    Session columns populate from the day-session block; after-hours
    fields stay None until the caller overlays the mcafa report. The
    volume column is deliberately left unset here — combined volume needs
    this date's own after-hours volume, which the day report does not
    carry (its combined column mixes in the PREVIOUS day's AH session).
    """
    rows = list(csv.reader(io.StringIO(_unzip_csv(content))))
    trading_day = _trading_day(rows)
    if trading_day is not None and trading_day != expected_date:
        logger.warning("  mcaf report date %s != requested %s — using the report's",
                       trading_day, expected_date)

    out: List[Dict[str, object]] = []
    for row in rows:
        match = RE_CONTRACT.match(row[0].strip()) if row and row[0] else None
        if match is None or len(row) < 17:
            continue
        day_volume = _int(row[9]) or 0
        settlement = _num(row[10])
        settlement_change = _num(row[11])
        record: Dict[str, object] = {
            "date": trading_day or expected_date,
            "code": contract_code(match.group(1), match.group(2)),
            "day_open": _price(row[6], day_volume > 0),
            "day_high": _price(row[7], day_volume > 0),
            "day_low": _price(row[8], day_volume > 0),
            "day_volume": day_volume,
            "settlement": settlement,
            "settlement_change": settlement_change,
            "prev_settlement": (
                round(settlement - settlement_change, 6)
                if settlement is not None and settlement_change is not None else None
            ),
            "contract_high": _nonzero(row[12]),
            "contract_low": _nonzero(row[13]),
            "open_interest": _int(row[15]),
            "oi_change": _int(row[16]),
        }
        out.append(record)
    logger.info("  mcaf %s: %d contract rows", trading_day or expected_date, len(out))
    return out


def parse_ah_report(content: bytes, expected_date: date) -> Dict[str, Dict[str, object]]:
    """Parse an mcafa zip into per-code after-hours session fields.

    Returns {code: {ah_open, ah_high, ah_low, ah_close, ah_volume}} for
    the T+1 session that ran the evening of *expected_date*.
    """
    rows = list(csv.reader(io.StringIO(_unzip_csv(content))))
    trading_day = _trading_day(rows)
    if trading_day is not None and trading_day != expected_date:
        logger.warning("  mcafa report date %s != requested %s — using the report's",
                       trading_day, expected_date)

    out: Dict[str, Dict[str, object]] = {}
    for row in rows:
        match = RE_CONTRACT.match(row[0].strip()) if row and row[0] else None
        if match is None or len(row) < 6:
            continue
        volume = _int(row[5]) or 0
        out[contract_code(match.group(1), match.group(2))] = {
            "ah_open": _price(row[1], volume > 0),
            "ah_high": _price(row[2], volume > 0),
            "ah_low": _price(row[3], volume > 0),
            "ah_close": _price(row[4], volume > 0),
            "ah_volume": volume,
        }
    logger.info("  mcafa %s: %d contract rows", trading_day or expected_date, len(out))
    return out


# ---------------------------------------------------------------------------
# Shared-futures-table shaping (stats.futures_identity / basic_stats)
# ---------------------------------------------------------------------------

_MCA_NAME: str = "MSCI中国A50互联互通指数期货"
_MCA_UNDERLYING_CODE: str = "MICN0A5C0PCY"   # the HKEX marquee RIC of the underlying index
_MCA_UNDERLYING_NAME: str = "MSCI中国A50互联互通指数"

CONTRACT_TYPE_INDEX: int = 0  # static.label_dim 'contract_type' — same code as CFFEX index futures


def _third_friday(year: int, month: int) -> date:
    """The 3rd Friday of a month — the MCA last trading day (the contract
    spec shifts it one business day earlier when it hits a Mainland
    holiday; the approximation keeps the calendar rule)."""
    first = date(year, month, 1)
    first_friday = first + timedelta(days=(4 - first.weekday()) % 7)
    return first_friday + timedelta(days=14)


def identity_rows_for(rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """The deduplicated ``stats.futures_identity`` twins of fetched rows.

    ``MCA26X`` → contract month "2611", days-to-expiry measured to the 3rd
    Friday of the contract month.
    """
    seen: Set[Tuple[str, date]] = set()
    out: List[Dict[str, object]] = []
    for row in rows:
        key = (str(row["code"]), row["date"])  # type: ignore[arg-type]
        if key in seen:
            continue
        seen.add(key)
        code, d = key
        yy, letter = code[3:5], code[5]
        mm = MONTH_LETTERS_INDEX[letter]
        out.append({
            "date": d,
            "code": code,
            "product_code": "MCA",
            "contract_month": f"{yy}{mm:02d}",
            "contract_year_month": f"20{yy}-{mm:02d}",
            "contract_type": CONTRACT_TYPE_INDEX,
            "name": _MCA_NAME,
            "underlying_code": _MCA_UNDERLYING_CODE,
            "underlying_name": _MCA_UNDERLYING_NAME,
            "days_to_expiry": max(0, (_third_friday(2000 + int(yy), mm) - d).days),
        })
    return out


def db_rows_for(rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """Map parsed report rows onto ``stats.futures_basic_stats`` columns.

    No session close is published (the settlement IS the official close),
    so ``close``/``change_pct``/``trading_amount``/``delta`` stay NULL;
    ``trading_shares`` carries the day + after-hours combined volume and
    remains NULL until the after-hours report has been fetched.
    """
    return [
        {
            "date": row["date"],
            "code": row["code"],
            "open": row["day_open"],
            "high": row["day_high"],
            "low": row["day_low"],
            "close": None,
            "settlement_price": row["settlement"],
            "prev_settlement": row["prev_settlement"],
            "change": row["settlement_change"],
            "change_pct": None,
            "trading_shares": row["volume"],
            "trading_amount": None,
            "open_interest": row["open_interest"],
            "open_interest_change": row["oi_change"],
            "delta": None,
        }
        for row in rows
    ]
