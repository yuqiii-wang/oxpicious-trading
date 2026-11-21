---
name: ui-test
description: Verification for anything that reaches the data_viz UI — send the request FROM the UI (open the page in the browser with concrete params), then assert the API payloads the page actually received (array lengths, date-axis alignment, element positions, new field values) plus the server log and browser console, and only then take ONE screenshot pass for visual confirmation. Every request is sent by the browser to the respective server (Vite proxy → Express → the respective server_cluster area) — never by cmd (curl / node fetch / Python HTTP from the shell); the browser-context fetch is the only fallback, for scopes the UI cannot reach; never test via Python. Use whenever verifying or testing a change under data_viz/ (React pages/components, chart option builders, tooltips, api/routes, api/services, shared/types.ts) or an analysis/ pipeline feeding those endpoints — including requests like "test this UI change", "verify the page/chart", "does it look right", or any moment a session that edited data_viz files moves to verification.
---

# data_viz UI test — UI request + payload/log check first, screenshot second

Send the request from the UI, not from a raw HTTP call and never from a
Python test (repo-wide convention — oxpicious-dev-conventions
"Verification"): open the page that renders the change in the browser,
with the concrete params a user would pick, so the request flows the real
stack (Vite proxy → Express route → service SQL → pipeline rows). A raw
curl/node fetch proves a URL works, not that the page works — the UI path
is the test. Every request the test sends is issued BY THE BROWSER to the
RESPECTIVE SERVER — never by cmd (no `curl` / `node fetch` / ad-hoc
Python HTTP one-liner from the shell). The respective server is the
Express API behind the Vite proxy for `data_viz` pages, which itself
delegates Python work to the respective `server_cluster/` area server
(ports in `server_cluster/cluster_config.json`: llm 8767, downloader
8769, builder 8770, analyzer 8771, strategist 8772, monitor 8773,
config 8774, maintainer 8775) — the same no-cmd rule
`py-runner.service.ts` already enforces server-side.

Precision still comes from the PAYLOAD, not pixels: a rendering pass can
only say "something looks wrong"; the payload check says exactly which
array or position is wrong. So assert the response bodies the page
actually received and the logs the request produced, then spend ONE
screenshot pass to confirm pixels — green data does not prove green
rendering (theme reads, option-builder mapping), so the screenshot still
happens, just last.

## Tier 0 — scope the change to affected endpoints and arrays

From the diff, map each changed file to what it affects, and find the
endpoint URLs in the header comment of each `data_viz/api/routes/*.ts`
file (they document every route with params):

| Changed under | Meaning for the test |
|---|---|
| `analysis/**`, `builds/**` | DB rows feed the responses — re-run the pipeline first (WSL `python -m ...` per oxpicious-dev-conventions), then data-check the endpoints that read those tables |
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
- which page + concrete params will drive each endpoint from the UI

## Tier 1 — UI-driven request + data/log check (no pixel judgment yet)

1. Servers: Express API at `http://localhost:3001` (`npm run server:dev`),
   Vite at `http://localhost:5173` proxying `/api`; `npm run dev` runs
   both. If they are not already running, start them yourself as a
   background task so you hold the log output — the Express server's
   stdout IS the server log you check in step 4 (plain
   `console.log`/`console.error`, nodemon-restarted).
2. Load the `browser-use:control-browser` skill and open the page that
   renders the changed arrays, with the SAME concrete params written down
   in Tier 0 (same code / sec_type / metric) — this is the request.
3. From the browser tooling's network/console access, take the API
   responses the page just fetched and assert per the Tier-0 invariants.
   If the tooling cannot expose a response body, re-fetch the EXACT URL
   the page called (copy it from the network log) FROM THE OPEN PAGE'S
   OWN CONTEXT (browser-tooling `fetch` against the same origin /
   `/api` proxy path) and assert that — still the UI's request, sent by
   the browser to the same server, not an invented cmd-sent one:
   - HTTP 200 and the expected envelope keys
   - every parallel array's length equals its axis array's length
   - head AND tail elements sit on the same dates across arrays
     (alignment = positions)
   - new fields present on every element, right type, plausible
     first/mid/last samples
   - null/NaN only where semantics allow (count them; watch for runs)
   - empty scope (unknown code, filtered-out scope) → clean empty arrays,
     not a 500
   - bad params → the route's 400 with its message
4. Check the logs the request produced: the Express server stdout (route
   actually hit, SQL errors, stack traces, warnings) and the browser
   console (client-side errors, unhandled rejections).
5. Type gate: `cd data_viz && npm run check` and
   `npx eslint <changed files>` — `shared/types.ts` is the contract both
   sides compile against; a shape mismatch that would silently misrender
   in the browser usually surfaces here first.

Grow a check beyond a one-liner → save it under `temp_scripts/` and
re-run it per iteration (repo convention for throwaway scripts).

**Fallback — only for scopes the UI cannot reach** (a param combination
with no UI entry point, an error branch the page cannot trigger): the
page-context fetch, still sent BY THE BROWSER to the respective server —
NEVER a shell one-liner (`curl` / `node --input-type=module -e` /
Python `requests`). With the page open (any `data_viz` page at the Vite
origin — `/api` is proxied to Express), run the fetch in the page's own
JS context via the browser tooling's evaluate, and assert the returned
JSON in the tooling's script context (`jq` is NOT installed):

```js
// evaluated in the open page's context — browser-sent, Vite proxy → Express
const res = await fetch('/api/analysis/mov-ave-spread/chart?sec_type=etf&code=510050');
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
  return j.code + ' ' + j.name + ': ' + pairs.length + ' pairs x ' + n + ' rows; '
    + first?.date + ' -> ' + last?.date + ' | ' + (bad.length ? 'FAIL\n' + bad.join('\n') : 'PASS');
}
```

This is a fallback, not the default lane — when you use it, say why in
the report.

**Gate: any FAIL → fix and repeat Tier 1. Never screenshot a page whose
data is red — it proves nothing and burns a full render pass.**

## Tier 2 — screenshot pass (only once Tier 1 is green)

1. The browser is already open from Tier 1 — same session, same params.
2. Verify visually: series and bands present in the right positions,
   tooltip rows show the new fields, no unexpected empty-state
   placeholders. For ANY chart change also toggle light/dark — a chart
   keeping stale colors after the toggle is a non-reactive theme read, a
   known bug class (see ui-charts).
3. Save screenshots to `temp_scripts/` with descriptive names.
4. Visual issue → fix (nearly always in the `src/` consumption path,
   since data is green), re-run the ONE Tier-1 check for anything the
   fix touched, screenshot once more.

A full interactive black-box pass (clicks, inputs, flows, per-step
evidence) is the `web-gui-tester` skill's job — this skill is the fast
verification lane after a code change; escalate to it when the user asks
for real GUI testing rather than change verification.

## When the screenshot may be skipped

- Tier 1 green AND the diff provably cannot change rendering (pure SQL
  rewrite with identical response shape, perf-only change, comments) —
  data + log check alone; say so explicitly in the report.
- Anything touching `chartOption/`, tooltip formatters, layout, colors,
  or adding/removing series → always screenshot.
- The user asks to see it → always screenshot.

## Report

One line per endpoint: the UI-driven request (page + params), the payload
assertions and PASS/FAIL, and the log findings; then the screenshot
verdicts with saved paths. When a run stops at Tier 1, state which
assertion or log line failed and skip Tier 2 explicitly — never report a
screenshot that was not taken. If you used the page-context fallback,
state why.
