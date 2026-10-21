"""analysis.analysis_insights.resolver — resolve metric values for one ask.

Given an analysis page (``<tab>.<page>``) and the ask context — the
classification-nav security (``code`` + ``sec_type``), an optional
industry id, and the as-of date chosen in the code trend / modal —
resolve every registered metric's value:

  * ``source.kind = "table"``  — the actual curve value: the row at the
    LATEST ``source.date`` column value <= the as-of date (latest
    available when no as-of), equality-filtered by ``source.match`` (a
    ``"$latest"`` match value = MAX(col) within the filtered rows),
    value = ``source.value`` of that row, delta = value minus the
    previous-date row's value. Optional ``source.order``
    ``{"by": "date"|"value", "dir": ...}`` picks the row instead.
  * ``source.kind = "compute"`` or the metric's ``compute`` FALLBACK —
    the value is computed on demand from the security's daily baseline
    view (close / pct_change / wilder_rsi / ma_gap_pct /
    dist_from_high_low); ``compute.args`` may pin code/sec_type.

These semantics are the CONTRACT mirrored by
``data_viz/api/services/analysis-insights.service.ts`` (the Express
fast path for table metrics — the spec jsonb drives both, keep them in
sync) and documented in the registry DDL header
(``database/sql/analysis/static/analysis_insights/01_insight_templates.sql``).

Lean by design: psycopg + stdlib only (spawned per Ask AI modal open);
no pandas anywhere in the module graph.
"""
from __future__ import annotations

import datetime as dt
from calendar import monthrange
from typing import Any, Dict, List, Optional, Sequence

from _common.db_commons import get_read_db_connection
from _common.labels import SEC_TYPE_CODE, SEC_TYPE_LABEL

TABLE = "analysis_insights.insight_templates"

# per-sec_type daily baseline views (close + ma{5,20,60,120,255} cols)
DAILY_VIEWS = {
    "etf": "stats.v_etf_margin",
    "index": "stats.v_index_daily",
    "stock": "stats.v_stock_daily",
}

# close history fetch cap — enough for Wilder RSI convergence (window*5)
MAX_BARS = 400


def normalize_as_of(raw: Optional[str]) -> Optional[dt.date]:
    """'YYYY-MM-DD' | 'YYYY-MM' → the as-of date (month = its last day,
    clamped to today so a future month means now); None/blank → None
    (latest available). A date passes through unchanged."""
    if raw is None:
        return None
    if isinstance(raw, dt.date):
        return raw
    if not raw.strip():
        return None
    text = raw.strip()
    try:
        if len(text) == 7:
            year, month = int(text[:4]), int(text[5:7])
            last = dt.date(year, month, monthrange(year, month)[1])
            return min(last, dt.date.today())
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def _q(ident: str) -> str:
    """Quote one identifier (defuses any injection via spec jsonb)."""
    return '"' + ident.replace('"', '""') + '"'


def _qt(qualified: str) -> str:
    """Quote a schema-qualified name: 'a.b' → "a"."b" (never one literal
    identifier with a dot inside, which would miss the real table)."""
    return ".".join(_q(part) for part in qualified.split("."))


class AskContext:
    """The ask's identity params (from the Ask AI modal)."""

    def __init__(
        self,
        code: Optional[str] = None,
        sec_type: Optional[str] = None,
        industry: Optional[str] = None,
        as_of: Optional[str] = None,
    ):
        self.code = (code or "").strip() or None
        self.sec_type = (sec_type or "").strip() or None
        self.industry = (industry or "").strip() or None
        self.as_of = normalize_as_of(as_of)

    @property
    def sec_type_code(self) -> Optional[int]:
        if self.sec_type is None:
            return None
        return SEC_TYPE_CODE.get(self.sec_type)


