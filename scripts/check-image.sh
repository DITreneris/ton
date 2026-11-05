#!/usr/bin/env bash
# Fail if the Docker image cannot build, or if the bundled gas profile
# is missing after pip-install inside the image.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

TAG="${TON_GRAM_IMAGE_TAG:-ton-gram-paper}"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required for scripts/check-image.sh" >&2
  exit 2
fi

docker build -t "$TAG" .
docker run --rm --user root "$TAG" python -c "
from pathlib import Path
from ton_gram.config import Settings
from ton_gram.measure import bundled_profile_path, hydrate_cost_model

path = bundled_profile_path()
assert path.is_file(), path
profile = hydrate_cost_model(Settings(data_dir=Path('/tmp/check-image')), None)
assert profile is not None
assert (profile.get('applied') or {}).get('measured_net_cost_p90_nano') == 210479331
print('image_ok', path)
"

echo "docker image check passed: $TAG"
