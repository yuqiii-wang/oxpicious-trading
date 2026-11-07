/**
 * docs.service — the static.docs UI-description registry.
 *
 * Serves the authored UI description texts (chart AI-Ask specs, info
 * popovers, column infos) from static.docs, INSERT-seeded by
 * database/sql/static/docs/static/ — the per-area data files, the single
 * source of truth. The UI fetches the whole registry once per session
 * (a few hundred text rows) and resolves the {{param}} templates
 * client-side (data_viz/src/shared/docs) — the templates are data, the
 * runtime params live where the charts live.
 */
import { cachedRows } from "./classification-cache.js";
import { querySqlView } from "../lib/sql-views.js";
import type { QueryResultRow } from "pg";

export interface DocSeriesEntry {
  key: string;
  name: string;
  unit?: string;
  description?: string;
}

export interface DocNoteEntry {
  key: string;
  text: string;
}

export interface DocRow extends QueryResultRow {
  doc_id: string;
  title: string | null;
  intro: string | null;
  series: DocSeriesEntry[] | null;
  notes: DocNoteEntry[] | null;
  ordinal: number;
}

/** All registry rows (10-min TTL — they change only on a seed run). */
export function listDocs(): Promise<DocRow[]> {
  return cachedRows("static-docs:all", () =>
    querySqlView<DocRow>("data_viz/api/services/docs/list_all"),
  );
}

/** One registry row as the summary-run matcher reads it — the run
 *  matching needs the run-matching label arrays (contract:
 *  database/sql/static/docs/00_schema.sql) that the /api/docs UI
 *  contract deliberately does not carry. */
export interface DocLabeledRow extends QueryResultRow {
  doc_id: string;
  title: string | null;
  intro: string | null;
  series: DocSeriesEntry[] | null;
  notes: DocNoteEntry[] | null;
  /** Force-load vocabulary — exact phrase join with the run's effective
   *  prompts' bound_labels → the doc must ride the run. */
  bound_labels: string[];
  /** Loose vocabulary — substring match against the question / prompt
   *  labels, loaded only within the bridge's prompt budget. */
  optional_labels: string[];
}

/** All registry rows WITH the label arrays (10-min TTL) — the summary
 *  bridge's bound/optional doc matching input. */
export function listDocsWithLabels(): Promise<DocLabeledRow[]> {
  return cachedRows("static-docs:labeled", () =>
    querySqlView<DocLabeledRow>("data_viz/api/services/docs/list_with_labels"),
  );
}