def load_page_templates(conn, tab: str, page: str) -> List[Dict[str, Any]]:
    """The page's metric rows, in registry order (empty when unknown)."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT template_id, tab, page, metric, label, unit, "
            f"description, spec FROM {TABLE} "
            f"WHERE tab = %s AND page = %s ORDER BY ordinal",
            (tab, page),
        )
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _resolve_table_metric(
    conn, spec: Dict[str, Any], ctx: AskContext,
) -> Dict[str, Any]:
    """One table-kind metric per the contract in the module docstring."""
    table = _qt(spec["table"])
    date_col = _q(spec.get("date", "date"))
    value_col = _q(spec["value"])
    code_col = spec.get("code")
    sec_col = spec.get("sec_type")
    match = spec.get("match") or {}
    order = spec.get("order") or {}
    order_by = order.get("by", "date")
    order_dir = order.get("dir", "desc")

    where: List[str] = []
    args: List[Any] = []
    if ctx.as_of is not None:
        where.append(f"{date_col} <= %s")
        args.append(ctx.as_of)
    if code_col:
        code = ctx.code or ctx.industry
        if not code:
            return {"unresolved": "needs a security/industry"}
        where.append(f"{_q(code_col)} = %s")
        args.append(code)
    if sec_col:
        if ctx.sec_type_code is None:
            return {"unresolved": "needs a sec_type"}
        where.append(f"{_q(sec_col)} = %s")
        args.append(ctx.sec_type_code)

    latest_dims: List[str] = []
    for key, val in match.items():
        if val == "$latest":
            latest_dims.append(key)
        else:
            where.append(f"{_q(key)} = %s")
            args.append(val)
    for key in latest_dims:
        cur_sql = f"SELECT MAX({_q(key)}) FROM {table}"
        if where:
            cur_sql += " WHERE " + " AND ".join(where)
        with conn.cursor() as cur:
            cur.execute(cur_sql, args)
            found = cur.fetchone()[0]
        if found is None:
            return {"unresolved": f"no rows to pick $latest {key}"}
        where.append(f"{_q(key)} = %s")
        args.append(found)

    # one row per date (ties broken by value desc), newest / ordered first
    # (the outer query sees only the d/v aliases); rows whose value is
    # NULL are skipped so the metric falls back to the latest NON-NULL
    # date (partial-day tables), and NULLs never win an ordered pick.
    order_alias = "v" if order_by == "value" else "d"
    sql = (
        f"SELECT d, v FROM ("
        f"  SELECT {date_col} AS d, {value_col} AS v,"
        f"         ROW_NUMBER() OVER (PARTITION BY {date_col}"
        f"                            ORDER BY {value_col} DESC NULLS LAST) AS rn"
        f"  FROM {table}"
        + (" WHERE " + " AND ".join(where) if where else "")
        + f") t WHERE rn = 1 AND v IS NOT NULL"
        f" ORDER BY {order_alias} {'DESC' if order_dir != 'asc' else 'ASC'}"
        f" NULLS LAST, d DESC LIMIT 2"
    )
    with conn.cursor() as cur:
        cur.execute(sql, args)
        rows = cur.fetchall()
    if not rows or rows[0][1] is None:
        return {"unresolved": "no row at/before the as-of date"}
    out: Dict[str, Any] = {
        "value": _plain(rows[0][1]), "value_date": _iso(rows[0][0]),
    }
    if len(rows) > 1 and rows[1][0] != rows[0][0]:
        prev = _plain(rows[1][1])
        out["prev_value"] = prev
        if isinstance(out["value"], (int, float)) and isinstance(prev, (int, float)):
            out["delta"] = out["value"] - prev
    return out


def _plain(v: Any) -> Any:
    """DB scalar → JSON-safe (Decimal → float, date stays ISO via caller)."""
    if isinstance(v, dt.date):
        return v.isoformat()
    if hasattr(v, "to_eng_string"):  # decimal.Decimal
        return float(v)
    return v


def _iso(v: Any) -> Optional[str]:
    return v.isoformat() if isinstance(v, dt.date) else (
        str(v) if v is not None else None)


def _fetch_closes(
    conn, view: str, code: str, as_of: Optional[dt.date], limit: int,
) -> List[Any]:
    """Latest-first (date, close-as-float) pairs from the sec_type's daily
    view. Rows with NULL close (today's intraday stubs before the
    basic_stats join fills in) are skipped so LIMIT counts real bars."""
    sql = (f"SELECT date, close FROM {_qt(view)} "
           f"WHERE code = %s AND close IS NOT NULL")
    args: List[Any] = [code]
    if as_of is not None:
        sql += " AND date <= %s"
        args.append(as_of)
    sql += " ORDER BY date DESC LIMIT %s"
    args.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return [(row[0], float(row[1])) for row in cur.fetchall()]


def _wilder_rsi(closes: Sequence[float], window: int) -> Optional[float]:
    """Classic Wilder RSI at the series' last point (SMA seed + recursion)."""
    if len(closes) < window + 1:
        return None
    # closes are newest-first; walk oldest → newest
    seq = list(reversed(closes))
    gains = losses = 0.0
    for i in range(1, window + 1):
        change = seq[i] - seq[i - 1]
        if change >= 0:
            gains += change
        else:
            losses -= change
    avg_gain, avg_loss = gains / window, losses / window
    for i in range(window + 1, len(seq)):
        change = seq[i] - seq[i - 1]
        avg_gain = (avg_gain * (window - 1) + max(change, 0.0)) / window
        avg_loss = (avg_loss * (window - 1) + max(-change, 0.0)) / window
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _resolve_compute(
    conn, fn: str, args: Dict[str, Any], ctx: AskContext,
) -> Dict[str, Any]:
    """One compute-fn value from the daily baseline view (on demand)."""
    code = args.get("code") or ctx.code
    sec_type = args.get("sec_type") or ctx.sec_type
    view = DAILY_VIEWS.get(sec_type or "")
    if not view or not code:
        return {"unresolved": "needs a security (code + sec_type)"}
    window = int(args.get("window", 0) or 0)
    days = int(args.get("days", 0) or 0)
    if fn == "pct_change":
        closes = _fetch_closes(conn, view, code, ctx.as_of, days + 1)
        if len(closes) < days + 1 or not closes[0][1]:
            return {"unresolved": "not enough close history"}
        last, first = closes[0][1], closes[days][1]
        if not first:
            return {"unresolved": "not enough close history"}
        return {"value": (last / first - 1.0) * 100.0,
                "value_date": _iso(closes[0][0])}
    if fn == "wilder_rsi":
        closes = _fetch_closes(
            conn, view, code, ctx.as_of, min(MAX_BARS, window * 5 + 10))
        values = [c for _, c in closes if c is not None]
        rsi = _wilder_rsi(values, window)
        if rsi is None:
            return {"unresolved": "not enough close history"}
        return {"value": rsi, "value_date": _iso(closes[0][0])}
    if fn == "ma_gap_pct":
        ma_col = f"ma{window}"
        sql = (f"SELECT date, close, {_q(ma_col)} FROM {_qt(view)} "
               f"WHERE code = %s AND close IS NOT NULL")
        params: List[Any] = [code]
        if ctx.as_of is not None:
            sql += " AND date <= %s"
            params.append(ctx.as_of)
        sql += " ORDER BY date DESC LIMIT 1"
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
        if not row:
            return {"unresolved": "no baseline row"}
        _, close, ma = row
        if ma is None or not ma:
            closes = _fetch_closes(conn, view, code, ctx.as_of, window)
            if len(closes) < window:
                return {"unresolved": "not enough close history"}
            ma = sum(c for _, c in closes) / window
        return {"value": (float(close) / float(ma) - 1.0) * 100.0,
                "value_date": _iso(row[0])}
    if fn == "dist_from_high_low":
        closes = _fetch_closes(conn, view, code, ctx.as_of, window)
        if len(closes) < window:
            return {"unresolved": "not enough close history"}
        values = [c for _, c in closes]
        close = values[0]
        edge = max(values) if args.get("side") == "high" else min(values)
        if not edge:
            return {"unresolved": "degenerate window"}
        return {"value": (close / edge - 1.0) * 100.0,
                "value_date": _iso(closes[0][0])}
    # fn == "close"
    closes = _fetch_closes(conn, view, code, ctx.as_of, 1)
    if not closes or closes[0][1] is None:
        return {"unresolved": "no baseline row"}
    return {"value": float(closes[0][1]), "value_date": _iso(closes[0][0])}


