/**
 * Fourier Frequencies analysis service.
 *
 * Reads from analysis.fourier_freqs — per-(sec_type, code, last_date,
 * range_days) dominant cycle period (freq, trading days) + amplitude
 * (yuan) from a real FFT on the trailing range_days close prices.
 *
 * Currently populated for sec_type='index' only (the Python populator
 * is run with --sec-type index first). The service accepts a sec_type
 * param for forward-compatibility but the page is index-only.
 *
 * Mirrors the pe-and-dividends service shape (codes + chart + themes +
 * strategy-themes) so the page can reuse SecClassificationNav verbatim.
 */
import { queryRows, formatDate, toNum } from "../../lib/db.js";
import type { QueryResultRow } from "pg";
import { stripExchangeSuffix, matchesExchange } from "../../lib/classify-etf.js";
import { stripped } from "./_shared.js";
import { buildStrategyThemesFromRows, matchesClassification } from "../_shared.js";
import type {
  FourierFreqsSecType,
  FourierFreqsCodeRow,
  FourierFreqsCodesResponse,
  FourierFreqsChartResponse,
  FourierFreqsChartRow,
  FourierFreqsSpectrumResponse,
  FourierFreqsSpectrumRow,
  SectorNode,
  IndustryNode,
  StrategyNode,
} from "../../../shared/types.js";

// ----------------------------------------------------------------------------
//  Config — index-only for now; structure allows etf/stock later.
// ----------------------------------------------------------------------------
const VALID_SEC_TYPES: ReadonlySet<FourierFreqsSecType> = new Set(["index"]);

function normalizeSecType(raw: string | undefined | null): FourierFreqsSecType {
  const v = (raw ?? "").trim().toLowerCase();
  if (!VALID_SEC_TYPES.has(v as FourierFreqsSecType)) {
    throw new Error(`Invalid sec_type: ${raw!}. Expected 'index'.`);
  }
  return v as FourierFreqsSecType;
}

const IDENTITY_TABLE: Record<FourierFreqsSecType, string> = {
  index: "stats.index_identity",
};

const META_TYPE: Record<FourierFreqsSecType, string> = {
  index: "index",
};

// ----------------------------------------------------------------------------
//  DB row types
// ----------------------------------------------------------------------------
interface DbCodeRow extends QueryResultRow {
  code: string;
  name: string;
  first_date: Date | string;
  last_date: Date | string;
  n_dates: number;
  range_days: number;
  latest_freq: number | null;
}

interface DbChartRow extends QueryResultRow {
  last_date: Date | string;
  range_days: number;
  freq: number;
  amplitude: number;
}

interface DbMetaRow extends QueryResultRow {
  code: string;
  name: string;
  sector_id: string;
  sector_label: string;
  industry_id: string;
  industry_label: string;
  industry_slug: string;
  is_industry_not_strategy: boolean;
  exchange: string;
}

// ----------------------------------------------------------------------------
//  listFourierFreqsCodes — one row per code with first/last date, n_dates,
//  and the latest dominant freq per range_days. Pivots the 5 range_days
//  values into a latest_freq map so the codes list stays one-row-per-code.
// ----------------------------------------------------------------------------
function buildCodesSql(secType: FourierFreqsSecType): string {
  return `
    WITH latest_name AS (
      SELECT DISTINCT ON (code) code, name
      FROM ${IDENTITY_TABLE[secType]}
      ORDER BY code, date DESC
    ),
    code_dates AS (
      SELECT
        code,
        MIN(last_date) AS first_date,
        MAX(last_date) AS last_date,
        COUNT(DISTINCT last_date) AS n_dates
      FROM analysis.fourier_freqs
      WHERE sec_type = $1
      GROUP BY code
    ),
    latest_row AS (
      SELECT DISTINCT ON (code, range_days) code, range_days, freq
      FROM analysis.fourier_freqs
      WHERE sec_type = $1
      ORDER BY code, range_days, last_date DESC
    )
    SELECT
      cd.code,
      COALESCE(n.name, '')  AS name,
      cd.first_date,
      cd.last_date,
      cd.n_dates,
      lr.range_days,
      lr.freq               AS latest_freq
    FROM code_dates cd
    LEFT JOIN latest_name n  ON n.code  = cd.code
    LEFT JOIN latest_row lr  ON lr.code = cd.code
    ORDER BY cd.code, lr.range_days
  `;
}

