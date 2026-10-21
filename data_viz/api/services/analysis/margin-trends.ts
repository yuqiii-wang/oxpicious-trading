/**
 * Margin Trends analysis service — INDUSTRY-LEVEL.
 *
 * Two endpoints feed the single-industry margin trends page:
 *   • themes / strategy-themes — L1 sector → L2 industry tree from
 *     analysis.margin_industry_stats (industries WITH margin data only)
 *   • underlyings — ALL broad-market index codes
 *     (stats.sec_index_tags.is_broad_market) for the exchange
 *     attribution's mapped-underlying dropdown.
 *   • industry-series — per-(security, date) margin series for ONE
 *     industry + attribution. 'index' reads the margin_index_series
 *     TABLE (weighted-avg constituent-stock margin, built by Python
 *     vectorization); 'etf' reads stats.etf_liquidity_margin for the
 *     industry's ETFs; 'industry' reads analysis.margin_industry_stats —
 *     the industry's OWN margin curve as ONE series (code = industry_id;
 *     the static.label_dim sec_type=3 contract): raw (default) =
 *     SUM(stock+etf), ?normalize=true = SUM of the per-side member
 *     AVERAGES (*_norm cols, non-null non-zero members) — with the
 *     composite close from stats.industry_basic_stats
 *     (mean_close, pool_size='all') for the amplification view;
 *     'exchange' reads analysis.exchange_margin_summary with the close /
 *     turnover / regime of the ?underlying= broad-market index
 *     (default 沪深300 000300).
 *   • trends — sustained UP/DOWN trend episodes from
 *     analysis.margin_changes (shade overlay on the series plot).
 *     SECTIONED detection: episodes never span a null-balance gap
 *     (line break) or a contributing-membership change on aggregate
 *     curves (the member-change dots) — a population jump is never
 *     attributed as a margin rise/drop.
 *
 * Sources:
 *   analysis.margin_industry_stats        — industry tree + 'industry' series
 *   analysis.margin_index_series (TABLE)  — 'index' series
 *   stats.etf_liquidity_margin            — 'etf' series
 *   stats.industry_basic_stats            — 'industry' composite close/turnover
 *   analysis.margin_changes               — trend episodes
 *
 * RONGZI (融资 / cash-borrow) only — RONQIN (融券 / sec borrow) EXCLUDED.
 */
import { queryRows, formatDate, toNum } from "../../lib/db.js";
import type { QueryResultRow } from "pg";
import { buildStrategyThemesFromRows } from "../_shared.js";
import { labelDecodeSql } from "../label-codes.js";
import type {
  MarginIndustrySeriesResponse,
  MarginTrendsShadeResponse,
  MarginSeriesRow,
  MarginSecurity,
  MarginUnderlyingEntry,
  MarginAttributionType,
  MarketRegime,
  SectorNode,
  IndustryNode,
  StrategyNode,
} from "../../../shared/types.js";

// ----------------------------------------------------------------------------
//  DB row types
// ----------------------------------------------------------------------------
interface DbMetaRow extends QueryResultRow {
  industry_id: string;
  industry_label: string;
  sector_id: string;
  sector_label: string;
  industry_slug: string;
  is_industry_not_strategy: boolean;
}

interface DbIndexSeriesRow extends QueryResultRow {
  index_code: string;
  date: Date | string;
  index_margin_balance: string | number | null;
  index_margin_buy: string | number | null;
  close: string | number | null;
  trading_amount: string | number | null;
  regime: string | null;
}

interface DbEtfSeriesRow extends QueryResultRow {
  code: string;
  date: Date | string;
  rz_balance: string | number | null;
  rz_buy: string | number | null;
  close: string | number | null;
  trading_amount: string | number | null;
  regime: string | null;
}

interface DbSecurityRow extends QueryResultRow {
  industry_id: string;
  code: string;
  name: string;
}

/** Narrow a decoded regime label to the closed MarketRegime vocabulary
 *  (labelDecodeSql returns TEXT; anything else becomes null). */
const REGIME_LABEL_SET = new Set(["calm", "hot", "panic", "quiet"]);
function toRegime(v: string | null): MarketRegime | null {
  return v != null && REGIME_LABEL_SET.has(v) ? (v as MarketRegime) : null;
}

/** Narrow a raw ?attribution= query value to the closed vocabulary —
 *  anything unrecognized falls back to 'index' (the page default). */
function narrowAttribution(raw: string | undefined): MarginAttributionType {
  if (raw === "etf") return "etf";
  if (raw === "industry") return "industry";
  if (raw === "exchange") return "exchange";
  return "index";
}

