#!/usr/bin/env bash
# WSL [boot] entry point — /etc/wsl.conf runs THIS script and nothing else.
#
# The former one-liner boot command
#   su - yuqi -c "nohup bash .../wsl_boot_services.sh >> log 2>&1 &"
# stopped surviving the WSL boot path after a WSL update changed how
# [boot] command strings are parsed (2026-11-10 was the last fleet boot it
# started): the nested double quotes never reached /bin/sh, so su -c got
# `nohup` with NO operand ("nohup: missing operand", logs/wsl_boot.log) and
# the whole always-on fleet stopped coming back after a WSL idle-VM
# shutdown / reboot — the dead-fleet window the UI's "Start stream always"
# button then reported as HTTP 502. A [boot] command must therefore be
# quote-free and self-contained: this file holds the su + nohup + redirect
# INSIDE bash, where quoting is parsed once and reliably.
#
# Deployment: copied verbatim to /usr/local/bin/oxpicious-wsl-boot-hook.sh
# (ON the Linux fs, so the boot can run it before /mnt/e exists) and
# registered there:
#   [boot]
#   command = /usr/local/bin/oxpicious-wsl-boot-hook.sh
# Runs as ROOT (wsl.conf [boot] has no user context). Waits (bounded ~120s)
# for the Windows drive to mount, then drops to yuqi and backgrounds the
# fleet starter so the boot is not blocked; output appends to the same
# logs/wsl_boot.log.

_repo=/mnt/e/oxpicious-trading
for _ in $(seq 1 60); do
  [ -d "$_repo/scripts" ] && break
  sleep 2
done
if [ ! -d "$_repo/scripts" ]; then
  echo "wsl_boot_hook: $_repo not mounted after 120s — giving up"
  exit 1
fi

su - yuqi -c 'nohup bash /mnt/e/oxpicious-trading/scripts/wsl_boot_services.sh >> /mnt/e/oxpicious-trading/logs/wsl_boot.log 2>&1 &'
