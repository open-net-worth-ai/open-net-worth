#!/usr/bin/env bash
# Host a public Open Net Worth demo on one AWS Lightsail VM, reached through a Cloudflare tunnel.
#
#   demo-aws.sh provision          create the VM (Ubuntu 24.04, 2 GB, Docker, only SSH open)
#   demo-aws.sh ship [--worktree | --ref REF]  build the image for linux/amd64 and load it on the VM
#   demo-aws.sh book --seed        generate a fresh fabricated demo book on the VM
#   demo-aws.sh book --reset       replace the live book with a fresh one, keeping Lucius's AI settings
#   demo-aws.sh book [DIR]         or copy an existing hosted demo book (default ~/.open-net-worth-demo)
#   demo-aws.sh lucius             point Lucius at OpenRouter (key read silently, never echoed)
#   demo-aws.sh verify             open the VM's demo at http://127.0.0.1:15175 over SSH
#   demo-aws.sh tunnel             start the connector for a tunnel DEDICATED to the demo
#   demo-aws.sh ssh | logs | status
#
# AWS account and region come from the AWS CLI's own AWS_PROFILE / AWS_REGION. The VM has no
# public HTTP port; see README.md for the tunnel setup and why it must serve only the demo.
set -euo pipefail

REGION="${AWS_REGION:-$(aws configure get region 2>/dev/null || true)}"
REGION="${REGION:-us-east-1}"
NAME="${ONW_DEMO_VM:-onw-demo}"
SSH_KEY="${ONW_DEMO_SSH_KEY:-${HOME}/.ssh/id_ed25519}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../.." && pwd)"
STACK=/srv/onw-demo-stack
BOOK=/srv/onw-demo

ls_() { aws lightsail --region "${REGION}" "$@"; }
die() { echo "ERROR: $*" >&2; exit 1; }
confirm() { read -r -p "$1 [y/N] " a; [[ "${a}" == "y" || "${a}" == "Y" ]]; }

IP=""
# ONW_DEMO_HOST skips the AWS lookup: the VM sits on a static IP, and an unattended job should
# not depend on an AWS sign-in session that expires.
need_vm() {
  [[ -n "${ONW_DEMO_HOST:-}" ]] && { IP="${ONW_DEMO_HOST}"; return 0; }
  IP="$(ls_ get-instance --instance-name "${NAME}" --query 'instance.publicIpAddress' --output text 2>/dev/null)" \
    && [[ -n "${IP}" && "${IP}" != "None" ]] \
    || die "no running Lightsail instance '${NAME}' in ${REGION} — run provision first"
}
SSH_OPTS=(-i "${SSH_KEY}" -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30)
remote() { ssh "${SSH_OPTS[@]}" "ubuntu@${IP}" "$@"; }
# Sets KEY=VALUE in the VM's .env from STDIN, so the value never appears in argv on either side.
remote_env_set() {
  remote "umask 077; f=${STACK}/.env; touch \$f; v=\$(cat); \
    { grep -v '^$1=' \$f || true; printf '%s=%s\n' '$1' \"\$v\"; } > \$f.tmp && mv \$f.tmp \$f"
}
remote_env_has() { remote "grep -q '^$1=' ${STACK}/.env 2>/dev/null"; }
compose() { remote "cd ${STACK} && docker compose $*"; }
# Reads a secret from a pipe when there is one, else from a hidden prompt.
read_secret() {
  local v
  if [[ -t 0 ]]; then read -r -s -p "$1" v; echo >&2; else read -r v; fi
  printf '%s' "${v}"
}

