# Orchestration step files

One JSON per tier under `scripts/orchestration/` — the registry of
`python -m` steps for the nightly pipeline. The tier files are consumed
by THREE surfaces (one registry, three front-ends):

1. **The maintainer's nightly chain** (the schedule owner since the
   2026-11 sh retirement): `server_cluster.maintainer`'s
   `nightly_pipeline` job fires at 16:00 Asia/Shanghai on every
   A-share TRADING day (the whole pass skips on non-trading days;
   `MAINTAINER_PIPELINE_AT` env overrides the time) and drives the
   tiers serially — each tier run through the monitor area's
   orchestration runner over HTTP.
2. **The monitor area's runner** (the executor):
   `server_cluster/monitor/orchestration.py` — `POST /orchestration/run
   {"file": "<tier>"}` runs one tier's steps one by one in key order
   (a real `python -m` child per step through the area's run registry).
   The data_viz OrchestrationPage proxies this (`POST
   /api/monitor/orchestration/run`).
3. **The read side**: `server_cluster.config` (port 8774) serves the
   files as JSON; the Express API's `orchestration.service.ts` reads
   them for the UI inventory.

The former sh drivers (`main_run.sh` → `scripts/<tier>.sh` →
`scripts/run_steps.sh`) are retired — the schedule and guards now live
in the maintainer job (`server_cluster/maintainer/pipeline.py`).

## Schema

Top-level keys are the step numbers (run order). Each step:

- `script` — the `python -m` module to run.
- `src_host` — optional query-destination host group (e.g. `sse`,
  `szse`, `bse`, `huatai`, `efunds`, `eastmoney`) enabling
  **diff-src-parallel** execution (downloads tiers only): consecutive
  steps whose `src_host` values DIFFER run concurrently as one wave;
  the whole wave completes before the next starts, so step order is
  never violated and same-host steps never overlap. A step WITHOUT
  `src_host` runs alone (fully serial) — give a step no `src_host`
  when it has an ordering dependency on earlier steps (e.g.
  `downloads.macro.ai_daily` feeds the Zhihu topic-search phase) or
  must stay serial for other reasons (`downloads_once`'s heavy bulk
  DB writes). The host group follows the external_request_config URL
  netlocs (registrable-domain family, so `yunhq`/`query`/`www` of
  sse.com.cn are one `sse`), including the code-level hosts the
  request-config tree does not carry (`futures.sgx_a50` →
  `eastmoney` klines, `futures.hkex.msci_a50` → `hkex`,
  `index.bse.composition` → the CSI co-published `csindex` autofile).
- `can_be_parallel` — `false` for every step today; the runner executes
  strictly serially and warns if a step ever sets it `true`.
- `args` — optional CLI args list (e.g. `["--options-eod"]`).
- `note` — optional ordering/dependency comment, ignored by the runner.

## Run semantics (run_steps semantics, unchanged)

Waves: the runner groups the tier's steps into consecutive waves — a
step carrying `src_host` joins the open wave iff its host is not yet
claimed in that wave and the wave is under the width cap
(`ORCHESTRATION_MAX_WAVE_WIDTH`, default 8); any other step closes the
wave and runs as a singleton. Each wave's steps are submitted together
(each still a real `python -m` child through the run registry — the
registry dedupe, per-service `max_parallel` claims and single-instance
file locks all still apply) and polled to their exits before the next
wave opens. The width cap bounds concurrent child processes (RAM /
DB-write load); the nightly `downloads_daily` pass compresses from 30
serial steps to ~10 waves.

A step's failure never aborts the batch or tier (monitoring is
alert-only); the run ends `failed` if any step failed. A tier that
exceeds the maintainer's wait cap (`MAINTAINER_PIPELINE_TIER_TIMEOUT_S`,
default 6h) or whose run the monitor loses (a monitor restart wipes its
in-memory run table) ABORTS the chain — dependents never run on partial
inputs; the steps' incremental guards self-heal on the next night's
fire or a manual run. An interrupted chain resumes from its state file
(`server_cluster/_state/nightly_pipeline.json`) when the maintainer
restarts.

## What runs when

The nightly chain fires at 16:00 China time on A-share trading days
and runs the tiers sequentially: monitoring → downloads
(daily/monthly/quarterly/semiannual/once) → builds
(daily/semiannual/once/monthly) → analysis → strategy
(`NIGHTLY_CHAIN` in `server_cluster/maintainer/pipeline.py` is the
canonical order). The whole pass skips on non-trading days; a manual
run with `{"force": true}` bypasses the gate (the former
`FORCE_DOWNLOADS=1`). The monthly builds tier (importance ranks)
carries its own month guard inside the step (no-op unless a new month
started). Higher-cadence tiers (monthly, quarterly) ride every pass —
their tasks' internal skip logic makes off-cadence runs no-ops: the
monthly tasks refresh on the 1st of each month, and the quarterly
tier's tasks (the index/ETF composition downloads) refresh on the 1st
of a quarter-start month (Jan/Apr/Jul/Oct) — the former
`downloads_seasonally` tier, renamed 2026-11 when the four index
composition / linked-ETF steps moved off the monthly tier.

A pass with failures (or an aborted/interrupted one) KEEPS its record
in `server_cluster/_state/nightly_pipeline.json` as the recovery
worklist; a clean pass deletes it. The 21:00 ZCode check reads and
drives the recovery surface below.

## Manual + recovery runs

- One tier (UI): the data_viz OrchestrationPage → the monitor runner.
- One tier (curl): `curl -X POST 127.0.0.1:8773/orchestration/run -d
  '{"file": "downloads_daily"}'` — an in-flight run of the same file is
  refused (409); the nightly chain adopts (waits out) an in-flight run
  instead of racing it.
- The whole chain / a subset (also the rerun-particular-tasks path):
  `curl -X POST 127.0.0.1:8775/jobs/nightly_pipeline/run -d '{"tiers":
  ["downloads_daily", "analysis_daily"], "force": true}'` (empty
  `tiers` = the whole chain, chain order kept; a second concurrent
  chain is refused).
- The last pass's record: `GET 127.0.0.1:8775/pipeline/last-pass` —
  per-tier outcomes, what remains (`remaining`), whether a chain is
  running now.
- Finish the remaining tiers of a failed/aborted/interrupted pass:
  `curl -X POST 127.0.0.1:8775/pipeline/finish` (chain order, gates
  off; blocks until done — poll `last-pass` meanwhile).
