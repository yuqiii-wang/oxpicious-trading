/**
 * useKgScopeTables — the SHARED underlying data of the KG table-scope
 * pickers (2026-11-11): the knowledge-graph ns0 TABLE entities grouped
 * by schema (analysis / stats / llm …), fetched ONCE per session from
 * GET /api/config/kg?namespace=0 (the same plain fetch the Knowledge
 * Graph config page uses — the shared client cache must never serve a
 * pre-save state there) and cached at module level, so every consumer
 * (the config page's scope dropdown, the AI summary's forced-table
 * knob) imports the SAME list instead of re-fetching per mount.
 *
 * Fail-soft: a fetch error lands as [] — the pickers then render an
 * empty dropdown, never break their page.
 */
import { useEffect, useState } from "react";
import { fetchKgGraph } from "@/lib/api-client/config";

/** One schema's nest: the group header and the FULL table names
 *  under it (schema-qualified — the ticked values ride verbatim). */
export interface KgSchemaGroup {
  schema: string;
  tables: string[];
}

/** The schemas the AI-summary table LIST never offers (2026-11-11):
 *  the operational monitoring store is not a study corpus — its tables
 *  cannot be picked as a run's section list or KG scope. This filters
 *  the SHARED list (the Scopes panel tree + the Generate dialog knob);
 *  the Knowledge Graph config page renders the whole corpus and is
 *  unaffected. */
const LIST_EXCLUDED_SCHEMAS = new Set(["monitor"]);

/** (schema, bare name) split — the sort tuple and the group key the
 *  nested dropdown renders (group header = the schema, options = the
 *  relation names under it, the Scopes panel's two-layer shape). */
export const kgNameSchema = (name: string): [string, string] => {
  const dot = name.indexOf(".");
  return dot >= 0 ? [name.slice(0, dot), name.slice(dot + 1)] : [name, ""];
};

/** The module-level session cache — one fetch for every consumer. */
let groupsCache: Promise<KgSchemaGroup[]> | null = null;

function loadKgSchemaGroups(): Promise<KgSchemaGroup[]> {
  return fetchKgGraph(0)
    .then((body) => {
      const tables = body.entities
        .filter(
          (e) =>
            e.entityType === "table" &&
            !LIST_EXCLUDED_SCHEMAS.has(kgNameSchema(e.name)[0]),
        )
        .map((e) => e.name);
      const bySchema = new Map<string, string[]>();
      for (const name of tables) {
        const [schema] = kgNameSchema(name);
        const bucket = bySchema.get(schema);
        if (bucket === undefined) {
          bySchema.set(schema, [name]);
        } else {
          bucket.push(name);
        }
      }
      return [...bySchema.entries()]
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([schema, names]) => ({
          schema,
          tables: names.sort((a, b) => a.localeCompare(b)),
        }));
    })
    .catch(() => [] as KgSchemaGroup[]);
}

export function useKgScopeTables(): {
  groups: KgSchemaGroup[];
  /** True once the session fetch settled (success OR fail-soft []). */
  ready: boolean;
} {
  const [groups, setGroups] = useState<KgSchemaGroup[]>([]);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    let cancelled = false;
    if (groupsCache === null) {
      groupsCache = loadKgSchemaGroups();
    }
    groupsCache.then((loaded) => {
      if (!cancelled) {
        setGroups(loaded);
        setReady(true);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);
  return { groups, ready };
}
