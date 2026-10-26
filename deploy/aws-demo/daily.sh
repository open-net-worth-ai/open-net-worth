#!/usr/bin/env bash
# Keep the AWS demo current from this Mac: once a day, ship origin/main if it moved, then give
# visitors a fresh book (yesterday's edits and conversations go; Lucius's AI settings stay).
#
#   daily.sh install [HH:MM]   install the LaunchAgent (default 10:00, local time)
#   daily.sh uninstall         remove it
#   daily.sh run               what the agent runs; safe to run by hand
#
# Install resolves the VM's static IP once (AWS_PROFILE / AWS_REGION, or ONW_DEMO_HOST=<ip>) and
# bakes it into the agent, so the daily run needs only SSH, never an AWS session. A failed
# deploy rolls back to the tag that was running and raises a macOS notification; the log is $LOG.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../.." && pwd)"
DEMO="${HERE}/demo-aws.sh"
LABEL=ai.opennetworth.demo-daily
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
LOG="${HOME}/Library/Logs/onw-demo-daily.log"
LOCK="${TMPDIR:-/tmp}/onw-demo-daily.lock"
STARTED_DOCKER=0

notify() { osascript -e "display notification \"$1\" with title \"Open Net Worth demo\"" >/dev/null 2>&1 || true; }
deployed_tag() { "${DEMO}" ssh "grep '^ONW_DEMO_TAG=' /srv/onw-demo-stack/.env | cut -d= -f2"; }

healthy() {
  local _
  for _ in $(seq 1 20); do
    "${DEMO}" ssh "curl -fsS -m 5 http://127.0.0.1:5175/api/health" 2>/dev/null | grep -q '"status":"ok"' && return 0
    sleep 6
  done
  return 1
}

rollback() {
  echo "==> rolling back to $1"
  "${DEMO}" ssh "cd /srv/onw-demo-stack && sed -i 's/^ONW_DEMO_TAG=.*/ONW_DEMO_TAG=$1/' .env && docker compose up -d app"
}

docker_up() {
  docker info >/dev/null 2>&1 && return 0
  open -a Docker
  STARTED_DOCKER=1
  local _
  for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && return 0; sleep 2; done
  echo "Docker did not start" >&2
  return 1
}

# The image lives on the VM once shipped; locally it is ~2 GB of dead weight, plus build cache.
cleanup() {
  if docker info >/dev/null 2>&1; then
    docker images onw-demo -q | xargs -r docker rmi -f >/dev/null 2>&1 || true
    docker builder prune -af >/dev/null 2>&1 || true
    # Not osascript: a LaunchAgent may not send Apple Events to another app without a TCC grant.
    if [[ "${STARTED_DOCKER}" == 1 ]]; then docker desktop stop >/dev/null 2>&1 || true; fi
  fi
  rmdir "${LOCK}" 2>/dev/null || true
}

cmd_run() {
  mkdir -p "$(dirname "${LOG}")"
  exec >>"${LOG}" 2>&1
  echo "=== $(date '+%F %T')"
  mkdir "${LOCK}" 2>/dev/null || { echo "another run holds ${LOCK}; skipping"; return 0; }
  trap cleanup EXIT

  git -C "${REPO_ROOT}" fetch --quiet origin main
  local want have
  want="$(git -C "${REPO_ROOT}" rev-parse --short=9 origin/main)"
  have="$(deployed_tag)"
  if [[ "${want}" == "${have}" ]]; then
    echo "==> ${have} is already live"
  else
    echo "==> shipping origin/main ${want} (live: ${have})"
    docker_up
    if ! "${DEMO}" ship --ref origin/main || ! healthy; then
      rollback "${have}"
      notify "Deploy of ${want} failed — rolled back to ${have}. See ${LOG}"
      return 1
    fi
  fi

  "${DEMO}" book --reset
  healthy || { notify "Demo is unhealthy after the daily book reset. See ${LOG}"; return 1; }
  echo "==> done: $(deployed_tag) live, fresh book"
}

cmd_install() {
  local at="${1:-10:00}" hour minute host="${ONW_DEMO_HOST:-}"
  if [[ -z "${host}" ]]; then
    host="$(aws lightsail get-instance --instance-name "${ONW_DEMO_VM:-onw-demo}" \
      --query 'instance.publicIpAddress' --output text 2>/dev/null)" || true
  fi
  [[ -n "${host}" && "${host}" != "None" ]] \
    || { echo "ERROR: cannot find the VM's IP — sign in to AWS, or set ONW_DEMO_HOST=<ip>" >&2; exit 1; }
  hour="$((10#${at%%:*}))"
  minute="$((10#${at##*:}))"
  mkdir -p "$(dirname "${PLIST}")"
  cat >"${PLIST}" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>${HERE}/daily.sh</string><string>run</string></array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>${hour}</integer><key>Minute</key><integer>${minute}</integer></dict>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>ONW_DEMO_HOST</key><string>${host}</string>
  </dict>
  <key>StandardOutPath</key><string>${LOG}</string>
  <key>StandardErrorPath</key><string>${LOG}</string>
</dict>
</plist>
PLIST
  launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "${PLIST}"
  printf '==> installed: daily at %02d:%02d for %s, log %s\n' "${hour}" "${minute}" "${host}" "${LOG}"
}

cmd_uninstall() {
  launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
  rm -f "${PLIST}"
  echo "==> removed ${LABEL}"
}

case "${1:-}" in
  run)       cmd_run ;;
  install)   shift; cmd_install "$@" ;;
  uninstall) cmd_uninstall ;;
  *) sed -n '2,10p' "$0"; exit 2 ;;
esac
