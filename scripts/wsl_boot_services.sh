#!/usr/bin/env bash
# WSL [boot] auto-start for the always-on service fleet.
#
# Registered as the distro's [boot] command in /etc/wsl.conf (wrapped in
# su + nohup there so the boot is not blocked and the files stay
# yuqi-owned). Every WSL boot — after a Windows reboot, `wsl --shutdown`,
# or the idle-VM shutdown that kills the whole fleet leaving stale
# heartbeat rows with no stop records — re-runs this script, which starts
# the stream watchdog; the watchdog's first reconcile pass then respawns
# every stale MONITORED_SERVICE (the monitor server — which supervises
# the other seven server_cluster.* servers and, via the downloader area,
# the five downloads.stream.* pollers, as RESIDENTS — plus
# database.db_health, llm_agents.mcp, llm_agents.sandbox,
# llm_agents.deep_agents.server on 127.0.0.1:8766).
# Idempotent: stream_watchdog.sh's pgrep guard makes a rerun over a live
# fleet a no-op, and the spawned services' own max_parallel registration
# refuses duplicates.
#
# The [boot] hook may run before the Windows drive mounts, so wait
# (bounded, ~60s) for the repo to appear before starting anything.

_repo=/mnt/e/oxpicious-trading
for _ in $(seq 1 30); do
  [ -d "$_repo/scripts" ] && break
  sleep 2
done
if [ ! -d "$_repo/scripts" ]; then
  echo "wsl_boot_services: $_repo not mounted after 60s — giving up"
  exit 1
fi

# The watchdog spawns services with sys.executable, so the conda env must
# be active here (same contract scripts/stream_watchdog.sh documents).
source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate base

cd "$_repo"
bash scripts/stream_watchdog.sh
echo "wsl_boot_services: fleet starter done at $(date '+%F %T')"