// ----------------------------------------------------------------------------
//  META_SQL — one row per industry that has rows in
//  analysis.margin_industry_stats, JOINed with stats.sec_classification
//  (type='index') to recover sector_id / sector_label / industry_slug /
//  is_industry_not_strategy.
// ----------------------------------------------------------------------------
const META_SQL = `
  WITH margin_industries AS (
    SELECT DISTINCT industry_id, industry_label
    FROM analysis.margin_industry_stats
  )
  SELECT
    mi.industry_id,
    COALESCE(NULLIF(mi.industry_label, ''), mi.industry_id) AS industry_label,
    COALESCE(sc.sector_id,              'OTHER')  AS sector_id,
    COALESCE(sc.sector_label,           '其他')   AS sector_label,
    COALESCE(sc.industry_slug,          LOWER(mi.industry_id)) AS industry_slug,
    COALESCE(sc.is_industry_not_strategy, TRUE)  AS is_industry_not_strategy
  FROM margin_industries mi
  LEFT JOIN LATERAL (
    SELECT sector_id, sector_label, industry_slug, is_industry_not_strategy
    FROM stats.sec_classification
    WHERE industry_id = mi.industry_id
      AND type = 'index'
      AND is_active = TRUE
    LIMIT 1
  ) sc ON TRUE
`;

// ----------------------------------------------------------------------------
//  ITEMS_SQL — securities per industry, parametrized by attribution.
//    attribution='index' → distinct index_code from margin_index_series TABLE
//    attribution='etf'   → ETF codes from sec_classification
//  Returns (industry_id, code, name) for populating the L3 items[] in each
//  IndustryNode, so SecClassificationNav shows non-zero counts + clickable
//  security chips when an industry is selected.
//
//  The DISTINCT runs BEFORE the sec_classification LATERAL name lookup:
//  deduping first gives ~200 lateral probes instead of one per raw series
//  row (~325k), which made the themes endpoint take 370-670ms (2026-09-29).
// ----------------------------------------------------------------------------
const INDEX_ITEMS_SQL = `
  WITH distinct_codes AS (
    SELECT DISTINCT industry_id, index_code
    FROM analysis.margin_index_series
    WHERE industry_id IS NOT NULL AND industry_id <> ''
  )
  SELECT d.industry_id,
         d.index_code AS code,
         COALESCE(NULLIF(sc.name, ''), d.index_code) AS name
  FROM distinct_codes d
  LEFT JOIN LATERAL (
    SELECT name FROM stats.sec_classification
    WHERE code = d.index_code AND type = 'index' AND is_active = TRUE
    LIMIT 1
  ) sc ON TRUE
  ORDER BY d.index_code
`;

const ETF_ITEMS_SQL = `
  SELECT sc.industry_id,
         sc.code,
         COALESCE(NULLIF(sc.name, ''), sc.code) AS name
  FROM stats.sec_classification sc
  WHERE sc.type = 'etf'
    AND sc.parent_index_is_primary = TRUE
    AND sc.is_active = TRUE
    AND sc.industry_id IS NOT NULL AND sc.industry_id <> ''
  ORDER BY sc.code
`;

// ----------------------------------------------------------------------------
//  listMarginTrendThemes — L1 sector → L2 industry tree, restricted to
//  industries that have rows in analysis.margin_industry_stats. Each industry
//  node is populated with items[] (securities) based on attribution, so the
//  L3 column shows indices or ETFs and the count is non-zero.
// ----------------------------------------------------------------------------
export async function listMarginTrendThemes(
  rawAttribution?: string,
): Promise<SectorNode[]> {
  const attribution = narrowAttribution(rawAttribution);

  const [rows, itemRows] = await Promise.all([
    queryRows<DbMetaRow>(META_SQL, []),
    queryRows<DbSecurityRow>(
      attribution === "etf" ? ETF_ITEMS_SQL : INDEX_ITEMS_SQL,
      [],
    ),
  ]);

  // Group items by industry_id.
  const itemsByIndustry = new Map<string, Array<{ code: string; name: string }>>();
  for (const r of itemRows) {
    if (!itemsByIndustry.has(r.industry_id)) {
      itemsByIndustry.set(r.industry_id, []);
    }
    itemsByIndustry.get(r.industry_id)!.push({ code: r.code, name: r.name });
  }

  const sectorMap = new Map<string, {
    sector_label: string;
    industries: Map<string, IndustryNode>;
  }>();

  for (const r of rows) {
    // ALL classification types (industries, broad-markets, strategies)
    // are included — no is_industry_not_strategy filter. The LEFT column
    // shows the full universe; the RIGHT column (strategy-themes) still
    // filters to is_industry_not_strategy=FALSE for strategies only.
    if (!sectorMap.has(r.sector_id)) {
      sectorMap.set(r.sector_id, {
        sector_label: r.sector_label,
        industries: new Map(),
      });
    }
    const sector = sectorMap.get(r.sector_id)!;
    if (!sector.industries.has(r.industry_id)) {
      const items = itemsByIndustry.get(r.industry_id) ?? [];
      sector.industries.set(r.industry_id, {
        industry_id: r.industry_id,
        industry_label: r.industry_label,
        industry_slug: r.industry_slug,
        count: items.length,
        items,
      });
    }
  }

  const sectors: SectorNode[] = [];
  for (const [sector_id, sector] of sectorMap) {
    const industries = Array.from(sector.industries.values()).sort((a, b) => {
      if (a.industry_id === "OTHER") return 1;
      if (b.industry_id === "OTHER") return -1;
      return a.industry_label.localeCompare(b.industry_label);
    });
    sectors.push({
      sector_id,
      sector_label: sector.sector_label,
      count: industries.length,
      industries,
    });
  }
  sectors.sort((a, b) => {
    if (a.sector_id === "OTHER") return 1;
    if (b.sector_id === "OTHER") return -1;
    return a.sector_label.localeCompare(b.sector_label);
  });
  return sectors;
}

