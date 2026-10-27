/**
 * shared/docs — the UI-description registry store.
 *
 * The authored UI description texts live in static.docs
 * (database/sql/static/docs/static/ — the seed files are the source of
 * record) and ride GET /api/docs as {{param}} TEMPLATES. This module is
 * the client side of that contract:
 *
 *   - `DocsProvider` — mounts once at the app root, fetches the registry
 *     once per session, and exposes it through context.
 *   - `useDocs()` — the consumer hook: `text(id, params)` for an intro,
 *     `seriesEntry(id, key, params)` / `series(id, params)` for AI-Ask
 *     series semantics, `notes(id)` / `noteText(id, key, params)` for
 *     caveats, `title(id)` for popover titles.
 *
 * TEMPLATE CONTRACT (mirrors the 00_schema.sql header): {{param}}
 * substitutes a runtime value; {{#param}}…{{/param}} includes the section
 * when the param is truthy, {{^param}}…{{/param}} when falsy — flat, no
 * nesting. Before the registry arrives every lookup resolves to "" (and
 * `ready` is false), so early renders show less text, never a crash.
 * AI-Ask series NAMES must resolve byte-identically to the plotted
 * series names (derivePlotInfo matches semantics by name).
 */
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { fetchDocs, type DocRow } from "@/lib/api-client/docs";

export interface DocsSeriesEntry {
  key: string;
  name: string;
  unit?: string;
  description?: string;
}

export interface DocsNoteEntry {
  key: string;
  text: string;
}

/** Template parameters — strings/numbers interpolate; booleans gate the
 *  {{#param}} / {{^param}} sections. */
export type DocsParams = Record<string, string | number | boolean | undefined>;

function isTruthy(v: unknown): boolean {
  return v !== undefined && v !== null && v !== false && v !== 0 && v !== "";
}

/** Resolve one template against params (see the module docstring). */
export function resolveTemplate(
  tpl: string,
  params?: DocsParams,
): string {
  if (!params || !tpl.includes("{{")) return tpl;
  let out = tpl
    .replace(/\{\{#(\w+)\}\}([\s\S]*?)\{\{\/\1\}\}/g, (_m, key: string, body: string) =>
      isTruthy(params[key]) ? resolveTemplate(body, params) : "",
    )
    .replace(/\{\{\^(\w+)\}\}([\s\S]*?)\{\{\/\1\}\}/g, (_m, key: string, body: string) =>
      isTruthy(params[key]) ? "" : resolveTemplate(body, params),
    );
  out = out.replace(/\{\{(\w+)\}\}/g, (_m, key: string) => {
    const v = params[key];
    return v === undefined || v === null ? "" : String(v);
  });
  return out;
}

interface DocsEntry {
  title: string | null;
  intro: string | null;
  series: DocsSeriesEntry[];
  notes: DocsNoteEntry[];
}

/** The consumer-facing registry view. Identity is stable per load — a
 *  spec's useMemo can key on the `docs` object itself. */
export interface Docs {
  /** False until the registry's first successful load. */
  ready: boolean;
  /** Raw entry lookup (rare — prefer the resolvers below). */
  entry(docId: string): DocsEntry | undefined;
  /** The resolved intro ("" when the doc or the registry is missing). */
  text(docId: string, params?: DocsParams): string;
  /** The popover / column title. */
  title(docId: string): string;
  /** One resolved AI-Ask series entry by its stable key. */
  seriesEntry(
    docId: string,
    key: string,
    params?: DocsParams,
  ): DocsSeriesEntry | undefined;
  /** The whole resolved series catalog (all entries, one params set). */
  series(docId: string, params?: DocsParams): DocsSeriesEntry[];
  /** The resolved notes, in seed order (optionally keyed). */
  notes(docId: string, keys?: readonly string[], params?: DocsParams): string[];
  /** One resolved note by key ("" when missing). */
  noteText(docId: string, key: string, params?: DocsParams): string;
}

const EMPTY_DOCS: Docs = {
  ready: false,
  entry: () => undefined,
  text: () => "",
  title: () => "",
  seriesEntry: () => undefined,
  series: () => [],
  notes: () => [],
  noteText: () => "",
};

function toEntry(row: DocRow): DocsEntry {
  return {
    title: row.title,
    intro: row.intro,
    series: row.series ?? [],
    notes: row.notes ?? [],
  };
}

const DocsContext = createContext<Docs>(EMPTY_DOCS);

export function DocsProvider({ children }: { children: ReactNode }) {
  const [rows, setRows] = useState<Map<string, DocsEntry> | null>(null);
  const failed = useRef(false);

  useEffect(() => {
    let alive = true;
    fetchDocs()
      .then((list) => {
        if (!alive) return;
        setRows(new Map(list.map((r) => [r.doc_id, toEntry(r)])));
      })
      .catch((err) => {
        // The registry is documentation — a failed load degrades to empty
        // texts (logged once), never blocks the app.
        failed.current = true;
        console.error("[docs] registry load failed:", err);
      });
    return () => {
      alive = false;
    };
  }, []);

  const docs = useMemo<Docs>(() => {
    if (!rows) return EMPTY_DOCS;
    return {
      ready: true,
      entry: (id) => rows.get(id),
      text: (id, params) => {
        const intro = rows.get(id)?.intro;
        return intro ? resolveTemplate(intro, params) : "";
      },
      title: (id) => rows.get(id)?.title ?? "",
      seriesEntry: (id, key, params) => {
        const found = rows.get(id)?.series.find((s) => s.key === key);
        if (!found) return undefined;
        return {
          key: found.key,
          name: resolveTemplate(found.name, params),
          ...(found.unit ? { unit: resolveTemplate(found.unit, params) } : {}),
          ...(found.description
            ? { description: resolveTemplate(found.description, params) }
            : {}),
        };
      },
      series: (id, params) =>
        (rows.get(id)?.series ?? []).map((s) => ({
          key: s.key,
          name: resolveTemplate(s.name, params),
          ...(s.unit ? { unit: resolveTemplate(s.unit, params) } : {}),
          ...(s.description
            ? { description: resolveTemplate(s.description, params) }
            : {}),
        })),
      notes: (id, keys, params) =>
        (rows.get(id)?.notes ?? [])
          .filter((n) => !keys || keys.includes(n.key))
          .map((n) => resolveTemplate(n.text, params)),
      noteText: (id, key, params) => {
        const found = rows.get(id)?.notes.find((n) => n.key === key);
        return found ? resolveTemplate(found.text, params) : "";
      },
    };
  }, [rows]);

  return <DocsContext.Provider value={docs}>{children}</DocsContext.Provider>;
}

/** The docs registry — reads update when the registry finishes loading. */
export function useDocs(): Docs {
  return useContext(DocsContext);
}

/** Drop the undefined entries a missing doc/series key leaves behind —
 *  the standard companion of `seriesEntry` lookups when building an
 *  AiAskSpec's series list. */
export function compactDefined<T>(xs: readonly (T | undefined)[]): T[] {
  return xs.filter((x): x is T => x !== undefined);
}
