"""Steps for analysis.news_text_daily — the scope builders + writes.

Compute layout (cudf.pandas, whole-frame): the scored-article frame is
grouped in int64 day-number space with ONE grouped pass per scope; the
trailing windows (z / ewm / flow median-IQR) run as whole-series rolling
ops on the calendar-indexed daily frame (ewm / rolling-quantile may log
sanctioned pandas fallbacks — the cuDF-misdiagnosis list names them, not
dtype breaches). The ONE sanctioned host core is the calendar ALIGNMENT
of the articles (analysis._common.calendar_roll.roll_to_calendar — a
searchsorted gather on the unwrapped host day-number array, a
positional gather with no cuDF-native form).

The COVERAGE basis arrives pre-aggregated from text.news_coverage_audit
(built by analysis.news_audit — the 2026-11-22 coverage migration): per
(trading day, industry_id) counts, '000001' = the whole-market rollup
row, already calendar-rolled by the audit — the MARKET scopes merge it
for n_articles / the AF flag, they never count raw articles themselves.

The INDUSTRY and SECTOR scopes attribute articles by CO-MENTION (the
2026-11-22 attribution rework): the mention frame
(fetch.fetch_mention_articles) carries one row per (article, industry)
whose keywords the article matches with >= MENTION_MIN_KW_CHARS total
matched chars — the primary single-tag attribution starved niche
industries (ADVMFG had 11 articles EVER while its keywords appeared in
47 recent-window articles, every one attributed to the financial
primaries that merely mention it). Industry rows aggregate the
(article, industry) frame directly; sector rows roll it up the catalog's
industry->sector map with a per-(article, sector) dedupe (one article
mentioning several industries of one sector counts once for that
sector). The n_articles column on these rows is the scored co-mention
count (the audit's basis is primary-tag and has no per-mention
equivalent). Label strings (industry_id / sector_id / source) ride only
as groupby keys; dates materialize once at the write boundary
(epoch_col_to_dt64 -> sanitize_for_db_insert date_cols).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from database.sec_statics import classification_store

from _common.db_commons import bulk_upsert_async
from _common.df_utils import epoch_col_to_dt64, host_array
from analysis._common import sanitize_for_db_insert
from analysis._common.calendar_roll import SECONDS_PER_DAY, roll_to_calendar
from analysis.news_text_daily.config import (
    BLEND_LLM_WEIGHT,
    BLEND_LEX_WEIGHT,
    DISAGREE_WEIGHT,
    EMA10_HALFLIFE,
    EMA3_HALFLIFE,
    FLOW_MIN_OBS,
    FLOW_NEG_CUT,
    FLOW_POS_CUT,
    FLOW_WINDOW,
    KIND_INDUSTRY,
    KIND_MARKET_ALL,
    KIND_SECTOR,
    LEVEL_MIN_SCORED,
    KIND_MARKET_RECAP,
    MARKET_INDEX_CODE,
    RECAP_INDUSTRY,
    SCORED_MIN,
    TABLE,
    DISP_Z_MIN_OBS,
    Z_MIN_OBS,
    Z_WINDOW,
)

RECAP_TAG = RECAP_INDUSTRY
#: the coverage-audit rollup row's industry_id (the whole-market counts)
MARKET_ROLLUP = MARKET_INDEX_CODE


# ---------------------------------------------------------------------------
#  Shared blend + alignment
# ---------------------------------------------------------------------------

def blend_scores(scored: pd.DataFrame) -> pd.DataFrame:
    """Attach the per-article blended score ``s``, its aggregate weight
    ``w`` (disagreeing articles carry DISAGREE_WEIGHT) and the weighted
    product ``sw`` — everything the grouped pass aggregates.

        s = 0.7*LLM + 0.3*lexicon  (lexicon absent -> LLM alone)
    """
    llm = scored["sentiment_level"].astype("float64")
    lex = scored["lex_score"].astype("float64")
    blended = BLEND_LLM_WEIGHT * llm + BLEND_LEX_WEIGHT * lex
    s = llm.where(lex.isna(), blended)
    disagrees = scored["disagrees"].astype("int64")
    w = 1.0 - DISAGREE_WEIGHT * disagrees.astype("float64")
    return scored.assign(s=s, w=w, sw=s * w)


def _count_cols(frame: pd.DataFrame, prefix: str) -> pd.DataFrame:
    """Sign-split counts on the blended score (bool -> int64 before
    arithmetic — the bool-numeric rule)."""
    s = frame["s"]
    frame[f"{prefix}_pos"] = (s > FLOW_POS_CUT).astype("int64")
    frame[f"{prefix}_neg"] = (s < FLOW_NEG_CUT).astype("int64")
    return frame


def _tone_z(tone: pd.DataFrame, cal_days: np.ndarray) -> pd.Series:
    """Keyword-tone z: log1p of the non-negative daily tone (zero-filled
    across the calendar), trailing Z_WINDOW."""
    tone_d = tone.set_index("day").reindex(cal_days).fillna({"tone": 0.0})
    tone_log = np.log1p(tone_d["tone"].clip(lower=0.0))
    tmu = tone_log.rolling(Z_WINDOW, min_periods=Z_MIN_OBS).mean()
    tsd = tone_log.rolling(Z_WINDOW, min_periods=Z_MIN_OBS).std()
    tz = np.where(tsd > 0, (tone_log - tmu) / tsd, 0.0)
    return pd.Series(tz, index=tone_log.index)


# ---------------------------------------------------------------------------
#  Market scopes (kind 0 market_all / kind 1 market_recap)
# ---------------------------------------------------------------------------

class MarketMetricStep:
    """Build + write the two market-kind daily rows.

    Full metric set per trading day: blended level (n_scored >= LEVEL_MIN_SCORED
    guard), trailing z, ewm halflife 3/10 lines, day change, abnormal-flow
    median/IQR flag, sign-split counts, origin dispersion, keyword-tone z.
    """

    title = "text.news_text_daily market scopes"

    async def run(
        self,
        conn,
        scored: pd.DataFrame,
        coverage: pd.DataFrame,
        cal_days: np.ndarray,
        tone: pd.DataFrame,
        target_days: set[int],
    ) -> int:
        tone_z = _tone_z(tone, cal_days)
        frames = [
            self._one_scope(
                KIND_MARKET_ALL, MARKET_INDEX_CODE, scored, coverage,
                cal_days, tone_z, None,
            ),
            self._one_scope(
                KIND_MARKET_RECAP, MARKET_INDEX_CODE, scored, coverage,
                cal_days, tone_z, RECAP_TAG,
            ),
        ]
        out = pd.concat(frames)
        out = out[out["day"].isin(target_days)]
        return await self._write(conn, out)

    def _one_scope(
        self,
        kind: int,
        scope_code: str,
        scored: pd.DataFrame,
        coverage: pd.DataFrame,
        cal_days: np.ndarray,
        tone_z: pd.Series,
        industry_filter: str | None,
    ) -> pd.DataFrame:
        sub = scored
        if industry_filter is not None:
            sub = scored[scored["industry_id"] == industry_filter]

        sub = roll_to_calendar(sub, cal_days)

        # coverage basis: PRE-AGGREGATED audit rows (already calendar-
        # rolled) — market_all reads the '000001' rollup row, the recap
        # scope reads the BROAD_SSE industry row; zero-filled across the
        # calendar.
        cov_id = MARKET_ROLLUP if industry_filter is None else industry_filter
        cov = coverage[coverage["industry_id"] == cov_id]
        n_all = (
            cov.set_index("day")["n_articles"]
            .reindex(cal_days).fillna(0).astype("int64")
        )
        n_all.name = "n_articles"

        # scored aggregates: ONE grouped pass (sw = s * w from blend_scores)
        sub = _count_cols(sub, "m")
        g = sub.groupby("day")
        agg = pd.DataFrame({
            "n_scored": g.size(),
            "w_sum": g["w"].sum(),
            "sw_sum": g["sw"].sum(),
            "flow_pos": g["m_pos"].sum(),
            "flow_neg": g["m_neg"].sum(),
        }).reindex(cal_days).fillna(0)
        agg["n_scored"] = agg["n_scored"].astype("int64")

        # origin dispersion: std across per-origin means (>= 2 origins;
        # a single origin's std is NaN by definition)
        origin_mean = (
            sub.groupby(["day", "source"])["s"].mean().reset_index()
            .groupby("day")["s"].std()
            .reindex(cal_days)
            .rename("disp_origin")
        )

        daily = agg.join(n_all).join(origin_mean)

        # origin-dispersion state, z-space (the 2026-11-22 improvement
        # study's dispersion-confirmation axis): moments SHIFTED 1 row —
        # the day's own dispersion never sets its own bar (the study's
        # measured form; sent_z above keeps the day in-window, its state
        # convention). Zero-variance history reads NEUTRAL 0.
        dmu = origin_mean.rolling(Z_WINDOW, min_periods=DISP_Z_MIN_OBS).mean().shift(1)
        dsd = origin_mean.rolling(Z_WINDOW, min_periods=DISP_Z_MIN_OBS).std().shift(1)
        dz = np.where(dsd > 0, (origin_mean - dmu) / dsd.where(dsd > 0), 0.0)
        daily["disp_z"] = pd.Series(
            np.where(origin_mean.notna(), dz, np.nan), index=origin_mean.index)

        # sentiment state (level computes from LEVEL_MIN_SCORED so thin
        # days carry a reading; low_coverage still flags < SCORED_MIN;
        # z needs sd > 0)
        level = (daily["sw_sum"] / daily["w_sum"].replace(0.0, np.nan)).where(
            daily["n_scored"] >= LEVEL_MIN_SCORED
        )
        mu = level.rolling(Z_WINDOW, min_periods=Z_MIN_OBS).mean()
        sd = level.rolling(Z_WINDOW, min_periods=Z_MIN_OBS).std()
        # zero-variance history (a flat trailing window — sparse early
        # recaps) reads as NEUTRAL 0, not a hole: no deviation is
        # measurable off a flat history
        z = np.where(sd > 0, (level - mu) / sd, 0.0)
        z = pd.Series(np.where(level.notna(), z, np.nan), index=level.index)
        daily["sent_level"] = level
        daily["sent_z"] = z
        daily["sent_ema3"] = z.ewm(halflife=EMA3_HALFLIFE, ignore_na=True).mean()
        daily["sent_ema10"] = z.ewm(halflife=EMA10_HALFLIFE, ignore_na=True).mean()
        daily["sent_delta"] = level.diff()

        # abnormal flow (median/IQR of the coverage counts)
        med = n_all.rolling(FLOW_WINDOW, min_periods=FLOW_MIN_OBS).median()
        q1 = n_all.rolling(FLOW_WINDOW, min_periods=FLOW_MIN_OBS).quantile(0.25)
        q3 = n_all.rolling(FLOW_WINDOW, min_periods=FLOW_MIN_OBS).quantile(0.75)
        iqr = (q3 - q1)
        # zero-IQR history (flat trailing counts) reads as NEUTRAL 0 —
        # no dispersion to measure abnormality against
        daily["flow_af"] = np.where(iqr > 0, (n_all - med) / iqr.where(iqr > 0), 0.0)

        daily["lex_tone_z"] = tone_z

        daily["day"] = cal_days  # reindex preserved this exact order
        daily["scope_kind"] = np.int64(kind)
        daily["scope_code"] = scope_code
        return daily.reset_index(drop=True)

    async def _write(self, conn, frame: pd.DataFrame) -> int:
        rows = _to_rows(frame, curve_columns=True)
        if not rows:
            print(f"[{self.title}] no target-day rows")
            return 0
        written = await bulk_upsert_async(
            conn, TABLE, rows,
            key_columns=["date", "scope_kind", "scope_code"],
        )
        print(f"[{self.title}] upserted {written:,} market-kind rows")
        return written


# ---------------------------------------------------------------------------
#  Industry scope (kind 2 — event-day rows)
# ---------------------------------------------------------------------------

class IndustryEventStep:
    """Build + write the per-industry event-day rows.

    CO-MENTION attribution (the 2026-11-22 rework — see the module
    docstring): a row exists on days an industry is substantially
    co-mentioned by scored articles (>= MENTION_MIN_KW_CHARS matched
    keyword chars), not only when articles carry it as their primary
    tag — niche industries (高端制造/ADVMFG …) populate from the same
    corpus instead of losing every article to the financial primaries
    that mention them. BROAD_SSE mention rows drop (the recap tag —
    the recap PRIMARY is already excluded in the view; a non-recap
    article mentioning 上证 is a broad-market mention, not an industry
    event). The validated event-study framing holds; curve columns stay
    NULL (the trailing windows are meaningless at ~1-2
    mentions/industry-day) and low_coverage marks rows under
    SCORED_MIN. n_articles = the scored co-mention count (the audit's
    primary-tag basis has no per-mention equivalent).
    """

    title = "text.news_text_daily industry event days"

    async def run(
        self,
        conn,
        mentions: pd.DataFrame,
        cal_days: np.ndarray,
        target_days: set[int],
    ) -> int:
        sub = mentions[mentions["industry_id"] != RECAP_TAG]
        if sub.empty:
            print(f"[{self.title}] no industry co-mention rows in scope")
            return 0

        sub = roll_to_calendar(sub, cal_days)

        sub = _count_cols(sub, "i")
        g = sub.groupby(["industry_id", "day"])
        agg = pd.DataFrame({
            "n_scored": g.size(),
            "w_sum": g["w"].sum(),
            "sw_sum": g["sw"].sum(),
            "flow_pos": g["i_pos"].sum(),
            "flow_neg": g["i_neg"].sum(),
        }).reset_index()

        agg = agg[agg["day"].isin(target_days)]
        agg["n_articles"] = agg["n_scored"].astype("int64")
        agg["sent_level"] = agg["sw_sum"] / agg["w_sum"]
        agg["scope_kind"] = np.int64(KIND_INDUSTRY)
        agg["scope_code"] = agg["industry_id"]

        rows = _to_rows(agg, curve_columns=False)
        if not rows:
            print(f"[{self.title}] no target-day rows")
            return 0
        written = await bulk_upsert_async(
            conn, TABLE, rows,
            key_columns=["date", "scope_kind", "scope_code"],
        )
        print(f"[{self.title}] upserted {written:,} industry event rows "
              f"({agg['industry_id'].nunique()} industries)")
        return written


# ---------------------------------------------------------------------------
#  Sector scope (kind 3 — event-day rows, the industry rollup)
# ---------------------------------------------------------------------------

class SectorEventStep:
    """Build + write the per-sector event-day rows (kind 3).

    The industry CO-MENTION frame rolled UP the taxonomy to the L1
    parent sector (the catalog's industry->sector map — the tagging
    source of record): a row exists on days the SECTOR is substantially
    co-mentioned (the same event-day framing as the industry scope);
    curve columns stay NULL and low_coverage marks rows under
    SCORED_MIN. ONE ARTICLE COUNTS ONCE PER SECTOR even when it
    mentions several member industries (drop_duplicates on
    (news_id, sector_id) BEFORE the sign-split counts — otherwise a
    3-industry TECH article would triple every TECH aggregate).
    Industries the catalog does not know (orphan codes) drop.
    BROAD_SSE mentions are NOT skipped here (unlike the industry
    scope): their catalog sector IS BROAD, so broad-market mentions
    legitimately count toward the BROAD sector scope (the recap
    PRIMARY is already excluded in the view).

    n_articles = the scored co-mention count (the audit's primary-tag
    basis has no per-mention equivalent).
    """

    title = "text.news_text_daily sector event days"

    def _industry_sector_map(self) -> dict[str, str]:
        """industry_id -> L1 sector_id from the classification catalog
        (the tagging source of record; OTHER is the fallback bucket,
        never a tag — skipped like builds.text's matcher does)."""
        out: dict[str, str] = {}
        for sector_id, sector in classification_store.load_catalog().items():
            for industry_id in sector.get("industries", {}):
                if industry_id != "OTHER":
                    out[industry_id] = sector_id
        return out

    async def run(
        self,
        conn,
        mentions: pd.DataFrame,
        cal_days: np.ndarray,
        target_days: set[int],
    ) -> int:
        sub = mentions.copy()
        sub["sector_id"] = sub["industry_id"].map(self._industry_sector_map())
        sub = sub[sub["sector_id"].notna()]
        if sub.empty:
            print(f"[{self.title}] no mapped sector co-mention rows in scope")
            return 0
        # one article counts ONCE per sector (see the docstring)
        sub = sub.drop_duplicates(subset=["news_id", "sector_id"])

        sub = roll_to_calendar(sub, cal_days)

        sub = _count_cols(sub, "sec")
        g = sub.groupby(["sector_id", "day"])
        agg = pd.DataFrame({
            "n_scored": g.size(),
            "w_sum": g["w"].sum(),
            "sw_sum": g["sw"].sum(),
            "flow_pos": g["sec_pos"].sum(),
            "flow_neg": g["sec_neg"].sum(),
        }).reset_index()

        agg = agg[agg["day"].isin(target_days)]
        agg["n_articles"] = agg["n_scored"].astype("int64")
        agg["sent_level"] = agg["sw_sum"] / agg["w_sum"]
        agg["scope_kind"] = np.int64(KIND_SECTOR)
        agg["scope_code"] = agg["sector_id"]

        rows = _to_rows(agg, curve_columns=False)
        if not rows:
            print(f"[{self.title}] no target-day rows")
            return 0
        written = await bulk_upsert_async(
            conn, TABLE, rows,
            key_columns=["date", "scope_kind", "scope_code"],
        )
        print(f"[{self.title}] upserted {written:,} sector event rows "
              f"({agg['sector_id'].nunique()} sectors)")
        return written


# ---------------------------------------------------------------------------
#  Row materialization (the write boundary)
# ---------------------------------------------------------------------------

_CURVE_COLUMNS = [
    "sent_z", "sent_ema3", "sent_ema10", "sent_delta", "flow_af", "lex_tone_z",
    "disp_z",
]

_ROW_COLUMNS = [
    "date", "scope_kind", "scope_code", "n_articles", "n_scored",
    "sent_level", "sent_z", "sent_ema3", "sent_ema10", "sent_delta",
    "flow_af", "flow_pos", "flow_neg", "disp_origin", "disp_z", "lex_tone_z",
    "low_coverage",
]


def _to_rows(frame: pd.DataFrame, *, curve_columns: bool) -> list[dict]:
    """Materialize the row dicts for bulk_upsert_async.

    Dates convert at THIS boundary: day numbers -> epoch seconds ->
    datetime64[us] (one epoch_col_to_dt64 host pass) -> python dates via
    sanitize's date_cols branch. NULL-able columns stay NaN until the
    sanitize sweep turns them into None.
    """
    out = pd.DataFrame(index=frame.index)
    out["date"] = epoch_col_to_dt64(
        host_array(frame["day"]).astype("float64") * SECONDS_PER_DAY,
        unit="us",
        index=frame.index,
    )
    out["scope_kind"] = frame["scope_kind"].astype("int64")
    out["scope_code"] = frame["scope_code"]
    out["n_articles"] = frame["n_articles"].astype("int64")
    out["n_scored"] = frame["n_scored"].astype("int64")
    out["sent_level"] = frame["sent_level"].astype("float64")
    if curve_columns:
        for col in _CURVE_COLUMNS:
            out[col] = frame[col].astype("float64")
        out["disp_origin"] = frame["disp_origin"].astype("float64")
    else:
        for col in _CURVE_COLUMNS:
            out[col] = np.nan
        out["disp_origin"] = np.nan
    out["flow_pos"] = frame["flow_pos"].astype("int64")
    out["flow_neg"] = frame["flow_neg"].astype("int64")
    out["low_coverage"] = (frame["n_scored"] < SCORED_MIN).astype("bool")
    out = out[_ROW_COLUMNS]
    return sanitize_for_db_insert(out, date_cols=["date"], round_to=4)