// ----------------------------------------------------------------------------
//  listMarginTrendStrategyThemes — parallel L1 strategy → L2 theme tree.
// ----------------------------------------------------------------------------
export async function listMarginTrendStrategyThemes(): Promise<StrategyNode[]> {
  const rows = await queryRows<DbMetaRow>(META_SQL, []);

  const filteredRows = rows
    .filter((r) => !r.is_industry_not_strategy)
    .map((r) => ({
      code: r.industry_id,
      name: r.industry_label,
      sector_id: r.sector_id,
      sector_label: r.sector_label,
      industry_id: r.industry_id,
      industry_label: r.industry_label,
      industry_slug: r.industry_slug,
      is_industry_not_strategy: r.is_industry_not_strategy,
    }));

  return buildStrategyThemesFromRows(filteredRows);
}

// ----------------------------------------------------------------------------
//  getMarginIndustrySeries — per-(security, date) margin series for ONE
//  industry + ONE attribution. Returns the securities list (codes + labels)
//  and the full daily series, so the series plot can render one line per
//  security.
//
//  attribution='index' → analysis.margin_index_series TABLE (weighted-avg
//    constituent-stock margin per index_code). Securities = distinct
//    index_code values with their sec_classification.name label.
//  attribution='etf'   → stats.etf_liquidity_margin for the industry's
//    ETFs (via sec_classification parent_index_is_primary). Securities =
//    the industry's ETF codes with their name labels.
// ----------------------------------------------------------------------------
//  The close join is a LEFT JOIN LATERAL per-row PK probe on
//  index_basic_stats/etf_basic_stats: a plain JOIN made the planner
//  hash-join and seq-scan the whole basic-stats table (~830k index /
//  all etf rows) per request — the ETF branch took 2.6-3.1s (2026-09-29).
// ----------------------------------------------------------------------------
const INDEX_SERIES_SQL = `
  SELECT s.index_code, s.date, s.index_margin_balance, s.index_margin_buy,
         ib.close, ib.trading_amount, mr.regime
  FROM analysis.margin_index_series s
  LEFT JOIN LATERAL (
    SELECT close, trading_amount FROM stats.index_basic_stats b
    WHERE b.code = s.index_code AND b.date = s.date
    LIMIT 1
  ) ib ON TRUE
  LEFT JOIN LATERAL (
    SELECT ${labelDecodeSql("regime", "r.regime")} AS regime
    FROM stats.market_regimes r
    -- sec_type stores its static.label_dim SMALLINT code (1 = index)
    WHERE r.code = s.index_code AND r.date = s.date AND r.sec_type = 1
    LIMIT 1
  ) mr ON TRUE
  WHERE s.industry_id = $1::text
  ORDER BY s.index_code, s.date
`;

const INDEX_SECURITIES_SQL = `
  SELECT DISTINCT s.index_code AS code,
         COALESCE(NULLIF(sc.name, ''), s.index_code) AS name
  FROM analysis.margin_index_series s
  LEFT JOIN LATERAL (
    SELECT name FROM stats.sec_classification
    WHERE code = s.index_code AND type = 'index' AND is_active = TRUE
    LIMIT 1
  ) sc ON TRUE
  WHERE s.industry_id = $1::text
  ORDER BY code
`;

const ETF_SECURITIES_SQL = `
  SELECT sc.code, COALESCE(NULLIF(sc.name, ''), sc.code) AS name
  FROM stats.sec_classification sc
  WHERE sc.type = 'etf'
    AND sc.industry_id = $1::text
    AND sc.parent_index_is_primary = TRUE
    AND sc.is_active = TRUE
  ORDER BY sc.code
`;

