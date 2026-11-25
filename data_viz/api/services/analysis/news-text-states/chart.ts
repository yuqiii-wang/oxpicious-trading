/**
 * News Text States — the daily news-text state panel service
 * (text.news_text_daily, built by analysis.news_text_daily):
 * getNewsTextStatesChart (one scope's daily sentiment-state / flow-flag
 * curves with the close read live from stats for price context, plus
 * the RAW mood layer LEFT JOINed from analysis.text_metrics_daily —
 * the merged-in Text Sentiment Metrics page's Tetlock z family),
 * listNewsTextStatesScopes (the scope selector source — market kinds +
 * industries via the 'news_scope_kind' label index + the sector
 * rollup), and listNewsTextStatesEvents (the recent industry event
 * days).
 */
import { formatDate, toNum } from "@api/lib/db";
import { querySqlView, sqlFrag } from "@api/lib/sql-views";
import { HttpError } from "@api/lib/http";
import type { QueryResultRow } from "pg";
import { stripped } from "@api/services/analysis/_shared";
import { labelDecodeSql } from "@api/services/infra/label-codes.js";
import type {
  NewsTextStatesChartResponse,
  NewsTextStatesChartRow,
  NewsTextStatesDisagreementResponse,
  NewsTextStatesDisagreementRow,
  NewsTextStatesEventRow,
  NewsTextStatesEventsResponse,
  NewsTextStatesRecentLevelRow,
  NewsTextStatesRecentLevelsResponse,
  NewsTextStatesScopeRow,
  NewsTextStatesScopesResponse,
  NewsTextStatesStrongNegEventRow,
  NewsTextStatesStrongNegEventsResponse,
} from "@shared/types";

interface DbChartRow extends QueryResultRow {
  date: Date | string;
  close: number | string | null;
  n_articles: number | string | null;
  n_scored: number | string | null;
  sent_level: number | string | null;
  sent_z: number | string | null;
  sent_ema3: number | string | null;
  sent_ema10: number | string | null;
  sent_delta: number | string | null;
  flow_af: number | string | null;
  flow_pos: number | string | null;
  flow_neg: number | string | null;
  disp_origin: number | string | null;
  lex_tone_z: number | string | null;
  low_coverage: boolean | null;
  sentiment_z: number | string | null;
  sentiment_ma5: number | string | null;
  flow_z: number | string | null;
  kw_tone: number | string | null;
}

// ----------------------------------------------------------------------------
//  Price-context source per scope (moved in from the deleted text-metrics
//  chart service, whose fragments these were by design — this service is
//  their only consumer now; market row: the SSE Composite; industries:
//  the pool-'all' slice of industry_basic_stats, renamed to the generic
//  b.date / b.close bindings). Shared fragment passed into the
//  chart_series template.
//
//  industry(idParam) — the industry predicate MUST live on the b side:
//  stats.industry_basic_stats is one row per (industry_id, date,
//  pool_size), so an unfiltered pool-3 slice joins EVERY industry's
//  composite per date and multiplies the metric rows ~40x (the
//  interleaved-composites close bug). idParam is the pg placeholder for
//  the requested industry_id — the consumer passes the index its
//  template's value slot actually binds (documented in the view header):
//  news-text-states $2 (its scope_code).
//
//  sector(idParam) — a sector has no composite table of its own: the
//  price context is the equal-weight mean of its member industries'
//  pool-3 composites (the same slice industry(idParam) reads one of),
//  membership via stats.sec_index_tags' (sector_id, industry_id) tag
//  pairs. Same planner note: the sector predicate lives inside the b
//  subquery, never on the outer join.
// ----------------------------------------------------------------------------
export const CHART_FROM_CLAUSE = {
  market:
    "FROM (SELECT date, close FROM stats.index_basic_stats\n" +
    "      WHERE code = '000001') b",
  industry: (idParam: string) =>
    "FROM (SELECT date, mean_close AS close\n" +
    "      FROM stats.industry_basic_stats\n" +
    "      WHERE pool_size = 3 AND mean_close IS NOT NULL\n" +
    "        AND industry_id = " + idParam + ") b",
  sector: (idParam: string) =>
    "FROM (SELECT b.date, AVG(b.mean_close) AS close\n" +
    "      FROM stats.industry_basic_stats b\n" +
    "      WHERE b.pool_size = 3 AND b.mean_close IS NOT NULL\n" +
    "        AND b.industry_id IN (SELECT t.industry_id\n" +
    "                              FROM stats.sec_index_tags t\n" +
    "                              WHERE t.sector_id = " + idParam + ")\n" +
    "      GROUP BY b.date) b",
} as const;

interface DbScopeRow extends QueryResultRow {
  scope_kind: number | string;
  scope_kind_label: string | null;
  scope_code: string;
  scope_label: string | null;
  rows: number | string;
  min_date: Date | string;
  max_date: Date | string;
}

