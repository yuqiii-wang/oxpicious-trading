---
name: ui-test
description: Tiered verification for anything that reaches the data_viz UI — FIRST assert the affected API response arrays (lengths, date-axis alignment, element positions, new field values) with direct HTTP checks and NO browser, and ONLY after those pass take page screenshots for visual confirmation. Use whenever verifying or testing a change under data_viz/ (React pages/components, chart option builders, tooltips, api/routes, api/services, shared/types.ts) or an analyze/ pipeline feeding those endpoints — including requests like "test this UI change", "verify the page/chart", "does it look right", or any moment a session that edited data_viz files moves to verification.
---

# data_viz UI test — data check first, screenshot second

Do NOT reach for the browser on every change. The cheap, precise check is
the API payload: a rendering pass can only say "something looks wrong";
the payload check says exactly which array or position is wrong. Iterate
fixes against data checks (seconds each), then spend ONE screenshot pass
to confirm pixels — green data does not prove green rendering (theme
reads, option-builder mapping), so the screenshot still happens, just
last.

## Tier 0 — scope the change to affected endpoints and arrays

From the diff, map each changed file to what it affects, and find the
endpoint URLs in the header comment of each `data_viz/api/routes/*.ts`
file (they document every route with params):

| Changed under | Meaning for the test |
|---|---|
| `analyze/**`, `builds/**` | DB rows feed the responses — re-run the pipeline first (WSL `python -m ...` per oxpicious-dev-conventions), then data-check the endpoints that read those tables |
| `data_viz/shared/types.ts` | the response contract both sides compile against — data-check every endpoint whose response type changed |
| `data_viz/api/services/**` | SQL + array shaping/ordering — data-check its routes' endpoints |
| `data_viz/api/routes/**` | endpoint contract/params — data-check the route incl. bad-param and empty-scope cases |
| `data_viz/src/**` (component, chartOption builder, tooltip) | consumption only — data-check the endpoint(s) the component fetches, then the screenshot matters most |

Before checking, write down the invariants the diff implies:
- which arrays change (new fields, changed element shape, changed length or ordering)
- which arrays must stay ALIGNED with which (parallel arrays sharing the
  date axis, e.g. every `pairs[i].rows` in a chart response must be
  index-aligned with `shared` — a position shift here is the classic
  misrender)
- expected head/tail sample values

## Tier 1 — data check (no browser, no screenshot)

Servers: Express API at `http://localhost:3001` (`npm run server:dev`),
Vite at `http://localhost:5173` proxying `/api`. `jq` is NOT installed —
use `node --input-type=module -e` one-liners (no backticks / `$` inside
the double-quoted string, or bash eats them):

```bash
node --input-type=module -e "
const url = 'http://localhost:3001/api/analysis/mov-ave-spread/chart?sec_type=etf&code=510050';
const res = await fetch(url);
const j = await res.json().catch(() => null);
const bad = [];
if (!res.ok || !j) bad.push('HTTP ' + res.status);
else {
  const pairs = j.pairs ?? [];
  const n = pairs[0]?.rows?.length ?? 0;
  for (const p of pairs) {
    if (p.rows.length !== n) bad.push(p.pair_label + ' rows=' + p.rows.length + ' != ' + n);
    if (p.rows.length !== (j.shared?.length ?? -1)) bad.push(p.pair_label + ' misaligned with shared');
  }
  const first = pairs[0]?.rows?.[0];
  const last = pairs[0]?.rows?.at(-1);
  console.log(j.code + ' ' + j.name + ': ' + pairs.length + ' pairs x ' + n + ' rows; ' + first?.date + ' -> ' + last?.date);
}
console.log(bad.length ? 'FAIL\n' + bad.join('\n') : 'PASS');
"
```

Assert per the Tier-0 invariants:
- HTTP 200 and the expected envelope keys
- every parallel array's length equals its axis array's length
- head AND tail elements sit on the same dates across arrays (alignment = positions)
- new fields present on every element, right type, plausible first/mid/last samples
- null/NaN only where semantics allow (count them; watch for runs)
- empty scope (unknown code, filtered-out scope) → clean empty arrays, not a 500
- bad params → the route's 400 with its message

Then the type gate: `cd data_viz && npm run check` and
`npx eslint <changed files>` — `shared/types.ts` is the contract both
sides compile against; a shape mismatch that would silently misrender in
the browser usually surfaces here first.

Grow a check beyond a one-liner → save it under `temp_scripts/` and
re-run it per iteration (repo convention for throwaway scripts).

**Gate: any FAIL → fix and repeat Tier 1. Never screenshot a page whose
data is red — it proves nothing and burns a full render pass.**

## Tier 2 — screenshot pass (only once Tier 1 is green)

1. `npm run dev` runs both servers; Vite page at `http://localhost:5173`.
2. Load the `browser-use:control-browser` skill and open the page that
   renders the changed arrays, with the SAME concrete params used in
   Tier 1 (same code / sec_type / metric).
3. Verify visually: series and bands present in the right positions,
   tooltip rows show the new fields, no unexpected empty-state
   placeholders. For ANY chart change also toggle light/dark — a chart
   keeping stale colors after the toggle is a non-reactive theme read, a
   known bug class (see ui-charts).
4. Save screenshots to `temp_scripts/` with descriptive names.
5. Visual issue → fix (nearly always in the `src/` consumption path,
   since data is green), re-run the ONE Tier-1 check for anything the
   fix touched, screenshot once more.

A full interactive black-box pass (clicks, inputs, flows, per-step
evidence) is the `web-gui-tester` skill's job — this skill is the fast
verification lane after a code change; escalate to it when the user asks
for real GUI testing rather than change verification.

## When the screenshot may be skipped

- Tier 1 green AND the diff provably cannot change rendering (pure SQL
  rewrite with identical response shape, perf-only change, comments) —
  data check alone; say so explicitly in the report.
- Anything touching `chartOption/`, tooltip formatters, layout, colors,
  or adding/removing series → always screenshot.
- The user asks to see it → always screenshot.

## Report

One line per data check (endpoint URL + the assertions + PASS/FAIL),
then the screenshot verdicts with saved paths. When a run stops at
Tier 1, state which assertion failed and skip Tier 2 explicitly — never
report a screenshot that was not taken.
