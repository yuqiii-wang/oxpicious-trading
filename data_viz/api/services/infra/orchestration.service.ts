/**
 * Orchestration steps service — read-only access to the repo's
 * orchestration step files under scripts/orchestration/ (one JSON per
 * tier; the maintainer's nightly_pipeline job drives the tiers serially
 * at 16:00 China time on A-share trading days through the monitor
 * area's orchestration runner — the retired sh tier's successor; see
 * scripts/orchestration/README.md).
 *
 * A tier's top-level keys are its STEP NUMBERS (run order); each step
 * holds { script (the python -m module), src_host (optional — the
 * query-destination host group enabling the downloads tiers'
 * diff-src-parallel waves: consecutive steps with distinct hosts run
 * concurrently; absent = fully serial), can_be_parallel (legacy flag,
 * false for every step), args (optional CLI args list), note (optional
 * ordering/dependency comment, ignored by the runner) }.
 *
 * Reads are mtime-cached via static-config's readJsonCached (same
 * rationale: the files are tiny; the cache only exists so a burst of
 * requests in one poll tick doesn't re-read the disk). There is no
 * write path — the files are git-reviewed edits, same as the rest of
 * the static config trees.
 */
import fs from "fs";
import path from "path";
import { readJsonCached, serviceConfigLooseFor } from "./static-config.service.js";

const ORCHESTRATION_DIR = path.resolve(
  import.meta.dirname ?? __dirname,
  "..",
  "..",
  "..",
  "..",
  "scripts",
  "orchestration",
);

/** Tier run order — the display mirror of the maintainer's canonical
 *  NIGHTLY_CHAIN (server_cluster/maintainer/pipeline.py; per
 *  scripts/orchestration/README.md "What runs when"): monitoring →
 *  downloads (daily/monthly/quarterly/semiannual/once) → builds
 *  (daily/semiannual/once/monthly — monthly importance ranks AFTER the
 *  daily/once builds) → analysis → strategy. Tiers not listed here
 *  sort alphabetically after the known ones. */
const TIER_RUN_ORDER = [
  "monitoring_occasionally",
  "downloads_daily",
  "downloads_weekly",
  "downloads_monthly",
  "downloads_quarterly",
  "downloads_semiannually",
  "downloads_once",
  "builds_daily",
  "builds_semiannually",
  "builds_once",
  "builds_monthly",
  "analysis_daily",
  "strategy_daily",
  "strategy_once",
];

/** Cadence suffixes a tier name can end with (the non-resident freq
 *  vocab of the service_config tree — residents carry is_resident and
 *  no freq, and have no orchestration tier of their own). */
const TIER_FREQS = [
  "daily",
  "weekly",
  "monthly",
  "quarterly",
  "semiannually",
  "once",
  "occasionally",
] as const;

export interface OrchestrationStep {
  /** Step number — the JSON's top-level key, the runner's execution
   *  order within the tier. */
  step: number;
  /** The python -m module the runner executes. */
  script: string;
  /** Extra CLI args (empty when the step takes none). */
  args: string[];
  /** Query-destination host group (downloads tiers' diff-src-parallel):
   *  consecutive steps with DISTINCT hosts share one concurrent wave in
   *  the monitor area's runner. Null = the step runs fully serial. */
  srcHost: string | null;
  /** false for every step today — the runner executes strictly
   *  serially and only warns if a step ever sets this true. */
  canBeParallel: boolean;
  /** The step's give-way rank, joined from its script's service_config
   *  entry (explicit ``priority`` else derived from freq + domain —
   *  same chain as the monitor runner's priority_for; higher = wins
   *  conflicts: a strictly lower-priority step holds back / stops
   *  while a higher one runs, and a step also holds back / stops while
   *  another run's child claims the same src_host — same-src never
   *  overlaps regardless of rank, the equal-rank case the rank alone
   *  cannot order). */
  priority: number;
  /** Ordering/dependency comment, ignored by the runner (null when
   *  absent). */
  note: string | null;
}

export interface OrchestrationTier {
  /** File stem, e.g. "builds_daily" — pairs with scripts/<tier>.sh. */
  tier: string;
  /** Leading segment of the tier name, e.g. "builds". */
  domain: string;
  /** Trailing cadence segment, e.g. "daily" ("" when the name carries
   *  no known freq suffix). */
  freq: string;
  /** The tier's sh script + step JSON as relative file paths. */
  sourceRef: string;
  steps: OrchestrationStep[];
}

/** Split a tier stem into domain + freq at a known cadence suffix
 *  ("builds_daily" → builds | daily); unknown shapes keep the whole
 *  stem as domain with an empty freq. */
function splitTierName(stem: string): { domain: string; freq: string } {
  for (const freq of TIER_FREQS) {
    if (stem.endsWith(`_${freq}`)) {
      return { domain: stem.slice(0, stem.length - freq.length - 1), freq };
    }
  }
  return { domain: stem, freq: "" };
}

/** Validate + normalize one step entry (null drops a malformed step
 *  instead of taking the read down). */
function normalizeStep(key: string, value: unknown): OrchestrationStep | null {
  if (typeof value !== "object" || value == null) return null;
  const d = value as Record<string, unknown>;
  const step = Number(key);
  if (!Number.isInteger(step) || typeof d.script !== "string" || d.script === "") {
    return null;
  }
  return {
    step,
    script: d.script,
    args: Array.isArray(d.args)
      ? d.args.filter((a): a is string => typeof a === "string")
      : [],
    srcHost: typeof d.src_host === "string" && d.src_host !== "" ? d.src_host : null,
    canBeParallel: d.can_be_parallel === true,
    priority: serviceConfigLooseFor(d.script).priority,
    note: typeof d.note === "string" && d.note !== "" ? d.note : null,
  };
}

/** The whole orchestration inventory: one tier per scripts/orchestration/
 *  JSON file, steps sorted by step number, tiers in run order. */
export function readOrchestrationTiers(): OrchestrationTier[] {
  let files: string[];
  try {
    files = fs
      .readdirSync(ORCHESTRATION_DIR, { withFileTypes: true })
      .filter((e) => e.isFile() && e.name.endsWith(".json"))
      .map((e) => path.join(ORCHESTRATION_DIR, e.name));
  } catch {
    return []; // no dir (yet) or unreadable → empty inventory
  }
  return files
    .map((file) => {
      const stem = path.basename(file, ".json");
      const doc = readJsonCached<Record<string, unknown>>(file, {});
      const steps = Object.entries(doc)
        .map(([k, v]) => normalizeStep(k, v))
        .filter((s): s is OrchestrationStep => s != null)
        .sort((a, b) => a.step - b.step);
      const { domain, freq } = splitTierName(stem);
      return {
        tier: stem,
        domain,
        freq,
        sourceRef: `scripts/${stem}.sh; scripts/orchestration/${stem}.json`,
        steps,
      };
    })
    .sort((a, b) => {
      const ia = TIER_RUN_ORDER.indexOf(a.tier);
      const ib = TIER_RUN_ORDER.indexOf(b.tier);
      return (
        (ia === -1 ? TIER_RUN_ORDER.length : ia) -
          (ib === -1 ? TIER_RUN_ORDER.length : ib) ||
        a.tier.localeCompare(b.tier)
      );
    });
}
