#!/usr/bin/env bash
set -euo pipefail
source_dir=$1
revision=$2
[[ "$revision" =~ ^[0-9a-f]{40}$ ]] || exit 2
[[ $EUID == 0 ]] || { echo 'Run through SSM as root'; exit 2; }
exec 9>/var/lock/ai-gate-deploy.lock
flock -n 9 || { echo 'Another deployment is active'; exit 1; }
docker compose version >/dev/null
# Fail closed: never silently store application data on the root volume.
mountpoint -q /srv/ai-gate
config=/etc/ai-gate/production.env
test -s "$config"
[[ $(stat -c %u "$config") == 0 ]]
[[ $(stat -c %a "$config") == 600 ]]
install -d -o 10001 -g 10001 /srv/ai-gate/data /srv/ai-gate/models
install -d /opt/ai-gate/releases
release="/opt/ai-gate/releases/$revision"
# Never overwrite a previous release, which may be the rollback target.
if [[ ! -d "$release" ]]; then
  mkdir "$release"
  cp -a "$source_dir/." "$release/"
fi
cat > "$release/deploy/release.yaml" <<YAML
services:
  gate:
    image: ai-gate-system:$revision
YAML
compose() {
  local dir=$1; shift
  docker compose --project-name ai-gate --env-file "$config" \
    -f "$dir/deploy/compose.yaml" -f "$dir/deploy/release.yaml" "$@"
}
compose "$release" config -q
docker build -t "ai-gate-system:$revision" "$release"
# Pull before replacing a working container.
compose "$release" pull proxy
previous=$(readlink -f /opt/ai-gate/current || true)
if compose "$release" up -d --no-build --wait --wait-timeout 180; then
  ln -sfn "$release" /opt/ai-gate/current
  echo "Deployment healthy: $revision"
else
  echo 'Deployment failed; restoring previous release if present' >&2
  if [[ -n "$previous" && -d "$previous" ]]; then
    compose "$previous" up -d --no-build --wait --wait-timeout 180 || {
      echo 'ROLLBACK FAILED: manual intervention required' >&2; exit 1;
    }
  else
    compose "$release" stop || true
  fi
  exit 1
fi