const ETF_SERIES_SQL = `
  SELECT e.code, e.date, e.rz_balance, e.rz_buy, e.trading_amount, eb.close,
         mr.regime
  FROM stats.etf_liquidity_margin e
  LEFT JOIN LATERAL (
    SELECT close FROM stats.etf_basic_stats b
    WHERE b.code = e.code AND b.date = e.date
    LIMIT 1
  ) eb ON TRUE
  LEFT JOIN LATERAL (
    SELECT ${labelDecodeSql("regime", "r.regime")} AS regime
    FROM stats.market_regimes r
    -- sec_type stores its static.label_dim SMALLINT code (0 = etf)
    WHERE r.code = e.code AND r.date = e.date AND r.sec_type = 0
    LIMIT 1
  ) mr ON TRUE
  WHERE e.code IN (
    SELECT sc.code FROM stats.sec_classification sc
    WHERE sc.type = 'etf'
      AND sc.industry_id = $1::text
      AND sc.parent_index_is_primary = TRUE
      AND sc.is_active = TRUE
  )
  ORDER BY e.code, e.date
`;

// ----------------------------------------------------------------------------
//  'industry' attribution — the industry's OWN margin curve. ONE series
//  per industry (code = industry_id, the sec_type=3 label contract).
//  Two flavors via the ?normalize= param:
//    raw (default)  — balance/buy are the SUM(stock+etf) aggregates
//      already materialized in margin_industry_stats (0/NULL per side =
//      no contribution on that date — COALESCE-sums to the contributing
//      side; an all-NULL day NULLIFs to NULL so the line BREAKS instead
//      of dipping to 0);
//    normalized — the SUM of the per-side member AVERAGES
//      (stock_margin_*_norm + etf_margin_*_norm; each AVG runs over that
//      side's members with a NON-NULL NON-ZERO rz_* on the date). The
//      per-member-normalized curve — comparable across industries with
//      different member counts. Note the two sides' averages are summed
//      (stock side + ETF side), NOT averaged together. n_members rides
//      along (members behind the average; see the SQL below) — the
//      tooltip shows it in normalized mode.
//  close/trading_amount always come from the industry composite in
//  stats.industry_basic_stats (pool_size = 3 'all' slice); the
//  amplification analytics (daily returns, buy-share terciles) always
//  derive client-side from the RAW response — the Raw | Normalized toggle
//  switches the margin LINES only.
//  regime joins stats.market_regimes sec_type=3 (the industry composite's
//  own regime labels, built by builds.market_regimes --sec-type industry)
//  so the companion chart's regime×tercile split renders here too.
// ----------------------------------------------------------------------------
interface DbIndustrySeriesRow extends QueryResultRow {
  industry_id: string;
  date: Date | string;
  margin_balance: string | number | null;
  margin_buy: string | number | null;
  close: string | number | null;
  trading_amount: string | number | null;
  /** builds.market_regimes sec_type=3 label (decoded) — the industry
   *  composite's own regime; feeds the hyped-vs-drained regime split. */
  regime: string | null;
  /** Normalized variant only — members behind the average (sum/avg per side). */
  n_members?: number | string | null;
}

const INDUSTRY_SERIES_JOIN_SQL = `
  FROM analysis.margin_industry_stats mi
  LEFT JOIN LATERAL (
    SELECT mean_close, total_trading_amount
    FROM stats.industry_basic_stats b
    -- pool_size = 3 is the 'all' slice (static.label_dim 'pool_size')
    WHERE b.industry_id = mi.industry_id AND b.date = mi.date
      AND b.pool_size = 3
    LIMIT 1
  ) ib ON TRUE
  LEFT JOIN LATERAL (
    SELECT ${labelDecodeSql("regime", "r.regime")} AS regime
    FROM stats.market_regimes r
    -- sec_type stores its static.label_dim SMALLINT code (3 = industry)
    WHERE r.code = mi.industry_id AND r.date = mi.date AND r.sec_type = 3
    LIMIT 1
  ) mr ON TRUE
  WHERE mi.industry_id = $1::text
  ORDER BY mi.industry_id, mi.date
`;

function industrySeriesSql(
  balanceExpr: string,
  buyExpr: string,
  extraSelect = "",
): string {
  return `
  SELECT mi.industry_id, mi.date,
         ${balanceExpr} AS margin_balance,
         ${buyExpr}     AS margin_buy,
         ib.mean_close AS close, ib.total_trading_amount AS trading_amount,
         mr.regime
         ${extraSelect}
  ${INDUSTRY_SERIES_JOIN_SQL}
`;
}

