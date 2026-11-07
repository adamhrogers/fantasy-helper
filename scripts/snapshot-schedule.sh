#!/usr/bin/env bash
# Run the daily snapshots on this Mac with launchd until M3 moves them to GitHub Actions.
# M3 part 1 built the cloud job; this stays installed through a two-day overlap, then is
# uninstalled (docs/runbooks/cloud-snapshots.md, ADR 0023).
#
#   scripts/snapshot-schedule.sh install     # 06:30 morning set, 17:30 evening set (local time)
#   scripts/snapshot-schedule.sh status      # job state, last exit code, recent log lines
#   scripts/snapshot-schedule.sh run-now     # trigger the morning job immediately
#   scripts/snapshot-schedule.sh render DIR  # write the plists to DIR without loading them
#   scripts/snapshot-schedule.sh uninstall
#
# A job missed while the Mac is asleep runs when it wakes. A job missed while it's shut down
# is skipped, so leave the Mac asleep rather than off overnight until M3.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UV="$(command -v uv || true)"
AGENTS="$HOME/Library/LaunchAgents"
LOGS="$HOME/Library/Logs/fantasy-helper"
DOMAIN="gui/$(id -u)"
JOBS=("morning:6:30" "evening:17:30")

label() { echo "com.fantasyhelper.snapshot.$1"; }

write_plist() {
  local set="$1" hour="$2" minute="$3" path="$AGENTS/$(label "$1").plist"
  cat >"$path" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$(label "$set")</string>
  <key>ProgramArguments</key>
  <array>
    <string>$UV</string><string>run</string><string>--frozen</string>
    <string>fh</string><string>snapshot</string><string>--set</string><string>$set</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$(dirname "$UV"):/usr/bin:/bin</string>
    <key>FH_LOG_JSON</key><string>true</string>
  </dict>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>$hour</integer><key>Minute</key><integer>$minute</integer></dict>
  <key>StandardOutPath</key><string>$LOGS/snapshot-$set.log</string>
  <key>StandardErrorPath</key><string>$LOGS/snapshot-$set.log</string>
</dict>
</plist>
PLIST
  plutil -lint "$path" >/dev/null
  echo "$path"
}

case "${1:-}" in
  install)
    [[ -n "$UV" ]] || { echo "uv not found on PATH" >&2; exit 1; }
    [[ -f "$REPO/.env" ]] || { echo "no .env in $REPO (see .env.example)" >&2; exit 1; }
    mkdir -p "$AGENTS" "$LOGS"
    for job in "${JOBS[@]}"; do
      IFS=: read -r set hour minute <<<"$job"
      launchctl bootout "$DOMAIN/$(label "$set")" 2>/dev/null || true
      plist="$(write_plist "$set" "$hour" "$minute")"
      launchctl bootstrap "$DOMAIN" "$plist"
      printf 'installed %s at %02d:%02d -> %s\n' "$(label "$set")" "$hour" "$minute" "$plist"
    done
    echo "logs: $LOGS"
    ;;
  uninstall)
    for job in "${JOBS[@]}"; do
      set="${job%%:*}"
      launchctl bootout "$DOMAIN/$(label "$set")" 2>/dev/null || true
      rm -f "$AGENTS/$(label "$set").plist"
      echo "removed $(label "$set")"
    done
    ;;
  status)
    for job in "${JOBS[@]}"; do
      set="${job%%:*}"
      echo "== $(label "$set")"
      launchctl print "$DOMAIN/$(label "$set")" 2>/dev/null \
        | grep -E '^\s*(state|last exit code|runs) =' || echo "  not installed"
      [[ -f "$LOGS/snapshot-$set.log" ]] && tail -n 9 "$LOGS/snapshot-$set.log" | sed 's/^/  /'
    done
    ;;
  render)
    [[ -n "${2:-}" ]] || { echo "usage: $0 render DIR" >&2; exit 2; }
    mkdir -p "$2"
    AGENTS="$2"
    for job in "${JOBS[@]}"; do
      IFS=: read -r set hour minute <<<"$job"
      write_plist "$set" "$hour" "$minute"
    done
    ;;
  run-now)
    launchctl kickstart -p "$DOMAIN/$(label morning)"
    echo "started; see: $0 status"
    ;;
  *)
    sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