/** Meta SQL shared by themes + strategy-themes. Returns one row per code
 *  in analysis.fourier_freqs (filtered by sec_type) with its precomputed
 *  L1/L2 classification from stats.sec_classification. */
const META_SQL = `
  WITH ff_codes AS (
    SELECT DISTINCT code
    FROM analysis.fourier_freqs
    WHERE sec_type = $1::text
  )
  SELECT
    sc.code,
    COALESCE(m.name, '')             AS name,
    COALESCE(m.sector_id,       'OTHER')  AS sector_id,
    COALESCE(m.sector_label,    '其他')   AS sector_label,
    COALESCE(m.industry_id,     'OTHER')  AS industry_id,
    COALESCE(m.industry_label,  '未分类') AS industry_label,
    COALESCE(m.industry_slug,   'other')  AS industry_slug,
    COALESCE(m.is_industry_not_strategy, TRUE) AS is_industry_not_strategy,
    COALESCE(m.exchange, '')               AS exchange
  FROM ff_codes sc
  LEFT JOIN stats.sec_classification m ON m.code = sc.code AND m.type = $2::text
  WHERE COALESCE(m.is_active, TRUE) = TRUE
`;

export async function listFourierFreqsCodes(
  rawSecType: string | undefined | null,
  sector?: string | null,
  industry?: string | null,
  strategy?: string | null,
  theme?: string | null,
  rawExchange?: string | null,
): Promise<FourierFreqsCodesResponse> {
  const secType = normalizeSecType(rawSecType);
  const sectorFilter = (sector ?? "").trim();
  const industryFilter = (industry ?? "").trim();
  const strategyFilter = (strategy ?? "").trim();
  const themeFilter = (theme ?? "").trim();
  const hasClassFilter = !!(sectorFilter || industryFilter || strategyFilter || themeFilter);
  const exFilter = (rawExchange ?? "").trim() || null;
  const needMeta = hasClassFilter || !!exFilter;

  const rows = await queryRows<DbCodeRow>(buildCodesSql(secType), [secType]);

  let classMap: Map<string, DbMetaRow> | null = null;
  if (needMeta) {
    const metaType = META_TYPE[secType];
    const metaRows = await queryRows<DbMetaRow>(META_SQL, [secType, metaType]);
    classMap = new Map<string, DbMetaRow>();
    for (const m of metaRows) {
      const code = stripExchangeSuffix(m.code);
      if (!code) continue;
      classMap.set(code, m);
    }
  }

  // Collapse the per-range_days rows into one code row with a latest_freq map.
  const byCode = new Map<string, FourierFreqsCodeRow>();
  for (const r of rows) {
    const code = stripped(r.code);
    if (classMap) {
      const meta = classMap.get(code);
      if (hasClassFilter && (!meta || !matchesClassification(meta, sectorFilter, industryFilter, strategyFilter, themeFilter))) {
        continue;
      }
      if (exFilter && (!meta || !matchesExchange(meta.exchange, exFilter))) {
        continue;
      }
    }
    let entry = byCode.get(code);
    if (!entry) {
      entry = {
        code,
        name: r.name ?? "",
        first_date: formatDate(r.first_date),
        last_date: formatDate(r.last_date),
        n_dates: Number(r.n_dates) || 0,
        latest_freq: {},
      };
      byCode.set(code, entry);
    }
    const rd = Number(r.range_days);
    if (Number.isFinite(rd)) {
      entry.latest_freq[rd] = toNum(r.latest_freq);
    }
  }
  return { codes: Array.from(byCode.values()) };
}

// ----------------------------------------------------------------------------
//  getFourierFreqsChart — per-(last_date, range_days) freq + amplitude for
//  one code. Returns ALL range_days values (20/60/255/500/750) so the
//  frontend can render one line per range_days.
// ----------------------------------------------------------------------------
function buildChartSql(): string {
  return `
    SELECT last_date, range_days, freq, amplitude_close_price AS amplitude
    FROM analysis.fourier_freqs
    WHERE sec_type = $2
      AND REGEXP_REPLACE(code, '\\.(SZ|SS|BJ|HK)$', '') = $1::text
    ORDER BY last_date ASC, range_days ASC
  `;
}