// n_members — the number of member securities behind that date's averages.
// Not stored; derived EXACTLY from the stored pair as sum / avg per side
// (both cover the same non-null non-zero members), so rounding can never
// flip the integer (norm keeps 4 decimals on ~1e8+ yuan magnitudes).
// Selected by BOTH variants — the member-count change dots render in raw
// mode too (a membership change shifts the SUM curve as well).
const INDUSTRY_N_MEMBERS_EXPR = `
(CASE WHEN COALESCE(mi.stock_margin_balance_norm, 0) > 0
           AND mi.stock_margin_balance IS NOT NULL
      THEN ROUND(mi.stock_margin_balance / mi.stock_margin_balance_norm)::int
      ELSE 0 END
+ CASE WHEN COALESCE(mi.etf_margin_balance_norm, 0) > 0
           AND mi.etf_margin_balance IS NOT NULL
      THEN ROUND(mi.etf_margin_balance / mi.etf_margin_balance_norm)::int
      ELSE 0 END) AS n_members`;

const INDUSTRY_SERIES_SQL = industrySeriesSql(
  "NULLIF(COALESCE(mi.stock_margin_balance, 0)\n" +
  "       + COALESCE(mi.etf_margin_balance, 0), 0)",
  "NULLIF(COALESCE(mi.stock_margin_buy, 0)\n" +
  "       + COALESCE(mi.etf_margin_buy, 0), 0)",
  `,
         ${INDUSTRY_N_MEMBERS_EXPR}`,
);

const INDUSTRY_SERIES_NORM_SQL = industrySeriesSql(
  "NULLIF(COALESCE(mi.stock_margin_balance_norm, 0)\n" +
  "       + COALESCE(mi.etf_margin_balance_norm, 0), 0)",
  "NULLIF(COALESCE(mi.stock_margin_buy_norm, 0)\n" +
  "       + COALESCE(mi.etf_margin_buy_norm, 0), 0)",
  `,
         ${INDUSTRY_N_MEMBERS_EXPR}`,
);

const INDUSTRY_SECURITIES_SQL = `
  SELECT industry_id AS code,
         COALESCE(NULLIF(industry_label, ''), industry_id) AS name
  FROM analysis.margin_industry_stats
  WHERE industry_id = $1::text
  LIMIT 1
`;

// ----------------------------------------------------------------------------
//  'exchange' attribution — the exchange's OWN market-wide margin totals
//  from analysis.exchange_margin_summary (ONE series, code = the exchange
//  code 'SS'/'SZ'; the industry_id param carries the exchange code).
//  These are the OFFICIAL exchange summary totals — NOT derivable from the
//  per-security detail exports (systematic ~0.5-0.75% gap).
//
//  MAPPED UNDERLYING (user-selectable broad-market index): exchange rows
//  carry no composite price/turnover of their own, so the daily-return +
//  hyped-vs-drained views read close/trading_amount from
//  stats.index_basic_stats and the regime label from stats.market_regimes
//  of the underlying (sec_type=1), the same underlying-join pattern as the
//  'index'/'etf' attributions. The underlying is ANY broad-market index
//  (stats.sec_index_tags.is_broad_market = TRUE) passed as the ?underlying=
//  param; a missing / non-broad code falls back to 沪深300 (000300) —
//  validated inside the SQL so one round-trip both resolves and guards the
//  code. The buyShare (exchange total rz_buy / underlying whole-index
//  turnover) is scale-offset — fine for RANK-based tercile bucketing (same
//  caveat as the index attribution, see MarginSeriesRow.trading_amount).
// ----------------------------------------------------------------------------
interface DbExchangeSeriesRow extends QueryResultRow {
  exchange: string;
  date: Date | string;
  rz_balance: string | number | null;
  rz_buy: string | number | null;
  close: string | number | null;
  trading_amount: string | number | null;
  regime: string | null;
  underlying_code: string;
  underlying_name: string | null;
}

/** 沪深300 — the default market-wide composite proxy underlying for BOTH
 *  exchanges' daily-return / hyped-vs-drained views (spans SSE + SZSE),
 *  used when the request omits ?underlying= or passes a non-broad code. */
const DEFAULT_EXCHANGE_UNDERLYING = "000300";

