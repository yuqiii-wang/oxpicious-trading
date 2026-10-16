#!/usr/bin/env bash
# Shared runner for the orchestration step files (scripts/orchestration/).
# Usage (CWD must be the repo root): bash scripts/run_steps.sh <steps.json>
#
# Runs the file's steps one by one, in num order. can_be_parallel is false
# for every step today — the runner is strictly serial and warns if a step
# ever sets it true. Step failures are logged and counted, never fatal
# (monitoring is alert-only); the runner exits non-zero if any step
# failed. DRY_RUN=1 echoes the plan without executing anything.
set -u

_json=$1
[ -d _common ] || { echo "run_steps.sh: run from the repo root" >&2; exit 2; }

# Plan: one "num|parallel|script|args" line per step, in numeric key order.
_plan=$(python - "$_json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    steps = json.load(fh)
for num in sorted(steps, key=int):
    step = steps[num]
    args = " ".join(step.get("args", []))
    par = 1 if step.get("can_be_parallel") else 0
    print(f"{num}|{par}|{step['script']}|{args}")
PY
) || exit 1

_failed=0

while IFS='|' read -r num par script args; do
  if [ "$par" = "1" ]; then
    echo "[step $num] WARNING: can_be_parallel is not honored, running serially"
  fi
  echo "[step $num] python -m $script $args"
  if [ "${DRY_RUN:-0}" = "1" ]; then
    echo "[step $num] done (dry-run)"
    continue
  fi
  if python -m $script $args; then
    echo "[step $num] done"
  else
    echo "[step $num] FAILED"
    _failed=$((_failed + 1))
  fi
done <<< "$_plan"

if [ "$_failed" -gt 0 ]; then
  echo "[orchestration] $_json: $_failed step(s) FAILED"
  exit 1
fi
echo "[orchestration] $_json: ok"
