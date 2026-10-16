# Orchestration step files

One JSON per tier script in `scripts/` (`<tier>.json` ↔ `scripts/<tier>.sh`).
The sh keeps only its guard (A-share trading day + start time for the daily
tiers) and delegates to `scripts/run_steps.sh`, which executes the file's
steps one by one in key order.

## Schema

Top-level keys are the step numbers (run order). Each step:

- `script` — the `python -m` module to run.
- `can_be_parallel` — `false` for every step today; the runner executes
  strictly serially and warns if a step ever sets it `true`.
- `args` — optional CLI args list (e.g. `["--options-eod"]`).
- `note` — optional ordering/dependency comment, ignored by the runner.

## Runner knobs (`scripts/run_steps.sh`)

`DRY_RUN=1` echoes the plan (order) without executing.

A step's failure never aborts the batch or tier (monitoring is alert-only);
the runner exits non-zero if any step failed.

## What runs when

`main_run.sh` starts at 18:00 and runs the tiers sequentially:
monitoring → stream watchdog → downloads (daily/monthly/semiannual/once)
→ builds (daily/semiannual/once/monthly) → analysis → strategy. The daily
tiers' guard (A-share trading day AND local time >= 18:00) only protects
against off-schedule manual runs; `FORCE_DOWNLOADS=1` bypasses it. The
monthly builds tier (importance ranks) carries its own month guard inside
the step (no-op unless a new month started).