const EXCHANGE_SERIES_SQL = `
  WITH underlying AS (
    SELECT u.code,
           COALESCE(NULLIF(sc.name, ''), u.code) AS name
    FROM (
      SELECT CASE WHEN EXISTS (
        SELECT 1 FROM stats.sec_index_tags t
        WHERE t.code = $2::text AND t.is_broad_market = TRUE
      ) THEN $2::text
         ELSE '${DEFAULT_EXCHANGE_UNDERLYING}' END AS code
    ) u
    LEFT JOIN LATERAL (
      SELECT name FROM stats.sec_classification
      WHERE code = u.code AND type = 'index' AND is_active = TRUE
      LIMIT 1
    ) sc ON TRUE
  )
  SELECT em.exchange, em.date, em.rz_balance, em.rz_buy,
         ib.close, ib.trading_amount, mr.regime,
         (SELECT code FROM underlying) AS underlying_code,
         (SELECT name FROM underlying) AS underlying_name
  FROM analysis.exchange_margin_summary em
  LEFT JOIN LATERAL (
    SELECT close, trading_amount FROM stats.index_basic_stats b
    WHERE b.code = (SELECT code FROM underlying) AND b.date = em.date
    LIMIT 1
  ) ib ON TRUE
  LEFT JOIN LATERAL (
    SELECT ${labelDecodeSql("regime", "r.regime")} AS regime
    FROM stats.market_regimes r
    -- sec_type stores its static.label_dim SMALLINT code (1 = index)
    WHERE r.code = (SELECT code FROM underlying) AND r.date = em.date AND r.sec_type = 1
    LIMIT 1
  ) mr ON TRUE
  WHERE em.exchange = $1::text
  ORDER BY em.date
`;

/** Chart-title / legend label for the exchange attribution: the exchange
 *  names the series (its official margin totals are the plotted curve);
 *  the mapped underlying (user-selected broad-market index) rides the
 *  separate `underlying_label` response field so the chart title shows
 *  BOTH. */
const EXCHANGE_LABELS: Record<string, string> = {
  SS: "上交所 (SSE)",
  SZ: "深交所 (SZSE)",
};

// ----------------------------------------------------------------------------
//  listMarginTrendUnderlyings — ALL broad-market index codes
//  (stats.sec_index_tags, BOOL_OR(is_broad_market) = TRUE) with display
//  names from stats.sec_classification. Restricted to codes that HAVE
//  index_basic_stats rows — the DUMMY_* synthetic orphan-ETF parents are
//  tagged broad-market too but carry no OHLC, so they would render an
//  empty daily-return view. Drives the exchange attribution's underlying
//  dropdown (which index's close/turnover/regime the daily-return +
//  hyped-vs-drained views map onto). Membership in this list is exactly
//  what the series endpoint's SQL accepts as ?underlying=.
// ----------------------------------------------------------------------------
interface DbUnderlyingRow extends QueryResultRow {
  code: string;
  name: string;
}

const UNDERLYINGS_SQL = `
  WITH broad AS (
    SELECT code
    FROM stats.sec_index_tags
    GROUP BY code
    HAVING BOOL_OR(is_broad_market) = TRUE
  )
  SELECT b.code,
         COALESCE(NULLIF(sc.name, ''), b.code) AS name
  FROM broad b
  LEFT JOIN LATERAL (
    SELECT name FROM stats.sec_classification
    WHERE code = b.code AND type = 'index' AND is_active = TRUE
    LIMIT 1
  ) sc ON TRUE
  WHERE EXISTS (
    SELECT 1 FROM stats.index_basic_stats ibs WHERE ibs.code = b.code
  )
  ORDER BY b.code
`;

export async function listMarginTrendUnderlyings(): Promise<MarginUnderlyingEntry[]> {
  const rows = await queryRows<DbUnderlyingRow>(UNDERLYINGS_SQL, []);
  return rows.map((r) => ({ code: r.code, name: r.name }));
}

/** Narrow the raw ?normalize= query value — only "1"/"true" enable the
 *  normalized curve (anything else falls back to raw). */
function isNormalizeParam(raw: string | undefined): boolean {
  return raw === "1" || raw === "true";
}