function buildNameSql(secType: FourierFreqsSecType): string {
  return `
    SELECT DISTINCT ON (code) code, name
    FROM ${IDENTITY_TABLE[secType]}
    WHERE REGEXP_REPLACE(code, '\\.(SZ|SS|BJ|HK)$', '') = $1::text
    ORDER BY code, date DESC
  `;
}

export async function getFourierFreqsChart(
  rawCode: string,
  rawSecType: string | undefined | null,
): Promise<FourierFreqsChartResponse> {
  const secType = normalizeSecType(rawSecType);
  const target = stripped(rawCode);

  const [chartRows, nameRows] = await Promise.all([
    queryRows<DbChartRow>(buildChartSql(), [target, secType]),
    queryRows<{ name: string | null }>(buildNameSql(secType), [target]),
  ]);

  const name = nameRows[0]?.name ?? "";

  const rows: FourierFreqsChartRow[] = chartRows.map((r) => ({
    last_date: formatDate(r.last_date),
    range_days: Number(r.range_days),
    freq: Number(r.freq),
    amplitude: toNum(r.amplitude) ?? 0,
  }));

  return { code: target, name, rows };
}

// ----------------------------------------------------------------------------
//  listFourierFreqsThemes — L1 sector → L2 industry → items tree, restricted
//  to codes that have rows in analysis.fourier_freqs for the requested
//  sec_type. Mirrors listPeAndDividendThemes().
// ----------------------------------------------------------------------------
export async function listFourierFreqsThemes(
  rawSecType: string | undefined | null,
  rawExchange?: string | null,
): Promise<SectorNode[]> {
  const secType = normalizeSecType(rawSecType);
  const exFilter = (rawExchange ?? "").trim() || null;
  const metaType = META_TYPE[secType];
  const rows = await queryRows<DbMetaRow>(META_SQL, [secType, metaType]);

  const sectorMap = new Map<string, {
    sector_label: string;
    industries: Map<string, IndustryNode>;
  }>();

  for (const r of rows) {
    if (!r.is_industry_not_strategy) continue;
    if (exFilter && !matchesExchange(r.exchange, exFilter)) continue;
    const code = stripExchangeSuffix(r.code);
    if (!code) continue;
    const item = { code, name: r.name ?? "" };

    if (!sectorMap.has(r.sector_id)) {
      sectorMap.set(r.sector_id, { sector_label: r.sector_label, industries: new Map() });
    }
    const sector = sectorMap.get(r.sector_id)!;
    if (!sector.industries.has(r.industry_id)) {
      sector.industries.set(r.industry_id, {
        industry_id: r.industry_id,
        industry_label: r.industry_label,
        industry_slug: r.industry_slug,
        count: 0,
        items: [],
      });
    }
    const ind = sector.industries.get(r.industry_id)!;
    ind.items.push(item);
    ind.count++;
  }

  const sectors: SectorNode[] = [];
  for (const [sector_id, sector] of sectorMap) {
    const industries = Array.from(sector.industries.values()).sort((a, b) => {
      if (a.industry_id === "OTHER") return 1;
      if (b.industry_id === "OTHER") return -1;
      return b.count - a.count;
    });
    sectors.push({
      sector_id,
      sector_label: sector.sector_label,
      count: industries.reduce((sum, i) => sum + i.count, 0),
      industries,
    });
  }
  sectors.sort((a, b) => {
    if (a.sector_id === "OTHER") return 1;
    if (b.sector_id === "OTHER") return -1;
    return b.count - a.count;
  });
  return sectors;
}

