/**
 * SQL view template loader — renders the SELECT implementations stored
 * under `database/sql_views/` (see that README for the layout and slot
 * conventions). `{{slot}}` placeholders fill from args: value args bind
 * to pg positional `$n` params (the same slot reused maps to the same
 * `$n`), fragment args (`sqlFrag(...)`) substitute verbatim. Rendering is
 * strict — every slot needs an arg and every arg must appear as a slot,
 * so typos fail fast at render instead of silently changing the query.
 * Templates re-read on mtime change: editing a view file takes effect
 * without restarting the dev server.
 */
import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { queryRows } from "@api/services/db.service.js";
import type { QueryResultRow } from "pg";

/** A raw SQL fragment arg — substituted verbatim into the template. */
export interface SqlViewFragment {
  readonly kind: "fragment";
  readonly text: string;
}

/** Wrap raw SQL text to pass it as a fragment arg (CTE blocks built by
 *  code, decode subqueries, conditional clauses). */
export function sqlFrag(text: string): SqlViewFragment {
  return { kind: "fragment", text };
}

/** Value args bind through pg positional params; arrays pass to the
 *  driver as-is (`= ANY({{codes}}::text[])`). */
export type SqlViewValue =
  | string
  | number
  | bigint
  | boolean
  | null
  | Date
  | readonly unknown[];
export type SqlViewArg = SqlViewValue | SqlViewFragment;
export type SqlViewArgs = Record<string, SqlViewArg>;

export interface RenderedSqlView {
  sql: string;
  params: unknown[];
}

const SLOT_RE = /\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/g;
const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_./-]*$/;

// Walk up from this module until database/sql_views exists — the server
// may be started from data_viz/ or the repo root.
let sqlViewsRoot: string | null = null;
function findSqlViewsRoot(): string {
  if (sqlViewsRoot) return sqlViewsRoot;
  const moduleDir = dirname(fileURLToPath(import.meta.url));
  let dir = moduleDir;
  for (let i = 0; i < 8; i++) {
    const candidate = join(dir, "database", "sql_views");
    if (existsSync(candidate)) {
      sqlViewsRoot = candidate;
      return candidate;
    }
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  throw new Error(
    `database/sql_views not found above ${moduleDir}`,
  );
}

interface CachedTemplate {
  mtimeMs: number;
  text: string;
}
const templateCache = new Map<string, CachedTemplate>();

function loadTemplate(name: string): string {
  if (!NAME_RE.test(name)) {
    throw new Error(`Invalid sql view name: '${name}'`);
  }
  const path = join(findSqlViewsRoot(), `${name}.sql`);
  const mtimeMs = statSync(path).mtimeMs;
  const hit = templateCache.get(path);
  if (hit && hit.mtimeMs === mtimeMs) return hit.text;
  const text = readFileSync(path, "utf8");
  templateCache.set(path, { mtimeMs, text });
  return text;
}

export function renderSqlView(
  name: string,
  args: SqlViewArgs = {},
): RenderedSqlView {
  const template = loadTemplate(name);
  const params: unknown[] = [];
  const paramIndexByArg = new Map<string, number>();
  const used = new Set<string>();
  const sql = template.replace(
    SLOT_RE,
    (_match: string, argName: string): string => {
      if (!(argName in args)) {
        throw new Error(
          `sql view '${name}': missing arg '${argName}' (got: ${
            Object.keys(args).join(", ") || "none"
          })`,
        );
      }
      const arg = args[argName];
      used.add(argName);
      if (
        typeof arg === "object" &&
        arg !== null &&
        (arg as SqlViewFragment).kind === "fragment"
      ) {
        return (arg as SqlViewFragment).text;
      }
      let idx = paramIndexByArg.get(argName);
      if (idx === undefined) {
        idx = params.push(arg as SqlViewValue);
        paramIndexByArg.set(argName, idx);
      }
      return `$${idx}`;
    },
  );
  const unknown = Object.keys(args).filter((k) => !used.has(k));
  if (unknown.length > 0) {
    throw new Error(
      `sql view '${name}': args not in template: ${unknown.join(", ")}`,
    );
  }
  return { sql, params };
}

export function querySqlView<T extends QueryResultRow>(
  name: string,
  args: SqlViewArgs = {},
): Promise<T[]> {
  const { sql, params } = renderSqlView(name, args);
  return queryRows<T>(sql, params);
}