export async function getMarginIndustrySeries(
  rawIndustryId: string,
  rawAttribution: string,
  rawUnderlying?: string,
  rawNormalize?: string,
): Promise<MarginIndustrySeriesResponse> {
  const industryId = (rawIndustryId ?? "").trim();
  if (!industryId) {
    throw new Error("industry_id is required.");
  }
  const attribution = narrowAttribution(rawAttribution);
  // 'exchange' underlying — resolved + broad-market-validated inside the
  // series SQL (falls back to 沪深300 when absent / not broad-market).
  const underlyingCode = (rawUnderlying ?? "").trim() || DEFAULT_EXCHANGE_UNDERLYING;
  // Normalized curve — consumed by the 'industry' attribution only (the
  // *_norm member-average columns live in margin_industry_stats); the
  // other attributions ignore the flag (index is already a weighted
  // average; etf/exchange have no per-member norm).
  const normalize = isNormalizeParam(rawNormalize);

  let securities: MarginSecurity[] = [];
  let seriesRows: MarginSeriesRow[] = [];
  let industryLabel = industryId;
  let underlyingLabel: string | undefined;
  let underlyingCodeOut: string | undefined;

  if (attribution === "index") {
    // Securities + series in parallel (the label probe needs securities).
    const [secRows, rows] = await Promise.all([
      queryRows<DbSecurityRow>(INDEX_SECURITIES_SQL, [industryId]),
      queryRows<DbIndexSeriesRow>(INDEX_SERIES_SQL, [industryId]),
    ]);
    securities = secRows.map((r) => ({ code: r.code, label: r.name }));

    seriesRows = rows.map((r) => ({
      code: r.index_code,
      date: formatDate(r.date),
      balance: toNum(r.index_margin_balance),
      buy: toNum(r.index_margin_buy),
      close: toNum(r.close),
      trading_amount: toNum(r.trading_amount),
      regime: toRegime(r.regime),
    }));

    // Recover industry_label from the first index's sec_classification row.
    if (securities.length > 0) {
      const labelRow = await queryRows<{ industry_label: string }>(
        `SELECT industry_label FROM stats.sec_classification
         WHERE code = $1 AND type = 'index' AND is_active = TRUE LIMIT 1`,
        [securities[0].code],
      );
      if (labelRow.length > 0 && labelRow[0].industry_label) {
        industryLabel = labelRow[0].industry_label;
      }
    }
  } else if (attribution === "etf") {
    const [secRows, rows] = await Promise.all([
      queryRows<DbSecurityRow>(ETF_SECURITIES_SQL, [industryId]),
      queryRows<DbEtfSeriesRow>(ETF_SERIES_SQL, [industryId]),
    ]);
    securities = secRows.map((r) => ({ code: r.code, label: r.name }));

    seriesRows = rows.map((r) => ({
      code: r.code,
      date: formatDate(r.date),
      balance: toNum(r.rz_balance),
      buy: toNum(r.rz_buy),
      close: toNum(r.close),
      trading_amount: toNum(r.trading_amount),
      regime: toRegime(r.regime),
    }));

    if (securities.length > 0) {
      const labelRow = await queryRows<{ industry_label: string }>(
        `SELECT industry_label FROM stats.sec_classification
         WHERE code = $1 AND type = 'etf' AND is_active = TRUE LIMIT 1`,
        [securities[0].code],
      );
      if (labelRow.length > 0 && labelRow[0].industry_label) {
        industryLabel = labelRow[0].industry_label;
      }
    }
  } else if (attribution === "exchange") {
    // 'exchange' — ONE series: the exchange's official market-wide totals
    // (code = 'SS'/'SZ'; the industry_id param IS the exchange code). The
    // mapped broad-market underlying (?underlying=) feeds the close /
    // turnover / regime joins and the response's underlying fields.
    const rows = await queryRows<DbExchangeSeriesRow>(
      EXCHANGE_SERIES_SQL, [industryId.toUpperCase(), underlyingCode],
    );
    industryLabel = EXCHANGE_LABELS[industryId.toUpperCase()] ?? industryId;
    securities = [{ code: industryId.toUpperCase(), label: industryLabel }];
    if (rows.length > 0) {
      // The SQL echoes the RESOLVED underlying (post broad-market fallback).
      underlyingCodeOut = rows[0].underlying_code;
      underlyingLabel = rows[0].underlying_name ?? rows[0].underlying_code;
    } else {
      underlyingCodeOut = underlyingCode;
      underlyingLabel = underlyingCode;
    }

    seriesRows = rows.map((r) => ({
      code: r.exchange,
      date: formatDate(r.date),
      balance: toNum(r.rz_balance),
      buy: toNum(r.rz_buy),
      close: toNum(r.close),
      trading_amount: toNum(r.trading_amount),
      regime: toRegime(r.regime),
    }));
  } else {
    // 'industry' — ONE series: the industry's own curve (code =
    // industry_id). ?normalize=true switches to the SUM of the per-side
    // member averages (*_norm columns); raw (default) is the SUM(stock+
    // etf) curve. The industry label rides the series query, so no extra
    // classification probe is needed.
    const [secRows, rows] = await Promise.all([
      queryRows<DbSecurityRow>(INDUSTRY_SECURITIES_SQL, [industryId]),
      queryRows<DbIndustrySeriesRow>(
        normalize ? INDUSTRY_SERIES_NORM_SQL : INDUSTRY_SERIES_SQL,
        [industryId],
      ),
    ]);
    securities = secRows.map((r) => ({ code: r.code, label: r.name }));
    if (securities.length > 0 && securities[0].label) {
      industryLabel = securities[0].label;
    }

    seriesRows = rows.map((r) => ({
      code: r.industry_id,
      date: formatDate(r.date),
      balance: toNum(r.margin_balance),
      buy: toNum(r.margin_buy),
      close: toNum(r.close),
      trading_amount: toNum(r.trading_amount),
      // Members behind that date's averages (sum/avg per side) — both
      // variants carry it; drives the member-count change dots.
      n_members: toNum(r.n_members),
      // The industry composite's own regime (builds.market_regimes
      // sec_type=3) — feeds the hyped-vs-drained regime split.
      regime: toRegime(r.regime),
    }));
  }

  return {
    industry_id: industryId,
    industry_label: industryLabel,
    underlying_label: underlyingLabel,
    underlying_code: underlyingCodeOut,
    attribution,
    normalized: normalize && attribution === "industry",
    securities,
    rows: seriesRows,
  };
}