interface DbEventRow extends QueryResultRow {
  date: Date | string;
  industry_id: string;
  industry_label: string | null;
  n_articles: number | string;
  n_scored: number | string;
  sent_level: number | string | null;
  flow_pos: number | string;
  flow_neg: number | string;
  low_coverage: boolean;
}

/** The 'news_scope_kind' codes the API accepts (0 market_all,
 *  1 market_recap, 2 industry, 3 sector). */
const SCOPE_KINDS = new Set([0, 1, 2, 3]);

/** Parse + validate the requested scope. Default = the market_all row. */
export function parseScope(
  rawKind: string | undefined | null,
  rawCode: string | undefined | null,
): { kind: number; code: string } {
  const kind = rawKind == null || rawKind === "" ? 0 : Number(rawKind);
  if (!Number.isInteger(kind) || !SCOPE_KINDS.has(kind)) {
    throw HttpError.invalidParam(
      "scope_kind",
      "must be one of 0 (market_all), 1 (market_recap), 2 (industry), 3 (sector)");
  }
  const code = stripped(rawCode ?? "");
  if (code.length > 64) {
    throw HttpError.invalidParam("scope_code", "too long (max 64 chars)");
  }
  // The market scopes' code is fixed by the pipeline ('000001'); an
  // industry/sector scope without a code falls back to the market row
  // too.
  return { kind, code: kind >= 2 ? code : "000001" };
}

export async function getNewsTextStatesChart(
  rawKind: string | undefined | null,
  rawCode: string | undefined | null,
): Promise<NewsTextStatesChartResponse> {
  const { kind, code } = parseScope(rawKind, rawCode);

  const chartRows = await querySqlView<DbChartRow>(
    "data_viz/api/services/analysis/news-text-states/chart_series",
    {
      // Industry/sector bind the scope on the b (price) side too — the
      // pool-3 slice of industry_basic_stats spans EVERY industry, so
      // without it the close interleaves all composites and multiplies
      // the metric rows ~40x (the sector fragment scopes its member
      // industries the same way). $2 = the template's scope_code value
      // slot (its first value slot, scope_kind, binds $1 — see the view
      // header).
      from_clause: sqlFrag(
        kind === 2
          ? CHART_FROM_CLAUSE.industry("$2::text")
          : kind === 3
            ? CHART_FROM_CLAUSE.sector("$2::text")
            : CHART_FROM_CLAUSE.market,
      ),
      scope_kind: kind,
      scope_code: code,
      // The raw layer's analysis.text_metrics_daily industry_id key:
      // '' (broad market) for the market kinds, the scope's own
      // industry_id for kind 2, NULL for kind 3 (sectors have no raw
      // rows — NULL never matches, so a sector scope shows the raw
      // chart's empty state instead of market data).
      raw_industry_id: kind === 2 ? code : kind === 3 ? null : "",
    },
  );

  const rows: NewsTextStatesChartRow[] = chartRows.map((r) => ({
    date: formatDate(r.date),
    close: toNum(r.close),
    n_articles: toNum(r.n_articles),
    n_scored: toNum(r.n_scored),
    sent_level: toNum(r.sent_level),
    sent_z: toNum(r.sent_z),
    sent_ema3: toNum(r.sent_ema3),
    sent_ema10: toNum(r.sent_ema10),
    sent_delta: toNum(r.sent_delta),
    flow_af: toNum(r.flow_af),
    flow_pos: toNum(r.flow_pos),
    flow_neg: toNum(r.flow_neg),
    disp_origin: toNum(r.disp_origin),
    lex_tone_z: toNum(r.lex_tone_z),
    low_coverage: r.low_coverage === true,
    sentiment_z: toNum(r.sentiment_z),
    sentiment_ma5: toNum(r.sentiment_ma5),
    flow_z: toNum(r.flow_z),
    kw_tone: toNum(r.kw_tone),
  }));

  return { scope_kind: kind, scope_code: code, rows };
}

export async function listNewsTextStatesScopes(
): Promise<NewsTextStatesScopesResponse> {
  const rows = await querySqlView<DbScopeRow>(
    "data_viz/api/services/analysis/news-text-states/scopes",
    {
      // The label-decode fragment must reference the grouped subquery's
      // alias g (see the view header).
      scope_kind_decode: sqlFrag(
        labelDecodeSql("news_scope_kind", "g.scope_kind")),
    },
  );
  const mapped: NewsTextStatesScopeRow[] = rows.map((r) => ({
    scope_kind: toNum(r.scope_kind) ?? 0,
    scope_kind_label: r.scope_kind_label ?? "unknown",
    scope_code: r.scope_code,
    scope_label: r.scope_label ?? r.scope_code,
    rows: toNum(r.rows) ?? 0,
    min_date: formatDate(r.min_date),
    max_date: formatDate(r.max_date),
  }));
  return { rows: mapped };
}

