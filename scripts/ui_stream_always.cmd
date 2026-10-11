@echo off
rem Task Scheduler action behind the Service Monitor UI's "Start stream
rem always" button (POST /api/monitor/stream-always re-creates + runs the
rem oxpicious-stream-always task). The task's console session is what
rem keeps the spawned services alive: WSL tears down a session's whole
rem process tree when its wsl.exe client exits, and a headless/detached
rem client does not even hold the distro open (2026-10-11 probes) — the
rem task session does (real pty, client stays connected, so do the
rem nohup'd services). The starters are idempotent (watchdog pgrep-guard,
rem pollers max_parallel guard), so the user's own main_run.sh coexists;
rem stop the button-started stream with: wsl -d Ubuntu-22.04 -- pkill -f
rem stream-always-keeper
cd /d E:\oxpicious-trading
wsl.exe -d Ubuntu-22.04 -- bash -lc "source ~/miniconda3/etc/profile.d/conda.sh && conda activate base && cd /mnt/e/oxpicious-trading && mkdir -p logs/$(date +%%F) && exec >> logs/$(date +%%F)/ui_stream_always.log 2>&1 && bash scripts/stream_watchdog.sh && bash scripts/stream_always.sh && echo [ui] stream-always task session up $(date +%%T) && exec -a stream-always-keeper sleep infinity"