// ----------------------------------------------------------------------------
//  getMarginTrends — sustained UP/DOWN TREND EPISODES for the securities in
//  ONE industry + ONE attribution, from analysis.margin_changes. Returns
//  (code, start_date, end_date, is_trend_up_not_down) per episode so the
//  1st plot can render a light shade (markArea) over each trend window.
//
//  attribution='index'    → sec_type='index', codes from margin_index_series
//  attribution='etf'      → sec_type='etf', codes from sec_classification
//  attribution='industry' → sec_type='industry', code = industry_id
// ----------------------------------------------------------------------------
interface DbTrendRow extends QueryResultRow {
  code: string;
  start_date: Date | string;
  end_date: Date | string;
  is_trend_up_not_down: boolean;
  rz_buy_vs_trading_amt_ratio: string | number | null;
}

const INDEX_TRENDS_SQL = `
  WITH industry_codes AS (
    SELECT DISTINCT index_code AS code
    FROM analysis.margin_index_series
    WHERE industry_id = $1::text
  )
  SELECT t.code, t.start_date, t.end_date, t.is_trend_up_not_down,
         t.rz_buy_vs_trading_amt_ratio
  FROM analysis.margin_changes t
  JOIN industry_codes ic ON ic.code = t.code
  WHERE t.sec_type = 1 -- static.label_dim 'sec_type': index
  ORDER BY t.start_date
`;

const ETF_TRENDS_SQL = `
  WITH industry_codes AS (
    SELECT sc.code
    FROM stats.sec_classification sc
    WHERE sc.type = 'etf'
      AND sc.industry_id = $1::text
      AND sc.parent_index_is_primary = TRUE
      AND sc.is_active = TRUE
  )
  SELECT t.code, t.start_date, t.end_date, t.is_trend_up_not_down,
         t.rz_buy_vs_trading_amt_ratio
  FROM analysis.margin_changes t
  JOIN industry_codes ic ON ic.code = t.code
  WHERE t.sec_type = 0 -- static.label_dim 'sec_type': etf
  ORDER BY t.start_date
`;

const INDUSTRY_TRENDS_SQL = `
  SELECT t.code, t.start_date, t.end_date, t.is_trend_up_not_down,
         t.rz_buy_vs_trading_amt_ratio
  FROM analysis.margin_changes t
  WHERE t.code = $1::text
    AND t.sec_type = 3 -- static.label_dim 'sec_type': industry
  ORDER BY t.start_date
`;

export async function getMarginTrends(
  rawIndustryId: string,
  rawAttribution: string,
): Promise<MarginTrendsShadeResponse> {
  const industryId = (rawIndustryId ?? "").trim();
  if (!industryId) {
    throw new Error("industry_id is required.");
  }
  const attribution = narrowAttribution(rawAttribution);

  // 'exchange' — analysis.margin_changes has no exchange rows (the trend
  // build covers etf/stock/index/industry only) → no shade episodes.
  if (attribution === "exchange") {
    return { industry_id: industryId, attribution, episodes: [] };
  }

  const sql =
    attribution === "etf" ? ETF_TRENDS_SQL
    : attribution === "industry" ? INDUSTRY_TRENDS_SQL
    : INDEX_TRENDS_SQL;
  const rows = await queryRows<DbTrendRow>(sql, [industryId]);

  const episodes = rows.map((r) => ({
    code: r.code,
    start_date: formatDate(r.start_date),
    end_date: formatDate(r.end_date),
    is_trend_up_not_down: r.is_trend_up_not_down,
    rz_buy_vs_trading_amt_ratio: toNum(r.rz_buy_vs_trading_amt_ratio),
  }));

  return {
    industry_id: industryId,
    attribution,
    episodes,
  };
}
