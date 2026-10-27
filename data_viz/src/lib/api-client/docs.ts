/**
 * docs client — the static.docs UI-description registry.
 *
 * GET /api/docs returns the whole registry (one row per described UI
 * element — chart AI-Ask spec, info popover, column info — INSERT-seeded
 * by database/sql/static/docs/static/). The rows carry {{param}}
 * templates; the shared docs store (data_viz/src/shared/docs) resolves
 * them with each consumer's runtime params.
 */
import type { DocsSeriesEntry, DocsNoteEntry } from "@/shared/docs";

export interface DocRow {
  doc_id: string;
  title: string | null;
  intro: string | null;
  series: DocsSeriesEntry[] | null;
  notes: DocsNoteEntry[] | null;
  ordinal: number;
}

export async function fetchDocs(): Promise<DocRow[]> {
  const res = await fetch("/api/docs");
  const text = await res.text();
  if (!res.ok) {
    throw new Error(`docs fetch failed (HTTP ${res.status}): ${text.slice(0, 200)}`);
  }
  return JSON.parse(text) as DocRow[];
}
