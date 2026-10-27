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
import { queryRows } from "../lib/db.js";
import { cachedRows } from "./classification-cache.js";
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
  return cachedRows("static-docs:all", async () =>
    queryRows<DocRow>(
      `SELECT doc_id, title, intro, series, notes, ordinal
         FROM static.docs
        ORDER BY doc_id`,
    ),
  );
}