export async function listNewsTextStatesEvents(
  rawLimit: string | undefined | null,
): Promise<NewsTextStatesEventsResponse> {
  const limit = rawLimit == null || rawLimit === "" ? 40 : Number(rawLimit);
  if (!Number.isInteger(limit) || limit < 1 || limit > 200) {
    throw HttpError.invalidParam("limit", "must be an integer in 1..200");
  }
  const rows = await querySqlView<DbEventRow>(
    "data_viz/api/services/analysis/news-text-states/events",
    { limit_rows: limit },
  );
  const mapped: NewsTextStatesEventRow[] = rows.map((r) => ({
    date: formatDate(r.date),
    industry_id: r.industry_id,
    industry_label: r.industry_label ?? r.industry_id,
    n_articles: toNum(r.n_articles) ?? 0,
    n_scored: toNum(r.n_scored) ?? 0,
    sent_level: toNum(r.sent_level),
    flow_pos: toNum(r.flow_pos) ?? 0,
    flow_neg: toNum(r.flow_neg) ?? 0,
    low_coverage: r.low_coverage === true,
  }));
  return { rows: mapped };
}

/** Parse + validate a window/limit-ish int arg with bounds. */
function parseBoundedInt(
  raw: string | undefined | null,
  fallback: number,
  min: number,
  max: number,
  name: string,
): number {
  const value = raw == null || raw === "" ? fallback : Number(raw);
  if (!Number.isInteger(value) || value < min || value > max) {
    throw HttpError.invalidParam(
      name, `must be an integer in ${min}..${max}`);
  }
  return value;
}

export async function listNewsTextDisagreement(
  rawWindowDays: string | undefined | null,
): Promise<NewsTextStatesDisagreementResponse> {
  const windowDays = parseBoundedInt(rawWindowDays, 120, 30, 365, "window_days");
  const rows = await querySqlView<{
    date_epoch: number | string;
    scored_count: number | string;
    disagree_count: number | string;
  }>(
    "data_viz/api/services/analysis/news-text-states/disagreement",
    { window_days: windowDays },
  );
  const mapped: NewsTextStatesDisagreementRow[] = rows.map((r) => ({
    date: formatDate(new Date(Number(r.date_epoch) * 1000)),
    scored_count: toNum(r.scored_count) ?? 0,
    disagree_count: toNum(r.disagree_count) ?? 0,
  }));
  return { rows: mapped };
}

export async function listNewsTextStrongNegEvents(
  rawWindowDays: string | undefined | null,
  rawLimit: string | undefined | null,
): Promise<NewsTextStatesStrongNegEventsResponse> {
  const windowDays = parseBoundedInt(rawWindowDays, 120, 30, 365, "window_days");
  const limit = parseBoundedInt(rawLimit, 20, 1, 100, "limit");
  const rows = await querySqlView<DbEventRow>(
    "data_viz/api/services/analysis/news-text-states/strong_neg_events",
    { window_days: windowDays, limit_rows: limit },
  );
  const mapped: NewsTextStatesStrongNegEventRow[] = rows.map((r) => ({
    date: formatDate(r.date),
    industry_id: r.industry_id,
    industry_label: r.industry_label ?? r.industry_id,
    n_articles: toNum(r.n_articles) ?? 0,
    n_scored: toNum(r.n_scored) ?? 0,
    sent_level: toNum(r.sent_level) ?? 0,
    flow_pos: toNum(r.flow_pos) ?? 0,
    flow_neg: toNum(r.flow_neg) ?? 0,
    low_coverage: r.low_coverage === true,
  }));
  return { rows: mapped };
}

interface DbRecentLevelRow extends QueryResultRow {
  scope_kind: number | string;
  scope_code: string;
  scope_label: string | null;
  date: Date | string;
  n_scored: number | string;
  sent_level: number | string | null;
  flow_pos: number | string;
  flow_neg: number | string;
  low_coverage: boolean;
}

export async function listNewsTextStatesRecentLevels(
  rawWindowDays: string | undefined | null,
): Promise<NewsTextStatesRecentLevelsResponse> {
  const windowDays = parseBoundedInt(rawWindowDays, 20, 5, 90, "window_days");
  const rows = await querySqlView<DbRecentLevelRow>(
    "data_viz/api/services/analysis/news-text-states/recent_levels",
    { window_days: windowDays },
  );
  const mapped: NewsTextStatesRecentLevelRow[] = rows.map((r) => ({
    scope_kind: toNum(r.scope_kind) ?? 0,
    scope_code: r.scope_code,
    scope_label: r.scope_label ?? r.scope_code,
    date: formatDate(r.date),
    n_scored: toNum(r.n_scored) ?? 0,
    sent_level: toNum(r.sent_level) ?? 0,
    flow_pos: toNum(r.flow_pos) ?? 0,
    flow_neg: toNum(r.flow_neg) ?? 0,
    low_coverage: r.low_coverage === true,
  }));
  return { rows: mapped };
}