// ----------------------------------------------------------------------------
//  listFourierFreqsStrategyThemes — parallel L1 strategy → L2 theme → items
//  tree from the strategy-primary rows (is_industry_not_strategy=FALSE).
// ----------------------------------------------------------------------------
export async function listFourierFreqsStrategyThemes(
  rawSecType: string | undefined | null,
  rawExchange?: string | null,
): Promise<StrategyNode[]> {
  const secType = normalizeSecType(rawSecType);
  const exFilter = (rawExchange ?? "").trim() || null;
  const metaType = META_TYPE[secType];
  const rows = await queryRows<DbMetaRow>(META_SQL, [secType, metaType]);

  const filteredRows = exFilter
    ? rows.filter((r) => matchesExchange(r.exchange, exFilter))
    : rows;

  const mappedRows = filteredRows.map((r) => ({
    code: stripExchangeSuffix(r.code),
    name: r.name,
    sector_id: r.sector_id,
    sector_label: r.sector_label,
    industry_id: r.industry_id,
    industry_label: r.industry_label,
    industry_slug: r.industry_slug,
    is_industry_not_strategy: r.is_industry_not_strategy,
  }));

  return buildStrategyThemesFromRows(mappedRows);
}

// ----------------------------------------------------------------------------
//  getFourierFreqsSpectrum — the FULL one-sided amplitude spectrum for ONE
//  (code, last_date) across ALL 5 range_days windows. Drives the per-date
//  spectrum bar charts on the Fourier Frequencies page (one chart per
//  range_days, reactive to a date clicked on the top index price plot).
//
//  When `rawLastDate` is null/empty, defaults to the MAX(last_date) for
//  that code so the page has an initial spectrum to show before the user
//  clicks anything.
// ----------------------------------------------------------------------------
interface DbSpectrumRow extends QueryResultRow {
  range_days: number;
  freq: number;
  amplitude_close_price: number;
  amplitude_spectrum: number[] | null;
}

export async function getFourierFreqsSpectrum(
  rawCode: string,
  rawSecType: string | undefined | null,
  rawLastDate?: string | null,
): Promise<FourierFreqsSpectrumResponse> {
  const secType = normalizeSecType(rawSecType);
  const target = stripped(rawCode);
  const lastDate = (rawLastDate ?? "").trim() || null;

  // Resolve the effective last_date. When not supplied, pick the latest
  // available date for this code (so the page has an initial spectrum).
  // Done in a single query that COALESCEs the param to MAX(last_date).
  const spectrumSql = `
    WITH resolved AS (
      SELECT COALESCE($3::date, MAX(last_date)) AS d
      FROM analysis.fourier_freqs
      WHERE sec_type = $2
        AND REGEXP_REPLACE(code, '\\.(SZ|SS|BJ|HK)$', '') = $1::text
    )
    SELECT
      f.range_days,
      f.freq,
      f.amplitude_close_price,
      f.amplitude_spectrum
    FROM analysis.fourier_freqs f, resolved r
    WHERE f.sec_type = $2
      AND REGEXP_REPLACE(f.code, '\\.(SZ|SS|BJ|HK)$', '') = $1::text
      AND f.last_date = r.d
      AND f.amplitude_spectrum IS NOT NULL
    ORDER BY f.range_days ASC
  `;

  const nameSql = buildNameSql(secType);

  const [specRows, nameRows, dateRow] = await Promise.all([
    queryRows<DbSpectrumRow>(spectrumSql, [target, secType, lastDate]),
    queryRows<{ name: string | null }>(nameSql, [target]),
    // Fetch the resolved last_date (for the response) — re-query only
    // when lastDate was null so the frontend knows which date it got.
    lastDate
      ? Promise.resolve<{ d: string | null }[]>([{ d: lastDate }])
      : queryRows<{ d: Date | string | null }>(
          `SELECT COALESCE(MAX(last_date)::text, NULL) AS d
           FROM analysis.fourier_freqs
           WHERE sec_type = $2
             AND REGEXP_REPLACE(code, '\\.(SZ|SS|BJ|HK)$', '') = $1::text`,
          [target, secType],
        ),
  ]);

  const name = nameRows[0]?.name ?? "";
  const resolvedDate = dateRow[0]?.d ? formatDate(dateRow[0].d) : "";

  const spectrums: FourierFreqsSpectrumRow[] = specRows.map((r) => ({
    range_days: Number(r.range_days),
    freq: Number(r.freq),
    amplitude: toNum(r.amplitude_close_price) ?? 0,
    spectrum: Array.isArray(r.amplitude_spectrum)
      ? r.amplitude_spectrum.map((v) => (typeof v === "number" ? v : Number(v) || 0))
      : [],
  }));

  return { code: target, name, last_date: resolvedDate, spectrums };
}