def _applies(spec: Dict[str, Any], ctx: AskContext) -> bool:
    """sec_types applicability gate (missing list = applies to all)."""
    labels = spec.get("sec_types")
    if not labels:
        return True
    return ctx.sec_type is not None and ctx.sec_type in labels


def resolve_page(
    conn,
    tab: str,
    page: str,
    ctx: AskContext,
    only: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """Resolve every (or ``only``) metric of one page; never raises."""
    templates = load_page_templates(conn, tab, page)
    results: List[Dict[str, Any]] = []
    for tpl in templates:
        if only and tpl["metric"] not in only:
            continue
        spec = tpl["spec"] or {}
        out: Dict[str, Any] = {
            "template_id": tpl["template_id"],
            "metric": tpl["metric"],
            "label": tpl["label"],
            "unit": tpl["unit"] or "",
            "description": tpl["description"] or "",
            "scope": spec.get("scope", "security"),
            "value": None,
            "value_date": None,
            "prev_value": None,
            "delta": None,
            "source_kind": spec.get("source", {}).get("kind", "table"),
            "computed": False,
            "unresolved": None,
        }
        if not _applies(spec, ctx):
            out["unresolved"] = f"not applicable to sec_type {ctx.sec_type!r}"
            results.append(out)
            continue
        source = spec.get("source") or {}
        # An industry-scope ask keys its code column by industry_id; when
        # the ask carries no sec_type, a source with a sec_type column
        # still resolves against the 'industry' slice.
        eff_ctx = ctx
        if ctx.sec_type is None and spec.get("scope") == "industry":
            eff_ctx = AskContext(code=ctx.code, sec_type="industry",
                                 industry=ctx.industry, as_of=ctx.as_of)
        try:
            if source.get("kind") == "table":
                found = _resolve_table_metric(conn, source, eff_ctx)
            else:
                found = {"unresolved": "compute-only"}
            fallback = spec.get("compute")
            if found.get("unresolved") and fallback:
                computed = _resolve_compute(
                    conn, fallback.get("fn", ""),
                    fallback.get("args") or {}, ctx)
                if not computed.get("unresolved"):
                    computed["computed"] = True
                    found = computed
            if source.get("kind") == "compute":
                computed = _resolve_compute(
                    conn, source.get("fn", ""), source.get("args") or {}, ctx)
                if not computed.get("unresolved"):
                    computed["computed"] = True
                    found = computed
            out.update(found)
        except Exception as exc:  # one bad metric must not fail the ask
            out["unresolved"] = f"resolver error: {exc}"
        results.append(out)
    return results


def resolve(
    tab: str,
    page: str,
    code: Optional[str] = None,
    sec_type: Optional[str] = None,
    industry: Optional[str] = None,
    as_of: Optional[str] = None,
    only: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Full resolve of one page — the CLI/Express entry point."""
    ctx = AskContext(
        code=code, sec_type=sec_type, industry=industry, as_of=as_of)
    conn = get_read_db_connection()
    try:
        metrics = resolve_page(conn, tab, page, ctx, only=only)
    finally:
        conn.close()
    return {
        "tab": tab,
        "page": page,
        "code": ctx.code,
        "sec_type": ctx.sec_type,
        "industry": ctx.industry,
        "as_of": ctx.as_of.isoformat() if ctx.as_of else None,
        "metrics": metrics,
    }