cmd_provision() {
  [[ -f "${SSH_KEY}.pub" ]] || die "no SSH public key at ${SSH_KEY}.pub (set ONW_DEMO_SSH_KEY)"
  ls_ get-instance --instance-name "${NAME}" >/dev/null 2>&1 && die "instance '${NAME}' already exists"
  # 2 GB, dual-stack (IPv4 included in the price). A Lucius turn plus the app peak well over
  # the 1 GB plan once a few visitors chat at once; the swap file is insurance, not capacity.
  local bundle
  bundle="$(ls_ get-bundles --query "bundles[?isActive && ramSizeInGb==\`2.0\` && contains(supportedPlatforms, 'LINUX_UNIX') && !contains(bundleId, 'ipv6')] | [0].bundleId" --output text)"
  [[ -n "${bundle}" && "${bundle}" != "None" ]] || die "no 2 GB Linux bundle found in ${REGION}"
  echo "==> ${REGION}: bundle ${bundle} ($(ls_ get-bundles --query "bundles[?bundleId=='${bundle}'] | [0].price" --output text) USD/mo)"

  # Lightsail names share ONE namespace across resource types, so the key pair and static IP
  # carry suffixes — a key pair named like the instance makes create-instances refuse it.
  ls_ get-key-pair --key-pair-name "${NAME}-key" >/dev/null 2>&1 \
    || ls_ import-key-pair --key-pair-name "${NAME}-key" --public-key-base64 "$(cat "${SSH_KEY}.pub")" >/dev/null

  local userdata
  userdata="$(cat <<'CLOUDINIT'
#!/bin/bash
set -eux
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io docker-compose-v2 rsync unattended-upgrades
usermod -aG docker ubuntu
systemctl enable --now docker
# uid 1000 is both `ubuntu` here and `onw` inside the image, so the book is writable by both.
mkdir -p /srv/onw-demo/data /srv/onw-demo/cache /srv/onw-demo/logs /srv/onw-demo-stack
chown -R 1000:1000 /srv/onw-demo /srv/onw-demo-stack
touch /var/lib/onw-demo-ready
CLOUDINIT
)"
  ls_ create-instances --instance-names "${NAME}" --availability-zone "${REGION}a" \
    --blueprint-id ubuntu_24_04 --bundle-id "${bundle}" --key-pair-name "${NAME}-key" \
    --user-data "${userdata}" --tags key=project,value=open-net-worth key=role,value=demo >/dev/null
  echo "==> waiting for the instance to run..."
  until [[ "$(ls_ get-instance --instance-name "${NAME}" --query 'instance.state.name' --output text)" == "running" ]]; do sleep 5; done

  # Lightsail opens 22 and 80 by default; the demo needs only SSH (the tunnel is outbound).
  ls_ put-instance-public-ports --instance-name "${NAME}" \
    --port-infos "fromPort=22,toPort=22,protocol=tcp,cidrs=${ONW_DEMO_SSH_CIDR:-0.0.0.0/0}" >/dev/null
  # A static IP is free while attached, and keeps SSH stable across a stop/start.
  ls_ get-static-ip --static-ip-name "${NAME}-ip" >/dev/null 2>&1 || ls_ allocate-static-ip --static-ip-name "${NAME}-ip" >/dev/null
  ls_ attach-static-ip --static-ip-name "${NAME}-ip" --instance-name "${NAME}" >/dev/null
  need_vm
  echo "==> waiting for cloud-init (Docker install, ~2-3 min)..."
  sleep 20
  until remote test -f /var/lib/onw-demo-ready 2>/dev/null; do sleep 10; done
  echo "==> ready: ubuntu@${IP}   next: $0 ship"
}

cmd_ship() {
  local sha src ref=HEAD
  BUILD_TMP=""
  [[ "${1:-}" == "--ref" ]] && ref="${2:?--ref needs a git ref, e.g. origin/main}"
  if [[ "${1:-}" == "--worktree" ]]; then
    src="${REPO_ROOT}"
    sha="$(git -C "${REPO_ROOT}" rev-parse --short HEAD)-dirty-$(date +%Y%m%d%H%M)"
    echo "==> building from the WORKING TREE (uncommitted changes included)"
  else
    sha="$(git -C "${REPO_ROOT}" rev-parse --short=9 "${ref}")"
    BUILD_TMP="$(mktemp -d "${TMPDIR:-/tmp}/onw-demo-build.XXXXXX")"
    git -C "${REPO_ROOT}" worktree add --detach "${BUILD_TMP}" "${ref}" >/dev/null
    trap 'git -C "${REPO_ROOT}" worktree remove --force "${BUILD_TMP}" >/dev/null 2>&1 || true' EXIT
    src="${BUILD_TMP}"
    echo "==> building committed ${ref} ${sha} (pass --worktree to include uncommitted work)"
  fi
  docker buildx build --platform linux/amd64 -t "onw-demo:${sha}" --load "${src}"
  echo "==> uploading the image (a few minutes)..."
  docker save "onw-demo:${sha}" | gzip -1 | remote 'gunzip | docker load'
  # The compose file ships with the code it was written for, not with this checkout's copy.
  scp "${SSH_OPTS[@]}" -q "${src}/deploy/aws-demo/compose.yml" "ubuntu@${IP}:${STACK}/compose.yml"
  ensure_unpublished_password
  printf '%s' "${sha}" | remote_env_set ONW_DEMO_TAG
  if remote "test -s ${BOOK}/data/onw.db"; then
    compose up -d app
    remote "docker image prune -af --filter 'until=168h' >/dev/null" || true
    echo "==> running onw-demo:${sha}"
  else
    echo "==> image loaded; no book yet — next: $0 book --seed"
  fi
}

# The session signing key lives in .env, beside the book rather than inside it, so a reseed
# never logs visitors out (see scripts/run_demo.sh). Minted once on the VM unless supplied.
ensure_session_key() {
  local key_file="${1:-}"
  if [[ -n "${key_file}" && -s "${key_file}" ]]; then
    remote_env_set AUTH_SESSION_KEY < "${key_file}"
  elif ! remote_env_has AUTH_SESSION_KEY; then
    remote "head -c 48 /dev/urandom | base64 | tr -d '\n/+='" | remote_env_set AUTH_SESSION_KEY
  fi
}

# The password nobody signs in with: visitors need no login (DEMO_OPEN_ACCESS), but the book keeps
# a random one so it stays "configured" — first-run set-a-password closed, /mcp token kept.
ensure_unpublished_password() {
  remote_env_has DEMO_UNPUBLISHED_PASSWORD \
    || remote "head -c 48 /dev/urandom | base64 | tr -d '\n/+='" | remote_env_set DEMO_UNPUBLISHED_PASSWORD
}

cmd_book_seed() {
  remote_env_has ONW_DEMO_TAG || die "no image on the VM yet — run ship first"
  remote "test -s ${BOOK}/data/onw.db" && die "the VM already has a book — remove ${BOOK}/data/onw.db first to reseed"
  ensure_session_key
  ensure_unpublished_password
  echo "==> generating a fabricated demo book dated to today..."
  compose run --rm --no-deps app python3 -m backend.scripts.seed_demo --commit --quiet
  compose up -d app
  echo "==> demo is starting — next: $0 lucius, then $0 verify"
}

# Visitors edit the one shared book (a base-currency switch reaches everyone), so the daily job
# starts each day from a fresh one. The OpenRouter key lives IN the book, so its config is read
# out of the running container and written into the new book; it never leaves the VM.
cmd_book_reset() {
  remote_env_has ONW_DEMO_TAG || die "no image on the VM yet — run ship first"
  echo "==> replacing the book with a fresh one dated to today (AI settings kept)..."
  remote "STACK=${STACK} BOOK=${BOOK} bash -s" <<'SH'
set -euo pipefail
cd "${STACK}"
read_py='import json; from backend.db.model_config_store import ModelConfigStore as S; print(json.dumps(S().get() or {}))'
write_py='import json, sys; from backend.db.model_config_store import ModelConfigStore as S; raw = json.loads(sys.stdin.read()); raw and S().save(raw)'
# This script arrives on bash's stdin, so every command that could read stdin gets /dev/null.
ai="$(docker exec -e DEMO_MODE=0 onw-demo python3 -c "${read_py}" </dev/null)"
[[ "${ai}" == "{}" ]] && echo "    (no AI settings in the old book — run: demo-aws.sh lucius)"
docker compose stop app </dev/null
# The app's nightly backups are copies of the old book, conversations included.
rm -rf "${BOOK}/data" "${BOOK}/backups"
mkdir -m 755 "${BOOK}/data" "${BOOK}/backups"
docker compose run --rm --no-deps -T app python3 -m backend.scripts.seed_demo --commit --quiet </dev/null
printf '%s' "${ai}" | docker compose run --rm --no-deps -T -e DEMO_MODE=0 app python3 -c "${write_py}"
docker compose up -d app </dev/null
SH
  echo "==> fresh book is starting"
}

cmd_book_copy() {
  local root="${1:-${HOME}/.open-net-worth-demo}" stage
  root="${root%/}"
  [[ -f "${root}/data/onw.db" ]] || die "no demo book at ${root} (or use: $0 book --seed)"
  command -v sqlite3 >/dev/null || die "sqlite3 is needed locally to snapshot the book"
  stage="$(mktemp -d "${TMPDIR:-/tmp}/onw-demo-book.XXXXXX")"
  # The source demo can keep running: SQLite's online backup gives a consistent snapshot of a
  # live WAL database, which copying onw.db + its -wal file by hand does not.
  echo "==> snapshotting the database..."
  sqlite3 "${root}/data/onw.db" ".backup '${stage}/onw.db'"
  [[ "$(sqlite3 "${stage}/onw.db" 'PRAGMA integrity_check;')" == "ok" ]] || die "snapshot failed integrity_check"

  compose stop app 2>/dev/null || true
  local excl=(--exclude 'onw.db' --exclude 'onw.db-wal' --exclude 'onw.db-shm' --exclude '*.lock'
              --exclude 'backups/')
  echo "==> copying data/ and cache/ (logs and backups stay behind)..."
  rsync -az --delete -e "ssh ${SSH_OPTS[*]}" "${excl[@]}" "${root}/data/" "ubuntu@${IP}:${BOOK}/data/"
  [[ -d "${root}/cache" ]] && rsync -az --delete -e "ssh ${SSH_OPTS[*]}" "${root}/cache/" "ubuntu@${IP}:${BOOK}/cache/"
  rsync -az -e "ssh ${SSH_OPTS[*]}" "${stage}/onw.db" "ubuntu@${IP}:${BOOK}/data/onw.db"
  remote "rm -f ${BOOK}/data/onw.db-wal ${BOOK}/data/onw.db-shm"
  rm -rf "${stage}"
  # run_demo.sh keeps the hosted demo's key at <root>.session-key; carrying it keeps visitors' cookies valid.
  ensure_session_key "${root}.session-key"

  if remote_env_has ONW_DEMO_TAG; then
    compose up -d app
    echo "==> book in place and the demo is starting — next: $0 lucius, then $0 verify"
  else
    echo "==> book in place — next: $0 ship"
  fi
}

cmd_lucius() {
  local key model payload code
  key="$(read_secret "OpenRouter API key for the demo (input hidden): ")"
  [[ -n "${key}" ]] || die "no key given"
  key="$(printf '%s' "${key}" | tr -d '[:space:]')"
  # Ask OpenRouter before storing it: a hidden prompt makes a double paste invisible, and a bad
  # key otherwise surfaces only as every visitor's first chat failing. The key goes on stdin.
  local verdict
  verdict="$(printf '%s' "${key}" | python3 -c '
import sys, urllib.request, urllib.error
req = urllib.request.Request("https://openrouter.ai/api/v1/key",
                             headers={"Authorization": "Bearer " + sys.stdin.read()})
try:
    urllib.request.urlopen(req, timeout=20).read()
    print("ok")
except urllib.error.HTTPError as e:
    print(f"rejected (HTTP {e.code})")
except Exception as e:
    print(f"unreachable ({type(e).__name__})")
')"
  [[ "${verdict}" == "ok" ]] || die "OpenRouter ${verdict} that key (${#key} characters; a v1 key is 73) — paste it once and retry"
  model="${ONW_DEMO_MODEL:-}"
  if [[ -z "${model}" && -t 0 ]]; then read -r -p "Model [anthropic/claude-sonnet-5.5]: " model; fi
  model="${model:-anthropic/claude-sonnet-5.5}"
  payload="$(KEY="${key}" MODEL="${model}" python3 -c 'import json,os; print(json.dumps({"key": os.environ["KEY"], "model": os.environ["MODEL"]}))')"
  # The code travels base64'd (no quoting through two shells); the key travels on stdin only.
  # DEMO_MODE=0 for this one process: the running demo refuses AI-settings writes from visitors
  # (backend/api/demo_lockdown.py); the host's shell is the operator.
  code="$(base64 <<'PY' | tr -d '\n'
import json, sys
from backend.db.model_config_store import ModelConfigStore
cfg = json.loads(sys.stdin.readline())
store = ModelConfigStore()
store.update_provider("openrouter", {"api_key": cfg["key"], "model": cfg["model"], "enabled": True})
store.update({"default_model": f"openrouter:{cfg['model']}"})
print("==> Lucius now runs on", store.public().get("default_model"))
PY
)"
  printf '%s\n' "${payload}" | remote "docker exec -i -e DEMO_MODE=0 onw-demo python3 -c \"import base64; exec(base64.b64decode('${code}'))\""
  compose restart app
  echo "    Spend is bounded by DEMO_TOKENS_* in ${STACK}/.env (default 30M tokens/day instance-wide);"
  echo "    set a credit limit on the key itself at https://openrouter.ai/settings/keys as the hard stop."
}

cmd_verify() {
  remote "curl -fsS http://127.0.0.1:5175/api/health" && echo
  echo "==> forwarding http://127.0.0.1:15175 → the VM's demo (no login). Ctrl-C to stop."
  ssh "${SSH_OPTS[@]}" -N -L 15175:127.0.0.1:5175 "ubuntu@${IP}"
}

cmd_tunnel() {
  local token
  echo "The tunnel must serve ONLY this demo: every connector of a tunnel serves ALL its hostnames," >&2
  echo "so reusing one that also routes other sites would send their visitors to this VM." >&2
  token="$(read_secret "Cloudflare tunnel token (input hidden): " | tr -d '[:space:]')"
  [[ -n "${token}" ]] || die "no token given"
  printf '%s' "${token}" | remote_env_set TUNNEL_TOKEN
  compose --profile tunnel up -d tunnel
  for _ in $(seq 1 15); do
    remote "docker logs onw-demo-tunnel 2>&1 | grep -q 'Registered tunnel connection'" && break
    sleep 2
  done
  remote "docker logs onw-demo-tunnel 2>&1 | grep -E 'Registered tunnel connection|ERR' | tail -4 | cut -c1-160"
  echo "==> connector is up. Check your public hostname, then stop the demo's previous host."
}

cmd_status() {
  echo "VM: ubuntu@${IP} (${REGION})"
  remote "cd ${STACK} && docker compose --profile tunnel ps && free -h | head -2 && df -h ${BOOK} | tail -1"
}

case "${1:-}" in
  ship|book|lucius|verify|tunnel|status|logs|ssh) need_vm ;;
esac
case "${1:-}" in
  provision) cmd_provision ;;
  ship)      shift; cmd_ship "$@" ;;
  book)      shift
             case "${1:-}" in
               --seed)  cmd_book_seed ;;
               --reset) cmd_book_reset ;;
               *)       cmd_book_copy "${1:-}" ;;
             esac ;;
  lucius)    cmd_lucius ;;
  verify)    cmd_verify ;;
  tunnel)    cmd_tunnel ;;
  status)    cmd_status ;;
  logs)      remote "docker logs --tail 200 -f onw-demo" ;;
  ssh)       shift; remote "$@" ;;
  *) sed -n '2,15p' "$0"; exit 2 ;;
esac
